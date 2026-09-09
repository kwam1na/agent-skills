#!/usr/bin/env python3
"""Qualify the exact bootstrap profile archive outside the producer checkout."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


DRIVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DRIVER_ROOT))

from agent_skills.release import build_release, verify_release
from agent_skills.validate import canonical_json


SCENARIOS = ("fresh-install", "profile-validation", "rollback")
TRUSTED_PATHS = (
    "agent_skills/cli.py",
    "agent_skills/lifecycle.py",
    "agent_skills/receipt.py",
    "agent_skills/release.py",
    "agent_skills/validate.py",
    "profiles/bootstrap.json",
    "scripts/qualify-bootstrap.py",
    "skills/bootstrap-agent-harness/SKILL.md",
    "skills/bootstrap-agent-harness/agents/openai.yaml",
)


def _sha256(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _driver_digest() -> str:
    records = [
        {"path": path, "sha256": _sha256((DRIVER_ROOT / path).read_bytes())}
        for path in TRUSTED_PATHS
    ]
    return _sha256(canonical_json(records).encode())


def _run(arguments: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        env={
            **os.environ,
            "AGENT_SKILLS_CHECKOUT": "",
            "AGENT_SKILLS_EXECUTION_CONTEXT": "",
            "GITHUB_EVENT_NAME": "",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "",
        },
        text=True,
        capture_output=True,
        timeout=60,
    )


def _scenario(identifier: str, evidence: list[str]) -> dict[str, object]:
    summary = {
        "assertionCount": len(evidence),
        "evidence": evidence,
        "id": identifier,
        "result": "passed",
    }
    return {**summary, "checkpointSha256": _sha256(canonical_json(summary).encode())}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def qualify(archive: Path, metadata: Path) -> dict[str, object]:
    archive = archive.resolve()
    metadata = metadata.resolve()
    verified = verify_release(archive, metadata)
    if verified.profile_id != "bootstrap":
        raise ValueError("qualification.release_binding: bootstrap candidate is required")
    with tempfile.TemporaryDirectory(prefix="agent-skills-bootstrap-qualification-") as temporary:
        base = Path(temporary)
        candidate = base / "candidate"
        verify_release(archive, metadata, extract_to=candidate)
        product = (candidate / "runtime/runtime.json").is_file()

        repository = base / "fresh-repository"
        repository.mkdir()
        _require(_run(["git", "init", "-q", "-b", "main"], cwd=repository).returncode == 0, "git init failed")
        install_arguments = [
                sys.executable,
                "-B",
                str(archive),
                "--root",
                str(repository),
                *(["--product"] if product else []),
                "apply",
                "--archive",
                str(archive),
                "--metadata",
                str(metadata),
                "--maintenance",
            ]
        installed = _run(
            install_arguments,
            cwd=repository,
        )
        _require(installed.returncode == 0, "fresh bootstrap install failed")
        status = json.loads(installed.stdout)["status"]
        _require(status["lifecycle"] == "current" and not status["blockers"], "fresh lifecycle is not current")
        if product:
            _require(status.get("productReady") is True, "fresh product is not ready")
        for host in (".agents", ".claude"):
            _require(
                (repository / host / "skills/bootstrap-agent-harness/SKILL.md").read_bytes()
                == (DRIVER_ROOT / "skills/bootstrap-agent-harness/SKILL.md").read_bytes(),
                f"{host} bootstrap exposure differs",
            )
        fresh_evidence = [
            "archive-native apply installs without producer checkout state",
            "fresh lifecycle reports current with no blockers",
            "Codex exposure carries the exact bootstrap skill",
            "Claude Code exposure carries the exact bootstrap skill",
        ]
        if product:
            fresh_evidence.append("installed runtime reports product ready")
        fresh = _scenario(
            "fresh-install",
            fresh_evidence,
        )

        validation = _run(
            [sys.executable, "-B", "-m", "agent_skills.validate", "--root", "."],
            cwd=candidate,
        )
        _require(validation.returncode == 0, "extracted bootstrap profile validation failed")
        profile = json.loads((candidate / "profiles/bootstrap.json").read_text())
        _require(profile["members"] == ["bootstrap-agent-harness"], "bootstrap profile members differ")
        validated = _scenario(
            "profile-validation",
            [
                "extracted candidate passes standalone corpus validation",
                "profile selects exactly bootstrap-agent-harness",
            ],
        )

        rollback_repository = base / "rollback-repository"
        rollback_repository.mkdir()
        _require(_run(["git", "init", "-q", "-b", "main"], cwd=rollback_repository).returncode == 0, "git init failed")
        prior_archive = base / "prior.zip"
        prior_metadata = base / "prior.release.json"
        build_release(
            candidate,
            prior_archive,
            prior_metadata,
            release_id="bootstrap-qualification-prior",
            profile_id="bootstrap",
            runtime_directory=candidate / "runtime" if product else None,
        )
        for release_archive, release_metadata in ((prior_archive, prior_metadata), (archive, metadata)):
            applied = _run(
                [
                    sys.executable,
                    "-B",
                    str(release_archive),
                    "--root",
                    str(rollback_repository),
                    *(["--product"] if product else []),
                    "apply",
                    "--archive",
                    str(release_archive),
                    "--metadata",
                    str(release_metadata),
                    "--maintenance",
                ],
                cwd=rollback_repository,
            )
            _require(applied.returncode == 0, "bootstrap update path failed")
        prior_digest = verify_release(prior_archive, prior_metadata).archive_sha256
        prior_archive.unlink()
        prior_metadata.unlink()
        rolled_back = _run(
            [
                sys.executable,
                "-B",
                str(rollback_repository / ".agent-skills/current"),
                "--root",
                str(rollback_repository),
                *(["--product"] if product else []),
                "rollback",
                "--maintenance",
            ],
            cwd=rollback_repository,
        )
        _require(rolled_back.returncode == 0, "offline rollback failed")
        rollback_status = json.loads(rolled_back.stdout)["status"]
        _require(rollback_status["active"]["archiveSha256"] == prior_digest, "rollback restored wrong generation")
        rollback = _scenario(
            "rollback",
            [
                "the same apply command updates a current bootstrap generation",
                "installed bytes roll back without checkout, archive, or metadata",
                "rollback restores the retained prior generation",
            ],
        )

    scenarios = [fresh, validated, rollback]
    assertion_count = sum(int(scenario["assertionCount"]) for scenario in scenarios)
    return {
        "qualificationDriverSha256": _driver_digest(),
        "product": {
            "installedReady": product,
            "runtimeIncluded": product,
        },
        "release": {
            "archiveSha256": verified.archive_sha256,
            "metadataSha256": verified.metadata_sha256,
            "profile": verified.profile_id,
            "releaseId": verified.release_id,
        },
        "replay": "python3 scripts/qualify-bootstrap.py --archive <archive> --metadata <metadata> --record <record>",
        "scenarios": scenarios,
        "schemaVersion": "agent-skills-bootstrap-qualification/1",
        "summary": {
            "assertionCount": assertion_count,
            "failedScenarioCount": 0,
            "passedScenarioCount": len(scenarios),
            "requiredScenarioCount": len(SCENARIOS),
            "result": "passed",
        },
    }


def _atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            target.write(contents)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        if args.record.resolve() in {args.archive.resolve(), args.metadata.resolve()}:
            raise ValueError("qualification.record_path: output cannot replace candidate inputs")
        args.record.unlink(missing_ok=True)
        record = qualify(args.archive, args.metadata)
        _atomic_write(args.record, canonical_json(record))
    except (OSError, ValueError, subprocess.SubprocessError):
        print(canonical_json({"error": "bootstrap-qualification-failed", "ok": False}), end="")
        return 1
    print(canonical_json(record), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
