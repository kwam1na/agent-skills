"""Bare remotes and clones for tests of a shared record across machines, and the check that nothing was forced.

Every bare remote this builds logs every ref update and allows a forced one, as a forge does, so a forced update the
product made would land and be recorded. ``forced_updates`` reads that log: every recorded update to a branch or ref a
shared record writes must be a fast-forward. Later stories import this helper and never change it.
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

from quiet_git import quiet_clone, quieten

ZERO = "0" * 40
# After the quiet settings, which turn the log off: the last value of a setting wins.
LOGGED = "[core]\n\tlogAllRefUpdates = always\n[receive]\n\tdenyNonFastForwards = false\n"
# What a shared record writes on the remote: its plan branch, its lead branch, and every seat's partition.
SHARED_REFS = ("refs/heads/plan/*", "refs/heads/lead/*", "refs/agent-skills/*")


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *arguments], cwd=repository, check=True,
                          capture_output=True, text=True).stdout.strip()


def bare_remote(root: Path, name: str = "remote.git") -> Path:
    """A bare remote under ``root`` whose main holds one commit, logging every ref update and allowing forced ones."""
    remote = root / name
    subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(remote)], check=True,
                   capture_output=True)
    quieten(remote)
    with (remote / "config").open("a", encoding="utf-8") as config:
        config.write(LOGGED)
    seed = root / f"{name}.seed"
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(seed)], check=True, capture_output=True)
    quieten(seed)
    (seed / "README.md").write_text("A shared delivery's fixture.\n", encoding="utf-8")
    git(seed, "add", "README.md")
    git(seed, "-c", "user.name=Seed", "-c", "user.email=seed@example.invalid", "commit", "--quiet", "-m", "Seed")
    git(seed, "push", "--quiet", str(remote), "main")
    return remote


def clone(remote: Path, target: Path, *, name: str) -> Path:
    """A machine's clone of ``remote`` at ``target``, with its own git identity."""
    quiet_clone(remote, target, settings=f"[user]\n\tname = {name}\n\temail = {name.lower()}@example.invalid\n")
    return target


def ref_updates(remote: Path, patterns: tuple[str, ...] = SHARED_REFS) -> list[tuple[str, str, str]]:
    """Every update the remote logged to a ref matching ``patterns``, as ``(ref, old, new)``, oldest first."""
    updates = []
    logs = remote / "logs"
    for path in sorted(logs.rglob("*")) if logs.is_dir() else []:
        ref = path.relative_to(logs).as_posix()
        if not path.is_file() or not any(fnmatch.fnmatchcase(ref, pattern) for pattern in patterns):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            old, new = line.split(" ", 2)[:2]
            updates.append((ref, old, new))
    return updates


def forced_updates(remote: Path, patterns: tuple[str, ...] = SHARED_REFS) -> list[tuple[str, str, str]]:
    """Every logged update to a ref matching ``patterns`` that was not a fast-forward."""
    forced = []
    for ref, old, new in ref_updates(remote, patterns):
        if old == ZERO or new == ZERO:
            continue
        ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", old, new], cwd=remote, capture_output=True)
        if ancestor.returncode != 0:
            forced.append((ref, old, new))
    return forced


def assert_fast_forward_only(case, remote: Path, patterns: tuple[str, ...] = SHARED_REFS) -> None:
    """The remote logged at least one update to ``patterns``, and every one was a fast-forward."""
    case.assertTrue(ref_updates(remote, patterns), f"{remote} logged no update to {patterns}")
    case.assertEqual(forced_updates(remote, patterns), [])
