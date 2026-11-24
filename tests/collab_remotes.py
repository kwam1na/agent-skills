"""Bare remotes for multi-machine tests, built so a forced update would land and be recorded.

A forge accepts a forced push unless it is told not to, so a test remote does too: `receive.denyNonFastForwards` stays
off. It records every ref update in its reflog (`core.logAllRefUpdates=always`), and `assert_fast_forwards_only` reads
that log: a forced update the product made would have landed, and shows there as a step that is not a fast-forward.
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

from tests.quiet_git import quiet_clone, quieten

ZERO = "0" * 40
# What a shared record writes on the remote: its plan branch, its lead branch, and every seat's partition.
SHARED_REFS = ("refs/heads/plan/*", "refs/heads/lead/*", "refs/agent-skills/*")


def git(remote: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=remote, capture_output=True, text=True)


def bare_remote(path: Path) -> Path:
    """A bare repository at ``path``, quietened, that allows forced updates and logs every ref update."""
    subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(path)], check=True,
                   capture_output=True)
    quieten(path)
    # After quieten's settings, which turn ref logs off, so these win.
    with (path / "config").open("a", encoding="utf-8") as config:
        config.write("[core]\n\tlogAllRefUpdates = always\n[receive]\n\tdenyNonFastForwards = false\n")
    return path


def updates(remote: Path, ref: str) -> list[tuple[str, str]]:
    """Every recorded update of ``ref``, oldest first, as (old, new) commit ids; old is all zeros for a creation."""
    log = remote / "logs" / ref
    if not log.is_file():
        return []
    found = []
    for line in log.read_text(encoding="utf-8").splitlines():
        old, new = line.split(" ", 2)[:2]
        found.append((old, new))
    return found


def assert_fast_forwards_only(remote: Path, ref: str) -> None:
    """Fail unless ``remote``'s reflog for ``ref`` is non-empty and every update in it is a fast-forward: a creation,
    or a step from a commit to one descending from it. A deletion fails too."""
    steps = updates(remote, ref)
    if not steps:
        raise AssertionError(f"{remote} has no recorded update of {ref}: its reflog is empty or off")
    for old, new in steps:
        if new.strip("0") == "":
            raise AssertionError(f"{ref} was deleted on {remote}, at {old}")
        if old.strip("0") == "":
            continue
        if git(remote, "merge-base", "--is-ancestor", old, new).returncode != 0:
            raise AssertionError(f"{ref} on {remote} moved from {old} to {new}, which is not a fast-forward: "
                                 "a forced update")


def clone(remote: Path, target: Path, *, name: str) -> Path:
    """A machine's quietened clone of ``remote``'s main at ``target``, with its own git identity."""
    quiet_clone(remote, target, settings=f"[user]\n\tname = {name}\n\temail = {name.lower()}@example.invalid\n")
    return target


def ref_updates(remote: Path, patterns: tuple[str, ...] = SHARED_REFS) -> list[tuple[str, str, str]]:
    """Every update the remote logged to a ref matching ``patterns``, as ``(ref, old, new)``, oldest first."""
    found = []
    logs = remote / "logs"
    for path in sorted(logs.rglob("*")) if logs.is_dir() else []:
        ref = path.relative_to(logs).as_posix()
        if path.is_file() and any(fnmatch.fnmatchcase(ref, pattern) for pattern in patterns):
            found += [(ref, old, new) for old, new in updates(remote, ref)]
    return found


def forced_updates(remote: Path, patterns: tuple[str, ...] = SHARED_REFS) -> list[tuple[str, str, str]]:
    """Every logged update to a ref matching ``patterns`` that was not a fast-forward; a creation or deletion is not."""
    return [(ref, old, new) for ref, old, new in ref_updates(remote, patterns)
            if old != ZERO and new != ZERO and git(remote, "merge-base", "--is-ancestor", old, new).returncode != 0]
