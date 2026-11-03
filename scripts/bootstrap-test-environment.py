#!/usr/bin/env python3
"""Create the worktree-private environment used by repository test commands."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENVIRONMENT = ".venv"
NODE_MINIMUM = 20
NODE_MISSING = (
    "Node.js 20 or newer, with npm, is required for the page-test lane; "
    "install it and rerun python3 scripts/bootstrap-test-environment.py."
)


def environment_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def create_environment(root: Path, name: str = DEFAULT_ENVIRONMENT) -> Path:
    root = root.resolve(strict=True)
    environment = root / name
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


def bootstrap(root: Path = ROOT, environment: str = DEFAULT_ENVIRONMENT) -> Path:
    root = root.resolve(strict=True)
    # The page-test lane is development-only: a checkout that declares it
    # installs its locked packages into the checkout, never into a release.
    page_lane = (root / "package-lock.json").is_file()
    npm = node_tools()[1] if page_lane else None
    interpreter = create_environment(root, environment)
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


def environment_name(name: str) -> str:
    # Only these are ignored by git, and a folder elsewhere would land outside the checkout or in the corpus.
    if not re.fullmatch(r"\.venv(-[A-Za-z0-9._]+)?", name):
        raise argparse.ArgumentTypeError(f"{name!r} is not .venv or a .venv-<name> folder in the checkout")
    return name


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--environment",
        type=environment_name,
        default=DEFAULT_ENVIRONMENT,
        help="the environment's folder in the checkout, so a second Python leaves .venv alone",
    )
    print(bootstrap(environment=parser.parse_args(argv).environment))


if __name__ == "__main__":
    main()
