from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_skills.release import build_release


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qualify-core.py"
WORKFLOW_SCENARIOS = {
    "workflow-graph-conformance",
    "diagnosis-causal-evidence",
    "review-acquisition-exact-rounds",
    "authority-negative-handoffs",
}


def _load_runner():
    spec = importlib.util.spec_from_file_location("qualify_core", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QualificationTests(unittest.TestCase):
    def test_checked_product_core_replays_exactly(self) -> None:
        product = ROOT / "qualifications/product"
        current = "core-adoption-v1"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            output = directory / "core.json"
            result = self._run_qualification(product / f"{current}.zip",
                                             product / f"{current}.release.json", output, directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(output.read_bytes(), (product / f"{current}.json").read_bytes())

    def test_record_output_cannot_alias_candidate_archive_or_metadata(self) -> None:
        for target in ("archive", "metadata", "symlink"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                archive = directory / "candidate.zip"
                metadata = directory / "candidate.release.json"
                archive.write_bytes(b"candidate archive sentinel")
                metadata.write_bytes(b"candidate metadata sentinel")
                record = archive if target == "archive" else metadata
                if target == "symlink":
                    record = directory / "aliased-record.json"
                    record.symlink_to(archive)
                result = self._run_qualification(archive, metadata, record, directory)
                self.assertEqual(result.returncode, 1)
                self.assertTrue(archive.exists(), "archive was deleted")
                self.assertTrue(metadata.exists(), "metadata was deleted")
                self.assertEqual(archive.read_bytes(), b"candidate archive sentinel")
                self.assertEqual(metadata.read_bytes(), b"candidate metadata sentinel")
                if target == "symlink":
                    self.assertTrue(record.is_symlink())

    def test_workflow_qualification_requires_each_new_behavior_boundary(self) -> None:
        runner = _load_runner()
        self.assertTrue(WORKFLOW_SCENARIOS <= set(runner.REQUIRED_SCENARIOS))
        for scenario in WORKFLOW_SCENARIOS:
            self.assertTrue(runner.SCENARIO_SENSORS[scenario])
            checkpoint = runner.Checkpoint(scenario)
            with mock.patch.object(runner, "_run_sensor") as sensor:
                runner._run_sensors(checkpoint, scenario)
            self.assertEqual(sensor.call_count, len(runner.SCENARIO_SENSORS[scenario]))

    def test_trusted_workflow_closure_includes_independent_consumer_and_inputs(self) -> None:
        runner = _load_runner()
        required = {
            "tests/test_workflow_graph_contract.py", "tests/test_diagnosis.py",
            "tests/test_review_orchestration.py", "tests/consumers/workflow_consumer.py",
            "tests/fixtures/workflow-graph/result-templates.json",
            "tests/scenarios/core/diagnose.json", "tests/scenarios/core/obtain-review.json",
            "schemas/workflow-graph.schema.json", "schemas/workflow-stage-result.schema.json",
            "workflows/delivery-v1.json",
        }
        self.assertTrue(required <= set(runner.TRUSTED_EVIDENCE_PATHS))
        self.assertFalse(any(path.startswith("qualifications/") for path in runner.TRUSTED_EVIDENCE_PATHS))
        for scenario in WORKFLOW_SCENARIOS:
            self.assertTrue(runner.PACKAGED_EVIDENCE_PATHS[scenario])
            self.assertTrue(set(runner.PACKAGED_EVIDENCE_PATHS[scenario]) <= set(runner.TRUSTED_EVIDENCE_PATHS))

    def test_skipped_and_expected_failure_sensors_cannot_qualify_a_scenario(self) -> None:
        runner = _load_runner()

        class VacuousSensor(unittest.TestCase):
            @unittest.skip("not exercised")
            def test_skipped(self):
                self.fail("never run")

            @unittest.expectedFailure
            def test_expected_failure(self):
                self.fail("still broken")

        for method in ("test_skipped", "test_expected_failure"):
            with self.subTest(method=method), mock.patch.object(
                unittest.defaultTestLoader, "loadTestsFromName",
                return_value=unittest.TestSuite([VacuousSensor(method)]),
            ):
                record = runner._run("workflow-graph-conformance", lambda checkpoint: runner._run_sensor(checkpoint, method))
                self.assertEqual(record["result"], "failed")

    def test_record_requires_current_graph_binding_and_exact_core_release_identity(self) -> None:
        runner = _load_runner()
        release = {"releaseId": "core-workflows-v2", "profile": "core",
                   "archiveSha256": "a" * 64, "metadataSha256": "b" * 64}
        scenarios = [runner._run(name, lambda checkpoint: checkpoint.check(True, "asserted"))
                     for name in runner.REQUIRED_SCENARIOS]
        for digest in (None, "", "a" * 63, "a" * 64 + "\n"):
            with self.subTest(digest=digest), self.assertRaisesRegex(ValueError, "qualification.graph_binding"):
                runner.finalize_record(release, scenarios, workflow_graph_sha256=digest)
        for changes in ({"profile": "linear"}, {"metadataSha256": "old"}, {"sourceRevision": "unbound"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "qualification.release_binding"):
                runner.finalize_record({**release, **changes}, scenarios, workflow_graph_sha256="c" * 64)
        record = runner.finalize_record(release, scenarios, workflow_graph_sha256="c" * 64)
        self.assertEqual(record["workflowGraphSha256"], "c" * 64)

    def test_old_scenario_set_and_duplicate_new_scenario_cannot_qualify(self) -> None:
        runner = _load_runner()
        scenarios = [runner._run(name, lambda checkpoint: checkpoint.check(True, "asserted"))
                     for name in runner.REQUIRED_SCENARIOS]
        old = [item for item in scenarios if item["id"] not in WORKFLOW_SCENARIOS]
        for records in (old, scenarios + [scenarios[-1]]):
            with self.subTest(ids=[item["id"] for item in records]), self.assertRaisesRegex(ValueError, "qualification.anti_vacuity"):
                runner.finalize_record({}, records, workflow_graph_sha256="c" * 64)

    def test_empty_behavioral_sensor_set_cannot_pass_on_identity_checks_alone(self) -> None:
        runner = _load_runner()
        scenario = "workflow-graph-conformance"
        with mock.patch.dict(runner.SCENARIO_SENSORS, {scenario: ()}):
            record = runner._run(scenario, lambda checkpoint: runner._run_sensors(checkpoint, scenario))
        self.assertEqual(record["result"], "failed")
        self.assertEqual(record["failure"], "required scenario has no behavioral sensors")

    def test_every_packaged_workflow_sensor_consumer_and_fixture_is_trusted(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary)
            paths = {path for scenario in WORKFLOW_SCENARIOS for path in runner.PACKAGED_EVIDENCE_PATHS[scenario]}
            for relative in paths:
                target = candidate / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            for scenario in sorted(WORKFLOW_SCENARIOS):
                for relative in runner.PACKAGED_EVIDENCE_PATHS[scenario]:
                    with self.subTest(scenario=scenario, path=relative):
                        path = candidate / relative
                        original = path.read_bytes()
                        path.write_bytes(original + b"\n")
                        record = runner._run(scenario, lambda checkpoint: runner._run_packaged_sensors(checkpoint, candidate, scenario))
                        path.write_bytes(original)
                        self.assertEqual(record["result"], "failed")
                        self.assertEqual(record["failure"], f"packaged evidence matches trusted bytes: {relative}")

    def test_packaged_sensors_reject_checkout_import_fallback(self) -> None:
        runner = _load_runner()
        scenario = "workflow-graph-conformance"
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary)
            for relative in runner.PACKAGED_EVIDENCE_PATHS[scenario]:
                target = candidate / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            checkout_module = mock.Mock(__file__=str(ROOT / "tests/test_workflow_graph_contract.py"), ROOT=ROOT)
            with mock.patch.object(runner.importlib, "import_module", return_value=checkout_module), \
                 mock.patch.object(runner, "_run_sensors") as sensors:
                record = runner._run(scenario, lambda checkpoint: runner._run_packaged_sensors(checkpoint, candidate, scenario))
            self.assertEqual(record["result"], "failed")
            self.assertIn("extracted candidate", record["failure"])
            sensors.assert_not_called()

    def test_each_new_trusted_input_changes_driver_identity_without_record_recursion(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary)
            for relative in runner.TRUSTED_EVIDENCE_PATHS:
                target = copied / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            before = runner.qualification_driver_sha256(copied)
            paths = {path for scenario in WORKFLOW_SCENARIOS for path in runner.PACKAGED_EVIDENCE_PATHS[scenario]}
            paths.update({"schemas/workflow-graph.schema.json", "schemas/workflow-stage-result.schema.json", "workflows/delivery-v1.json"})
            for relative in sorted(paths):
                with self.subTest(path=relative):
                    path = copied / relative
                    original = path.read_bytes()
                    path.write_bytes(original + b"\n")
                    self.assertNotEqual(runner.qualification_driver_sha256(copied), before)
                    path.write_bytes(original)
            records = copied / "qualifications"
            records.mkdir()
            (records / "core-workflows-v2.json").write_text("untrusted record", encoding="utf-8")
            self.assertEqual(runner.qualification_driver_sha256(copied), before)

    def test_new_runtime_and_public_schema_tampering_fails_exact_candidate_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            candidate = directory / "mutated-workflow-candidate"
            shutil.copytree(ROOT, candidate, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            schema_path = candidate / "schemas/workflow-stage-result.schema.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            schema["additionalProperties"] = True
            schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
            changes = {
                "agent_skills/workflow_graph.py": (
                    'if "trust" in output and output["trust"] != "untrusted":',
                    'if False:  # planted feedback trust bypass',
                ),
                "agent_skills/diagnosis.py": (
                    'result.update(status="succeeded", output={"kind": "confirmed", "evidenceRef": evidence_ref})',
                    'result.update(status="succeeded", output={"kind": "confirmed", "evidenceRef": evidence_ref}, fixAuthorized=True)',
                ),
                "agent_skills/review_orchestration.py": (
                    'all(value.get(key) == context[key] for key in BINDINGS)',
                    'all(True for key in BINDINGS)',
                ),
            }
            for relative, (before, after) in changes.items():
                path = candidate / relative
                source = path.read_text(encoding="utf-8")
                self.assertEqual(source.count(before), 1, relative)
                path.write_text(source.replace(before, after), encoding="utf-8")
            archive = directory / "mutated.zip"
            metadata = directory / "mutated.release.json"
            verified = build_release(candidate, archive, metadata, release_id="core-tamper-vector", profile_id="core")
            record_path = directory / "mutated.qualification.json"
            result = self._run_qualification(archive, metadata, record_path, directory)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["release"]["archiveSha256"], verified.archive_sha256)
            scenarios = {item["id"]: item for item in record["scenarios"]}
            for name in WORKFLOW_SCENARIOS:
                with self.subTest(scenario=name):
                    self.assertEqual(scenarios[name]["result"], "failed")
                    self.assertTrue(scenarios[name]["failure"].startswith("sensor failed:"), scenarios[name])

    def _run_qualification(
        self,
        archive: Path,
        metadata: Path,
        record: Path,
        cwd: Path,
        script: Path = SCRIPT,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(script),
                "--archive",
                str(archive),
                "--metadata",
                str(metadata),
                "--record",
                str(record),
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_complete_archive_qualification_is_reproducible_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first_archive = directory / "first.zip"
            first_metadata = directory / "first.release.json"
            second_archive = directory / "second.zip"
            second_metadata = directory / "second.release.json"
            release_id = "core-workflows-adoption-v1"
            first = build_release(
                ROOT,
                first_archive,
                first_metadata,
                release_id=release_id,
                profile_id="core",
            )
            second = build_release(
                ROOT,
                second_archive,
                second_metadata,
                release_id=release_id,
                profile_id="core",
            )
            self.assertEqual(first, second)
            self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
            self.assertEqual(first_metadata.read_bytes(), second_metadata.read_bytes())

            record_path = directory / "qualification.json"
            secret = "qualification-secret-must-not-leak"
            environment = {**os.environ, "QUALIFICATION_SECRET_SENTINEL": secret}
            with mock.patch.dict(os.environ, environment, clear=True):
                result = self._run_qualification(
                    first_archive, first_metadata, record_path, directory
                )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["release"]["releaseId"], release_id)
            self.assertEqual(record["release"]["archiveSha256"], first.archive_sha256)
            self.assertEqual(
                record["qualificationDriverSha256"],
                _load_runner().qualification_driver_sha256(ROOT),
            )
            self.assertEqual(record["summary"]["result"], "passed")
            self.assertEqual(
                {scenario["id"] for scenario in record["scenarios"]},
                {
                    "delivery-provider-rails-conformance",
                    "fresh-install-and-host-discovery",
                    "trackerless-workflows",
                    "fake-adapter-workflows",
                    "tamper-rejection",
                    "rollback",
                    "missing-capability",
                    "bounded-review-failure",
                    *WORKFLOW_SCENARIOS,
                },
            )
            self.assertEqual(record["workflowGraphSha256"], hashlib.sha256(
                (ROOT / "workflows/delivery-v1.json").read_bytes()).hexdigest())
            self.assertTrue(all(item["result"] == "passed" for item in record["scenarios"]))
            self.assertGreater(record["summary"]["assertionCount"], len(record["scenarios"]))
            serialized = record_path.read_text(encoding="utf-8")
            self.assertNotIn(str(directory), serialized)
            self.assertNotIn(secret, serialized)
            self.assertNotIn("linear", serialized.lower())
            self.assertEqual(
                record_path.read_bytes(),
                (ROOT / "qualifications" / "core-workflows-adoption-v1.json").read_bytes(),
            )

            replay = self._run_qualification(
                second_archive,
                second_metadata,
                directory / "replay.json",
                directory,
            )
            self.assertEqual(replay.returncode, 0, replay.stdout + replay.stderr)
            self.assertEqual(
                record_path.read_bytes(),
                (directory / "replay.json").read_bytes(),
            )

    def test_anti_vacuity_rejects_missing_or_unasserted_scenarios(self) -> None:
        runner = _load_runner()
        release = {
            "archiveSha256": "a" * 64,
            "metadataSha256": "b" * 64,
            "profile": "core",
            "releaseId": "core-example",
        }
        with self.assertRaisesRegex(ValueError, "qualification.anti_vacuity"):
            runner.finalize_record(release, [])
        scenarios = [
            {
                "assertionCount": 0,
                "checkpointSha256": "c" * 64,
                "evidence": ["named checkpoint"],
                "id": scenario_id,
                "result": "passed",
            }
            for scenario_id in runner.REQUIRED_SCENARIOS
        ]
        with self.assertRaisesRegex(ValueError, "qualification.anti_vacuity"):
            runner.finalize_record(release, scenarios)

    def test_passing_evidence_requires_exact_positive_count_and_nonblank_string_list(self) -> None:
        runner = _load_runner()
        release = {"archiveSha256": "a" * 64, "metadataSha256": "b" * 64,
                   "profile": "core", "releaseId": "core-example"}
        scenarios = [runner._run(name, lambda checkpoint: checkpoint.check(True, "asserted"))
                     for name in runner.REQUIRED_SCENARIOS]
        malformed = (
            {"assertionCount": True}, {"assertionCount": 1.0},
            {"assertionCount": 0}, {"assertionCount": -1}, {"assertionCount": 2},
            {"evidence": "asserted"}, {"evidence": ("asserted",)},
            {"evidence": []}, {"evidence": [""]}, {"evidence": [" \t\n"]},
            {"evidence": [True]}, {"evidence": ["asserted", None], "assertionCount": 2},
            {"evidence": ["asserted", "another assertion"]},
        )
        for changes in malformed:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "qualification.anti_vacuity"):
                runner.finalize_record(release, [{**scenarios[0], **changes}, *scenarios[1:]],
                                       workflow_graph_sha256="c" * 64)
        valid = runner.finalize_record(release, scenarios, workflow_graph_sha256="c" * 64)
        self.assertEqual(valid["summary"]["result"], "passed")
        failed = runner._run(scenarios[0]["id"], lambda checkpoint: checkpoint.check(False, "failed assertion"))
        result = runner.finalize_record(release, [failed, *scenarios[1:]], workflow_graph_sha256="c" * 64)
        self.assertEqual(result["summary"]["result"], "failed")

    def test_passing_candidate_stdout_requires_zero_process_exit(self) -> None:
        runner = _load_runner()
        scenario = "workflow-graph-conformance"
        passing = runner._run(scenario, lambda checkpoint: checkpoint.check(True, "asserted"))
        for returncode in (0, 1, -9):
            with self.subTest(returncode=returncode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                process = subprocess.CompletedProcess([], returncode, json.dumps(passing), "secret stderr")
                with mock.patch.object(runner.subprocess, "run", return_value=process):
                    record = runner._candidate_scenario(scenario, directory / "candidate", directory,
                                                        directory / "archive.zip", directory / "metadata.json")
                if returncode == 0:
                    self.assertEqual(record, passing)
                else:
                    self.assertEqual(record["result"], "failed")
                    self.assertEqual(record["failure"], f"candidate-process:{returncode}")
                    self.assertNotIn("secret stderr", json.dumps(record))

    def test_unexpected_failure_retains_only_a_stable_non_secret_summary(self) -> None:
        runner = _load_runner()
        secret = "unexpected-secret-must-not-leak"

        def fail(_checkpoint) -> None:
            raise RuntimeError(secret)

        record = runner._run("trackerless-workflows", fail)

        self.assertEqual(record["result"], "failed")
        self.assertEqual(record["failure"], "unexpected:RuntimeError")
        self.assertNotIn(secret, json.dumps(record))

    def test_trusted_sensor_change_changes_the_driver_identity(self) -> None:
        runner = _load_runner()
        recorded = json.loads(
            (ROOT / "qualifications" / "core-workflows-adoption-v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            recorded["qualificationDriverSha256"],
            runner.qualification_driver_sha256(ROOT),
        )
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "trusted-evidence"
            for relative in runner.TRUSTED_EVIDENCE_PATHS:
                source = ROOT / relative
                target = copied / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            before = runner.qualification_driver_sha256(copied)
            sensor = copied / "tests" / "test_router.py"
            sensor_bytes = sensor.read_bytes()
            sensor.write_bytes(sensor_bytes + b"\n# changed trusted sensor\n")

            after_sensor_change = runner.qualification_driver_sha256(copied)
            sensor.write_bytes(sensor_bytes)
            trusted_fixture = copied / "tests" / "scenarios" / "core" / "routing.json"
            trusted_fixture.write_bytes(trusted_fixture.read_bytes() + b"\n")

            after_fixture_change = runner.qualification_driver_sha256(copied)

            self.assertEqual(before, recorded["qualificationDriverSha256"])
            self.assertNotEqual(after_sensor_change, before)
            self.assertNotEqual(after_fixture_change, before)

    def test_trusted_release_verifier_change_changes_the_driver_identity(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "trusted-evidence"
            for relative in runner.TRUSTED_EVIDENCE_PATHS:
                source = ROOT / relative
                target = copied / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)

            before = runner.qualification_driver_sha256(copied)
            verifier = copied / "agent_skills" / "release.py"
            verifier.write_bytes(verifier.read_bytes() + b"\n# changed trusted verifier\n")

            self.assertNotEqual(runner.qualification_driver_sha256(copied), before)

    def test_trusted_driver_qualifies_the_supplied_archive_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            candidate = directory / "broken-candidate"
            shutil.copytree(
                ROOT,
                candidate,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            router = candidate / "agent_skills" / "router.py"
            source = router.read_text(encoding="utf-8")
            router.write_text(
                source.replace(
                    "    return WorkflowKind.CLARIFY\n\n\ndef _declared_workflow",
                    "    return WorkflowKind.IMPLEMENT\n\n\ndef _declared_workflow",
                    1,
                ),
                encoding="utf-8",
            )
            archive = directory / "broken.zip"
            metadata = directory / "broken.release.json"
            build_release(
                candidate,
                archive,
                metadata,
                release_id="broken-core",
                profile_id="core",
            )
            record_path = directory / "broken.qualification.json"

            result = self._run_qualification(
                archive, metadata, record_path, directory
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["release"]["releaseId"], "broken-core")
            self.assertEqual(record["summary"]["result"], "failed")
            trackerless = next(
                item
                for item in record["scenarios"]
                if item["id"] == "trackerless-workflows"
            )
            self.assertEqual(trackerless["result"], "failed")

    def test_contract_conformance_comes_from_the_supplied_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            candidate = directory / "contract-candidate"
            shutil.copytree(
                ROOT,
                candidate,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            fixture_path = (
                candidate
                / "tests"
                / "fixtures"
                / "delivery-provider-rails-v1.json"
            )
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            supported = next(
                case for case in fixture["cases"] if case["id"] == "supported-complete"
            )
            supported["expected"]["consumer"]["status"] = "failed"
            fixture_path.write_text(
                json.dumps(fixture, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            archive = directory / "contract-candidate.zip"
            metadata = directory / "contract-candidate.release.json"
            build_release(
                candidate,
                archive,
                metadata,
                release_id="contract-candidate",
                profile_id="core",
            )
            record_path = directory / "contract-candidate.qualification.json"

            result = self._run_qualification(
                archive,
                metadata,
                record_path,
                directory,
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            conformance = next(
                item
                for item in record["scenarios"]
                if item["id"] == "delivery-provider-rails-conformance"
            )
            self.assertEqual(conformance["result"], "failed")

    def test_modified_packaged_contract_sensor_fails_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            candidate = directory / "modified-sensor-candidate"
            shutil.copytree(
                ROOT,
                candidate,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            sensor = candidate / "tests" / "test_delivery_provider_rails_contract.py"
            sensor.write_bytes(sensor.read_bytes() + b"\n# candidate-only sensor change\n")
            archive = directory / "modified-sensor.zip"
            metadata = directory / "modified-sensor.release.json"
            build_release(
                candidate,
                archive,
                metadata,
                release_id="modified-sensor",
                profile_id="core",
            )
            record_path = directory / "modified-sensor.qualification.json"

            result = self._run_qualification(
                archive,
                metadata,
                record_path,
                directory,
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            conformance = next(
                item
                for item in record["scenarios"]
                if item["id"] == "delivery-provider-rails-conformance"
            )
            self.assertEqual(conformance["result"], "failed")

    def test_missing_required_contract_case_fails_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            candidate = directory / "missing-case-candidate"
            shutil.copytree(
                ROOT,
                candidate,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            fixture_path = (
                candidate
                / "tests"
                / "fixtures"
                / "delivery-provider-rails-v1.json"
            )
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            fixture["cases"] = [
                case
                for case in fixture["cases"]
                if case["id"] != "unsupported-negotiation"
            ]
            fixture_path.write_text(
                json.dumps(fixture, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            archive = directory / "missing-case.zip"
            metadata = directory / "missing-case.release.json"
            build_release(
                candidate,
                archive,
                metadata,
                release_id="missing-case",
                profile_id="core",
            )
            record_path = directory / "missing-case.qualification.json"

            result = self._run_qualification(
                archive,
                metadata,
                record_path,
                directory,
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            conformance = next(
                item
                for item in record["scenarios"]
                if item["id"] == "delivery-provider-rails-conformance"
            )
            self.assertEqual(conformance["result"], "failed")

    def test_failed_verification_invalidates_a_prior_passing_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "core.zip"
            metadata = directory / "core.release.json"
            build_release(
                ROOT,
                archive,
                metadata,
                release_id="core-workflows-v2",
                profile_id="core",
            )
            record_path = directory / "qualification.json"
            first = self._run_qualification(
                archive, metadata, record_path, directory
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(
                json.loads(record_path.read_text(encoding="utf-8"))["summary"]["result"],
                "passed",
            )

            with archive.open("ab") as target:
                target.write(b"tampered")
            second = self._run_qualification(
                archive, metadata, record_path, directory
            )

            self.assertEqual(second.returncode, 1, second.stdout + second.stderr)
            self.assertFalse(record_path.exists())

    def test_omitted_runner_content_does_not_affect_exact_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "core.zip"
            metadata = directory / "core.release.json"
            build_release(
                ROOT,
                archive,
                metadata,
                release_id="core-workflows-v2",
                profile_id="core",
            )
            expected_record = directory / "expected.json"
            expected = self._run_qualification(
                archive, metadata, expected_record, directory
            )
            self.assertEqual(expected.returncode, 0, expected.stdout + expected.stderr)

            runner = directory / "mutated-runner"
            shutil.copytree(
                ROOT,
                runner,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            omitted = runner / "skills" / "bootstrap-agent-harness" / "SKILL.md"
            omitted.write_bytes(omitted.read_bytes() + b"\n<!-- unrelated runner bytes -->\n")
            candidate_only = runner / "agent_skills" / "router.py"
            candidate_only.write_bytes(
                candidate_only.read_bytes() + b"\n# unused runner mirror change\n"
            )
            actual_record = directory / "actual.json"
            actual = self._run_qualification(
                archive,
                metadata,
                actual_record,
                directory,
                runner / "scripts" / "qualify-core.py",
            )

            runner_module = _load_runner()
            self.assertEqual(
                runner_module.qualification_driver_sha256(runner),
                runner_module.qualification_driver_sha256(ROOT),
            )
            self.assertEqual(actual.returncode, 0, actual.stdout + actual.stderr)
            self.assertEqual(actual_record.read_bytes(), expected_record.read_bytes())


if __name__ == "__main__":
    unittest.main()
