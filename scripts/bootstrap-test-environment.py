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
PYTHON_PIN = ".python-version"
PYTHON_MISSING = (
    "Python {version} is required for this checkout's {environment}, as {pin} says; "
    "install it with uv python install {version} and rerun python3 scripts/bootstrap-test-environment.py."
)
NODE_MINIMUM = 20
NODE_MISSING = (
    "Node.js 20 or newer, with npm, is required for the page-test lane; "
    "install it and rerun python3 scripts/bootstrap-test-environment.py."
)


def environment_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def pinned_version(root: Path, name: str) -> str | None:
    # Only the default environment follows the pin, so a named one can still try a second Python.
    pin = root / PYTHON_PIN
    if name != DEFAULT_ENVIRONMENT or not pin.is_file():
        return None
    return pin.read_text(encoding="utf-8").strip()


def find_python(version: str) -> str:
    if version == f"{sys.version_info.major}.{sys.version_info.minor}":
        return sys.executable
    found = shutil.which(f"python{version}")
    if found is not None:
        return found
    uv = shutil.which("uv")
    if uv is not None:
        answer = subprocess.run([uv, "python", "find", version], capture_output=True, text=True, check=False)
        if answer.returncode == 0 and answer.stdout.strip():
            return answer.stdout.strip()
    raise SystemExit(PYTHON_MISSING.format(version=version, environment=DEFAULT_ENVIRONMENT, pin=PYTHON_PIN))


def built_on_another_python(environment: Path, version: str) -> bool:
    settings = environment / "pyvenv.cfg"
    if not settings.is_file():
        return environment.exists()
    built = re.search(r"^version(?:_info)?\s*=\s*(\d+\.\d+)", settings.read_text(encoding="utf-8"), re.MULTILINE)
    return built is None or built.group(1) != version


def create_environment(root: Path, name: str = DEFAULT_ENVIRONMENT) -> Path:
    root = root.resolve(strict=True)
    environment = root / name
    version = pinned_version(root, name)
    python = sys.executable if version is None else find_python(version)
    # Building over another Python's environment would leave its packages behind.
    clear = ["--clear"] if version is not None and built_on_another_python(environment, version) else []
    subprocess.run(
        [python, "-m", "venv", *clear, str(environment)],
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
        help="the environment's folder in the checkout, built with the Python running this script rather than the pinned one",
    )
    print(bootstrap(environment=parser.parse_args(argv).environment))


if __name__ == "__main__":
    main()
