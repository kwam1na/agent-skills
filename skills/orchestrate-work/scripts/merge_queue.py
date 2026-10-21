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
import concurrent.futures
import contextlib
import copy
import datetime
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePath
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
QUEUE_STOPPING = frozenset({"base_broken", "merged_unverified", "batch_tree_mismatch"})
# Reasons for which the runner attempted nothing and leaves the queue as it was.
REFUSALS = frozenset({
    "queue_locked", "candidate_unknown", "already_merged", "binding_invalid", "not_synced",
    "ref_invalid", "ref_not_on_base", "approval_required",
})
IDENTITY = ("merge-queue", "merge-queue@localhost")
PRIVATE_REF = "refs/merge-queue"
STATE_PATCH_ATTEMPTS = 5
# How many passing durations, and whole runs, the check history keeps.
HISTORY_LENGTH = 5
# How many failing tests a stop names before it says how many more there are.
STOP_FAILURES = 10
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
GIT_OWN_DIRECTORIES = frozenset({
    "objects", "refs", "reftable", "worktrees", "hooks", "info", "logs", "modules", "lfs", "rr-cache",
    "branches", "remotes", "rebase-merge", "rebase-apply", "sequencer",
})
# Written into the private git directory of every checkout the runner adds,
# so a later run tells its own leftovers from a worktree someone else owns.
CHECKOUT_MARKER = "merge-queue-checkout"


def folded(path: Path) -> PurePath:
    """The path without case or Unicode normal form: a filesystem may name one folder by every such variant."""
    return PurePath(unicodedata.normalize("NFC", str(path)).casefold())


def within(path: Path, parent: Path) -> bool:
    return folded(path).is_relative_to(folded(parent))
# How long a killed process's output is drained before the runner stops waiting.
DRAIN_SECONDS = 5
# Inherited variables that would point git somewhere other than the runner's checkouts.
GIT_LOCATION_VARIABLES = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
})
# Variables that change how git reads a pathspec; under any of them the workRoot checks match the wrong paths.
GIT_PATHSPEC_VARIABLES = frozenset({
    "GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS",
})
# Where the runner's own git finds the user's global and system configuration: always the operator's.
GIT_CONFIG_HOME_VARIABLES = (
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "XDG_CONFIG_HOME",
    "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM",
)
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
# Windows reads environment names without case.
WINDOWS = os.name == "nt"


def environment_name(name: str) -> str:
    """The name as the platform compares it."""
    return name.upper() if WINDOWS else name


def is_link(path: Path) -> bool:
    """Whether the path is a symlink or a Windows junction, either of which leads somewhere else."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    # A junction is a mount-point reparse point; other reparse points, such as
    # cloud placeholders or deduplicated files, stay where they are.
    return (bool(getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)
            and getattr(info, "st_reparse_tag", 0) == IO_REPARSE_TAG_MOUNT_POINT)


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
    for variable in sorted(binding["environment"]):
        if variable.upper() in GIT_PATHSPEC_VARIABLES:
            errors.append(f"/environment: {variable} changes how git reads a pathspec and must not be set")
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
    if "checkOutput" in binding:
        try:
            pattern = re.compile(binding["checkOutput"]["failurePattern"])
        except re.error as error:
            errors.append(f"/checkOutput/failurePattern: not a regular expression: {error}")
        else:
            if "id" not in pattern.groupindex:
                errors.append("/checkOutput/failurePattern: needs a named group id")
    own = {check["id"] for check in [*binding["alwaysRun"], *binding["fallback"]]}
    reusable = binding.get("reusableChecks", [])
    if len(set(reusable)) != len(reusable):
        errors.append("/reusableChecks: ids must be unique")
    checks = {check["id"]: check for check in [*binding["alwaysRun"], *binding["fallback"]]}
    for name in reusable:
        if name not in own:
            errors.append(f"/reusableChecks: {name} is not one of the binding's own checks (alwaysRun or fallback)")
            continue
        check = checks[name]
        # Only the tree may vary between the lane's run and the one it stands for.
        profile = binding["profiles"].get(check.get("environmentProfile", ""), {"environment": {}})
        varying = [element for element in [*check["argv"], *profile["environment"].values()]
                   if PLACEHOLDER.match(element) and PLACEHOLDER.match(element)[1] != "treeSha"]
        if varying:
            errors.append(f"/reusableChecks: {name} uses {', '.join(sorted(set(varying)))}, which the tree does not "
                          "determine; a reusable check may use only {treeSha}")
        dependants = sorted(other for other, entry in checks.items() if name in entry["prerequisites"])
        if dependants:
            errors.append(f"/reusableChecks: {name} is a prerequisite of {', '.join(dependants)}, which would run "
                          "without its output")
    for index, flake in enumerate(binding.get("flakes", [])):
        if "tests" in flake and not flake["tests"]:
            errors.append(f"/flakes/{index}/tests: name at least one pattern, or leave tests out for the whole check")
        for pattern in flake.get("tests", []):
            try:
                re.compile(pattern)
            except re.error as error:
                errors.append(f"/flakes/{index}/tests: {pattern!r} is not a regular expression: {error}")
    return errors


def check_definition(check: dict, binding: dict) -> str:
    """How a check runs, as one digest: its argv, cwd, prerequisites, timeout and environment.

    The argv is digested before its placeholders are filled in, so the same
    definition digests alike on every tree; a reusable check may use only
    ``{treeSha}``, which the tree determines. The binding's prepare steps,
    which set up every check's checkout, are part of it. A change to the
    binding that changes how the check runs changes the digest; its reasons
    and ``parallelSafe`` do not.
    """
    profile = check.get("environmentProfile")
    described = {
        "id": check["id"],
        "argv": check["argv"],
        "cwd": check["cwd"],
        "prerequisites": check["prerequisites"],
        "timeoutSeconds": check.get("timeoutSeconds", binding["checkTimeoutSeconds"]),
        "environmentProfile": profile,
        "profileEnvironment": binding["profiles"].get(profile, {"environment": {}})["environment"] if profile else {},
        "environment": binding["environment"],
        # Every check runs in a checkout the prepare steps set up.
        "prepare": binding["prepare"],
    }
    text = json.dumps(described, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(text.encode("ascii")).hexdigest()


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


class InlineExecutor(concurrent.futures.Executor):
    """Runs each submitted call at once, on the caller's thread, so an interrupt reaches it directly."""

    def __init__(self, max_workers: int = 1) -> None:
        del max_workers

    def submit(self, fn, /, *args, **kwargs) -> concurrent.futures.Future:  # type: ignore[override]
        future: concurrent.futures.Future = concurrent.futures.Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as error:  # noqa: BLE001 - delivered through the future, as a pool would
            future.set_exception(error)
        return future


@dataclass
class Probe:
    """One check run on a prefix of a batch: what failed, what was selected, where it ran, and its result."""

    failed: list[dict]
    checks: list[dict]
    checkout: Path
    changes: list[dict]
    result: Result


@dataclass
class Member:
    """One candidate in a batch: what it resolved to, and the commit and tree the batch built with it."""

    subject: str
    item: dict
    branch: str
    head: str
    commit: str
    tree: str


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
        self._logs = 0

    # -- git ---------------------------------------------------------------

    def environment(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {
            key: value for key, value in os.environ.items()
            if key not in GIT_LOCATION_VARIABLES and key not in GIT_PATHSPEC_VARIABLES
            and not re.match(r"GIT_CONFIG_(KEY|VALUE)_\d+\Z", key)
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
        # The binding's environment never moves the runner's own git to another
        # repository, index or object store; the hooks setting is passed above.
        # Nor does it choose the global or system configuration the pins sit on.
        own = {environment_name(name) for name in GIT_CONFIG_HOME_VARIABLES}
        env = {
            key: value for key, value in self.environment().items()
            if environment_name(key) not in GIT_LOCATION_VARIABLES and environment_name(key) not in own
        }
        for name in GIT_CONFIG_HOME_VARIABLES:
            if name in os.environ:
                env[name] = os.environ[name]
        result = run_process([*prefix, *args], cwd=cwd or self.repository, env=env, stdin=stdin)
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
        private = Path(self.git("rev-parse", "--absolute-git-dir", cwd=path).stdout.strip())
        (private / CHECKOUT_MARKER).write_bytes(b"")
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

    def write(
        self,
        *,
        changes: dict | None = None,
        selector: str | None = None,
        assignments: dict | None = None,
        build: Callable[[dict], tuple[dict, dict]] | None = None,
    ) -> None:
        """Write runner-owned fields, retrying when the lead wrote first.

        ``build`` derives the changes and assignments from the state read for
        each attempt, for a patch that must carry the rest of a list as it is now.
        """
        for _ in range(STATE_PATCH_ATTEMPTS):
            current = self.state()
            version = current["version"]
            if build is not None:
                changes, assignments = build(current)
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

    def write_progress(self, subjects: list[str], states: list[dict]) -> None:
        """Show the checks' live states in the checking panel and the queue rows, in one write.

        Written before a check starts and after it ends, so both views name the
        check that is running and never disagree. Every member of a batch shows
        the batch's checks. A step carries its times and its expected duration;
        a queue row's check carries its failing tests and its log. A reused
        check carries the head of the lane's run it was reused from.
        """
        steps = [
            {"label": record["id"], "state": record["state"],
             **{key: record[key] for key in ("startedAt", "finishedAt", "expectedSeconds", "reusedFrom") if key in record}}
            for record in states
        ]
        rows = [
            {"id": record["id"], "state": record["state"],
             **({"failures": [failure["id"] for failure in record["failures"]]} if record.get("failures") else {}),
             **({"log": record["log"]} if "log" in record else {}),
             **({"reusedFrom": record["reusedFrom"]} if "reusedFrom" in record else {})}
            for record in states
        ]

        def build(current: dict) -> tuple[dict, dict]:
            queue = [dict(item) for item in current["queue"]]
            for item in queue:
                if item["subject"] in subjects:
                    item["checks"] = [dict(row) for row in rows]
            return {"queue": queue}, {"/meta/checking/now/steps": steps}

        self.write(build=build)

    def history(self) -> dict:
        found = self.state()["meta"].get("checkHistory")
        return copy.deepcopy(found) if found else {"checks": {}, "runs": []}

    def record_history(self, passed: dict[str, int], flaked: dict[str, int], run_seconds: int | None) -> None:
        """Keep each check's last five passing durations, its flake count, and the last five whole runs."""

        def build(current: dict) -> tuple[dict, dict]:
            history = copy.deepcopy(current["meta"].get("checkHistory") or {"checks": {}, "runs": []})
            for name in set(passed) | set(flaked):
                record = history["checks"].setdefault(name, {"seconds": [], "flakes": 0})
                if name in passed:
                    record["seconds"] = [*record["seconds"], passed[name]][-HISTORY_LENGTH:]
                record["flakes"] += flaked.get(name, 0)
            if run_seconds is not None:
                history["runs"] = [*history["runs"], run_seconds][-HISTORY_LENGTH:]
            return {}, {"/meta/checkHistory": history}

        self.write(build=build)

    def save_log(self, check_id: str, completed: Completed) -> str:
        """Write a check's whole output under the state directory; returns its path relative to it."""
        self._logs += 1
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"{stamp}-{self._logs:03d}-{uuid.uuid4().hex[:8]}-{re.sub(r'[^A-Za-z0-9._-]', '_', check_id)}.log"
        directory = self.state_directory / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes((completed.stdout + completed.stderr).encode("utf-8", "replace"))
        return f"logs/{name}"

    def failures(self, completed: Completed) -> list[dict]:
        """The failing tests or steps the binding's failure pattern finds in a check's output, each once."""
        settings = self.binding.get("checkOutput")
        if not settings:
            return []
        pattern = re.compile(settings["failurePattern"])
        found: dict[str, str] = {}
        for stream in (completed.stdout, completed.stderr):
            for line in stream.splitlines():
                match = pattern.search(line)
                # A match whose id is missing or empty names nothing.
                if match is None or not match["id"]:
                    continue
                found.setdefault(match["id"], (match.groupdict().get("message") or "").strip())
        return [{"id": name, "message": message} for name, message in found.items()]

    def flake_for(self, check_id: str, failures: list[dict]) -> str | None:
        """The references of the declared flakes that cover every failure of this check, or None.

        A declaration without test patterns covers the whole check; one with
        patterns covers only failures it can name, so output the failure
        pattern cannot read is never retried on its strength.
        """
        declared = [flake for flake in self.binding.get("flakes", []) if flake["check"] == check_id]
        whole = [flake["ref"] for flake in declared if "tests" not in flake]
        if whole:
            return ", ".join(whole)
        if not failures:
            return None
        refs: list[str] = []
        for failure in failures:
            covering = [flake for flake in declared if any(re.fullmatch(pattern, failure["id"]) for pattern in flake["tests"])]
            if not covering:
                return None
            refs += [flake["ref"] for flake in covering if flake["ref"] not in refs]
        return ", ".join(refs)

    def finish_checking(self, subject: str) -> None:
        """Clear the checking panel; a check left running, by a stop mid-check, reads pending again.

        One write, so the panel and the queue row still agree after a stop.
        """

        def build(current: dict) -> tuple[dict, dict]:
            queue = [dict(item) for item in current["queue"]]
            for item in queue:
                if item["subject"] == subject:
                    item["checks"] = [
                        {**check, "state": "pending"} if check["state"] == "running" else check
                        for check in item["checks"]
                    ]
            return {"queue": queue}, {"/meta/checking/now": None}

        self.write(build=build)

    def recover_interrupted(self) -> None:
        """Clear the live state a run killed outright left behind, and journal it.

        Called first, while holding the queue lock. The lock is released by the
        operating system when its holder dies, so anything still reading running
        or mid-step, or that the checking panel names, now belongs to a run that
        no longer exists. Each such item stops, its running checks read pending
        again, and the panel clears.

        The journal entries come first: a second kill between them and the
        write repeats an entry at the next run, but never loses one.
        """
        current = self.state()
        named = (current["meta"]["checking"]["now"] or {}).get("subject")
        interrupted = {
            item["subject"]: item["stage"] for item in current["queue"]
            if item["subject"] == named
            or item["stage"] in ("syncing", "checking", "merging")
            or any(check["state"] == "running" for check in item["checks"])
        }
        if not interrupted and current["meta"]["checking"]["now"] is None:
            return
        for subject, stage in interrupted.items():
            summary = f"interrupted: a run ended while {subject} was {stage}; its running checks read pending again"
            if stage == "merging":
                summary += "; check the forge, since the merge may have landed"
            self.journal("candidate.stopped", summary, subject)

        def build(current: dict) -> tuple[dict, dict]:
            queue = [dict(item) for item in current["queue"]]
            for item in queue:
                if item["subject"] in interrupted:
                    item["checks"] = [
                        {**check, "state": "pending"} if check["state"] == "running" else check
                        for check in item["checks"]
                    ]
                    if item["stage"] != "merged":
                        item["stage"] = "stopped"
            return {"queue": queue}, {"/meta/checking/now": None}

        self.write(build=build)

    def set_item(self, subject: str, **fields: object) -> None:
        self.write(selector=f"queue[subject={subject}]", assignments={f"/{key}": value for key, value in fields.items()})

    def journal(self, kind: str, summary: str, subject: str | None = None) -> None:
        lane = None
        if subject is not None:
            with contextlib.suppress(Stop):
                lane = self.item(subject).get("lane")
        delivery_state.append_journal(self.state_directory, kind=kind, summary=summary, lane=lane, subject=subject,
                                      writer="runner")
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

    def read_binding(self, base_head: str) -> dict:
        """The binding as the base commit holds it, validated."""
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
        return binding

    def evidence(self, check_id: str, head: str, result: str = "passed") -> dict:
        """What a lane records of its own run of a reusable check at ``head``, for its hand-off.

        The check's definition is digested from the binding in the head's own
        tree, the one the lane ran it under; the runner reuses the run only if
        the base's binding digests it alike. Only a check that binding marks
        reusable has evidence. Nothing is fetched.
        """
        commit = self.git("rev-parse", "--verify", "--quiet", f"{head}^{{commit}}", check=False)
        if commit.returncode != 0:
            raise Stop("ref_invalid", f"{head} is not a commit here")
        commit_id = commit.stdout.strip()
        binding = self.read_binding(commit_id)
        if check_id not in binding.get("reusableChecks", []):
            raise Stop("binding_invalid", f"the binding does not mark {check_id} reusable")
        check = next(check for check in [*binding["alwaysRun"], *binding["fallback"]] if check["id"] == check_id)
        return {"check": check_id, "definition": check_definition(check, binding), "head": commit_id,
                "tree": self.tree(commit_id), "result": result}

    def tree_of(self, head: str) -> str | None:
        """The tree of a commit this repository has, or None."""
        found = self.git("rev-parse", "--verify", "--quiet", f"{head}^{{tree}}", check=False)
        return found.stdout.strip() if found.returncode == 0 else None

    def reusable(self, members: list[tuple[str, dict]], checks: list[dict], tree: str) -> dict[str, dict]:
        """The checks to reuse rather than run on ``tree``, each with the run it is reused from.

        A check is reused only when the binding marks it reusable and one of
        the candidates being checked handed off evidence of a passing run of
        that check, under the same definition, at a head whose tree, read here,
        is ``tree``. Evidence is read from each candidate's own hand-off, so it
        is never reused for another candidate.
        """
        declared = set(self.binding.get("reusableChecks", []))
        # A check another selected check builds on runs, so its output is there.
        needed = {name for check in checks for name in check["prerequisites"]}
        found: dict[str, dict] = {}
        for check in checks:
            if check["id"] not in declared:
                continue
            definition = check_definition(check, self.binding)
            why: list[str] = []
            for subject, item in members:
                for entry in item.get("evidence", []):
                    if entry["check"] != check["id"]:
                        continue
                    reason = (
                        "another check needs its output" if check["id"] in needed
                        else "its definition changed since the lane's run" if entry["definition"] != definition
                        else f"the lane's run {entry['result']}" if entry["result"] != "passed"
                        else "the tree is not the one the lane's run checked" if entry["tree"] != tree
                        else "the head of the lane's run is not here, or has another tree"
                        if self.tree_of(entry["head"]) != tree else None
                    )
                    if reason is None:
                        found[check["id"]] = {"subject": subject, "head": entry["head"], "tree": tree}
                        break
                    why.append(f"{subject} at {entry['head']}: {reason}")
                if check["id"] in found:
                    break
            if check["id"] not in found and why:
                self.notes.append(f"{check['id']} was run, not reused: " + "; ".join(why))
        return found

    def load(self) -> str:
        """Fetch the base, read the binding from it, and set up workRoot."""
        # A relative workRoot resolves against --repo, so --repo must be the
        # top level git reports; from a subdirectory, "../tools" would name a
        # tracked folder the checks below never examined.
        top = self.git("rev-parse", "--show-toplevel", check=False)
        if top.returncode != 0 or folded(Path(top.stdout.strip()).resolve()) != folded(self.repository):
            raise Stop("binding_invalid", f"--repo must be the top level of a checkout, not {self.repository}")
        if not self.valid_branch(self.base):
            raise Stop("ref_invalid", f"not a valid branch name: {self.base}")
        base_head = self.fetch(self.base, "base")
        binding = self.read_binding(base_head)
        self.binding = binding
        root = Path(binding["workRoot"])
        self.work_root = (root if root.is_absolute() else self.repository / root).resolve()
        self.guard_work_root()
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

    def worktrees(self) -> list[tuple[Path, bool, bool]]:
        """Every checkout git lists for this repository, the current one included: path, bare, prunable.

        The list is NUL-separated, so a path holding a newline is read whole.
        """
        listed: list[tuple[Path, bool, bool]] = []
        for block in self.git("worktree", "list", "--porcelain", "-z").stdout.split("\0\0"):
            fields = [field for field in block.split("\0") if field]
            if fields and fields[0].startswith("worktree "):
                bare = "bare" in fields
                prunable = any(field == "prunable" or field.startswith("prunable ") for field in fields)
                listed.append((Path(fields[0][len("worktree "):]).resolve(), bare, prunable))
        return listed

    def own_checkout(self, path: Path, prunable: bool) -> bool:
        """Whether ``path`` is a checkout an earlier run of the runner added under workRoot/checkouts."""
        if not within(path, self.work_root / "checkouts"):
            return False
        if not path.exists():
            return prunable
        private = self.git("rev-parse", "--absolute-git-dir", cwd=path, check=False)
        return private.returncode == 0 and (Path(private.stdout.strip()) / CHECKOUT_MARKER).exists()

    def submodule_at(self, checkout: Path, relative: list[str]) -> str | None:
        """The submodule path in ``checkout`` that is ``relative`` or one of its parents, if any."""
        ancestors = ["/".join(relative[:depth]) for depth in range(1, len(relative) + 1)]
        listed = self.git("ls-files", "--stage", "-z", "--", *(f":(icase,literal){path}" for path in ancestors),
                          cwd=checkout, check=False).stdout
        wanted = {unicodedata.normalize("NFC", path).casefold() for path in ancestors}
        for entry in listed.split("\0"):
            metadata, _, path = entry.partition("\t")
            if metadata.startswith("160000 ") and unicodedata.normalize("NFC", path).casefold() in wanted:
                return path
        return None

    def guard_work_root(self) -> None:
        """Refuse a workRoot that reaches what git, or any checkout of the repository, owns.

        The runner deletes what it finds under workRoot, so it is checked
        against git's own view of the repository: the git directory, the
        common git directory a linked worktree shares, and every listed
        checkout. Paths are compared without case or Unicode normal form.
        The runner's own leftover checkouts under workRoot/checkouts, which it
        marks, are the one kind of checkout workRoot may hold.
        """
        work = self.work_root
        git_directories = []
        for flag in ("--absolute-git-dir", "--git-common-dir"):
            reported = Path(self.git("rev-parse", flag).stdout.strip())
            git_directories.append((reported if reported.is_absolute() else self.repository / reported).resolve())
        checkouts = self.worktrees()
        if not any(folded(path) == folded(self.repository) for path, _, _ in checkouts):
            checkouts.append((self.repository, False, False))
        for path, _, prunable in checkouts:
            if within(path, work) and not self.own_checkout(path, prunable):
                raise Stop("binding_invalid", f"workRoot must not contain a checkout of the repository: {path}")
        for directory in git_directories:
            if within(directory, work) and folded(directory) != folded(work):
                raise Stop("binding_invalid", "workRoot must not contain the git directory")
        for directory in git_directories:
            if within(work, directory):
                # Beside git's own data, never in it.
                parts = folded(work).relative_to(folded(directory)).parts
                if not parts or parts[0] in GIT_OWN_DIRECTORIES:
                    raise Stop("binding_invalid", "workRoot must not be the git directory or one of git's own folders")
        for path, bare, _ in checkouts:
            if bare or not path.exists() or not within(work, path):
                continue
            if any(within(work, directory) and within(directory, path) for directory in git_directories):
                # Inside this checkout's own git directory, which the rules above cover.
                continue
            # Inside a checkout it must hold no tracked file, sit in no
            # submodule, and be a path that checkout ignores under its own
            # rules. The path is checked as a directory, so a "name/" rule
            # matches before it exists, and the tracked-file match ignores case.
            parts = list(work.parts[len(path.parts):])
            relative = "/".join(parts) + "/"
            if self.git("ls-files", "--", f":(icase){relative}", cwd=path, check=False).stdout.strip():
                raise Stop("binding_invalid", f"workRoot must not hold tracked files of the checkout at {path}")
            submodule = self.submodule_at(path, parts)
            if submodule is not None:
                raise Stop("binding_invalid", f"workRoot must not sit in the submodule {submodule} of the checkout at {path}")
            if self.git("check-ignore", "-q", "--no-index", "--", relative, cwd=path, check=False).returncode != 0:
                raise Stop("binding_invalid", f"a workRoot inside the checkout at {path} must be ignored there")
        for name in ("checkouts", "empty-hooks"):
            # The runner empties these folders; through a symlink it would empty the target.
            if is_link(work / name):
                raise Stop("binding_invalid", f"workRoot/{name} must not be a symlink or junction")

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
        self.settle(subject, item, checkout, context, inputs, declared, f"Regenerate after syncing {branch} with {self.base}")
        self.seam("after_sync", subject)
        self.compare_with_review(checkout, item["head"], base_head, declared)
        synced = self.rev("HEAD", cwd=checkout)
        self.push(branch, head, synced, subject)
        result.syncedHead = synced
        self.set_item(subject, stage="synced", syncedHead=synced)
        self.write(assignments={"/meta/baseHead": base_head})
        result.outcome = "synced"

    def settle(
        self, subject: str, item: dict, checkout: Path, context: Context, inputs: dict, declared: list[str], message: str,
    ) -> None:
        """Run the sync passes until they change nothing, committing each pass that changes something.

        Nothing is pushed until every later check passes, so a stop leaves the
        branch as it was.
        """
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
            self.git("commit", "--quiet", "-m", message, cwd=checkout)
        undeclared = [path for path in sorted(touched) if not matches_any(path, declared)]
        if undeclared:
            raise Stop("undeclared_change", "a sync step changed files outside the declared paths", undeclared)

    def merge_base_in(self, checkout: Path, base_head: str, branch: str) -> None:
        self.merge_in(checkout, base_head, base_head, "--theirs", f"Merge {self.base} into {branch}")

    def merge_in(
        self, checkout: Path, other: str, base_side: str, side: str, message: str, against: str = "the base",
    ) -> None:
        """Merge ``other`` into the checkout; only regenerable paths may conflict, and they take the base side.

        ``base_side`` is the commit that holds the base's version of a path and
        ``side`` the checkout option that selects it: the base is the incoming
        side when a candidate syncs, and the checkout's own side when a batch
        merges a candidate into the tree it has accumulated.
        """
        merged = self.git("merge", "--no-ff", "--no-commit", "--no-edit", other, cwd=checkout, check=False)
        conflicts = sorted(set(filter(None, self.git(
            "diff", "--name-only", "-z", "--diff-filter=U", cwd=checkout).stdout.split("\0"))))
        if merged.returncode != 0 and not conflicts:
            raise RuntimeError(f"merging {other} failed: {merged.stderr.strip()}")
        outside = [path for path in conflicts if not matches_any(path, self.binding["regenerable"]["paths"])]
        if outside:
            self.git("merge", "--abort", cwd=checkout, check=False)
            raise Stop("source_conflict", f"{against} conflicts outside the regenerable paths", outside)
        for path in conflicts:
            # Take the base side; a path the base deleted stays deleted.
            if self.git("cat-file", "-e", f"{base_side}:{path}", cwd=checkout, check=False).returncode == 0:
                self.git("checkout", side, "--", path, cwd=checkout)
                self.git("add", "--", path, cwd=checkout)
            else:
                self.git("rm", "--quiet", "--force", "--", path, cwd=checkout)
        if self.git("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=checkout, check=False).returncode == 0:
            # Committed before anything else runs, so every later step sees the new merge base.
            self.git("commit", "--quiet", "-m", message, cwd=checkout)

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
        self.checking(subject, item["title"], [{"label": "selecting checks", "state": "running"}])
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
        self.write_progress([subject], [{"id": check["id"], "state": "pending"} for check in checks])
        reused = self.reusable([(subject, item)], checks, self.tree(synced))
        outcomes = self.run_checks([subject], checks, checkout, context.with_values(treeSha=self.tree(synced)), result,
                                   reused=reused)
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

    def run_checks(
        self, subjects: list[str], checks: list[dict], checkout: Path, context: Context, result: Result,
        whole_run: bool = True, reused: dict[str, dict] | None = None,
    ) -> dict[str, str]:
        """Run the checks in order, and record each one the lane's evidence lets this run reuse instead."""
        reused = reused or {}
        outcomes: dict[str, str] = {}
        records = {check["id"]: {"id": check["id"], "state": "pending"} for check in checks}
        history = self.history()["checks"]
        for check in checks:
            expected = delivery_state.median_seconds(history.get(check["id"], {}).get("seconds", []))
            if expected is not None:
                records[check["id"]]["expectedSeconds"] = expected
        passed: dict[str, int] = {}
        flaked: dict[str, int] = {}
        began = time.monotonic()

        def show() -> None:
            self.write_progress(subjects, [records[check["id"]] for check in checks])

        def begin(check: dict) -> None:
            record = records[check["id"]]
            record.update(state="running", startedAt=now())
            for key in ("finishedAt", "failures", "log"):
                record.pop(key, None)

        def finish(check: dict, completed: Completed) -> dict:
            """Record one finished run of a check; a failed or timed-out run keeps its output and failing tests."""
            state = "timed_out" if completed.timed_out else "passed" if completed.returncode == 0 else "failed"
            record = records[check["id"]]
            record.update(state=state, finishedAt=now())
            entry = {"id": check["id"], "state": state, "exitCode": completed.returncode,
                     "seconds": round(completed.seconds, 3)}
            record.pop("failures", None)
            record.pop("log", None)
            if state == "passed":
                passed[check["id"]] = int(round(completed.seconds))
            else:
                entry["failures"] = self.failures(completed)
                entry["log"] = self.save_log(check["id"], completed)
                record["failures"], record["log"] = entry["failures"], entry["log"]
            return entry

        limit = self.binding.get("parallelChecks", 1)
        pending = list(checks)
        running: dict[concurrent.futures.Future, dict] = {}
        pool_type = concurrent.futures.ThreadPoolExecutor if limit > 1 else InlineExecutor
        with pool_type(max_workers=limit) as pool:
            while pending or running:
                # Start checks in order; the first one that cannot start yet holds back the rest.
                while pending:
                    check = pending[0]
                    if any(name not in outcomes for name in check["prerequisites"]):
                        break
                    if any(outcomes[name] not in ("passed", "reused") for name in check["prerequisites"]):
                        pending.pop(0)
                        outcomes[check["id"]] = "skipped"
                        records[check["id"]]["state"] = "skipped"
                        result.checks.append({"id": check["id"], "state": "skipped", "exitCode": None, "seconds": 0})
                        show()
                        continue
                    if check["id"] in reused:
                        # The tree is the one the lane checked and passed: its run stands, on the record.
                        pending.pop(0)
                        source = reused[check["id"]]
                        outcomes[check["id"]] = "reused"
                        records[check["id"]].pop("expectedSeconds", None)
                        records[check["id"]].update(state="reused", reusedFrom=source["head"])
                        result.checks.append({"id": check["id"], "state": "reused", "exitCode": None, "seconds": 0,
                                              "reusedFrom": dict(source)})
                        for subject in subjects:
                            self.journal("check.reused",
                                         f"{check['id']} reused from the lane's run at {source['head']} "
                                         f"({source['subject']}): the tree is the one it checked and passed", subject)
                        show()
                        continue
                    alone = not check.get("parallelSafe") or limit == 1
                    if running and (alone or len(running) >= limit or any(
                        not item.get("parallelSafe") for item in running.values()
                    )):
                        break
                    pending.pop(0)
                    # Live state is written before the work starts, not after it.
                    begin(check)
                    show()
                    running[pool.submit(self.run_one, check, checkout, context)] = check
                    if alone:
                        break
                if not running:
                    continue
                done, _ = concurrent.futures.wait(running, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    check = running.pop(future)
                    completed = future.result()
                    assert completed is not None
                    entry = finish(check, completed)
                    flake = self.flake_for(check["id"], entry["failures"]) if entry["state"] == "failed" else None
                    if flake is not None:
                        # Every failure is a declared flake: one retry, on the record.
                        named = ", ".join(failure["id"] for failure in entry["failures"]) or "the whole check"
                        for subject in subjects:
                            self.journal("check.retried",
                                         f"{check['id']} failed only on known flakes ({named}; {flake}); retrying once",
                                         subject)
                        flaked[check["id"]] = flaked.get(check["id"], 0) + 1
                        first = {"flake": flake, "failures": entry["failures"], "log": entry["log"],
                                 "exitCode": entry["exitCode"]}
                        begin(check)
                        show()
                        again = self.run_one(check, checkout, context)
                        assert again is not None
                        entry = {**finish(check, again), "retried": first}
                    outcomes[check["id"]] = entry["state"]
                    result.checks.append(entry)
                show()
        # A run that reused a check is not a whole run, so it does not shorten the run estimates.
        whole = checks and whole_run and not reused
        self.record_history(passed, flaked, int(round(time.monotonic() - began)) if whole else None)
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
        raise Stop("check_failed", "failing checks: " + "; ".join(self.failure_text(check["id"], result) for check in failed))

    @staticmethod
    def failure_text(check_id: str, result: Result) -> str:
        """A failed check as a stop names it: its failing tests and its log."""
        entry = next((item for item in reversed(result.checks) if item["id"] == check_id), {})
        parts = []
        if entry.get("failures"):
            named = [failure["id"] for failure in entry["failures"]]
            more = f" and {len(named) - STOP_FAILURES} more" if len(named) > STOP_FAILURES else ""
            parts.append("failing " + ", ".join(named[:STOP_FAILURES]) + more)
        if entry.get("log"):
            parts.append("log " + entry["log"])
        return check_id + (f" ({'; '.join(parts)})" if parts else "")

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

    def merge(self, subject: str, result: Result, batch_synced: str | None = None) -> None:
        """Merge a checked candidate; a batch passes the synced head it built from the checked tree instead."""
        item = self.item(subject)
        synced = batch_synced or item["syncedHead"]
        if batch_synced is None and (synced is None or item["stage"] != "checked"):
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

    # -- batch -------------------------------------------------------------

    def build_batch(self, members: list[str], base_head: str, results: dict[str, Result]) -> list[Member]:
        """Merge each member into one tree, in order, and record the commit and tree after each.

        Each member goes through the same steps a sync runs, on the tree the
        batch has built so far. A member that stops drops out of the batch and
        the rest continue.
        """
        # Prepare runs once, in the checkout of the base the batch starts from.
        checkout = self.checkout(base_head, "batch", context=self.context(None, base_head).with_values(
            branch=self.base, head=base_head, expectedHead=base_head, treeSha=self.tree(base_head)))
        # Each member resolves from the base, as a sync does, never from the tree built so far.
        anchor = self.checkout(base_head, "batch-anchor", prepare=False)
        inputs = {entry["id"]: entry for entry in self.binding["leadInputs"]}
        built: list[Member] = []
        accumulated = base_head
        for subject in members:
            result = results[subject]
            result.base, result.baseHead = self.base, base_head
            try:
                item = self.item(subject)
                if item["stage"] == "merged":
                    raise Stop("already_merged", f"{subject} is already merged")
                branch, head = self.resolve(subject, self.context(subject, base_head), anchor)
                context = self.context(subject, accumulated)
                result.head = head
                if head not in (item["head"], item["syncedHead"]):
                    raise Stop("candidate_moved", f"{subject} is at {head}, not the reviewed or last synced head")
                tip = self.fetch(branch, "candidate")
                if tip != head:
                    raise Stop("candidate_moved", f"{branch} moved to {tip} after {subject} resolved to {head}")
                unknown = [name for name in item["leadInputs"] if name not in inputs]
                if unknown:
                    raise Stop("binding_invalid", f"the binding declares no lead input {unknown}")
                context = context.with_values(branch=branch, head=head)
                self.merge_in(checkout, head, accumulated, "--ours", f"Merge {branch} into the batch",
                              against="the batch built so far")
                declared = self.declared_paths(item)
                self.settle(subject, item, checkout, context, inputs, declared, f"Regenerate after adding {branch} to the batch")
                self.compare_with_review(checkout, item["head"], accumulated, declared)
            except Stop as stop:
                # The tree goes back to what the batch had built before this member.
                self.git("merge", "--abort", cwd=checkout, check=False)
                self.git("reset", "--quiet", "--hard", accumulated, cwd=checkout)
                self.git("clean", "--quiet", "-fd", cwd=checkout)
                stop.detail = f"dropped from the batch: {stop.detail}"
                self.stopped(result, stop, subject)
                continue
            accumulated = self.rev("HEAD", cwd=checkout)
            built.append(Member(subject, item, branch, head, accumulated, self.tree(accumulated)))
        return built

    def check_tree(
        self, members: list[Member], base_head: str, result: Result, what: str, whole_run: bool = True,
    ) -> tuple[list[dict], list[dict], Path, list[dict]]:
        """Select and run the checks for the tree the batch built up to its last member.

        Returns the failed checks, the selected checks, the checkout and the changes.
        """
        subjects = [member.subject for member in members]
        commit = members[-1].commit
        names = ", ".join(subjects)
        self.checking(subjects[0], f"{what} of {len(subjects)}: {names}", [{"label": "selecting checks", "state": "running"}])
        context = self.context(subjects[0], base_head).with_values(
            branch=members[-1].branch, head=commit, expectedHead=commit, treeSha=self.tree(commit))
        checkout = self.checkout(commit, "batch-check", context=context)
        self.assert_clean(checkout, "a fresh checkout of the batch")
        changes = self.changes(base_head, commit)
        selection, checks = self.select(subjects[0], base_head, commit, changes, checkout, context)
        result.selection = selection
        if not checks and changes:
            raise Stop("no_checks", "nothing would check this batch's changes")
        self.write_progress(subjects, [{"id": check["id"], "state": "pending"} for check in checks])
        # A member's evidence counts only when the combined tree is the tree it checked.
        reused = self.reusable([(member.subject, member.item) for member in members], checks, members[-1].tree)
        outcomes = self.run_checks(subjects, checks, checkout, context, result, whole_run=whole_run, reused=reused)
        self.assert_clean(checkout, "the checkout after the batch's checks")
        failed = [check for check in checks if outcomes[check["id"]] in ("failed", "timed_out")]
        return failed, checks, checkout, changes

    def culprit(self, built: list[Member], base_head: str, whole: Probe) -> tuple[int, Probe, Probe | None]:
        """Find the first member whose prefix of the batch fails.

        Returns its position, the failing prefix's probe, and the probe of the
        passing prefix just before it, or None when the culprit is the first
        member. The whole batch is known to fail, so it is not checked again.
        """
        search = self.binding.get("batch", {}).get("culpritSearch", "bisect")
        tested: dict[int, Probe] = {len(built): whole}

        def fails(count: int) -> bool:
            result = Result("batch", built[count - 1].subject)
            prefix_failed, checks, checkout, changes = self.check_tree(
                built[:count], base_head, result, "Finding the failing member, prefix", whole_run=False)
            tested[count] = Probe(prefix_failed, checks, checkout, changes, result)
            return bool(prefix_failed)

        if search == "serial":
            found = next((count for count in range(1, len(built)) if fails(count)), len(built))
        else:
            passing, failing = 0, len(built)
            while failing - passing > 1:
                middle = (passing + failing) // 2
                if fails(middle):
                    failing = middle
                else:
                    passing = middle
            found = failing
        return found, tested[found], tested.get(found - 1)

    def land(self, member: Member, previous_tree: str, result: Result) -> None:
        """Land one member with exactly the tree the batch recorded for it."""
        self.seam("before_batch_landing", member.subject)
        base_now = self.fetch(self.base, "base")
        if self.tree(base_now) != previous_tree:
            raise Stop(
                "batch_tree_mismatch",
                f"{self.base} at {base_now} is not the tree the batch recorded before {member.subject}; nothing more lands",
            )
        # The synced head carries the checked tree itself, on top of the base as it now is.
        synced = self.git(
            "commit-tree", member.tree, "-p", member.head, "-p", base_now,
            "-m", f"Merge {self.base} into {member.branch}",
        ).stdout.strip()
        self.push(member.branch, member.head, synced, member.subject)
        result.syncedHead = synced
        # Never "checked": only the whole batch was checked, so a run killed here
        # must not leave this tree mergeable on its own. It stays "checking"
        # until the merge moves it on, and the next run stops it.
        self.set_item(member.subject, syncedHead=synced)
        self.merge(member.subject, result, batch_synced=synced)

    def batch_round(self, members: list[str], results: list[Result]) -> list[str]:
        """Build, check once and land one batch. Returns the members left for another round.

        Every member not left for another round ends this round with exactly
        one result document.
        """
        documents = {subject: Result("batch", subject) for subject in members}
        landed: list[str] = []
        carried: list[str] = []
        self.notes = []

        def requeue(subjects: list[str], why: Stop, queue_stopped: bool = False) -> None:
            """Send members back to queued, each with a result and a journal entry saying why nothing of it landed.

            The entry ends the member's check in the record, so it does not read
            as still being checked until a later run checks it again.
            """
            for subject in subjects:
                if documents[subject].stop is not None:
                    continue
                detail = f"{subject} did not land and is queued again: {why.reason}: {why.detail}"
                with contextlib.suppress(Stop, delivery_state.StateError):
                    self.set_item(subject, stage="queued", checks=[])
                with contextlib.suppress(Stop, delivery_state.StateError, OSError):
                    self.journal("candidate.stopped", detail, subject)
                documents[subject].stop = {
                    "reason": why.reason, "detail": detail,
                    "paths": [], "queueStopped": queue_stopped or why.reason in QUEUE_STOPPING,
                }

        try:
            base_head = self.load()
            self.start()
            built = self.build_batch(members, base_head, documents)
            if len(built) < 2:
                carried = [member.subject for member in built]
                return carried
            subjects = [member.subject for member in built]
            for subject in subjects:
                self.set_item(subject, stage="checking", checks=[])
            titles = ", ".join(member.item["title"] for member in built)
            for subject in subjects:
                self.journal("check.started", f"Checking {titles} as one batch", subject)
            shared = Result("batch", None)
            failed, checks, checkout, changes = self.check_tree(built, base_head, shared, "Batch")
            keep, later, record = built, [], shared
            if failed:
                found, failing, passing = self.culprit(built, base_head, Probe(failed, checks, checkout, changes, shared))
                culprit = built[found - 1]
                stop = Stop(
                    "check_failed",
                    f"culprit of the batch: "
                    f"{'; '.join(self.failure_text(check['id'], failing.result) for check in failing.failed)} "
                    f"first fail when {culprit.subject} joins",
                )
                if found == 1:
                    # Alone on the base, the usual rule tells the base's own failures apart.
                    try:
                        self.classify(failing.failed, failing.checks, failing.checkout, base_head, failing.changes,
                                      self.context(culprit.subject, base_head), failing.result)
                    except Stop as classified:
                        if classified.reason == "base_broken":
                            stop = classified
                documents[culprit.subject].selection = failing.result.selection
                documents[culprit.subject].checks = failing.result.checks
                self.stopped(documents[culprit.subject], stop, culprit.subject)
                if stop.reason in QUEUE_STOPPING:
                    requeue([member.subject for member in built[found:]], stop)
                    return []
                keep, later = built[:found - 1], built[found:]
                record = passing.result if passing is not None else shared
                for member in later:
                    with contextlib.suppress(Stop, delivery_state.StateError):
                        self.set_item(member.subject, stage="queued", checks=[])
            else:
                for subject in subjects:
                    self.journal("check.finished", f"Checks passed for {titles} as one batch", subject)
            previous = self.tree(base_head)
            for member in keep:
                document = documents[member.subject]
                document.selection, document.checks = record.selection, list(record.checks)
                try:
                    self.land(member, previous, document)
                except Exception as error:  # noqa: BLE001 - a partial landing stops the queue however it ends
                    stop = error if isinstance(error, Stop) else Stop(
                        "runner_error", f"{type(error).__name__}: {error}"[:800])
                    self.stopped(document, stop, member.subject)
                    if landed:
                        # The base now holds a tree that may not have been checked on its own.
                        document.notes.append(
                            f"{self.base} holds the tree the batch built up to {landed[-1]}, which may not have been "
                            f"checked on its own; run the full health check before anything else merges",
                        )
                        document.stop["queueStopped"] = True
                        with contextlib.suppress(Stop, delivery_state.StateError, OSError):
                            self.journal("base.broken", f"{stop.reason} after a partial landing; "
                                         f"{self.base} holds the batch's tree up to {landed[-1]}", member.subject)
                    if stop.reason == "batch_tree_mismatch":
                        # Nothing of it landed; it is batched again once the base is confirmed.
                        with contextlib.suppress(Stop, delivery_state.StateError):
                            self.set_item(member.subject, stage="queued", checks=[])
                    requeue([other.subject for other in keep[keep.index(member) + 1:]] +
                            [other.subject for other in later], stop, queue_stopped=bool(landed))
                    return []
                landed.append(member.subject)
                previous = member.tree
            carried = [member.subject for member in later]
            return carried
        except Exception as error:  # noqa: BLE001 - every run ends with a result document
            stop = error if isinstance(error, Stop) else Stop("runner_error", f"{type(error).__name__}: {error}"[:800])
            for subject in members:
                if subject not in landed and documents[subject].stop is None:
                    self.stopped(documents[subject], stop, subject)
            return []
        finally:
            self.remove_checkouts()
            for subject in members:
                document = documents[subject]
                document.hooksBypassed = self.hooks_bypassed
                document.notes.extend(note for note in self.notes if note not in document.notes)
                with contextlib.suppress(Exception):
                    self.finish_checking(subject)
            # A member left for another round reports there, not here.
            results.extend(documents[subject] for subject in members if subject not in carried)

    def batch(self, subjects: list[str]) -> list[Result]:
        if len(subjects) < 2 or len(set(subjects)) != len(subjects):
            result = Result("batch", subjects[0] if subjects else None)
            self.stopped(result, Stop("binding_invalid", "a batch needs two or more different candidates"), None)
            return [result]
        results: list[Result] = []
        members = list(subjects)
        while members:
            if len(members) == 1:
                self._run_one(members[0], results)
                break
            members = self.batch_round(members, results)
        return results

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
                    self.finish_checking(subject)
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
            if queue_stopped:
                # Every stop that stops the queue leaves the base to confirm by hand; serve waits for base.repaired.
                self.journal("base.broken", f"{stop.reason}: {stop.detail}", subject)
            if subject is not None:
                self.journal("candidate.stopped", f"{stop.reason}: {stop.detail}", subject)

    def prepared(self, subcommand: str, subject: str | None, step: Callable[[Result], None]) -> Result:
        def guarded(result: Result) -> None:
            self.load()
            self.start()
            step(result)
        return self.invoke(subcommand, subject, guarded)

    def recover(self) -> None:
        """Take the queue lock just to clear what a killed run left; a lock another runner holds is left alone."""
        try:
            with self.locked():
                with contextlib.suppress(delivery_state.StateError, OSError):
                    self.recover_interrupted()
        except Stop:
            pass
        finally:
            shutil.rmtree(self._bootstrap_hooks, ignore_errors=True)

    def run_command(self, subcommand: str, subjects: list[str], approved_by: str | None = None) -> list[Result]:
        try:
            with self.locked():
                # A state that cannot be read or written is reported by the subcommand itself, as before.
                with contextlib.suppress(delivery_state.StateError, OSError):
                    self.recover_interrupted()
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
        if subcommand == "batch":
            return self.batch(subjects)
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
    # A result about no candidate is named for what it covers: a release, or serve's setup.
    about = document["candidate"] or ("release" if document["subcommand"] == "release" else "setup")
    name = f"{stamp}-{document['subcommand']}-{re.sub(r'[^A-Za-z0-9_.-]', '_', about)}.json"
    # UTF-8 bytes with "\n" line endings on every platform.
    (directory / name).write_bytes(delivery_state.dumps(document).encode("utf-8"))
    return document


def ascii_json(value: object) -> bytes:
    """JSON for stdout: ASCII-only, so no console code page can fail on it."""
    return (json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")


def _refused_stop(subject: str, head: str | None) -> Callable[[dict], tuple[dict | None, dict]]:
    """Stop a refused candidate only as serve took it: a lane that handed it off again since keeps its new head."""

    def build(current: dict) -> tuple[dict | None, dict]:
        item = next((entry for entry in current["queue"] if entry["subject"] == subject), None)
        if item is None or item["stage"] != "queued" or item["head"] != head:
            raise Stop("candidate_unknown", f"{subject} changed since serve took it")
        return None, {"/stage": "stopped"}

    return build


def _setup_stop(repository: Path, state_directory: Path, *, remote: str, base: str,
                lead: str) -> tuple[Result, dict | None]:
    """Read the binding from the base and check the checkout once, under the queue lock, before serving anything."""
    runner = Runner(repository, state_directory, remote=remote, base=base, lead=lead)

    def check(result: Result) -> None:
        with runner.locked():
            result.baseHead = runner.load()
            result.base = base
        # A refresh setting the tool cannot use would fail on every interval: refuse it here, once.
        member = (runner.binding or {}).get("state", {})
        try:
            delivery_state.refresh_interval(member)
            for source in delivery_state.SNAPSHOT_SOURCES:
                delivery_state.snapshot_config(member, source)
        except delivery_state.StateError as error:
            raise Stop("binding_invalid", str(error)) from None

    try:
        result = runner.invoke("serve", None, check)
    finally:
        shutil.rmtree(runner._bootstrap_hooks, ignore_errors=True)
    return result, runner.binding


def _refresh(repository: Path, state_directory: Path, member: dict) -> None:
    """Read the declared forge and tracker snapshots and reconcile them; a failure is reported, never fatal."""
    try:
        delivery_state.refresh_sources(state_directory, member, cwd=repository)
    except (delivery_state.StateError, OSError) as error:
        # Each source's own failures are recorded on the record; this is the refresh itself failing.
        print(f"serve: the refresh of the forge and tracker failed: {error}", file=sys.stderr, flush=True)


def serve(
    repository: Path,
    state_directory: Path,
    *,
    remote: str,
    base: str,
    lead: str = "lead",
    until_idle: bool = False,
    poll: float = 30.0,
    seams: dict[str, Seam] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_result: Callable[[Result], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> list[Result]:
    """Take what is queued, in order, whenever the merge slot is free.

    Two or more queued candidates run as one batch; one runs alone. A
    candidate waiting for the lead to accept its hand-off is left alone. The
    lead's meta.hold pauses serving, and so does a stop that stopped the queue
    until the lead journals base.repaired; a stopped candidate waits for its
    lane to hand it off again, and so does a refused one, which serve would
    otherwise take again every turn. A checkout or binding the runner cannot
    use ends serve before it takes anything, with that one result. Each turn
    takes the queue lock afresh, so a serve killed outright is cleared by the
    next one's recovery. Between turns, on the binding's state.refreshSeconds,
    serve reads the declared forge and tracker snapshots and reconciles them,
    whether or not it is held; a run in progress delays the read. With
    until_idle, serving ends once nothing more can be taken and returns every
    result; without it, serving never returns, so each result goes to
    on_result as it is written and none is kept.
    """
    results: list[Result] = []
    while True:
        # A wrong checkout or binding would refuse every candidate in turn: find it once, before taking any.
        setup, binding = _setup_stop(repository, state_directory, remote=remote, base=base, lead=lead)
        if setup.stop is None:
            break
        if setup.stop["reason"] != "queue_locked":
            write_result(state_directory, setup)
            if on_result is not None:
                on_result(setup)
            return [setup]
        if until_idle:
            return results
        sleep(poll)
    member = (binding or {}).get("state", {})
    interval = delivery_state.refresh_interval(member)
    due = clock()

    def pause() -> None:
        # An idle wait ends when the next read of the sources is due, so it is never late by a poll.
        sleep(poll if not interval else max(0.0, min(poll, due - clock())))

    while True:
        # A killed serve or run leaves live state behind; clear it first, even when nothing is queued.
        Runner(repository, state_directory, remote=remote, base=base, lead=lead).recover()
        if interval and clock() >= due:
            # Before the queue is read, so a merge the forge reports is never taken again.
            _refresh(repository, state_directory, member)
            due = clock() + interval
        state = delivery_state.read_state(state_directory)
        journal = delivery_state.read_journal(state_directory)
        waiting = [
            item["subject"] for item in sorted(state["queue"], key=lambda entry: (entry["order"], entry["subject"]))
            if item["stage"] == "queued" and not item.get("pending")
        ]
        if state["meta"].get("hold") or delivery_state.base_awaits_repair(journal) or not waiting:
            if until_idle:
                return results
            pause()
            continue
        heads = {item["subject"]: item["head"] for item in state["queue"]}
        runner = Runner(repository, state_directory, remote=remote, base=base, lead=lead, seams=seams)
        taken = runner.run_command("batch" if len(waiting) > 1 else "run", waiting)
        if all(result.stop is not None and result.stop["reason"] == "queue_locked" for result in taken):
            # Another runner holds the queue: nothing was attempted, so nothing is recorded.
            if until_idle:
                return results
            pause()
            continue
        for result in taken:
            write_result(state_directory, result)
            if result.outcome == "refused" and result.candidate is not None and result.stop["reason"] != "queue_locked":
                # A refusal leaves the candidate queued as it was; serve stops it, journaled once, as any stop.
                with contextlib.suppress(Stop, delivery_state.StateError):
                    runner.write(selector=f"queue[subject={result.candidate}]",
                                 build=_refused_stop(result.candidate, heads.get(result.candidate)))
        for result in taken:
            # Reported only once every result is written and every refusal stopped, so a reader never loses them.
            if on_result is not None:
                on_result(result)
        if until_idle:
            results += taken
        after = delivery_state.read_state(state_directory)
        moved = any(item["subject"] in waiting and item["stage"] != "queued" for item in after["queue"])
        if not moved:
            # Nothing it took left queued (another runner held the lock): wait rather than spin.
            if until_idle:
                return results
            pause()


def evidence(repository: Path, *, remote: str, base: str, check: str, head: str, result: str = "passed") -> dict:
    """A lane's evidence of its own run of a reusable check at ``head``, for ``delivery_state.py handoff --evidence``."""
    runner = Runner(repository, repository, remote=remote, base=base)
    try:
        return runner.evidence(check, head, result)
    finally:
        shutil.rmtree(runner._bootstrap_hooks, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="the repository the queue serves")
    parser.add_argument("--state", type=Path, help="the delivery state directory; every command but evidence needs it")
    parser.add_argument("--remote", required=True, help="the remote the base and candidates live on")
    parser.add_argument("--base", required=True, help="the base branch; its binding must name the same base")
    parser.add_argument("--lead", default="lead", help="the lease holder name the lead uses in the state")
    parser.add_argument("--wait", type=float, default=0.0, help="seconds to wait for the queue lock")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("sync", "check", "merge"):
        commands.add_parser(name).add_argument("candidate")
    commands.add_parser("run").add_argument("candidates", nargs="+")
    commands.add_parser("batch").add_argument("candidates", nargs="+")
    serving = commands.add_parser("serve", help="take queued candidates as they come, until held")
    serving.add_argument("--poll", type=float, default=30.0, help="seconds between looks at an idle or held queue")
    serving.add_argument("--until-idle", action="store_true", help="stop once nothing more can be taken")
    release = commands.add_parser("release")
    release.add_argument("ref")
    release.add_argument("--approved-by", help="records an approval the operator already gave")
    proof = commands.add_parser("evidence", help="a lane's evidence of its own run of a reusable check, for its hand-off")
    proof.add_argument("--check", required=True, help="the reusable check the lane ran")
    proof.add_argument("--head", required=True, help="the commit the lane ran it on")
    proof.add_argument("--result", choices=("passed", "failed"), default="passed", help="how the lane's run ended")
    args = parser.parse_args(argv)
    if args.command == "evidence":
        try:
            found = evidence(args.repo, remote=args.remote, base=args.base, check=args.check, head=args.head,
                             result=args.result)
        except Stop as stop:
            sys.stdout.buffer.write(ascii_json({"reason": stop.reason, "detail": stop.detail}))
            return 1
        except RuntimeError as error:
            sys.stdout.buffer.write(ascii_json({"reason": "git_failed", "detail": str(error)}))
            return 1
        sys.stdout.buffer.write(ascii_json(found))
        return 0
    if args.state is None:
        parser.error("--state is required")
    if args.command == "serve" and not args.until_idle:
        # Serving until stopped: one document a line as each is written; state/results keeps them all.
        def report(result: Result) -> None:
            try:
                sys.stdout.buffer.write(json.dumps(result.document(), ensure_ascii=True, sort_keys=True).encode("ascii") + b"\n")
                sys.stdout.buffer.flush()
            except BrokenPipeError:
                # Nobody reads any more: serving goes on, and state/results keeps every document.
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())

        def interrupt(signum: int, frame: object) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, interrupt)
        try:
            # Serving until stopped returns only when it cannot start.
            refused = serve(args.repo, args.state, remote=args.remote, base=args.base, lead=args.lead,
                            poll=args.poll, on_result=report)
        except KeyboardInterrupt:
            # A stop mid-turn leaves live state the next serve or run clears first.
            return 0
        return 1 if refused else 0
    if args.command == "serve":
        served = serve(args.repo, args.state, remote=args.remote, base=args.base, lead=args.lead,
                       until_idle=True, poll=args.poll)
        documents = [result.document() for result in served]
        sys.stdout.buffer.write(ascii_json(documents))
        sys.stdout.buffer.flush()
        return 0 if all(document["outcome"] not in ("stopped", "refused") for document in documents) else 1
    runner = Runner(
        args.repo, args.state, remote=args.remote, base=args.base, lead=args.lead,
        lock_wait=args.wait,
    )
    if args.command == "release":
        results = runner.run_command("release", [args.ref], args.approved_by)
    elif args.command in ("run", "batch"):
        results = runner.run_command(args.command, args.candidates)
    else:
        results = runner.run_command(args.command, [args.candidate])
    documents = [write_result(runner.state_directory, result) for result in results]
    sys.stdout.buffer.write(ascii_json(documents if args.command in ("run", "batch") else documents[0]))
    sys.stdout.buffer.flush()
    return 0 if all(document["outcome"] not in ("stopped", "refused") for document in documents) else 1


if __name__ == "__main__":
    raise SystemExit(main())
