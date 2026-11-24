"""Co-authored plan text: compose two authors' edits section by section, push them, and watch for a peer's.

`plan-sync` and `plan-watch` in run_delivery.py are this module's commands. A plan's sections are its Markdown
headings' own text, found by the orchestration scripts' region rules (`region_ownership`, passed in as `markdown`),
and the text before its first heading, front matter included, is one more section, `(preamble)`. Composing never runs
git's three-way merge on the plan: a section changed on only one side takes that side's text, and a section changed on
both is refused, by name, with both diffs. Nothing is ever force-pushed.
"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

PREAMBLE = "(preamble)"
# A push rejected for being behind recomposes and tries again this many times, the bound the state tool's own
# reconciliation uses; the next rejection stops.
RECONCILE_ATTEMPTS = 5
BEHIND = ("non-fast-forward", "fetch first", "stale info")


class Refused(Exception):
    """A refusal with its code, such as `plan.sections_overlap`; its text is the code, then what to do."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


# -- sections -------------------------------------------------------------------------------------------------------

def sections(text: str, markdown) -> list[tuple[tuple, str, str]]:
    """The plan's sections in order, each `(key, name, text)`. The texts join back to the plan exactly.

    A heading's section is its own text, up to the next heading of any level. Its key is the chain of heading lines
    enclosing it, by `markdown.markdown_block`, and the heading itself, with an ordinal when that chain repeats, so
    `### Scope` under two different stories are two sections, and a section keeps its key when another moves."""
    lines = text.splitlines(keepends=True)
    bare = [line.rstrip("\n") for line in lines]
    headings = sorted(markdown.markdown_headings(bare))
    found = [((PREAMBLE,), PREAMBLE, "".join(lines[:headings[0] - 1] if headings else lines))]
    seen: dict[tuple, int] = {}
    reach = {line: markdown.markdown_block(bare, line)[1] for line in headings}
    for at, line in enumerate(headings):
        end = headings[at + 1] if at + 1 < len(headings) else len(lines) + 1
        chain = tuple(bare[outer - 1].strip() for outer in headings[:at] if reach[outer] > line) \
            + (bare[line - 1].strip(),)
        seen[chain] = seen.get(chain, 0) + 1
        found.append(((*chain, seen[chain]), bare[line - 1].strip(), "".join(lines[line - 1:end - 1])))
    return found


def diff(name: str, before: str | None, after: str | None, side: str) -> str:
    said = difflib.unified_diff((before or "").splitlines(keepends=True), (after or "").splitlines(keepends=True),
                                f"base: {name}", f"{side}: {name}")
    return "".join(line if line.endswith("\n") else line + "\n" for line in said)


def compose(base: str, ours: str, theirs: str, markdown) -> str:
    """Theirs, with every section ours changed since base applied to it; refused when a section changed on both."""
    b, o, t = ({key: (name, body) for key, name, body in sections(text, markdown)} for text in (base, ours, theirs))
    body = lambda side, key: side[key][1] if key in side else None
    overlap = []
    for key in dict.fromkeys([*b, *o, *t]):
        was, mine, yours = body(b, key), body(o, key), body(t, key)
        if mine != was and yours != was and mine != yours:
            name = (o.get(key) or t.get(key) or b.get(key))[0]
            overlap.append(f"{name}\n{diff(name, was, mine, 'ours')}{diff(name, was, yours, 'theirs')}")
    if overlap:
        raise Refused("plan.sections_overlap",
                      f"{len(overlap)} section{'' if len(overlap) == 1 else 's'} changed on both sides; settle "
                      "each in your copy, so it reads as theirs or as you mean it to, then sync again:\n\n"
                      + "\n".join(overlap))
    result = [(key, text) for key, _, text in sections(theirs, markdown)]
    ordered = [key for key, _, _ in sections(ours, markdown)]
    for key in ordered:
        mine, was = body(o, key), body(b, key)
        if mine == was or key in [k for k, _ in result] and body(t, key) == mine:
            continue
        keys = [k for k, _ in result]
        if key in keys:
            result[keys.index(key)] = (key, mine)  # edited
            continue
        before = next((k for k in reversed(ordered[:ordered.index(key)]) if k in keys), (PREAMBLE,))
        result.insert(keys.index(before) + 1, (key, mine))  # added, after its nearest surviving predecessor
    for key in b:
        if key not in o:
            result = [(k, text) for k, text in result if k != key]  # deleted
    return "".join(text for _, text in result)


def changed_sections(before: str | None, after: str, markdown) -> list[str]:
    """The names of the sections `after` added, edited or deleted since `before`, in `after`'s order."""
    was = {key: (name, text) for key, name, text in sections(before or "", markdown)}
    now = sections(after, markdown)
    names = [name for key, name, text in now if was.get(key, (None, None))[1] != text]
    keys = {key for key, _, _ in now}
    return names + [name for key, (name, _) in was.items() if key not in keys]


# -- git ------------------------------------------------------------------------------------------------------------

def git(root: Path, *args: str, check: bool = True, env: dict | None = None, input: str | None = None) -> str:
    done = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, input=input,
                          env={**os.environ, **env} if env else None)
    if check and done.returncode != 0:
        raise Refused("plan.git_failed", f"git {' '.join(args[:3])} failed: {(done.stderr or done.stdout).strip()[-600:]}")
    return done.stdout.strip()


def resolved(root: Path, ref: str) -> str | None:
    return git(root, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}", check=False) or None


def ancestor(root: Path, older: str, newer: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", older, newer], cwd=root,
                          capture_output=True).returncode == 0


def shown(root: Path, commit: str, path: str) -> str | None:
    done = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=root, capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


def tracking(remote: str, branch: str) -> str:
    return f"refs/remotes/{remote}/{branch}"


def fetch(root: Path, remote: str, branch: str) -> str | None:
    """The remote branch's head, fetched into its remote-tracking ref by an explicit refspec, so no fetch setting
    changes; None when the remote has no such branch."""
    done = subprocess.run(["git", "fetch", "-q", "--no-tags", "--no-write-fetch-head", remote,
                           f"+refs/heads/{branch}:{tracking(remote, branch)}"], cwd=root, capture_output=True, text=True)
    if done.returncode != 0:
        if "couldn't find remote ref" in done.stderr:
            return None
        raise Refused("plan.git_failed", f"fetching {branch} from {remote} failed: {done.stderr.strip()[-600:]}")
    return resolved(root, tracking(remote, branch))


def push(root: Path, remote: str, branch: str) -> str | None:
    """Push HEAD to the branch, never forced: None when it landed, `behind` when the remote moved on, otherwise
    refused with git's reason."""
    done = subprocess.run(["git", "push", "--porcelain", remote, f"HEAD:refs/heads/{branch}"], cwd=root,
                          capture_output=True, text=True)
    if done.returncode == 0:
        return None
    said = done.stdout + done.stderr
    if any(reason in said for reason in BEHIND):
        return "behind"
    raise Refused("plan.git_failed", f"pushing {branch} to {remote} failed: {said.strip()[-600:]}")


def merged_tree(root: Path, ours: str, theirs: str, path: str, text: str) -> str:
    """Both commits' trees merged, with the plan at `path` replaced by its composed text. Any other file both sides
    changed apart is refused: plan-sync composes the plan, and nothing else."""
    done = subprocess.run(["git", "merge-tree", "--write-tree", "--name-only", "--no-messages", ours, theirs],
                          cwd=root, capture_output=True, text=True)
    if done.returncode not in (0, 1):
        raise Refused("plan.git_failed", f"git merge-tree failed: {done.stderr.strip()[-600:]}")
    tree, *conflicted = done.stdout.split("\n")
    others = sorted({name for name in conflicted if name and name != path})
    if others:
        raise Refused("plan.files_overlap", f"{', '.join(others)} changed on both sides; plan-sync composes only the "
                      "plan, so merge the branch yourself, then sync again")
    blob = git(root, "hash-object", "-w", "--stdin", input=text)
    mode = git(root, "ls-tree", ours, "--", path).split(" ", 1)[0] or "100644"
    with tempfile.TemporaryDirectory(prefix="plan-sync-") as folder:
        env = {"GIT_INDEX_FILE": str(Path(folder) / "index")}
        git(root, "read-tree", tree.strip(), env=env)
        git(root, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{path}", env=env)
        return git(root, "write-tree", env=env)


def checked_out(root: Path, branch: str) -> None:
    current = git(root, "symbolic-ref", "-q", "--short", "HEAD", check=False)
    if current != branch:
        exists = resolved(root, f"refs/heads/{branch}")
        start = f"git switch {branch}" if exists else f"git switch -c {branch}"
        raise Refused("plan.branch_not_checked_out",
                      f"the plan syncs on {branch}, but this checkout is on {current or 'a detached HEAD'}; run "
                      f"`{start}` here first")


def sync(root: Path, path: str, branch: str, remote: str, markdown, *, message: str | None = None,
         before_push: Callable[[int], None] | None = None) -> list[str]:
    """Compose the plan with the remote branch and push it; returns what it did, a line each.

    The working copy's plan text is ours, committed or not. A refusal before the first commit leaves the branch, the
    working copy and the remote as they were. A push rejected for being behind recomposes and retries, up to
    RECONCILE_ATTEMPTS times; the next rejection stops with `plan.push_exhausted`, leaving the branch at its last
    composed commit and the remote as the peer left it. `before_push(attempt)` runs before each push."""
    checked_out(root, branch)
    file = root / path
    if not file.is_file():
        raise Refused("plan.missing", f"no plan at {file}")
    ours_text = file.read_text(encoding="utf-8")
    said: list[str] = []
    theirs = fetch(root, remote, branch)
    head = resolved(root, "HEAD")
    if theirs and not ancestor(root, theirs, head):
        base = git(root, "merge-base", head, theirs, check=False)
        compose(shown(root, base, path) or "" if base else "", ours_text, shown(root, theirs, path) or "", markdown)
        merged_tree(root, head, theirs, path, ours_text)  # refuses another file's overlap before anything is written
    if shown(root, head, path) != ours_text or git(root, "status", "--porcelain", "--", path):
        git(root, "add", "--", path)
        git(root, "commit", "-q", "-m", message or f"Edit the plan {Path(path).name}", "--", path)
        said.append(f"committed {path}")
    rejected = 0
    while True:
        head = resolved(root, "HEAD")
        if theirs and not ancestor(root, theirs, head):
            if ancestor(root, head, theirs):
                git(root, "merge", "-q", "--ff-only", theirs)
                said.append(f"fast-forwarded {branch} to {remote}'s {theirs[:12]}")
                return said
            base = git(root, "merge-base", head, theirs, check=False)
            text = compose(shown(root, base, path) or "" if base else "", shown(root, head, path) or "",
                           shown(root, theirs, path) or "", markdown)
            tree = merged_tree(root, head, theirs, path, text)
            commit = git(root, "commit-tree", tree, "-p", head, "-p", theirs, "-m",
                         f"Compose {Path(path).name} with {remote}/{branch}")
            git(root, "merge", "-q", "--ff-only", commit)
            said.append(f"composed {path} with {remote}'s {theirs[:12]}")
        if theirs == resolved(root, "HEAD"):
            said.append(f"{branch} is current with {remote}")
            return said
        if before_push:
            before_push(rejected + 1)
        if push(root, remote, branch) is None:
            git(root, "update-ref", tracking(remote, branch), "HEAD")
            said.append(f"pushed {branch} to {remote} at {resolved(root, 'HEAD')[:12]}"
                        + (f", after {rejected} rejected push{'' if rejected == 1 else 'es'}" if rejected else ""))
            return said
        rejected += 1
        if rejected > RECONCILE_ATTEMPTS:
            raise Refused("plan.push_exhausted",
                          f"{remote} rejected {rejected} pushes of {branch}, each time because a peer had pushed "
                          f"first; {branch} stays at its last composed commit, {resolved(root, 'HEAD')[:12]}. "
                          "Sync again")
        theirs = fetch(root, remote, branch)


# -- watching -------------------------------------------------------------------------------------------------------

def push_line(root: Path, path: str, branch: str, markdown, before: str | None, after: str) -> str:
    """One line for a peer push: the branch, its new head, its author and subject, and the sections it changed."""
    author, subject = git(root, "log", "-1", "--format=%an%x00%s", after).split("\x00", 1)
    names = changed_sections(shown(root, before, path) if before else None, shown(root, after, path) or "", markdown)
    count = len(git(root, "rev-list", f"{before}..{after}").split()) if before else None
    what = f"{count} commit{'' if count == 1 else 's'}" if count else "new branch"
    listed = ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else "")
    line = f"{branch} {after[:12]} by {author} ({what}): {subject}; sections: {listed or 'none'}"
    return " ".join(line.split())


def watch(root: Path, path: str, branch: str, remote: str, markdown, *, poll: float, timeout: float,
          until: str | None, write: Callable[[str], None], sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic) -> int:
    """Print one line per peer push of the branch, and nothing else. Exits 0 at the timeout, or with `until`, 0 after
    the first line matching it and 3 at the timeout. Each tick fetches the branch once; it never pushes.

    A push is a peer's when this checkout's own branch doesn't already hold it, so a sync from here shows nothing.
    What this checkout last fetched is where the watch starts, so a push just before it starts is still shown."""
    pattern = re.compile(until) if until is not None else None
    seen = resolved(root, tracking(remote, branch))
    deadline = clock() + timeout
    while True:
        try:
            head = fetch(root, remote, branch)
        except Refused:
            head = seen  # an unreachable remote is tried again on the next tick
        if head and head != seen:
            own = resolved(root, f"refs/heads/{branch}")
            if not (own and ancestor(root, head, own)):
                line = push_line(root, path, branch, markdown, seen, head)
                write(line)
                if pattern and pattern.search(line):
                    return 0
            seen = head
        left = deadline - clock()
        if left <= 0:
            return 3 if pattern else 0
        sleep(min(poll, left))
