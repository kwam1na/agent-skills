#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


DRIVER_ROOT = Path(__file__).resolve().parents[1]


def _candidate_root(arguments: list[str]) -> Path | None:
    try:
        index = arguments.index("--candidate-root")
        return Path(arguments[index + 1]).resolve()
    except (ValueError, IndexError):
        return None


CANDIDATE_ROOT = _candidate_root(sys.argv)
if CANDIDATE_ROOT is None:
    sys.path.insert(0, str(DRIVER_ROOT))
else:
    sys.path[:0] = [
        str(CANDIDATE_ROOT),
        str(CANDIDATE_ROOT / "tests"),
        str(DRIVER_ROOT / "tests"),
    ]

from agent_skills.release import verify_release

if CANDIDATE_ROOT is not None:
    from agent_skills.cli import main as lifecycle_cli
    from agent_skills.lifecycle import Lifecycle
    from agent_skills.release import ReleaseError, build_release


SCENARIO_SENSORS = {
    "delivery-provider-rails-conformance": (
        "test_delivery_provider_rails_contract.DeliveryProviderRailsContractTests.test_schema_is_one_closed_versioned_message_union",
        "test_delivery_provider_rails_contract.DeliveryProviderRailsContractTests.test_one_fake_provider_independently_conforms_to_shared_cases",
        "test_delivery_provider_rails_contract.DeliveryProviderRailsContractTests.test_one_fake_consumer_independently_conforms_to_shared_cases",
        "test_delivery_provider_rails_contract.DeliveryProviderRailsContractTests.test_request_identity_axes_are_one_to_one_and_cancel_targets_an_attempt",
        "test_delivery_provider_rails_contract.DeliveryProviderRailsContractTests.test_accepted_cancellation_is_correlated_to_the_same_outbound_attempt",
        "test_delivery_provider_rails_contract.DeliveryProviderRailsContractTests.test_consumer_never_combines_events_from_different_attempts",
        "test_delivery_provider_rails_contract.DeliveryProviderRailsContractTests.test_invalid_renegotiation_fails_closed",
        "test_delivery_provider_rails_contract.DeliveryProviderRailsContractTests.test_absorbing_terminal_precedes_late_message_validation",
        "test_delivery_provider_rails_contract.DeliveryProviderRailsContractTests.test_schema_and_fakes_agree_on_named_malformed_messages",
    ),
    "trackerless-workflows": (
        "test_router.RouterTests.test_pinned_intents_route_deterministically",
        "test_workflows.PlanningWorkflowTests.test_plan_only_returns_a_plan_without_tracker_mutation",
        "test_workflows.ExecutionWorkflowTests.test_completion_requires_finish_line_sensors_and_approvals",
        "test_review_and_compounding.ReviewWorkflowTests.test_unanimous_alignment_is_explicit_and_preserves_lens_evidence",
        "test_review_and_compounding.CompoundingWorkflowTests.test_local_obvious_change_does_not_require_durable_knowledge",
    ),
    "fake-adapter-workflows": (
        "test_workflows.PlanningWorkflowTests.test_configured_planning_uses_only_normalized_tracker_operations",
        "test_workflows.ExecutionWorkflowTests.test_configured_execution_records_normalized_progress_and_evidence",
        "test_tracker_contract.TrackerContractTests.test_fake_adapter_executes_every_operation",
    ),
    "missing-capability": (
        "test_router.RouterTests.test_required_blocked_capability_returns_named_handoff",
        "test_workflows.PlanningWorkflowTests.test_plan_without_tracker_completes_and_reports_selected_tracking_handoff",
        "test_tracker_contract.TrackerContractTests.test_missing_capability_is_typed_and_actionable",
    ),
    "bounded-review-failure": (
        "test_review_and_compounding.ReviewWorkflowTests.test_unresolved_findings_at_loop_bound_block_honestly",
        "test_review_and_compounding.ReviewWorkflowTests.test_reviewer_failure_is_a_typed_blocked_outcome",
    ),
}
TRUSTED_EVIDENCE_PATHS = (
    "agent_skills/__init__.py",
    "agent_skills/catalog.py",
    "agent_skills/errors.py",
    "agent_skills/release.py",
    "agent_skills/validate.py",
    "scripts/qualify-core.py",
    "tests/scenarios/core/compound.json",
    "tests/scenarios/core/execute.json",
    "tests/scenarios/core/plan.json",
    "tests/scenarios/core/review.json",
    "tests/scenarios/core/routing.json",
    "tests/scenarios/core/tracker-capability.json",
    "tests/test_delivery_provider_rails_contract.py",
    "tests/test_review_and_compounding.py",
    "tests/test_router.py",
    "tests/test_tracker_contract.py",
    "tests/test_workflows.py",
)
REQUIRED_SCENARIOS = (
    "fresh-install-and-host-discovery",
    "delivery-provider-rails-conformance",
    "trackerless-workflows",
    "fake-adapter-workflows",
    "tamper-rejection",
    "rollback",
    "missing-capability",
    "bounded-review-failure",
)
DELIVERY_RAILS_SENSOR = "tests/test_delivery_provider_rails_contract.py"


class QualificationFailure(AssertionError):
    pass


class Checkpoint:
    def __init__(self, scenario_id: str) -> None:
        self.scenario_id = scenario_id
        self.assertion_count = 0
        self.evidence: list[str] = []

    def check(self, condition: bool, evidence: str, failure: str | None = None) -> None:
        self.assertion_count += 1
        if not condition:
            raise QualificationFailure(failure or evidence)
        self.evidence.append(evidence)


def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def qualification_driver_sha256(root: Path = DRIVER_ROOT) -> str:
    records = [
        {
            "path": relative,
            "sha256": hashlib.sha256((root / relative).read_bytes()).hexdigest(),
        }
        for relative in TRUSTED_EVIDENCE_PATHS
    ]
    return _sha256(records)


def _record(checkpoint: Checkpoint, result: str, failure: str | None = None) -> dict:
    summary = {
        "assertionCount": checkpoint.assertion_count,
        "evidence": checkpoint.evidence,
        "id": checkpoint.scenario_id,
        "result": result,
        **({"failure": failure} if failure else {}),
    }
    return {
        "assertionCount": checkpoint.assertion_count,
        "checkpointSha256": _sha256(summary),
        "evidence": checkpoint.evidence,
        **({"failure": failure} if failure else {}),
        "id": checkpoint.scenario_id,
        "result": result,
    }


def _run(scenario_id: str, operation) -> dict:
    checkpoint = Checkpoint(scenario_id)
    try:
        operation(checkpoint)
    except QualificationFailure as error:
        return _record(checkpoint, "failed", str(error))
    except Exception as error:
        return _record(checkpoint, "failed", f"unexpected:{type(error).__name__}")
    return _record(checkpoint, "passed")


def _run_sensor(checkpoint: Checkpoint, sensor: str) -> None:
    suite = unittest.defaultTestLoader.loadTestsFromName(sensor)
    result = unittest.TestResult()
    suite.run(result)
    checkpoint.check(
        result.testsRun == 1 and result.wasSuccessful(),
        f"sensor passed: {sensor}",
        f"sensor failed: {sensor}",
    )


def _run_sensors(checkpoint: Checkpoint, scenario_id: str, *_arguments) -> None:
    for sensor in SCENARIO_SENSORS[scenario_id]:
        _run_sensor(checkpoint, sensor)


def _run_delivery_rails_sensors(
    checkpoint: Checkpoint,
    candidate_root: Path,
) -> None:
    checkpoint.check(
        (candidate_root / DELIVERY_RAILS_SENSOR).read_bytes()
        == (DRIVER_ROOT / DELIVERY_RAILS_SENSOR).read_bytes(),
        "packaged conformance sensor matches the trusted sensor",
    )
    _run_sensors(checkpoint, "delivery-provider-rails-conformance")


def _fresh_install(
    checkpoint: Checkpoint,
    base: Path,
    archive: Path,
    metadata: Path,
) -> None:
    repository = base / "repository"
    repository.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    output = StringIO()
    with redirect_stdout(output):
        return_code = lifecycle_cli(
            [
                "--root",
                str(repository),
                "install",
                "--archive",
                str(archive),
                "--metadata",
                str(metadata),
                "--maintenance",
            ]
        )
    checkpoint.check(return_code == 0, "exact archive installs through the CLI")
    status = json.loads(output.getvalue())["status"]
    checkpoint.check(status["lifecycle"] == "current", "installed lifecycle is current")
    checkpoint.check(
        {item["host"] for item in status["exposures"]} == {"codex", "claude-code"},
        "both fixed hosts are reported",
    )
    checkpoint.check(
        all(item["state"] == "current" for item in status["exposures"]),
        "both fixed hosts discover the active generation",
    )
    for host in (".agents", ".claude"):
        checkpoint.check(
            (repository / host / "skills" / "deliver-work" / "SKILL.md").is_file(),
            f"{host} discovers the delivery entry point",
        )


def _tamper(
    checkpoint: Checkpoint,
    base: Path,
    archive: Path,
    metadata: Path,
) -> None:
    tampered = base / "tampered.zip"
    shutil.copyfile(archive, tampered)
    with tampered.open("ab") as target:
        target.write(b"tampered")
    try:
        verify_release(tampered, metadata)
    except ReleaseError as error:
        checkpoint.check(
            str(error).startswith("release.archive_checksum:"),
            "tampered bytes fail the detached archive checksum",
        )
    else:
        checkpoint.check(False, "tampered bytes fail the detached archive checksum")


def _rollback(
    checkpoint: Checkpoint,
    base: Path,
    archive: Path,
    metadata: Path,
) -> None:
    if CANDIDATE_ROOT is None:
        raise QualificationFailure("candidate root is required")
    prior_archive = base / "prior.zip"
    prior_metadata = base / "prior.release.json"
    build_release(
        CANDIDATE_ROOT,
        prior_archive,
        prior_metadata,
        release_id="qualification-prior-fixture",
        profile_id="core",
    )
    repository = base / "repository"
    repository.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    lifecycle = Lifecycle(repository)
    lifecycle.install(prior_archive, prior_metadata, maintainer=True)
    lifecycle.update(archive, metadata, maintainer=True)
    candidate = lifecycle.status().as_dict()
    checkpoint.check(
        candidate["active"]["archiveSha256"]
        == verify_release(archive, metadata).archive_sha256,
        "exact candidate archive becomes active before rollback",
    )
    result = lifecycle.rollback(maintainer=True)
    checkpoint.check(result["action"] == "rollback", "rollback uses the retained generation")
    checkpoint.check(
        result["status"]["active"]["releaseId"] == "qualification-prior-fixture",
        "rollback restores the derived prior fixture",
    )
    checkpoint.check(
        all(item["state"] == "current" for item in result["status"]["exposures"]),
        "both host projections follow the rolled-back generation",
    )


def finalize_record(release: dict[str, str], scenarios: list[dict]) -> dict:
    ids = tuple(item.get("id") for item in scenarios)
    if set(ids) != set(REQUIRED_SCENARIOS) or len(ids) != len(REQUIRED_SCENARIOS):
        raise ValueError("qualification.anti_vacuity: required scenarios are missing or duplicated")
    if any(
        item.get("result") == "passed"
        and (
            not isinstance(item.get("assertionCount"), int)
            or item["assertionCount"] < 1
            or not item.get("evidence")
        )
        for item in scenarios
    ):
        raise ValueError("qualification.anti_vacuity: every passing scenario requires asserted evidence")
    if any(item.get("result") == "failed" and not item.get("failure") for item in scenarios):
        raise ValueError("qualification.anti_vacuity: every failed scenario requires a failure summary")
    ordered = sorted(scenarios, key=lambda item: REQUIRED_SCENARIOS.index(item["id"]))
    failed = sum(item["result"] != "passed" for item in ordered)
    return {
        "qualificationDriverSha256": qualification_driver_sha256(),
        "redaction": {
            "environmentCaptured": False,
            "pathsCaptured": False,
            "retainedEvidence": "named assertions and deterministic checkpoint summaries",
        },
        "release": release,
        "replay": "python3 scripts/qualify-core.py --archive <archive> --metadata <metadata> --record <record>",
        "scenarios": ordered,
        "schemaVersion": "agent-skills-qualification/1",
        "summary": {
            "assertionCount": sum(item["assertionCount"] for item in ordered),
            "failedScenarioCount": failed,
            "passedScenarioCount": len(ordered) - failed,
            "requiredScenarioCount": len(REQUIRED_SCENARIOS),
            "result": "failed" if failed else "passed",
        },
    }


def _candidate_scenario(
    scenario_id: str,
    candidate_root: Path,
    base: Path,
    archive: Path,
    metadata: Path,
) -> dict:
    scenario_root = base / scenario_id
    scenario_root.mkdir()
    environment = dict(os.environ)
    environment["AGENT_SKILLS_EXECUTION_CONTEXT"] = ""
    environment["GITHUB_EVENT_NAME"] = ""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).resolve()),
            "--candidate-root",
            str(candidate_root),
            "--scenario",
            scenario_id,
            "--work-root",
            str(scenario_root),
            "--archive",
            str(archive),
            "--metadata",
            str(metadata),
        ],
        cwd=scenario_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        record = json.loads(result.stdout)
    except json.JSONDecodeError:
        checkpoint = Checkpoint(scenario_id)
        return _record(
            checkpoint,
            "failed",
            f"candidate-process:{result.returncode}",
        )
    if not isinstance(record, dict) or record.get("id") != scenario_id:
        checkpoint = Checkpoint(scenario_id)
        return _record(checkpoint, "failed", "candidate-process:invalid-record")
    return record


def qualify(archive: Path, metadata: Path) -> dict:
    archive = archive.resolve()
    metadata = metadata.resolve()
    verified = verify_release(archive, metadata)
    release = {
        "archiveSha256": verified.archive_sha256,
        "metadataSha256": verified.metadata_sha256,
        "profile": verified.profile_id,
        "releaseId": verified.release_id,
    }
    with tempfile.TemporaryDirectory(prefix="agent-skills-qualification-") as temporary:
        base = Path(temporary)
        candidate_root = base / "candidate"
        verify_release(archive, metadata, extract_to=candidate_root)
        scenarios = [
            _candidate_scenario(
                scenario_id,
                candidate_root,
                base,
                archive,
                metadata,
            )
            for scenario_id in REQUIRED_SCENARIOS
        ]
    return finalize_record(release, scenarios)


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


def _internal_main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--scenario", choices=REQUIRED_SCENARIOS, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args(arguments)
    operation = (
        (
            lambda checkpoint: _run_delivery_rails_sensors(
                checkpoint,
                args.candidate_root,
            )
        )
        if args.scenario == "delivery-provider-rails-conformance"
        else (lambda checkpoint: _run_sensors(checkpoint, args.scenario))
        if args.scenario in SCENARIO_SENSORS
        else (
            lambda checkpoint: {
                "fresh-install-and-host-discovery": _fresh_install,
                "tamper-rejection": _tamper,
                "rollback": _rollback,
            }[args.scenario](
                checkpoint,
                args.work_root,
                args.archive,
                args.metadata,
            )
        )
    )
    record = _run(args.scenario, operation)
    print(canonical_json(record), end="")
    return 0 if record["result"] == "passed" else 1


def main(arguments: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if "--candidate-root" in arguments:
        return _internal_main(arguments)
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        args.record.unlink(missing_ok=True)
        record = qualify(args.archive, args.metadata)
        _atomic_write(args.record, canonical_json(record))
    except (OSError, ValueError) as error:
        print(canonical_json({"error": type(error).__name__, "ok": False}), end="")
        return 1
    print(canonical_json(record), end="")
    return 0 if record["summary"]["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
