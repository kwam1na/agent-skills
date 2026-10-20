#!/usr/bin/env python3
"""Sync, check and merge one candidate at a time, and run releases.

Run with ``python3 -B``. The runner reads the repository's binding
(``orchestration-binding/1``) from the base, works only in detached temporary
checkouts under the binding's ``workRoot``, calls only ``git`` and the argv the
binding declares, and never runs a shell. Every subcommand writes a
``merge-queue-result/1`` document, journal entries, and only the state fields
the runner owns, through the state tool.

Subcommands: ``sync``, ``check``, ``merge``, ``run`` (sync, check and merge in
order, stopping at the first failure) and ``release``.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import contextlib
import datetime
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

import delivery_state  # noqa: E402
from delivery_state import load_schema, validate  # noqa: E402


BINDING_PATH = ".agents/orchestration.json"
RESULT_VERSION = "merge-queue-result/1"
PLACEHOLDERS = frozenset({
    "candidate", "branch", "head", "expectedHead", "base", "baseHead", "treeSha", "ref", "workRoot",
})
PLACEHOLDER = re.compile(r"^\{([A-Za-z]+)\}$")
# Reasons that stop the whole queue rather than one candidate.
QUEUE_STOPPING = frozenset({"base_broken", "merged_unverified"})
# Reasons for which the runner attempted nothing and leaves the queue as it was.
REFUSALS = frozenset({
    "queue_locked", "candidate_unknown", "already_merged", "binding_invalid", "not_synced",
    "ref_invalid", "ref_not_on_base", "approval_required",
})
IDENTITY = ("merge-queue", "merge-queue@localhost")
PRIVATE_REF = "refs/merge-queue"
STATE_PATCH_ATTEMPTS = 5
# The skill's renderer wrapper, called after every state write.
RENDER_WRAPPER = Path(__file__).resolve().parent / "render_state.py"
RENDER_TIMEOUT_SECONDS = 120
# Settings the runner's own git commands depend on, pinned on every call so a
# user's global or system config cannot change what the runner commits, sees
# or merges. They are pinned rather than isolated: dropping global and system
# config would also drop the sign-in helpers, url rewrites and ssh settings
# that fetch and push need. A pin overrides every config level, the
# repository's own included; settings outside this set, and .gitattributes,
# still apply, and argv the binding declares keeps the user's full config.
PINNED_GIT_CONFIG = (
    # Nothing is signed or required to be signed, so no step fails or waits on a signing prompt.
    "commit.gpgsign=false",
    "push.gpgSign=false",
    "merge.verifySignatures=false",
    # Bytes are committed as tools wrote them, unless the repository's own
    # .gitattributes says otherwise; a global attributes file never applies.
    "core.autocrlf=false",
    "core.safecrlf=false",
    f"core.attributesFile={os.devnull}",
    # A global excludes file never hides an undeclared change from status.
    f"core.excludesFile={os.devnull}",
    # Renames are always followed, so a rename never looks like a conflict;
    # a renamed directory never silently moves a file the candidate added there.
    "merge.renames=true",
    "merge.directoryRenames=conflict",
    # A resolution recorded in the repository never resolves a conflict for the runner.
    "rerere.enabled=false",
    "rerere.autoUpdate=false",
    # Defence in depth, for settings no fixture can make bite: diffs follow
    # renames, status reads the files rather than a cache, nothing opens an
    # editor, and nothing reaches into submodules.
    "diff.renames=true",
    "core.fsmonitor=false",
    "core.untrackedCache=false",
    "core.editor=true",
    "submodule.recurse=false",
)
# Folders git keeps in its own directory; workRoot never sits in one of them.
GIT_OWN_DIRECTORIES = frozenset({"objects", "refs", "worktrees", "hooks", "info", "logs", "modules", "lfs", "rr-cache"})
# How long a killed process's output is drained before the runner stops waiting.
DRAIN_SECONDS = 5
# Inherited variables that would point git somewhere other than the runner's checkouts.
GIT_LOCATION_VARIABLES = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
})


def now() -> str:
    return delivery_state.now()


class Stop(Exception):
    """The runner stopped with one of the result schema's reasons."""

    def __init__(self, reason: str, detail: str, paths: Iterable[str] = ()) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.paths = sorted(set(paths))


# ---------------------------------------------------------------------------
# Paths and patterns


def pattern_regex(pattern: str) -> re.Pattern[str]:
    """``*`` matches within one segment and ``**`` any number of segments."""
    parts = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            parts.append(".*")
            index += 2
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(parts) + r"\Z")


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(pattern_regex(pattern).match(path) for pattern in patterns)


def inside(root: Path, relative: str) -> Path | None:
    """``relative`` resolved under ``root``, or None when it leaves it."""
    if Path(relative).is_absolute():
        return None
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


# ---------------------------------------------------------------------------
# Binding


def binding_errors(binding: object) -> list[str]:
    errors = validate(binding, load_schema("orchestration-binding/1"))
    if errors or not isinstance(binding, dict):
        return errors
    commands: list[tuple[str, object]] = [
        ("/candidate", binding["candidate"]),
        ("/ready", binding["ready"]),
        ("/merge", binding["merge"]),
        ("/fullHealth", binding["fullHealth"]),
    ]
    commands += [(f"/prepare/{i}", argv) for i, argv in enumerate(binding["prepare"])]
    commands += [(f"/regenerable/regenerators/{i}", argv) for i, argv in enumerate(binding["regenerable"]["regenerators"])]
    commands += [(f"/postSync/{i}", step["argv"]) for i, step in enumerate(binding["postSync"])]
    commands += [(f"/leadInputs/{i}", item["argv"]) for i, item in enumerate(binding["leadInputs"])]
    commands += [(f"/release/{i}", target["argv"]) for i, target in enumerate(binding["release"])]
    for index, lease in enumerate(binding["leases"]):
        for key in ("materialize", "withdraw"):
            if key in lease:
                commands.append((f"/leases/{index}/{key}", lease[key]))
    if binding["provider"] is not None:
        commands.append(("/provider", binding["provider"]["argv"]))
    for where, argv in commands:
        if not argv:
            errors.append(f"{where}: an argv must not be empty")
        for element in argv:  # type: ignore[union-attr]
            match = PLACEHOLDER.match(element)
            if match and match[1] not in PLACEHOLDERS:
                errors.append(f"{where}: unknown placeholder {element}")
    if "{expectedHead}" not in binding["merge"]:
        errors.append("/merge: the merge argv must use {expectedHead} as a whole element")
    if binding["checkTimeoutSeconds"] > binding["checkTimeoutCeiling"]:
        errors.append("/checkTimeoutSeconds: above checkTimeoutCeiling")
    for name in ("alwaysRun", "fallback"):
        problems = selection_errors(binding[name], binding, check_cwd=None)
        errors.extend(f"/{name}: {problem}" for problem in problems)
        for check in binding[name]:
            profile = check.get("environmentProfile")
            if profile is not None and profile not in binding["profiles"]:
                errors.append(f"/{name}: {check['id']} names unknown environment profile {profile}")
    leases = {lease["id"] for lease in binding["leases"]}
    for index, item in enumerate(binding["leadInputs"]):
        if "lease" in item and item["lease"] not in leases:
            errors.append(f"/leadInputs/{index}: unknown lease {item['lease']}")
    if len({item["id"] for item in binding["leadInputs"]}) != len(binding["leadInputs"]):
        errors.append("/leadInputs: ids must be unique")
    if len({target["id"] for target in binding["release"]}) != len(binding["release"]):
        errors.append("/release: ids must be unique")
    return errors


def selection_errors(checks: list[dict], binding: dict, check_cwd: Path | None) -> list[str]:
    """Problems with a list of checks: the provider contract's semantic rules."""
    problems = []
    ids = [check["id"] for check in checks]
    if len(ids) != len(set(ids)):
        problems.append("check ids must be unique")
    selected = set(ids)
    for check in checks:
        if not check["argv"]:
            problems.append(f"{check['id']}: argv must not be empty")
        missing = [name for name in check["prerequisites"] if name not in selected]
        if missing:
            problems.append(f"{check['id']}: prerequisites name unselected checks {missing}")
        timeout = check.get("timeoutSeconds")
        if timeout is not None and timeout > binding["checkTimeoutCeiling"]:
            problems.append(f"{check['id']}: timeoutSeconds above the ceiling")
        if check_cwd is not None and inside(check_cwd, check["cwd"]) is None:
            problems.append(f"{check['id']}: cwd leaves the checkout")
        if check_cwd is None and (Path(check["cwd"]).is_absolute() or ".." in Path(check["cwd"]).parts):
            problems.append(f"{check['id']}: cwd leaves the checkout")
    if not problems and order_checks(checks) is None:
        problems.append("prerequisites form a cycle")
    return problems


def order_checks(checks: list[dict]) -> list[dict] | None:
    """Prerequisites first, otherwise in the given order; None on a cycle."""
    remaining = list(checks)
    done: set[str] = set()
    ordered = []
    while remaining:
        ready = [check for check in remaining if all(name in done for name in check["prerequisites"])]
        if not ready:
            return None
        first = ready[0]
        ordered.append(first)
        done.add(first["id"])
        remaining.remove(first)
    return ordered


# ---------------------------------------------------------------------------
# Processes


@dataclass
class Completed:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    seconds: float


def run_process(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float | None = None,
    stdin: str | None = None,
) -> Completed:
    """Run ``argv`` without a shell, in its own process group.

    On timeout the whole group is killed, so children a check spawned go with
    it. A missing executable is reported as a failure, not raised.
    """
    started = time.monotonic()
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Explicit UTF-8, never the locale's encoding: a Windows code page
            # cannot decode or encode every character a tool may print.
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
    except (FileNotFoundError, PermissionError, NotADirectoryError) as error:
        return Completed(None, "", str(error), False, time.monotonic() - started)
    try:
        stdout, stderr = process.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_group(process)
        try:
            stdout, stderr = process.communicate(timeout=DRAIN_SECONDS)
        except subprocess.TimeoutExpired:
            # Something outside the group still holds the pipes: stop waiting for it.
            with contextlib.suppress(OSError):
                process.kill()
            for stream in (process.stdout, process.stderr):
                with contextlib.suppress(OSError):
                    stream.close()  # type: ignore[union-attr]
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=DRAIN_SECONDS)
            stdout, stderr = "", ""
        return Completed(None, stdout, stderr, True, time.monotonic() - started)
    return Completed(process.returncode, stdout, stderr, False, time.monotonic() - started)


def kill_group(process: subprocess.Popen) -> None:
    """Kill a timed-out process and everything it started.

    On POSIX the process leads its own session, so the whole group goes. On
    Windows the process starts in a new process group and ``taskkill /T``
    ends its tree; a descendant that already detached from the tree survives,
    and the bounded drain in ``run_process`` keeps the runner from waiting on it.
    """
    if os.name == "nt":
        with contextlib.suppress(OSError):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
        with contextlib.suppress(OSError):
            process.kill()
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


# ---------------------------------------------------------------------------
# The runner


@dataclass
class Context:
    """Values a placeholder can take. Missing ones are refused when used."""

    values: dict[str, str] = field(default_factory=dict)

    def with_values(self, **values: str | None) -> "Context":
        merged = dict(self.values)
        merged.update({key: value for key, value in values.items() if value is not None})
        return Context(merged)

    def element(self, element: str) -> str:
        match = PLACEHOLDER.match(element)
        if not match or match[1] not in PLACEHOLDERS:
            return element
        if match[1] not in self.values:
            raise Stop("binding_invalid", f"{element} has no value at this step")
        return self.values[match[1]]

    def argv(self, argv: list[str]) -> list[str]:
        return [self.element(element) for element in argv]


@dataclass
class Result:
    subcommand: str
    candidate: str | None
    startedAt: str = field(default_factory=now)
    outcome: str = "stopped"
    stop: dict | None = None
    base: str | None = None
    baseHead: str | None = None
    head: str | None = None
    syncedHead: str | None = None
    mergedHead: str | None = None
    hooksBypassed: bool = False
    selection: dict | None = None
    checks: list[dict] = field(default_factory=list)
    release: dict | None = None
    notes: list[str] = field(default_factory=list)

    def document(self) -> dict:
        return {
            "schemaVersion": RESULT_VERSION,
            "subcommand": self.subcommand,
            "candidate": self.candidate,
            "outcome": self.outcome,
            "stop": self.stop,
            "startedAt": self.startedAt,
            "finishedAt": now(),
            "base": self.base,
            "baseHead": self.baseHead,
            "head": self.head,
            "syncedHead": self.syncedHead,
            "mergedHead": self.mergedHead,
            "hooksBypassed": self.hooksBypassed,
            "selection": self.selection,
            "checks": self.checks,
            "release": self.release,
            "notes": self.notes,
        }


Seam = Callable[["Runner", str], None]


class Runner:
    """One runner invocation against one repository.

    ``seams`` maps a step name to a callable run at that point, so tests can
    make every race deterministic. Names: ``after_resolve``, ``after_sync``,
    ``after_check`` and ``before_merge``.
    """

    def __init__(
        self,
        repository: Path,
        state_directory: Path,
        *,
        remote: str,
        base: str,
        lead: str = "lead",
        lock_wait: float = 0.0,
        seams: dict[str, Seam] | None = None,
        renderer: list[str] | None = None,
        # ``renderer`` is the interpreter prefix the wrapper runs under.
    ) -> None:
        self.repository = repository.resolve()
        self.state_directory = state_directory.resolve()
        self.remote = remote
        self.base = base
        self.lead = lead
        self.lock_wait = lock_wait
        self.seams = seams or {}
        self.renderer = renderer or [sys.executable, "-B"]
        # Notes collected while a subcommand runs, such as a failed render.
        self.notes: list[str] = []
        self.binding: dict = {}
        self.work_root = Path()
        # Until the binding is read, git runs with a private empty hooks
        # directory, so even the first fetch fires no repository hook.
        self._bootstrap_hooks = Path(tempfile.mkdtemp(prefix="merge-queue-hooks-"))
        self.hooks_directory: Path | None = self._bootstrap_hooks
        self.hooks_bypassed = False
        self.checkouts: list[Path] = []
        self._counter = 0

    # -- git ---------------------------------------------------------------

    def environment(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {
            key: value for key, value in os.environ.items()
            if key not in GIT_LOCATION_VARIABLES and not re.match(r"GIT_CONFIG_(KEY|VALUE)_\d+\Z", key)
        }
        env.update(self.binding.get("environment", {}))
        if extra:
            env.update(extra)
        env.update({
            "GIT_AUTHOR_NAME": IDENTITY[0],
            "GIT_AUTHOR_EMAIL": IDENTITY[1],
            "GIT_COMMITTER_NAME": IDENTITY[0],
            "GIT_COMMITTER_EMAIL": IDENTITY[1],
            "GIT_TERMINAL_PROMPT": "0",
        })
        if self.hooks_directory is not None:
            # Every git a declared argv runs gets the same empty hooks directory.
            env.update({
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.hooksPath",
                "GIT_CONFIG_VALUE_0": str(self.hooks_directory),
            })
        return env

    def git(self, *args: str, cwd: Path | None = None, check: bool = True, stdin: str | None = None) -> Completed:
        prefix = ["git"]
        for setting in PINNED_GIT_CONFIG:
            prefix += ["-c", setting]
        if self.hooks_directory is not None:
            prefix += ["-c", f"core.hooksPath={self.hooks_directory}"]
        result = run_process([*prefix, *args], cwd=cwd or self.repository, env=self.environment(), stdin=stdin)
        if check and result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result

    def rev(self, revision: str, cwd: Path | None = None) -> str:
        return self.git("rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}", cwd=cwd).stdout.strip()

    def tree(self, revision: str, cwd: Path | None = None) -> str:
        return self.git("rev-parse", "--verify", "--end-of-options", f"{revision}^{{tree}}", cwd=cwd).stdout.strip()

    def valid_branch(self, name: str) -> bool:
        return (
            not name.startswith("-")
            and self.git("check-ref-format", "--branch", name, check=False).returncode == 0
        )

    def fetch(self, branch: str, private: str) -> str:
        """Fetch ``branch`` into a private ref and return its commit.

        Only refs under the runner's own namespace change; the operator's
        branches and checkout are never written.
        """
        if not self.valid_branch(branch):
            raise Stop("ref_invalid", f"not a valid branch name: {branch}")
        target = f"{PRIVATE_REF}/{private}"
        self.git("fetch", "--no-tags", "--quiet", self.remote, f"+refs/heads/{branch}:{target}")
        return self.rev(target)

    # -- checkouts ---------------------------------------------------------

    def start(self) -> None:
        """Prune what an earlier, killed run left behind under workRoot."""
        checkouts = self.work_root / "checkouts"
        if checkouts.exists():
            shutil.rmtree(checkouts)
        self.git("worktree", "prune")
        checkouts.mkdir(parents=True, exist_ok=True)

    def checkout(self, commit: str, label: str, prepare: bool = True, context: Context | None = None) -> Path:
        self._counter += 1
        path = self.work_root / "checkouts" / f"{self._counter:02d}-{label}"
        self.git("worktree", "add", "--detach", "--quiet", str(path), commit)
        self.checkouts.append(path)
        if prepare:
            context = (context or Context()).with_values(treeSha=self.tree("HEAD", cwd=path))
            for argv in self.binding["prepare"]:
                completed = self.run_argv(argv, path, context, self.binding["checkTimeoutSeconds"])
                if completed.returncode != 0:
                    what = "timed out" if completed.timed_out else f"failed: {completed.stderr.strip()[-400:]}"
                    raise Stop("prepare_failed", f"prepare {argv[0]} {what} in the fresh {label} checkout of {commit}")
        return path

    def remove_checkouts(self) -> None:
        for path in self.checkouts:
            self.git("worktree", "remove", "--force", str(path), check=False)
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
        self.checkouts = []
        self.git("worktree", "prune", check=False)

    def run_argv(self, argv: list[str], cwd: Path, context: Context, timeout: float, extra_env: dict | None = None, stdin: str | None = None) -> Completed:
        resolved = context.argv(argv)
        env = {key: context.element(value) for key, value in (extra_env or {}).items()}
        return run_process(resolved, cwd=cwd, env=self.environment(env), timeout=timeout, stdin=stdin)

    def changed_paths(self, checkout: Path) -> list[str]:
        status = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all", cwd=checkout).stdout
        paths = []
        entries = status.split("\0")
        index = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            code, path = entry[:2], entry[3:]
            paths.append(path)
            if "R" in code or "C" in code:
                paths.append(entries[index])
                index += 1
        return sorted(set(paths))

    # -- state -------------------------------------------------------------

    def state(self) -> dict:
        return delivery_state.read_state(self.state_directory)

    def item(self, subject: str) -> dict:
        for element in self.state()["queue"]:
            if element["subject"] == subject:
                return element
        raise Stop("candidate_unknown", f"{subject} is not in the queue")

    def write(self, *, changes: dict | None = None, selector: str | None = None, assignments: dict | None = None) -> None:
        """Write runner-owned fields, retrying when the lead wrote first."""
        for _ in range(STATE_PATCH_ATTEMPTS):
            version = self.state()["version"]
            try:
                delivery_state.patch(
                    self.state_directory,
                    writer="runner",
                    version=version,
                    changes=changes,
                    selector=selector,
                    assignments=assignments,
                )
                break
            except delivery_state.StateError as error:
                if error.code != "version_stale":
                    raise
        else:
            raise delivery_state.StateError("version_stale", "the state kept changing under the runner")
        self.render()

    def set_item(self, subject: str, **fields: object) -> None:
        self.write(selector=f"queue[subject={subject}]", assignments={f"/{key}": value for key, value in fields.items()})

    def journal(self, kind: str, summary: str, subject: str | None = None) -> None:
        lane = None
        if subject is not None:
            with contextlib.suppress(Stop):
                lane = self.item(subject).get("lane")
        delivery_state.append_journal(self.state_directory, kind=kind, summary=summary, lane=lane, subject=subject)
        self.render()

    def checking(self, subject: str | None, title: str = "", steps: list[dict] | None = None) -> None:
        value = None if subject is None else {
            "subject": subject, "title": title, "startedAt": now(), "steps": steps or [],
        }
        self.write(assignments={"/meta/checking/now": value})

    def render(self) -> None:
        """Run the renderer wrapper after a state write.

        A failing wrapper, renderer or publisher is recorded in the result's
        notes and never fails the runner.
        """
        if not self.binding or not RENDER_WRAPPER.exists():
            return
        completed = run_process(
            [*self.renderer, str(RENDER_WRAPPER), "--dir", str(self.state_directory),
             "--state-config", json.dumps(self.binding["state"])],
            cwd=self.repository, env=self.environment(), timeout=RENDER_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            what = "timed out" if completed.timed_out else f"exited {completed.returncode}"
            note = f"rendering the state {what}"
            if note not in self.notes:
                self.notes.append(note)

    def seam(self, name: str, subject: str) -> None:
        if name in self.seams:
            self.seams[name](self, subject)

    # -- binding -----------------------------------------------------------

    def load(self) -> str:
        """Fetch the base, read the binding from it, and set up workRoot."""
        if not self.valid_branch(self.base):
            raise Stop("ref_invalid", f"not a valid branch name: {self.base}")
        base_head = self.fetch(self.base, "base")
        shown = self.git("show", f"{base_head}:{BINDING_PATH}", check=False)
        if shown.returncode != 0:
            raise Stop("binding_invalid", f"the base has no {BINDING_PATH}")
        try:
            binding = json.loads(shown.stdout)
        except json.JSONDecodeError as error:
            raise Stop("binding_invalid", f"{BINDING_PATH} is not JSON: {error}") from None
        errors = binding_errors(binding)
        if errors:
            raise Stop("binding_invalid", "; ".join(errors))
        if binding["base"] != {"remote": self.remote, "ref": self.base}:
            raise Stop("binding_invalid", "the binding names a different base than the one it was read from")
        self.binding = binding
        root = Path(binding["workRoot"])
        self.work_root = (root if root.is_absolute() else self.repository / root).resolve()
        if self.work_root == self.repository or self.repository.is_relative_to(self.work_root):
            raise Stop("binding_invalid", "workRoot must not contain the repository's checkout")
        git_directory = Path(self.git("rev-parse", "--absolute-git-dir").stdout.strip()).resolve()
        if self.work_root.is_relative_to(git_directory):
            # Beside git's own data, never in it.
            parts = self.work_root.relative_to(git_directory).parts
            if not parts or parts[0] in GIT_OWN_DIRECTORIES:
                raise Stop("binding_invalid", "workRoot must not be the git directory or one of git's own folders")
        elif self.work_root.is_relative_to(self.repository):
            # The runner deletes what it finds under workRoot, so inside the
            # working tree it must hold no tracked file and be a path the
            # repository ignores. The operator's checkout supplies the ignore
            # rules that apply to it. The path is checked as a directory, so a
            # "name/" ignore rule matches before it exists.
            relative = self.work_root.relative_to(self.repository).as_posix() + "/"
            if self.git("ls-files", "--", relative, check=False).stdout.strip():
                raise Stop("binding_invalid", "workRoot must not hold tracked files")
            if self.git("check-ignore", "-q", "--no-index", "--", relative, check=False).returncode != 0:
                raise Stop("binding_invalid", "a workRoot inside the working tree must be ignored")
        self.hooks_bypassed = binding["hooks"] == "bypass"
        if self.hooks_bypassed:
            self.hooks_directory = self.work_root / "empty-hooks"
            self.hooks_directory.mkdir(parents=True, exist_ok=True)
            for leftover in self.hooks_directory.iterdir():
                if leftover.is_dir():
                    shutil.rmtree(leftover)
                else:
                    leftover.unlink()
        else:
            self.hooks_directory = None
        return base_head

    def context(self, subject: str | None, base_head: str) -> Context:
        values = {"base": self.base, "baseHead": base_head, "workRoot": str(self.work_root)}
        if subject is not None:
            values["candidate"] = subject
        return Context(values)

    def resolve(self, subject: str, context: Context, cwd: Path) -> tuple[str, str]:
        completed = self.run_argv(self.binding["candidate"], cwd, context, self.binding["checkTimeoutSeconds"])
        try:
            resolved = json.loads(completed.stdout) if completed.returncode == 0 else None
        except json.JSONDecodeError:
            resolved = None
        if (
            not isinstance(resolved, dict)
            or not isinstance(resolved.get("branch"), str)
            or not isinstance(resolved.get("head"), str)
            or not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", resolved["head"])
        ):
            raise Stop("candidate_unresolved", f"{subject} did not resolve to a branch and head")
        branch = resolved["branch"]
        if not self.valid_branch(branch):
            raise Stop("ref_invalid", f"{subject} resolved to an invalid branch name")
        if branch == self.base:
            raise Stop("ref_invalid", f"{subject} resolved to the base branch")
        return branch, resolved["head"]

    # -- sync --------------------------------------------------------------

    def declared_paths(self, item: dict) -> list[str]:
        inputs = {entry["id"]: entry for entry in self.binding["leadInputs"]}
        paths = list(self.binding["regenerable"]["paths"])
        for step in self.binding["postSync"]:
            paths += step["paths"]
        for name in item["leadInputs"]:
            paths += inputs[name]["paths"]
        return paths

    def sync(self, subject: str, result: Result) -> None:
        item = self.item(subject)
        if item["stage"] == "merged":
            raise Stop("already_merged", f"{subject} is already merged")
        base_head = self.load()
        result.base, result.baseHead = self.base, base_head
        context = self.context(subject, base_head)
        anchor = self.checkout(base_head, "base", prepare=False)
        branch, head = self.resolve(subject, context, anchor)
        result.head = head
        self.seam("after_resolve", subject)
        if head not in (item["head"], item["syncedHead"]):
            raise Stop("candidate_moved", f"{subject} is at {head}, not the reviewed or last synced head")
        tip = self.fetch(branch, "candidate")
        if tip != head:
            raise Stop("candidate_moved", f"{branch} moved to {tip} after {subject} resolved to {head}")
        inputs = {entry["id"]: entry for entry in self.binding["leadInputs"]}
        unknown = [name for name in item["leadInputs"] if name not in inputs]
        if unknown:
            raise Stop("binding_invalid", f"the binding declares no lead input {unknown}")
        self.set_item(subject, stage="syncing")
        self.checking(subject, item["title"], [{"label": "sync", "state": "running"}])
        context = context.with_values(branch=branch, head=head)
        checkout = self.checkout(head, "sync", prepare=True, context=context.with_values(treeSha=self.tree(head)))
        self.merge_base_in(checkout, base_head, branch)
        declared = self.declared_paths(item)
        # Each pass that changes anything is committed; nothing is pushed until
        # every check below passes, so a stop leaves the branch as it was.
        touched: set[str] = set()
        for attempt in (1, 2):
            pass_context = context.with_values(treeSha=self.tree("HEAD", cwd=checkout))
            self.run_pass(subject, item, checkout, pass_context, inputs)
            changed = self.changed_paths(checkout)
            if not changed:
                break
            touched.update(changed)
            if attempt == 2:
                raise Stop("unstable_regeneration", "regeneration still changed files on its second pass", changed)
            self.git("add", "--all", cwd=checkout)
            self.git("commit", "--quiet", "-m", f"Regenerate after syncing {branch} with {self.base}", cwd=checkout)
        undeclared = [path for path in sorted(touched) if not matches_any(path, declared)]
        if undeclared:
            raise Stop("undeclared_change", "a sync step changed files outside the declared paths", undeclared)
        self.seam("after_sync", subject)
        self.compare_with_review(checkout, item["head"], base_head, declared)
        synced = self.rev("HEAD", cwd=checkout)
        self.push(branch, head, synced, subject)
        result.syncedHead = synced
        self.set_item(subject, stage="synced", syncedHead=synced)
        self.write(assignments={"/meta/baseHead": base_head})
        result.outcome = "synced"

    def merge_base_in(self, checkout: Path, base_head: str, branch: str) -> None:
        merged = self.git("merge", "--no-ff", "--no-commit", "--no-edit", base_head, cwd=checkout, check=False)
        conflicts = sorted(set(filter(None, self.git(
            "diff", "--name-only", "-z", "--diff-filter=U", cwd=checkout).stdout.split("\0"))))
        if merged.returncode != 0 and not conflicts:
            raise RuntimeError(f"merging the base failed: {merged.stderr.strip()}")
        outside = [path for path in conflicts if not matches_any(path, self.binding["regenerable"]["paths"])]
        if outside:
            self.git("merge", "--abort", cwd=checkout, check=False)
            raise Stop("source_conflict", "the base conflicts outside the regenerable paths", outside)
        for path in conflicts:
            # Take the base side; a path the base deleted stays deleted.
            if self.git("cat-file", "-e", f"{base_head}:{path}", cwd=checkout, check=False).returncode == 0:
                self.git("checkout", "--theirs", "--", path, cwd=checkout)
                self.git("add", "--", path, cwd=checkout)
            else:
                self.git("rm", "--quiet", "--force", "--", path, cwd=checkout)
        if self.git("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=checkout, check=False).returncode == 0:
            # Committed before anything else runs, so every later step sees the new merge base.
            self.git("commit", "--quiet", "-m", f"Merge {self.base} into {branch}", cwd=checkout)

    def run_pass(self, subject: str, item: dict, checkout: Path, context: Context, inputs: dict) -> None:
        timeout = self.binding["checkTimeoutSeconds"]
        for name in item["leadInputs"]:
            self.run_lead_input(inputs[name], checkout, context)
        for argv in self.binding["regenerable"]["regenerators"]:
            self.step(argv, checkout, context, timeout, "regenerator")
        for entry in self.binding["postSync"]:
            self.step(entry["argv"], checkout, context, timeout, "postSync step")

    def step(self, argv: list[str], checkout: Path, context: Context, timeout: float, what: str) -> None:
        completed = self.run_argv(argv, checkout, context, timeout)
        if completed.timed_out:
            raise Stop("sync_step_failed", f"{what} {argv[0]} timed out")
        if completed.returncode != 0:
            raise Stop("sync_step_failed", f"{what} {argv[0]} failed: {completed.stderr.strip()[-400:]}")

    def run_lead_input(self, entry: dict, checkout: Path, context: Context) -> None:
        timeout = entry.get("timeoutSeconds", self.binding["checkTimeoutSeconds"])
        lease_id = entry.get("lease")
        if lease_id is None:
            self.step(entry["argv"], checkout, context, timeout, f"lead input {entry['id']}")
            return
        held = [lease for lease in self.state()["leases"] if lease["id"] == lease_id]
        if not held or held[0]["holder"] != self.lead:
            raise Stop("lease_required", f"lead input {entry['id']} needs lease {lease_id}, which the lead does not hold")
        lease = next(item for item in self.binding["leases"] if item["id"] == lease_id)
        failure: Stop | None = None
        try:
            if "materialize" in lease:
                self.step(lease["materialize"], checkout, context, timeout, f"lease {lease_id} materialize")
            self.step(entry["argv"], checkout, context, timeout, f"lead input {entry['id']}")
        except Stop as stop:
            failure = stop
        finally:
            # Withdrawn on every exit, before anything is committed.
            if "withdraw" in lease:
                completed = self.run_argv(lease["withdraw"], checkout, context, timeout)
                if completed.returncode != 0:
                    raise Stop("lease_withdraw_failed", f"withdrawing lease {lease_id} failed; nothing is pushed")
        if failure is not None:
            raise failure

    def compare_with_review(self, checkout: Path, reviewed: str, base_head: str, declared: list[str]) -> None:
        """The synced tree equals the reviewed head merged with the base, outside declared paths."""
        merged = self.git("merge-tree", "--write-tree", "--no-messages", reviewed, base_head, cwd=checkout, check=False)
        if merged.returncode not in (0, 1) or not merged.stdout.strip():
            raise RuntimeError(f"git merge-tree failed: {merged.stderr.strip()}")
        expected = self.listing(merged.stdout.split()[0], checkout, declared)
        actual = self.listing(self.tree("HEAD", cwd=checkout), checkout, declared)
        differing = sorted({path for path in set(expected) | set(actual) if expected.get(path) != actual.get(path)})
        if differing:
            raise Stop("unreviewed_change", "the synced tree differs from the reviewed head merged with the base", differing)

    def listing(self, tree: str, checkout: Path, declared: list[str]) -> dict[str, str]:
        listed = self.git("ls-tree", "-r", "-z", "--full-tree", tree, cwd=checkout).stdout
        entries = {}
        for line in filter(None, listed.split("\0")):
            meta, path = line.split("\t", 1)
            if not matches_any(path, declared):
                entries[path] = meta
        return entries

    def push(self, branch: str, resolved: str, synced: str, subject: str) -> None:
        if branch == self.base:
            raise Stop("ref_invalid", "the runner never pushes to the base branch")
        pushed = self.git(
            "push", "--quiet", f"--force-with-lease=refs/heads/{branch}:{resolved}",
            self.remote, f"{synced}:refs/heads/{branch}", check=False,
        )
        if pushed.returncode != 0:
            current = self.git("ls-remote", "--heads", self.remote, f"refs/heads/{branch}", check=False).stdout.split()
            if current and current[0] != resolved:
                raise Stop("candidate_moved", f"{branch} moved during sync; nothing was pushed")
            raise Stop("push_refused", f"pushing {branch} failed: {pushed.stderr.strip()[-400:]}")

    # -- check -------------------------------------------------------------

    def check(self, subject: str, result: Result) -> None:
        item = self.item(subject)
        synced = item["syncedHead"]
        if synced is None or item["stage"] not in ("synced", "checking", "checked", "stopped"):
            raise Stop("not_synced", f"{subject} has no synced head to check")
        base_head = self.load()
        result.base, result.baseHead, result.syncedHead = self.base, base_head, synced
        context = self.context(subject, base_head)
        anchor = self.checkout(base_head, "resolve", prepare=False)
        branch, head = self.resolve(subject, context, anchor)
        result.head = head
        if head != synced or self.fetch(branch, "candidate") != synced:
            raise Stop("candidate_moved", f"{subject} is no longer at its synced head")
        if self.git("merge-base", "--is-ancestor", base_head, synced, check=False).returncode != 0:
            raise Stop("base_moved", f"{self.base} moved since {subject} was synced")
        context = context.with_values(branch=branch, head=synced, expectedHead=synced)
        self.set_item(subject, stage="checking", checks=[])
        self.checking(subject, item["title"], [{"label": "check", "state": "running"}])
        self.journal("check.started", f"Checking {item['title']}", subject)
        checkout = self.checkout(synced, "check", context=context.with_values(treeSha=self.tree(synced)))
        self.assert_clean(checkout, "a fresh checkout of the synced head")
        changes = self.changes(base_head, synced)
        selection, checks = self.select(subject, base_head, synced, changes, checkout, context)
        result.selection = selection
        if not checks:
            if changes:
                raise Stop("no_checks", "nothing would check this candidate's changes")
            result.notes.append("the candidate changes nothing, so no check was selected")
        self.set_item(subject, checks=[{"id": check["id"], "state": "pending"} for check in checks])
        outcomes = self.run_checks(subject, checks, checkout, context.with_values(treeSha=self.tree(synced)), result)
        self.assert_clean(checkout, "the checkout after the checks")
        failed = [check for check in checks if outcomes[check["id"]] in ("failed", "timed_out")]
        if failed:
            self.classify(failed, checks, checkout, base_head, changes, context, result)
        self.set_item(subject, stage="checked")
        self.write(assignments={"/meta/baseHead": base_head})
        self.journal("check.finished", f"Checks passed for {item['title']}", subject)
        result.outcome = "checked"

    def assert_clean(self, checkout: Path, what: str) -> None:
        changed = self.changed_paths(checkout)
        if changed:
            raise Stop("dirty_checkout", f"{what} is not clean", changed)

    def changes(self, base_head: str, head: str) -> list[dict]:
        listed = self.git("diff", "--name-status", "-z", "-M", "-C", base_head, head).stdout.split("\0")
        changes = []
        index = 0
        while index < len(listed) - 1:
            status = listed[index]
            index += 1
            if not status:
                continue
            letter = status[0]
            if letter in ("R", "C"):
                changes.append({"path": listed[index + 1], "status": letter, "oldPath": listed[index]})
                index += 2
            else:
                changes.append({"path": listed[index], "status": letter})
                index += 1
        return changes

    def provider(self, source: str, checkout: Path, request: dict, context: Context) -> tuple[dict, list[dict], bool, list[str]]:
        """Run one provider. Returns its record, its checks, widened, and uncovered."""
        settings = self.binding["provider"]
        if settings is None:
            return {"source": source, "outcome": "missing", "detail": "the binding declares no provider"}, [], False, []
        cwd = inside(checkout, settings["cwd"])
        if cwd is None or not cwd.is_dir():
            return {"source": source, "outcome": "missing", "detail": "the provider's cwd is not in the checkout"}, [], False, []
        completed = self.run_argv(settings["argv"], cwd, context, settings["timeoutSeconds"], stdin=json.dumps(request))
        if completed.timed_out:
            return {"source": source, "outcome": "timed_out", "detail": f"no answer within {settings['timeoutSeconds']}s"}, [], False, []
        if completed.returncode is None:
            return {"source": source, "outcome": "missing", "detail": completed.stderr.strip()[-400:]}, [], False, []
        if completed.returncode != 0:
            return {"source": source, "outcome": "failed", "detail": f"exited {completed.returncode}: {completed.stderr.strip()[-400:]}"}, [], False, []
        try:
            answer = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            return {"source": source, "outcome": "malformed", "detail": f"not JSON: {error}"}, [], False, []
        problems = validate(answer, load_schema("affected-checks/1"))
        if not problems:
            problems = selection_errors(answer["checks"], self.binding, check_cwd=checkout)
            problems += self.profile_errors(answer["checks"])
            # A provider never replaces the binding's own checks.
            reserved = {check["id"] for check in [*self.binding["alwaysRun"], *self.binding["fallback"]]}
            problems += [f"{check['id']}: reuses the id of a binding check" for check in answer["checks"] if check["id"] in reserved]
        if problems:
            return {"source": source, "outcome": "malformed", "detail": "; ".join(problems)[:800]}, [], False, []
        return (
            {"source": source, "outcome": "selected", "detail": f"{len(answer['checks'])} checks"},
            answer["checks"], answer["widened"], answer["uncovered"],
        )

    def profile_errors(self, checks: list[dict]) -> list[str]:
        known = self.binding["profiles"]
        return [
            f"{check['id']}: unknown environment profile {check['environmentProfile']}"
            for check in checks
            if "environmentProfile" in check and check["environmentProfile"] not in known
        ]

    def select(
        self, subject: str, base_head: str, synced: str, changes: list[dict], checkout: Path, context: Context,
    ) -> tuple[dict, list[dict]]:
        request = {"base": self.base, "baseHead": base_head, "head": synced, "changes": changes}
        providers = []
        selected: list[dict] = []
        widened = False
        uncovered: list[str] = []
        reasons: list[str] = []
        base_checkout = self.checkout(base_head, "provider", context=context.with_values(treeSha=self.tree(base_head)))
        runs = [("base", base_checkout)]
        settings = self.binding["provider"]
        inputs = settings["providerInputs"] if settings else []
        touched = [change for change in changes if matches_any(change["path"], inputs) or matches_any(change.get("oldPath", ""), inputs)]
        if touched:
            runs.append(("candidate", checkout))
        for source, where in runs:
            record, checks, more, missing = self.provider(source, where, request, context)
            providers.append(record)
            if record["outcome"] != "selected":
                reasons.append(f"provider_{record['outcome']}")
                continue
            widened = widened or more
            uncovered += missing
            # The base provider decides; the candidate's can only add to it.
            if source == "base" and changes and not checks:
                reasons.append("empty_selection")
            for check in checks:
                if check["id"] not in {item["id"] for item in selected}:
                    selected.append(check)
        if uncovered:
            reasons.append("uncovered")
        final = list(selected)
        for check in self.binding["alwaysRun"]:
            if check["id"] not in {item["id"] for item in final}:
                final.append(check)
        if reasons:
            widened = True
            for check in self.binding["fallback"]:
                if check["id"] not in {item["id"] for item in final}:
                    final.append(check)
        problems = selection_errors(final, self.binding, check_cwd=checkout)
        if problems:
            raise Stop("binding_invalid", "; ".join(problems))
        unknown = self.profile_errors(final)
        if unknown:
            raise Stop("profile_unknown", "; ".join(unknown))
        ordered = order_checks(final) or final
        selection = {
            "providers": providers,
            "fallbackReasons": sorted(set(reasons)),
            "widened": widened,
            "uncovered": sorted(set(uncovered)),
            "checks": [check["id"] for check in ordered],
        }
        return selection, ordered

    def run_one(self, check: dict, checkout: Path, context: Context) -> Completed | None:
        cwd = inside(checkout, check["cwd"])
        if cwd is None:
            return None
        timeout = check.get("timeoutSeconds", self.binding["checkTimeoutSeconds"])
        profile = self.binding["profiles"].get(check.get("environmentProfile", ""), {"environment": {}})
        return self.run_argv(check["argv"], cwd, context, timeout, extra_env=profile["environment"])

    def run_checks(self, subject: str, checks: list[dict], checkout: Path, context: Context, result: Result) -> dict[str, str]:
        outcomes: dict[str, str] = {}
        for check in checks:
            if any(outcomes.get(name) != "passed" for name in check["prerequisites"]):
                outcomes[check["id"]] = "skipped"
                result.checks.append({"id": check["id"], "state": "skipped", "exitCode": None, "seconds": 0})
                continue
            completed = self.run_one(check, checkout, context)
            assert completed is not None
            state = "timed_out" if completed.timed_out else "passed" if completed.returncode == 0 else "failed"
            outcomes[check["id"]] = state
            result.checks.append({
                "id": check["id"], "state": state, "exitCode": completed.returncode,
                "seconds": round(completed.seconds, 3),
            })
            self.set_item(subject, checks=[
                {"id": item["id"], "state": outcomes.get(item["id"], "pending")} for item in checks
            ])
        return outcomes

    def classify(self, failed: list[dict], selected: list[dict], checkout: Path, base_head: str, changes: list[dict], context: Context, result: Result) -> None:
        """A failure is the base's own only when the base fails the same check unchanged."""
        changed = {change["path"] for change in changes} | {change.get("oldPath") for change in changes}
        candidates = []
        root = checkout.resolve()
        for check in failed:
            cwd = inside(checkout, check["cwd"]) or root
            # The check's own directory is a path it names, as is every argv element that exists.
            named = [] if cwd == root else [cwd.relative_to(root).as_posix()]
            for element in check["argv"]:
                path = inside(cwd, element)
                if path is not None and path.exists() and path != cwd:
                    named.append(path.relative_to(checkout.resolve()).as_posix())
            unchanged = all(
                path not in changed and self.git("cat-file", "-e", f"{base_head}:{path}", check=False).returncode == 0
                for path in named
            )
            if unchanged:
                candidates.append(check)
        if candidates:
            base_checkout = self.checkout(base_head, "base-rerun", context=context.with_values(treeSha=self.tree(base_head)))
            base_context = context.with_values(head=base_head, treeSha=self.tree(base_head))
            by_id = {check["id"]: check for check in selected}
            for check in candidates:
                if not self.base_prerequisites_pass(check, by_id, base_checkout, base_context):
                    # The check cannot be judged on the base; the failure stays the candidate's.
                    continue
                completed = self.run_one(check, base_checkout, base_context)
                # A re-run that could not start says nothing about the base.
                started = completed is not None and (completed.timed_out or completed.returncode is not None)
                if started and (completed.timed_out or completed.returncode != 0):
                    raise Stop("base_broken", f"{check['id']} also fails on {self.base} at {base_head}")
        raise Stop("check_failed", "failing checks: " + ", ".join(check["id"] for check in failed))

    def base_prerequisites_pass(self, check: dict, by_id: dict[str, dict], checkout: Path, context: Context) -> bool:
        """Run a check's prerequisites, in order, on the base re-run checkout."""
        needed: list[str] = []

        def collect(name: str) -> None:
            for prerequisite in by_id[name]["prerequisites"]:
                if prerequisite not in needed:
                    collect(prerequisite)
                    needed.append(prerequisite)

        collect(check["id"])
        for name in needed:
            completed = self.run_one(by_id[name], checkout, context)
            if completed is None or completed.timed_out or completed.returncode != 0:
                return False
        return True

    # -- merge -------------------------------------------------------------

    def merge(self, subject: str, result: Result) -> None:
        item = self.item(subject)
        synced = item["syncedHead"]
        if synced is None or item["stage"] != "checked":
            raise Stop("not_synced", f"{subject} has not passed its checks at a synced head")
        base_head = self.load()
        result.base, result.baseHead, result.syncedHead = self.base, base_head, synced
        context = self.context(subject, base_head)
        anchor = self.checkout(base_head, "merge", prepare=False)
        self.seam("before_merge", subject)
        branch, head = self.resolve(subject, context, anchor)
        result.head = head
        if head != synced:
            raise Stop("candidate_moved", f"{subject} moved from its checked head {synced} to {head}")
        base_head = self.fetch(self.base, "base")
        result.baseHead = base_head
        # The candidate was checked with the base its synced head contains. Any
        # base commit the synced head lacks went unchecked with this candidate.
        if self.git("merge-base", "--is-ancestor", base_head, synced, check=False).returncode != 0:
            raise Stop("base_moved", f"{self.base} moved to {base_head}, which {subject}'s checked head does not contain")
        self.set_item(subject, stage="merging")
        context = context.with_values(branch=branch, head=synced, expectedHead=synced, baseHead=base_head, treeSha=self.tree(synced))
        timeout = self.binding["checkTimeoutSeconds"]
        if self.run_argv(self.binding["ready"], anchor, context, timeout).returncode != 0:
            raise Stop("ready_refused", f"ready refused {subject}")
        if self.run_argv(self.binding["merge"], anchor, context, timeout).returncode != 0:
            raise Stop("merge_refused", f"merge refused {subject} at {synced}")
        merged = self.fetch(self.base, "base")
        result.mergedHead = merged
        parent = self.git("rev-parse", "--verify", "-q", f"{merged}^1", check=False).stdout.strip()
        if self.tree(merged) != self.tree(synced) or parent != base_head:
            raise Stop("merged_unverified", f"{self.base} at {merged} is not {synced}'s tree on top of {base_head}")
        self.set_item(subject, stage="merged")
        self.write(assignments={"/meta/baseHead": merged})
        self.journal("candidate.merged", f"Merged {item['title']} at {synced}", subject)
        result.outcome = "merged"

    # -- release -----------------------------------------------------------

    def release(self, ref: str, approved_by: str | None, result: Result) -> None:
        base_head = self.load()
        result.base, result.baseHead = self.base, base_head
        if ref.startswith("-") or not re.fullmatch(r"[0-9A-Za-z._/@{}^~-]+", ref):
            raise Stop("ref_invalid", f"not a usable ref: {ref}")
        resolved = self.git("rev-parse", "--verify", "-q", "--end-of-options", f"{ref}^{{commit}}", check=False).stdout.strip()
        if not resolved:
            raise Stop("ref_invalid", f"{ref} does not name a commit")
        if self.git("merge-base", "--is-ancestor", resolved, base_head, check=False).returncode != 0:
            raise Stop("ref_not_on_base", f"{ref} is not {self.base} or one of its ancestors")
        targets = self.binding["release"]
        needing = [target["id"] for target in targets if target["requiresApproval"]]
        if needing and not approved_by:
            raise Stop("approval_required", f"targets {needing} need --approved-by")
        result.release = {"ref": ref, "commit": resolved, "targets": []}
        self.journal("release.started", f"Release of {resolved} started")
        context = self.context(None, base_head).with_values(ref=resolved, head=resolved)
        checkout = self.checkout(resolved, "release", context=context.with_values(treeSha=self.tree(resolved)))
        context = context.with_values(treeSha=self.tree(resolved))
        stopped = False
        for target in targets:
            if stopped:
                result.release["targets"].append({"id": target["id"], "state": "skipped", "exitCode": None, "approvedBy": None})
                continue
            timeout = target.get("timeoutSeconds", self.binding["checkTimeoutCeiling"])
            completed = self.run_argv(target["argv"], checkout, context, timeout)
            state = "timed_out" if completed.timed_out else "passed" if completed.returncode == 0 else "failed"
            result.release["targets"].append({
                "id": target["id"], "state": state, "exitCode": completed.returncode,
                "approvedBy": approved_by if target["requiresApproval"] else None,
            })
            if state != "passed" and target["stopsOnFailure"]:
                stopped = True
        if stopped:
            failed = [target["id"] for target in result.release["targets"] if target["state"] in ("failed", "timed_out")]
            self.journal("release.finished", f"Release of {resolved} stopped at {failed[-1]}")
            raise Stop("release_failed", f"release target {failed[-1]} failed")
        self.write(assignments={"/meta/releasedHead": resolved})
        self.journal("release.finished", f"Released {resolved}")
        result.outcome = "released"

    # -- driving -----------------------------------------------------------

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        lock = self.state_directory / delivery_state.QUEUE_LOCK
        try:
            with delivery_state.file_lock(lock, timeout=self.lock_wait):
                yield
        except delivery_state.StateError as error:
            if error.code == "lock_timeout":
                raise Stop("queue_locked", "another runner holds the queue") from None
            raise

    def invoke(self, subcommand: str, subject: str | None, step: Callable[[Result], None]) -> Result:
        result = Result(subcommand, subject)
        self.notes = []
        try:
            step(result)
        except Stop as stop:
            self.stopped(result, stop, subject)
        except Exception as error:  # noqa: BLE001 - every run ends with a result document
            self.stopped(result, Stop("runner_error", f"{type(error).__name__}: {error}"[:800]), subject)
        finally:
            result.hooksBypassed = self.hooks_bypassed
            self.remove_checkouts()
            with contextlib.suppress(Exception):
                if subject is not None and result.outcome != "refused":
                    self.checking(None)
            result.notes.extend(note for note in self.notes if note not in result.notes)
        return result

    def stopped(self, result: Result, stop: Stop, subject: str | None) -> None:
        queue_stopped = stop.reason in QUEUE_STOPPING
        result.stop = {"reason": stop.reason, "detail": stop.detail, "paths": stop.paths, "queueStopped": queue_stopped}
        refused = stop.reason in REFUSALS
        result.outcome = "refused" if refused else "stopped"
        if stop.reason == "queue_locked":
            return
        with contextlib.suppress(Stop, delivery_state.StateError):
            # A refusal leaves the queue as it was, but never a stage mid-step.
            in_progress = subject is not None and self.item(subject)["stage"] in ("syncing", "checking", "merging")
            if subject is not None and (not refused or in_progress):
                self.set_item(subject, stage="stopped")
            if stop.reason == "base_broken":
                self.journal("base.broken", stop.detail, subject)
            if subject is not None:
                self.journal("candidate.stopped", f"{stop.reason}: {stop.detail}", subject)

    def prepared(self, subcommand: str, subject: str | None, step: Callable[[Result], None]) -> Result:
        def guarded(result: Result) -> None:
            self.load()
            self.start()
            step(result)
        return self.invoke(subcommand, subject, guarded)

    def run_command(self, subcommand: str, subjects: list[str], approved_by: str | None = None) -> list[Result]:
        try:
            with self.locked():
                return self._run_locked(subcommand, subjects, approved_by)
        except Stop as stop:
            result = Result(subcommand, subjects[0] if subjects and subcommand != "release" else None)
            self.stopped(result, stop, None)
            return [result]
        finally:
            shutil.rmtree(self._bootstrap_hooks, ignore_errors=True)

    def _run_locked(self, subcommand: str, subjects: list[str], approved_by: str | None) -> list[Result]:
        if subcommand == "release":
            return [self.prepared("release", None, lambda result: self.release(subjects[0], approved_by, result))]
        steps = {"sync": self.sync, "check": self.check, "merge": self.merge}
        if subcommand in steps:
            return [self.prepared(subcommand, subjects[0], lambda result: steps[subcommand](subjects[0], result))]
        results: list[Result] = []
        for subject in subjects:
            outcome = self._run_one(subject, results)
            if outcome != "merged":
                break
        return results

    def _run_one(self, subject: str, results: list[Result]) -> str:
        """Sync, check and merge one candidate; a base that moves once is re-synced."""
        for attempt in (1, 2):
            for name, step in (("sync", self.sync), ("check", self.check), ("merge", self.merge)):
                result = self.prepared(name, subject, lambda result, step=step: step(subject, result))
                results.append(result)
                if result.outcome == "stopped" or result.outcome == "refused":
                    if result.stop and result.stop["reason"] == "base_moved" and attempt == 1:
                        break
                    return "stopped"
            else:
                return "merged"
        return "stopped"


# ---------------------------------------------------------------------------
# Command line


def write_result(state_directory: Path, result: Result) -> dict:
    document = result.document()
    errors = validate(document, load_schema("merge-queue-result/1"))
    if errors:
        raise RuntimeError("the runner built an invalid result: " + "; ".join(errors))
    directory = state_directory / "results"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = document["finishedAt"].replace(":", "").replace("-", "").replace(".", "")
    name = f"{stamp}-{document['subcommand']}-{re.sub(r'[^A-Za-z0-9_.-]', '_', document['candidate'] or 'release')}.json"
    # UTF-8 bytes with "\n" line endings on every platform.
    (directory / name).write_bytes(delivery_state.dumps(document).encode("utf-8"))
    return document


def ascii_json(value: object) -> bytes:
    """JSON for stdout: ASCII-only, so no console code page can fail on it."""
    return (json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="the repository the queue serves")
    parser.add_argument("--state", type=Path, required=True, help="the delivery state directory")
    parser.add_argument("--remote", required=True, help="the remote the base and candidates live on")
    parser.add_argument("--base", required=True, help="the base branch; its binding must name the same base")
    parser.add_argument("--lead", default="lead", help="the lease holder name the lead uses in the state")
    parser.add_argument("--wait", type=float, default=0.0, help="seconds to wait for the queue lock")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("sync", "check", "merge"):
        commands.add_parser(name).add_argument("candidate")
    commands.add_parser("run").add_argument("candidates", nargs="+")
    release = commands.add_parser("release")
    release.add_argument("ref")
    release.add_argument("--approved-by", help="records an approval the operator already gave")
    args = parser.parse_args(argv)
    runner = Runner(
        args.repo, args.state, remote=args.remote, base=args.base, lead=args.lead,
        lock_wait=args.wait,
    )
    if args.command == "release":
        results = runner.run_command("release", [args.ref], args.approved_by)
    elif args.command == "run":
        results = runner.run_command("run", args.candidates)
    else:
        results = runner.run_command(args.command, [args.candidate])
    documents = [write_result(runner.state_directory, result) for result in results]
    sys.stdout.buffer.write(ascii_json(documents if args.command == "run" else documents[0]))
    sys.stdout.buffer.flush()
    return 0 if all(document["outcome"] not in ("stopped", "refused") for document in documents) else 1


if __name__ == "__main__":
    raise SystemExit(main())
