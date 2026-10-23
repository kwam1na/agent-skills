#!/usr/bin/env python3
"""Create the worktree-private environment used by repository test commands."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE_MINIMUM = 20
NODE_MISSING = (
    "Node.js 20 or newer, with npm, is required for the page-test lane; "
    "install it and rerun python3 scripts/bootstrap-test-environment.py."
)


def environment_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def create_environment(root: Path) -> Path:
    root = root.resolve(strict=True)
    environment = root / ".venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(environment)],
        cwd=root,
        check=True,
    )
    return environment_python(environment)


def node_tools() -> tuple[str, str]:
    node = shutil.which("node")
    npm = shutil.which("npm")
    if node is not None and npm is not None:
        version = subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=False
        ).stdout.strip()
        major = version.removeprefix("v").split(".", 1)[0]
        if major.isdigit() and int(major) >= NODE_MINIMUM:
            return node, npm
    raise SystemExit(NODE_MISSING)


def bootstrap(root: Path = ROOT) -> Path:
    root = root.resolve(strict=True)
    # The page-test lane is development-only: a checkout that declares it
    # installs its locked packages into the checkout, never into a release.
    page_lane = (root / "package-lock.json").is_file()
    npm = node_tools()[1] if page_lane else None
    interpreter = create_environment(root)
    subprocess.run(
        [str(interpreter), "-m", "pip", "install", "-e", ".[test]"],
        cwd=root,
        check=True,
    )
    if npm is not None:
        subprocess.run(
            [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=root,
            check=True,
        )
    return interpreter


if __name__ == "__main__":
    print(bootstrap())
