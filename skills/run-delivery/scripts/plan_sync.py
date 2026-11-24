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


def pushed_record(root: Path, branch: str) -> Path:
    """Where this clone keeps every commit its own plan-sync pushed to the branch, one id a line, under the git common
    directory, so the clone's worktrees, one seat, share it."""
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    return common / "run-delivery" / "plan-sync" / (branch.replace("/", "%2F") + ".pushed")


def pushed_here(root: Path, branch: str) -> set[str]:
    record = pushed_record(root, branch)
    return set(record.read_text(encoding="utf-8").split()) if record.is_file() else set()


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
         before_push: Callable[[int], None] | None = None, scope: "Held | None" = None) -> list[str]:
    """Compose the plan with the remote branch and push it; returns what it did, a line each.

    The working copy's plan text is ours, committed or not. A refusal before the first commit leaves the branch, the
    working copy and the remote as they were. A push rejected for being behind recomposes and retries, up to
    RECONCILE_ATTEMPTS times; the next rejection stops with `plan.push_exhausted`, leaving the branch at its last
    composed commit and the remote as the peer left it. An overlap found on a retry, after the plan edit was committed,
    puts the branch and the working copy back as they were. `before_push(attempt)` runs before each push.

    With `scope`, the plan is shared: a change this clone makes to a section its seat doesn't hold, in the working copy
    or a commit the remote hasn't got, is refused with `plan.section_not_held` before anything is written."""
    checked_out(root, branch)
    file = root / path
    if not file.is_file():
        raise Refused("plan.missing", f"no plan at {file}")
    ours_text = file.read_text(encoding="utf-8")
    said: list[str] = []
    theirs = fetch(root, remote, branch)
    head = resolved(root, "HEAD")
    if scope is not None:
        scope.check(root, path, remote, ours_text, markdown)
    if theirs and not ancestor(root, theirs, head):
        base = git(root, "merge-base", head, theirs, check=False)
        compose(shown(root, base, path) or "" if base else "", ours_text, shown(root, theirs, path) or "", markdown)
        merged_tree(root, head, theirs, path, ours_text)  # refuses another file's overlap before anything is written
    start = head
    if shown(root, head, path) != ours_text or git(root, "status", "--porcelain", "--", path):
        git(root, "add", "--", path)
        git(root, "commit", "-q", "-m", message or f"Edit the plan {Path(path).name}", "--", path)
        said.append(f"committed {path}")
    try:
        return pushed(root, path, branch, remote, markdown, theirs, said, before_push)
    except Refused as error:
        if error.code in OVERLAPS:
            # A peer's push between tries overlapped: the branch and the working copy go back to how they were.
            git(root, "reset", "-q", "--keep", start)
            file.write_text(ours_text, encoding="utf-8")
        raise


# Refusals that leave the branch as it was before the sync, wherever they are raised.
OVERLAPS = ("plan.sections_overlap", "plan.files_overlap")


def pushed(root: Path, path: str, branch: str, remote: str, markdown, theirs: str | None, said: list[str],
           before_push: Callable[[int], None] | None) -> list[str]:
    """Compose HEAD with the remote's `theirs` and push it, recomposing after each rejection, within the bound."""
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
        # The commits this push brings, which no ref of the remote held when it was fetched: this clone's own.
        # Kept before the push, so a watch in this clone never reads its own push as a peer's while it lands; a push
        # that is rejected only leaves this clone's own commits listed, which they are.
        brought = git(root, "rev-list", "HEAD", "--not", f"--remotes={remote}").split()
        record = pushed_record(root, branch)
        record.parent.mkdir(parents=True, exist_ok=True)
        with record.open("a", encoding="utf-8") as kept:
            kept.write("".join(f"{commit}\n" for commit in brought))
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


# -- shared plans ---------------------------------------------------------------------------------------------------

KEY_CHARACTERS = r"[A-Za-z0-9_]"


def rekeyed(text: str, mapping: dict[str, str]) -> str:
    """The plan with each placeholder story key replaced by its tracker key, all at once, as whole keys only: `COLLAB-4`
    becomes `V26-3798` in `COLLAB-4:` and `COLLAB-4-1`, never in `COLLAB-40` or `XCOLLAB-4`."""
    if not mapping:
        return text
    keys = sorted(mapping, key=len, reverse=True)
    pattern = re.compile(rf"(?<!{KEY_CHARACTERS})(?:{'|'.join(map(re.escape, keys))})(?!{KEY_CHARACTERS})")
    return pattern.sub(lambda found: mapping[found.group(0)], text)


class Held:
    """What one seat may change in a shared plan: `held(text)` names the sections of a plan text it holds, as the
    state tool's `held_sections` does; `rekeys` are the record's `plan.rekeyed` data; `ask(names)` is the command that
    asks for sections."""

    def __init__(self, held: Callable[[str], list[str]], rekeys: list[dict], ask: Callable[[list[str]], str]):
        self.held, self.rekeys, self.ask = held, rekeys, ask

    def exempt(self, root: Path, path: str, commit: str, before: str | None, after: str | None) -> bool:
        """A re-keying commit: one a `plan.rekeyed` entry names, whose plan is its parent's with that map applied. The
        exemption is the commit's, never a section's, so a later commit to the same sections is checked as any is."""
        return any(entry.get("to") == commit and before is not None
                   and after == rekeyed(before, entry.get("map") or {}) for entry in self.rekeys)

    def changes(self, root: Path, path: str, remote: str, text: str) -> list[tuple[str, str | None, str]]:
        """What this clone changed in the plan that the remote hasn't got: each commit of its own that changed it, a
        composing merge and a re-keying left out, as `(commit, before, after)`, then the working copy's edit as
        `("the working copy", before, after)`."""
        found = []
        own = git(root, "rev-list", "--reverse", "--no-merges", "HEAD", "--not", f"--remotes={remote}", "--", path)
        for commit in own.split():
            before, after = shown(root, f"{commit}^", path), shown(root, commit, path) or ""
            if before != after and not self.exempt(root, path, commit, before, after):
                found.append((commit, before, after))
        head = shown(root, "HEAD", path)
        if head != text:
            found.append(("the working copy", head, text))
        return found

    def check(self, root: Path, path: str, remote: str, text: str, markdown) -> None:
        outside = {}
        for where, before, after in self.changes(root, path, remote, text):
            held = {*self.held(before or ""), *self.held(after)}
            for name in changed_sections(before, after, markdown):
                if name not in held:
                    outside.setdefault(name, []).append(where if where == "the working copy" else where[:12])
        if outside:
            listed = "; ".join(f"{name} (in {', '.join(where)})" for name, where in outside.items())
            raise Refused("plan.section_not_held",
                          f"this seat changed {len(outside)} section{'' if len(outside) == 1 else 's'} it doesn't "
                          f"hold: {listed}. Nothing was committed or pushed. Ask for "
                          f"{'it' if len(outside) == 1 else 'them'} with {self.ask(list(outside))}, or put the "
                          "change back")


def endorsements(journal: list[dict]) -> dict[str, list[str]]:
    """Each seat's endorsed plan commits, in the order it endorsed them."""
    found: dict[str, list[str]] = {}
    for entry in journal:
        if entry["kind"] == "plan.endorsed" and entry.get("origin"):
            found.setdefault(entry["origin"]["seat"], []).append(entry["data"]["commit"])
    return found


def blocking_feedback(journal: list[dict]) -> list[dict]:
    """Every `feedback.raised` that stops approval and no `feedback.resolved` answers."""
    answered = {target for entry in journal if entry["kind"] == "feedback.resolved"
                for target in entry.get("answers", [])}
    return [entry for entry in journal if entry["kind"] == "feedback.raised" and entry["data"].get("blocking") is True
            and entry["id"] not in answered]


def aligned_to(aligned: str | None, current: str, journal: list[dict], text_at: Callable[[str], str | None]) -> bool:
    """Whether the plan at `current` reads as it did at the commit plan review aligned on, or as that text with each
    `plan.rekeyed` map applied in turn, each re-keying starting from the text the one before it left."""
    if aligned is None:
        return False
    text, target = text_at(aligned), text_at(current)
    if text is None or target is None:
        return False
    for entry in journal:
        if text == target:
            return True
        if entry["kind"] == "plan.rekeyed" and text_at(entry["data"]["from"]) == text \
                and text_at(entry["data"]["to"]) == rekeyed(text, entry["data"]["map"]):
            text = text_at(entry["data"]["to"])
    return text == target


def approval_refusal(journal: list[dict], seats: dict, current: str, aligned: str | None,
                     text_at: Callable[[str], str | None]) -> Refused | None:
    """Why a shared plan isn't jointly approved at `current`, by the first of D11's four rules it fails; None when every
    admitted approver seat endorsed it, it reads as plan review aligned it, and no blocking feedback is open."""
    approvers = [seat for seat, about in seats.items() if about.get("admitted") and about.get("approver")]
    given = endorsements(journal)
    label = lambda seat: f"{seats[seat].get('label') or seat} ({seat})"
    missing = [seat for seat in approvers if not given.get(seat)]
    if missing:
        return Refused("plan.endorsement_missing",
                       f"{', '.join(map(label, missing))} {'has' if len(missing) == 1 else 'have'} not endorsed the "
                       f"plan; each approver seat runs lead-plan endorse at {current[:12]}")
    stale = [seat for seat in approvers if current not in given[seat]]
    if stale:
        return Refused("plan.endorsement_stale",
                       "; ".join(f"{label(seat)} endorsed {given[seat][-1][:12]}" for seat in stale)
                       + f", not the plan's current commit {current[:12]}; each runs lead-plan endorse again")
    if not aligned_to(aligned, current, journal, text_at):
        said = "no plan review round aligned on it" if aligned is None else \
            f"plan review aligned on {aligned[:12]}, and {current[:12]} reads otherwise beyond its recorded re-keyings"
        return Refused("plan.not_aligned", f"the endorsed plan isn't the one plan review aligned on: {said}; review "
                       "the change in another round, then endorse again")
    open_feedback = blocking_feedback(journal)
    if open_feedback:
        return Refused("plan.feedback_blocking",
                       f"{len(open_feedback)} piece{'' if len(open_feedback) == 1 else 's'} of feedback that stop "
                       f"approval {'is' if len(open_feedback) == 1 else 'are'} open: "
                       + "; ".join(f"entry {entry['id']} on {entry['data']['anchor']}" for entry in open_feedback))
    return None


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
    the first line matching it and 3 at the timeout. Each tick fetches the branch once; it never pushes. A watch none
    of whose fetches reached the remote ends with the last fetch's refusal instead.

    A push is a peer's unless every commit it brings is one this clone's own plan-sync pushed (`pushed_record`): seats,
    not people, so two clones under one git identity are still each other's peers. A sync from here shows nothing, and
    a peer's push shows even when a sync here composed over it before the next tick. What this checkout last fetched
    is where the watch starts, so a push just before it starts is still shown."""
    pattern = re.compile(until) if until is not None else None
    seen = resolved(root, tracking(remote, branch))
    deadline = clock() + timeout
    reached, failed = False, None
    while True:
        try:
            head = fetch(root, remote, branch)
            reached = True
        except Refused as error:
            head, failed = seen, error  # an unreachable remote is tried again on the next tick
        if head and head != seen:
            brought = git(root, "rev-list", *([f"{seen}..{head}"] if seen else ["-1", head])).split()
            if not set(brought) <= pushed_here(root, branch):
                line = push_line(root, path, branch, markdown, seen, head)
                write(line)
                if pattern and pattern.search(line):
                    return 0
            seen = head
        left = deadline - clock()
        if left <= 0:
            if not reached and failed:
                raise failed
            return 3 if pattern else 0
        sleep(min(poll, left))
