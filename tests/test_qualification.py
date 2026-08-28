from __future__ import annotations

import importlib.util
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


def _load_runner():
    spec = importlib.util.spec_from_file_location("qualify_core", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QualificationTests(unittest.TestCase):
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
            release_id = "core-v1"
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
                },
            )
            self.assertTrue(all(item["result"] == "passed" for item in record["scenarios"]))
            self.assertGreater(record["summary"]["assertionCount"], len(record["scenarios"]))
            serialized = record_path.read_text(encoding="utf-8")
            self.assertNotIn(str(directory), serialized)
            self.assertNotIn(secret, serialized)
            self.assertNotIn("linear", serialized.lower())
            self.assertEqual(
                record_path.read_bytes(),
                (ROOT / "qualifications" / "core-v1.json").read_bytes(),
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
            (ROOT / "qualifications" / "core-v1.json").read_text(encoding="utf-8")
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

    def test_failed_verification_invalidates_a_prior_passing_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "core.zip"
            metadata = directory / "core.release.json"
            build_release(
                ROOT,
                archive,
                metadata,
                release_id="core-v1",
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
                release_id="core-v1",
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
