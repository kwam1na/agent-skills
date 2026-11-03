"""Settings for a fixture's own repositories, so each writes fewer files without changing what it holds."""

from __future__ import annotations

import subprocess
from pathlib import Path

# Appended once rather than set by `git config`, which rewrites the file for each setting. What a repository
# receives stays one pack rather than a file per object, no fetch starts maintenance, and no ref keeps a log.
QUIET = "[transfer]\n\tunpackLimit = 1\n[maintenance]\n\tauto = false\n[core]\n\tlogAllRefUpdates = false\n"


def quieten(repository: Path) -> None:
    """Quieten ``repository``, a checkout or a bare repository, for everything git does in it from now on."""
    config = repository / ".git" / "config"
    if not config.exists():
        config = repository / "config"
    with config.open("a", encoding="utf-8") as settings:
        settings.write(QUIET)


def quiet_clone(remote: Path, target: Path, *, settings: str = "") -> None:
    """Clone ``remote``'s main to ``target``, quietened, with its configuration written once.

    The result is what `git clone` leaves, without the eight rewrites of the
    configuration it makes on the way. ``settings`` are more configuration
    lines, in place before anything is checked out.
    """
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(target)], check=True, capture_output=True)
    origin = (f'[remote "origin"]\n\turl = {remote.as_posix()}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
              '[branch "main"]\n\tremote = origin\n\tmerge = refs/heads/main\n')
    with (target / ".git" / "config").open("a", encoding="utf-8") as config:
        config.write(origin + QUIET + settings)
    for arguments in (["fetch", "--quiet", "origin"],
                      ["symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
                      ["checkout", "--quiet", "main"]):
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *arguments], cwd=target, check=True,
                       capture_output=True)
