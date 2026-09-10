#!/usr/bin/env python3
"""Create the worktree-private environment used by repository test commands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def environment_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def create_environment(root: Path, *, without_pip: bool = False) -> Path:
    root = root.resolve(strict=True)
    environment = root / ".venv"
    command = [sys.executable, "-m", "venv"]
    if without_pip:
        command.append("--without-pip")
    subprocess.run(
        [*command, str(environment)],
        cwd=root,
        check=True,
    )
    return environment_python(environment)


def bootstrap(root: Path = ROOT) -> Path:
    root = root.resolve(strict=True)
    interpreter = create_environment(root)
    subprocess.run(
        [str(interpreter), "-m", "pip", "install", "-e", ".[test]"],
        cwd=root,
        check=True,
    )
    return interpreter


if __name__ == "__main__":
    print(bootstrap())
