#!/usr/bin/env python3
"""Mutation sensors for the v1 metadata-only registry."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate-registry.py"


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def with_copy(mutator) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temporary:
        copied = Path(temporary) / "corpus"
        shutil.copytree(ROOT, copied, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        mutator(copied)
        return run(copied)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_missing_provenance_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "provenance.lock.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["entries"] = document["entries"][1:]
        write_json(path, document)

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("missing provenance record for skill: bootstrap-agent-harness" in result.stdout, result.stdout)
    print("verified rejection: missing provenance")


def test_unknown_profile_member_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "profiles" / "core.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["members"].append("not-a-shipped-skill")
        write_json(path, document)

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("unknown profile member: core -> not-a-shipped-skill" in result.stdout, result.stdout)
    print("verified rejection: unknown profile member")


def test_core_profile_is_exact_and_deterministic() -> None:
    first = run(ROOT, "--profile", "core")
    second = run(ROOT, "--profile", "core")
    require(first.returncode == 0, first.stdout)
    require(first.stdout == second.stdout, "profile resolution is not deterministic")
    require(json.loads(first.stdout) == {
        "files": [
            "skills/compound-delivery-kernel/SKILL.md",
            "skills/deliver-work/SKILL.md",
            "skills/deliver-work/agents/openai.yaml",
        ],
        "members": ["compound-delivery-kernel", "deliver-work"],
        "profile": "core",
    }, first.stdout)
    print("verified resolution: exact deterministic core profile")


def main() -> int:
    tests = [
        test_missing_provenance_rejects,
        test_unknown_profile_member_rejects,
        test_core_profile_is_exact_and_deterministic,
    ]
    for test in tests:
        test()
    print(f"ok: {len(tests)} registry mutation sensors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
