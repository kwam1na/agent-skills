"""Copy this repository's corpus for a test to change, leaving out what no fixture reads."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# A checkout's environments, other checkouts and generated files: only .git and __pycache__ used to be left out,
# so a copy also wrote every file of each virtual environment and node_modules.
NEVER_COPIED = (".git", ".venv", ".venv-*", "node_modules", ".worktrees", ".claude", ".agent-skills", ".pytest_cache",
                "__pycache__", "*.pyc", "*.egg-info", ".test-timings.json")


def copy_corpus(target: Path, *, source: Path = ROOT, leaving_out: tuple[str, ...] = ()) -> Path:
    """Copy ``source`` to ``target``, which never reaches into its own copy.

    A target inside ``source``, as under a ``TMPDIR`` inside a checkout, is left
    out along with every folder leading to it, so the copy ends.
    """
    destination = os.path.realpath(target)
    patterns = shutil.ignore_patterns(*NEVER_COPIED, *leaving_out)

    def ignored(folder: str, names: list[str]) -> set[str]:
        real = os.path.realpath(folder)
        return set(patterns(folder, names)) | {
            name for name in names
            if destination == (entry := os.path.join(real, name)) or destination.startswith(entry + os.sep)
        }

    shutil.copytree(source, target, ignore=ignored)
    return target


class SharedCorpus:
    """One copy of a tree that several tests take in turn, so each costs only the files it changes.

    ``restored()`` hands back the copy with exactly its first bytes, modes and
    entries: what a test added is removed, what it changed or removed is written
    back, and a symlink or folder put in a file's place is replaced. It reads
    the whole tree each time and compares it to what it held at the start, so
    one test's writes cannot reach another however it made them. Call it at the
    start of every test, and register it as a cleanup so the last test leaves
    nothing behind in the copy.
    """

    def __init__(self, target: Path, *, source: Path = ROOT, leaving_out: tuple[str, ...] = ()) -> None:
        self.root = copy_corpus(target, source=source, leaving_out=leaving_out)
        self.mode = stat.S_IMODE(self.root.stat().st_mode)
        self.folders: dict[str, int] = {}
        self.files: dict[str, tuple[int, bytes]] = {}
        # The copy follows the source's links, so any link found in it later is one a test made.
        for folder, names, files in os.walk(self.root):
            for name in [*names, *files]:
                path = Path(folder) / name
                relative = str(path.relative_to(self.root))
                mode = stat.S_IMODE(path.stat().st_mode)
                if path.is_dir():
                    self.folders[relative] = mode
                else:
                    self.files[relative] = (mode, path.read_bytes())

    def restored(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        self._open_folders()
        for folder, names, files in os.walk(self.root):
            for name in [*names, *files]:
                self._drop_stray(Path(folder) / name)
            names[:] = [name for name in names
                        if (Path(folder) / name).is_dir() and not (Path(folder) / name).is_symlink()]
        for relative in sorted(self.folders):
            os.makedirs(self.root / relative, exist_ok=True)
        for relative, (mode, content) in self.files.items():
            path = self.root / relative
            if not path.exists():
                path.write_bytes(content)
            else:
                # A file a test closed, or made read-only, opens again before it is compared.
                if stat.S_IMODE(path.stat().st_mode) != mode:
                    os.chmod(path, mode | 0o600)
                if path.read_bytes() != content:
                    os.chmod(path, mode | 0o600)
                    path.write_bytes(content)
            if stat.S_IMODE(path.stat().st_mode) != mode:
                os.chmod(path, mode)
        for relative, mode in {**self.folders, ".": self.mode}.items():
            if stat.S_IMODE((self.root / relative).stat().st_mode) != mode:
                os.chmod(self.root / relative, mode)
        return self.root

    def _open_folders(self) -> None:
        """Make every folder a test closed readable and writable again, so the rest can be compared."""
        self._open(self.root)
        for folder, names, _files in os.walk(self.root):
            for name in names:
                path = Path(folder) / name
                if not path.is_symlink():
                    self._open(path)

    @staticmethod
    def _open(folder: Path) -> None:
        mode = stat.S_IMODE(folder.stat().st_mode)
        if mode & 0o700 != 0o700:
            os.chmod(folder, mode | 0o700)

    def _drop_stray(self, path: Path) -> None:
        """Remove ``path`` unless it is what the copy held there, as the same kind of entry."""
        relative = str(path.relative_to(self.root))
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            if relative not in self.folders:
                shutil.rmtree(path)
        elif relative not in self.files:
            path.unlink()
