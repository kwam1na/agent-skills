#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


DRIVER_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DRIVER_ROOT), str(DRIVER_ROOT / "tests")]

from agent_skills.lifecycle import Lifecycle
from agent_skills.release import VerifiedRelease, verify_release


CONTRACT_PATH = "skills/linear-tracker-adapter/references/adapter-contract.json"
STATIC_SENSOR = (
    "test_linear_profile.LinearProfileTests."
    "test_linear_archive_has_no_alternate_transport_implementation"
)
TRUSTED_EVIDENCE_PATHS = (
    "agent_skills/fs.py",
    "agent_skills/generations.py",
    "agent_skills/hosts/base.py",
    "agent_skills/hosts/claude.py",
    "agent_skills/hosts/codex.py",
    "agent_skills/hosts/exposure.py",
    "agent_skills/journal.py",
    "agent_skills/lifecycle.py",
    "agent_skills/locking.py",
    "agent_skills/receipt.py",
    "agent_skills/release.py",
    "agent_skills/validate.py",
    "profiles/core.json",
    "profiles/linear.json",
    "scripts/qualify-linear.py",
    CONTRACT_PATH,
    "tests/test_linear_profile.py",
    "tests/test_linear_qualification.py",
)
REQUIRED_SCENARIOS = (
    "core-qualification-binding",
    "linear-install-and-host-discovery",
    "live-host-native-operations",
    "live-reconcile-before-retry",
    "controlled-degraded-outcomes",
    "forbidden-alternate-transport",
)
LIVE_EVIDENCE_KEYS = {
    "auditReferences",
    "checks",
    "evidenceSource",
    "schemaVersion",
}
LIVE_CHECKS = {
    "cleanupCount": 2,
    "cleanupStatus": "canceled",
    "createCount": 2,
    "duplicateCreateAttempts": 0,
    "initialCreateAttempts": 2,
    "readCount": 2,
    "reconciliationMatchCount": 1,
    "reconciliationSearchCount": 1,
    "relationCount": 1,
    "searchCount": 3,
    "updateCount": 2,
}
CONTROLLED_BOUNDARY_OUTCOMES = {
    "auth": "auth-required",
    "malformed": "malformed",
    "missing": "unavailable",
    "mutation-ambiguous": "reconciliation-required",
    "mutation-timeout": "reconciliation-required",
    "read-ambiguous": "retry",
    "read-timeout": "retry",
    "reconciliation-ambiguous": "reconciliation-required",
    "reconciliation-auth": "reconciliation-required",
    "reconciliation-malformed": "reconciliation-required",
    "reconciliation-miss": "reconciliation-required",
    "reconciliation-timeout": "reconciliation-required",
    "reconciliation-unavailable": "reconciliation-required",
    "unsupported": "unsupported",
}
AUDIT_REFERENCE = re.compile(r"linear-audit:[a-f0-9]{16}")
COMMIT_SHA = re.compile(r"[a-f0-9]{40}")
SHA256 = re.compile(r"[a-f0-9]{64}")


class QualificationFailure(AssertionError):
    pass


class Checkpoint:
    def __init__(self, scenario_id: str, evidence_source: str) -> None:
        self.scenario_id = scenario_id
        self.evidence_source = evidence_source
        self.assertion_count = 0
        self.evidence: list[str] = []

    def check(self, condition: bool, evidence: str, failure: str | None = None) -> None:
        self.assertion_count += 1
        if not condition:
            raise QualificationFailure(failure or evidence)
        self.evidence.append(evidence)


def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _digest_bytes(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _digest(value: object) -> str:
    return _digest_bytes(canonical_json(value).encode("utf-8"))


def qualification_driver_sha256(root: Path = DRIVER_ROOT) -> str:
    inputs = [
        {
            "path": relative,
            "sha256": _digest_bytes((root / relative).read_bytes()),
        }
        for relative in TRUSTED_EVIDENCE_PATHS
    ]
    return _digest(inputs)


def _load_document(path: Path, name: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"linear_qualification.{name}: invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"linear_qualification.{name}: expected an object")
    return value


def validate_live_evidence(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != LIVE_EVIDENCE_KEYS:
        raise ValueError("linear_qualification.live_evidence: unexpected document shape")
    if value["schemaVersion"] != "agent-skills-linear-live-evidence/1":
        raise ValueError("linear_qualification.live_evidence: unsupported schema")
    if value["evidenceSource"] != "live-host-native-linear-mcp":
        raise ValueError("linear_qualification.live_evidence: success path must be live MCP")
    references = value["auditReferences"]
    if (
        not isinstance(references, list)
        or len(references) != 2
        or len(set(references)) != len(references)
        or any(
            not isinstance(reference, str)
            or AUDIT_REFERENCE.fullmatch(reference) is None
            for reference in references
        )
    ):
        raise ValueError("linear_qualification.live_evidence: audit references are invalid")
    checks = value["checks"]
    if not isinstance(checks, dict) or set(checks) != set(LIVE_CHECKS):
        raise ValueError("linear_qualification.live_evidence: check shape is invalid")
    for name, minimum in LIVE_CHECKS.items():
        observed = checks[name]
        if name == "cleanupStatus":
            valid = observed == minimum
        elif name in {
            "cleanupCount",
            "createCount",
            "duplicateCreateAttempts",
            "initialCreateAttempts",
            "reconciliationMatchCount",
        }:
            valid = type(observed) is int and observed == minimum
        else:
            valid = type(observed) is int and observed >= minimum
        if not valid:
            raise ValueError(f"linear_qualification.live_evidence: invalid check {name}")
    return value


def controlled_boundary_matrix(contract: dict) -> list[dict]:
    outcomes = contract.get("failureOutcomes")
    if outcomes != CONTROLLED_BOUNDARY_OUTCOMES:
        raise ValueError("linear_qualification.contract: degraded outcomes changed")
    matrix = []
    for input_name, outcome in sorted(CONTROLLED_BOUNDARY_OUTCOMES.items()):
        mutation_uncertain = input_name.startswith("mutation-")
        action = {
            "auth-required": "restore host authorization before retrying",
            "malformed": "correct the named host contract field before retrying",
            "reconciliation-required": "reconcile the prior mutation and do not repeat it",
            "retry": "retry the read after the declared positive delay",
            "unavailable": "configure the host capability or continue the optional core path",
            "unsupported": "use a supported neutral operation or continue the optional core path",
        }.get(outcome)
        if not isinstance(action, str):
            raise ValueError("linear_qualification.contract: unknown normalized outcome")
        matrix.append(
            {
                "action": action,
                "input": input_name,
                "outcome": outcome,
                "repeatMutation": False if mutation_uncertain else None,
            }
        )
    return matrix


def _record(checkpoint: Checkpoint, result: str, failure: str | None = None) -> dict:
    summary = {
        "assertionCount": checkpoint.assertion_count,
        "evidence": checkpoint.evidence,
        "evidenceSource": checkpoint.evidence_source,
        "id": checkpoint.scenario_id,
        "result": result,
        **({"failure": failure} if failure else {}),
    }
    return {
        **summary,
        "checkpointSha256": _digest(summary),
    }


def _run(scenario_id: str, evidence_source: str, operation) -> dict:
    checkpoint = Checkpoint(scenario_id, evidence_source)
    try:
        operation(checkpoint)
    except QualificationFailure as error:
        return _record(checkpoint, "failed", str(error))
    except Exception as error:
        return _record(checkpoint, "failed", f"unexpected:{type(error).__name__}")
    return _record(checkpoint, "passed")


def _release_view(release: VerifiedRelease) -> dict[str, str]:
    return {
        "archiveSha256": release.archive_sha256,
        "metadataSha256": release.metadata_sha256,
        "profile": release.profile_id,
        "releaseId": release.release_id,
    }


def _core_binding(
    checkpoint: Checkpoint,
    core: VerifiedRelease,
    core_record: dict,
) -> None:
    checkpoint.check(core.profile_id == "core", "exact base profile identity verified")
    checkpoint.check(
        core_record.get("release") == _release_view(core),
        "base qualification names the exact base archive and metadata",
    )
    checkpoint.check(
        core_record.get("summary", {}).get("result") == "passed",
        "base qualification is passing",
    )


def _install_linear(
    checkpoint: Checkpoint,
    linear_archive: Path,
    linear_metadata: Path,
) -> None:
    with tempfile.TemporaryDirectory(prefix="agent-skills-linear-install-") as temporary:
        repository = Path(temporary) / "repository"
        repository.mkdir()
        subprocess.run(
            ["git", "init", "-q"],
            cwd=repository,
            check=True,
            capture_output=True,
        )
        result = Lifecycle(repository).install(
            linear_archive,
            linear_metadata,
            maintainer=True,
        )
        checkpoint.check(
            result["status"]["lifecycle"] == "current",
            "exact optional profile installs as the current generation",
        )
        checkpoint.check(
            result["status"]["active"]["profile"] == "linear",
            "installed generation retains the optional profile identity",
        )
        checkpoint.check(
            {exposure["host"] for exposure in result["status"]["exposures"]}
            == {"codex", "claude-code"}
            and all(
                exposure["state"] == "current"
                for exposure in result["status"]["exposures"]
            ),
            "both fixed hosts expose the current optional generation",
        )
        for host in (".agents", ".claude"):
            checkpoint.check(
                (repository / host / "skills" / "linear-tracker-adapter" / "SKILL.md").is_file(),
                f"{host} discovers the installed optional adapter",
            )


def _live_operations(checkpoint: Checkpoint, evidence: dict) -> None:
    checks = evidence["checks"]
    for name in (
        "createCount",
        "readCount",
        "updateCount",
        "searchCount",
        "relationCount",
    ):
        checkpoint.check(checks[name] >= 1, f"live host operation passed: {name}")
    checkpoint.check(
        checks["cleanupCount"] == checks["createCount"]
        and checks["cleanupStatus"] == "canceled",
        "every disposable live entity was canceled after evidence capture",
    )


def _live_reconciliation(checkpoint: Checkpoint, evidence: dict) -> None:
    checks = evidence["checks"]
    checkpoint.check(
        checks["reconciliationSearchCount"] >= 1,
        "stable-marker reconciliation ran before any repeat mutation",
    )
    checkpoint.check(
        checks["reconciliationMatchCount"] == 1,
        "stable-marker reconciliation found exactly one intended entity",
    )
    checkpoint.check(
        checks["duplicateCreateAttempts"] == 0,
        "reconciliation suppressed the duplicate create attempt",
    )


def _controlled_outcomes(checkpoint: Checkpoint, contract: dict) -> None:
    matrix = controlled_boundary_matrix(contract)
    expected = contract["failureOutcomes"]
    checkpoint.check(
        len(matrix) == len(expected),
        "controlled boundary matrix covers every declared degraded input",
    )
    for case in matrix:
        checkpoint.check(
            case["outcome"] == expected[case["input"]] and bool(case["action"]),
            f"controlled boundary normalized: {case['input']} -> {case['outcome']}",
        )
    checkpoint.check(
        all(
            case["repeatMutation"] is False
            for case in matrix
            if case["input"].startswith("mutation-")
        ),
        "controlled ambiguous mutations never repeat before reconciliation",
    )


def _static_transport_sensor(checkpoint: Checkpoint) -> None:
    suite = unittest.defaultTestLoader.loadTestsFromName(STATIC_SENSOR)
    result = unittest.TestResult()
    suite.run(result)
    checkpoint.check(
        result.testsRun == 1 and result.wasSuccessful(),
        "trusted static sensor rejects alternate provider transports",
        "trusted static forbidden-transport sensor failed",
    )


def _finalize_record(
    candidate: dict,
    core_record_sha256: str,
    live_evidence_sha256: str,
    audit_references: list[str],
    scenarios: list[dict],
) -> dict:
    ids = tuple(scenario.get("id") for scenario in scenarios)
    if set(ids) != set(REQUIRED_SCENARIOS) or len(ids) != len(REQUIRED_SCENARIOS):
        raise ValueError("linear_qualification.anti_vacuity: scenarios are missing or duplicated")
    if any(
        scenario.get("result") == "passed"
        and (
            type(scenario.get("assertionCount")) is not int
            or scenario["assertionCount"] < 1
            or not scenario.get("evidence")
            or not scenario.get("evidenceSource")
        )
        for scenario in scenarios
    ):
        raise ValueError("linear_qualification.anti_vacuity: passing evidence is empty")
    ordered = sorted(scenarios, key=lambda item: REQUIRED_SCENARIOS.index(item["id"]))
    failed = sum(scenario["result"] != "passed" for scenario in ordered)
    return {
        "auditReferences": audit_references,
        "candidate": candidate,
        "coreQualificationSha256": core_record_sha256,
        "liveEvidenceSha256": live_evidence_sha256,
        "qualificationDriverSha256": qualification_driver_sha256(),
        "redaction": {
            "environmentCaptured": False,
            "localPathsCaptured": False,
            "hostPayloadsCaptured": False,
            "retainedEvidence": "opaque audit references and named deterministic assertions",
        },
        "scenarios": ordered,
        "schemaVersion": "agent-skills-linear-qualification/1",
        "summary": {
            "assertionCount": sum(scenario["assertionCount"] for scenario in ordered),
            "failedScenarioCount": failed,
            "passedScenarioCount": len(ordered) - failed,
            "requiredScenarioCount": len(REQUIRED_SCENARIOS),
            "result": "failed" if failed else "passed",
        },
    }


def qualify(
    core_archive: Path,
    core_metadata: Path,
    core_record_path: Path,
    linear_archive: Path,
    linear_metadata: Path,
    live_evidence_path: Path,
    source_commit: str,
) -> dict:
    if COMMIT_SHA.fullmatch(source_commit) is None:
        raise ValueError("linear_qualification.source_commit: expected a full commit SHA")
    core = verify_release(core_archive, core_metadata)
    linear = verify_release(linear_archive, linear_metadata)
    if core.profile_id != "core" or linear.profile_id != "linear":
        raise ValueError("linear_qualification.profile: exact core and linear profiles are required")
    core_record = _load_document(core_record_path, "core_record")
    live_evidence_bytes = live_evidence_path.read_bytes()
    live_evidence = validate_live_evidence(
        _load_document(live_evidence_path, "live_evidence")
    )
    contract = _load_document(DRIVER_ROOT / CONTRACT_PATH, "contract")
    with tempfile.TemporaryDirectory(prefix="agent-skills-linear-extract-") as temporary:
        verify_release(
            linear_archive,
            linear_metadata,
            extract_to=Path(temporary) / "candidate",
        )
    scenarios = [
        _run(
            "core-qualification-binding",
            "trusted-checked-qualification",
            lambda checkpoint: _core_binding(checkpoint, core, core_record),
        ),
        _run(
            "linear-install-and-host-discovery",
            "exact-release-runtime",
            lambda checkpoint: _install_linear(
                checkpoint,
                linear_archive,
                linear_metadata,
            ),
        ),
        _run(
            "live-host-native-operations",
            live_evidence["evidenceSource"],
            lambda checkpoint: _live_operations(checkpoint, live_evidence),
        ),
        _run(
            "live-reconcile-before-retry",
            live_evidence["evidenceSource"],
            lambda checkpoint: _live_reconciliation(checkpoint, live_evidence),
        ),
        _run(
            "controlled-degraded-outcomes",
            "deterministic-controlled-boundary-double",
            lambda checkpoint: _controlled_outcomes(checkpoint, contract),
        ),
        _run(
            "forbidden-alternate-transport",
            "trusted-static-sensor",
            _static_transport_sensor,
        ),
    ]
    return _finalize_record(
        {
            "core": _release_view(core),
            "linear": _release_view(linear),
            "sourceCommitSha": source_commit,
        },
        _digest_bytes(core_record_path.read_bytes()),
        _digest_bytes(live_evidence_bytes),
        live_evidence["auditReferences"],
        scenarios,
    )


def validate_checked_record(
    record: dict,
    core_archive: Path,
    core_metadata: Path,
    core_record_path: Path,
    linear_archive: Path,
    linear_metadata: Path,
    source_commit: str,
) -> None:
    core = verify_release(core_archive, core_metadata)
    linear = verify_release(linear_archive, linear_metadata)
    expected_candidate = {
        "core": _release_view(core),
        "linear": _release_view(linear),
        "sourceCommitSha": source_commit,
    }
    if record.get("schemaVersion") != "agent-skills-linear-qualification/1":
        raise ValueError("linear_qualification.record: unsupported schema")
    if record.get("candidate") != expected_candidate:
        raise ValueError("linear_qualification.record: candidate identity mismatch")
    if record.get("coreQualificationSha256") != _digest_bytes(core_record_path.read_bytes()):
        raise ValueError("linear_qualification.record: base qualification mismatch")
    if record.get("qualificationDriverSha256") != qualification_driver_sha256():
        raise ValueError("linear_qualification.record: trusted driver mismatch")
    if record.get("summary", {}).get("result") != "passed":
        raise ValueError("linear_qualification.record: qualification is not passing")
    if SHA256.fullmatch(str(record.get("liveEvidenceSha256"))) is None:
        raise ValueError("linear_qualification.record: live evidence digest is invalid")
    if record.get("redaction") != {
        "environmentCaptured": False,
        "hostPayloadsCaptured": False,
        "localPathsCaptured": False,
        "retainedEvidence": "opaque audit references and named deterministic assertions",
    }:
        raise ValueError("linear_qualification.record: redaction policy is invalid")
    scenarios = record.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("linear_qualification.record: scenarios are missing")
    ids = tuple(scenario.get("id") for scenario in scenarios if isinstance(scenario, dict))
    if ids != REQUIRED_SCENARIOS or any(
        scenario.get("result") != "passed" for scenario in scenarios
    ):
        raise ValueError("linear_qualification.record: required scenarios are not passing")
    expected_sources = {
        "core-qualification-binding": "trusted-checked-qualification",
        "linear-install-and-host-discovery": "exact-release-runtime",
        "live-host-native-operations": "live-host-native-linear-mcp",
        "live-reconcile-before-retry": "live-host-native-linear-mcp",
        "controlled-degraded-outcomes": "deterministic-controlled-boundary-double",
        "forbidden-alternate-transport": "trusted-static-sensor",
    }
    for scenario in scenarios:
        if set(scenario) != {
            "assertionCount",
            "checkpointSha256",
            "evidence",
            "evidenceSource",
            "id",
            "result",
        }:
            raise ValueError("linear_qualification.record: scenario shape is invalid")
        summary = {
            field: scenario[field]
            for field in (
                "assertionCount",
                "evidence",
                "evidenceSource",
                "id",
                "result",
            )
        }
        if (
            type(scenario["assertionCount"]) is not int
            or scenario["assertionCount"] != len(scenario["evidence"])
            or scenario["evidenceSource"] != expected_sources[scenario["id"]]
            or scenario["checkpointSha256"] != _digest(summary)
        ):
            raise ValueError("linear_qualification.record: scenario evidence is invalid")
    expected_summary = {
        "assertionCount": sum(scenario["assertionCount"] for scenario in scenarios),
        "failedScenarioCount": 0,
        "passedScenarioCount": len(REQUIRED_SCENARIOS),
        "requiredScenarioCount": len(REQUIRED_SCENARIOS),
        "result": "passed",
    }
    if record.get("summary") != expected_summary:
        raise ValueError("linear_qualification.record: summary is invalid")
    references = record.get("auditReferences")
    if (
        not isinstance(references, list)
        or len(references) != 2
        or any(
            not isinstance(reference, str)
            or AUDIT_REFERENCE.fullmatch(reference) is None
            for reference in references
        )
    ):
        raise ValueError("linear_qualification.record: audit references are invalid")


def _atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
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
    parser.add_argument("--core-archive", type=Path, required=True)
    parser.add_argument("--core-metadata", type=Path, required=True)
    parser.add_argument("--core-record", type=Path, required=True)
    parser.add_argument("--linear-archive", type=Path, required=True)
    parser.add_argument("--linear-metadata", type=Path, required=True)
    parser.add_argument("--live-evidence", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        record = qualify(
            args.core_archive,
            args.core_metadata,
            args.core_record,
            args.linear_archive,
            args.linear_metadata,
            args.live_evidence,
            args.source_commit,
        )
    except Exception as error:
        print(f"qualification failed: {type(error).__name__}", file=sys.stderr)
        return 1
    _atomic_write(args.record, canonical_json(record))
    print(canonical_json(record), end="")
    return 0 if record["summary"]["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
