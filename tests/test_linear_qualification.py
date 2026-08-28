from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_skills.release import build_release


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qualify-linear.py"
RECORD = ROOT / "qualifications" / "linear-v1.json"
SOURCE_COMMIT = "7987895a5e5afc3e7d7ab92464bf114e7cef0cf9"


def _load_runner():
    spec = importlib.util.spec_from_file_location("qualify_linear", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _live_evidence() -> dict:
    return {
        "auditReferences": [
            "linear-audit:1111111111111111",
            "linear-audit:2222222222222222",
        ],
        "checks": {
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
        },
        "evidenceSource": "live-host-native-linear-mcp",
        "schemaVersion": "agent-skills-linear-live-evidence/1",
    }


class LinearQualificationTests(unittest.TestCase):
    def _build_candidates(self, directory: Path) -> tuple[Path, Path, Path, Path]:
        core_archive = directory / "core.zip"
        core_metadata = directory / "core.release.json"
        linear_archive = directory / "linear.zip"
        linear_metadata = directory / "linear.release.json"
        build_release(
            ROOT,
            core_archive,
            core_metadata,
            release_id="core-v1",
            profile_id="core",
        )
        build_release(
            ROOT,
            linear_archive,
            linear_metadata,
            release_id="linear-v1",
            profile_id="linear",
        )
        return core_archive, core_metadata, linear_archive, linear_metadata

    def _run_qualification(
        self,
        directory: Path,
        live_evidence: dict,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        core_archive, core_metadata, linear_archive, linear_metadata = (
            self._build_candidates(directory)
        )
        evidence_path = directory / "live-evidence.json"
        evidence_path.write_text(
            json.dumps(live_evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        record_path = directory / "record.json"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--core-archive",
                str(core_archive),
                "--core-metadata",
                str(core_metadata),
                "--core-record",
                str(ROOT / "qualifications" / "core-v1.json"),
                "--linear-archive",
                str(linear_archive),
                "--linear-metadata",
                str(linear_metadata),
                "--live-evidence",
                str(evidence_path),
                "--source-commit",
                SOURCE_COMMIT,
                "--record",
                str(record_path),
            ],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, record_path

    def test_exact_linear_qualification_is_redacted_and_binds_both_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            secret = "live-connector-secret-must-not-leak"
            environment = {**os.environ, "QUALIFICATION_SECRET_SENTINEL": secret}
            with mock.patch.dict(os.environ, environment, clear=True):
                result, record_path = self._run_qualification(
                    directory,
                    _live_evidence(),
                )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["candidate"]["sourceCommitSha"], SOURCE_COMMIT)
            self.assertEqual(record["candidate"]["core"]["profile"], "core")
            self.assertEqual(record["candidate"]["linear"]["profile"], "linear")
            self.assertEqual(record["summary"]["result"], "passed")
            self.assertEqual(
                {scenario["id"] for scenario in record["scenarios"]},
                {
                    "controlled-degraded-outcomes",
                    "core-qualification-binding",
                    "forbidden-alternate-transport",
                    "linear-install-and-host-discovery",
                    "live-host-native-operations",
                    "live-reconcile-before-retry",
                },
            )
            live = next(
                scenario
                for scenario in record["scenarios"]
                if scenario["id"] == "live-host-native-operations"
            )
            controlled = next(
                scenario
                for scenario in record["scenarios"]
                if scenario["id"] == "controlled-degraded-outcomes"
            )
            self.assertEqual(live["evidenceSource"], "live-host-native-linear-mcp")
            self.assertEqual(
                controlled["evidenceSource"], "deterministic-controlled-boundary-double"
            )
            serialized = record_path.read_text(encoding="utf-8")
            self.assertNotIn(str(directory), serialized)
            self.assertNotIn(secret, serialized)
            self.assertNotIn("rawHostPayload", serialized)
            self.assertNotIn("rawIdempotencyKey", serialized)

    def test_live_evidence_rejects_unknown_fields_raw_references_and_duplicates(self) -> None:
        runner = _load_runner()
        invalid_documents = []

        unknown = _live_evidence()
        unknown["rawHostPayload"] = {"id": "not-allowed"}
        invalid_documents.append(unknown)

        raw_reference = _live_evidence()
        raw_reference["auditReferences"][0] = "raw-host-identifier"
        invalid_documents.append(raw_reference)

        duplicate = _live_evidence()
        duplicate["checks"]["duplicateCreateAttempts"] = 1
        invalid_documents.append(duplicate)

        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaisesRegex(ValueError, "linear_qualification.live_evidence"):
                    runner.validate_live_evidence(document)

    def test_controlled_boundary_matrix_covers_every_declared_degraded_outcome(self) -> None:
        runner = _load_runner()
        contract = json.loads(
            (
                ROOT
                / "skills/linear-tracker-adapter/references/adapter-contract.json"
            ).read_text(encoding="utf-8")
        )

        matrix = runner.controlled_boundary_matrix(contract)

        self.assertEqual(
            {case["input"] for case in matrix},
            set(contract["failureOutcomes"]),
        )
        self.assertEqual(
            {case["outcome"] for case in matrix},
            set(contract["failureOutcomes"].values()),
        )
        self.assertTrue(all(case["action"] for case in matrix))
        mutation_ambiguities = {
            case["input"]: case
            for case in matrix
            if case["input"].startswith("mutation-")
        }
        self.assertTrue(
            all(
                case["outcome"] == "reconciliation-required"
                and case["repeatMutation"] is False
                for case in mutation_ambiguities.values()
            )
        )

    def test_checked_record_matches_current_driver_and_exact_release_identities(self) -> None:
        runner = _load_runner()
        record = json.loads(RECORD.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            core_archive, core_metadata, linear_archive, linear_metadata = (
                self._build_candidates(Path(temporary))
            )

            runner.validate_checked_record(
                record,
                core_archive,
                core_metadata,
                ROOT / "qualifications" / "core-v1.json",
                linear_archive,
                linear_metadata,
                SOURCE_COMMIT,
            )
            tampered = copy.deepcopy(record)
            tampered["scenarios"][0]["evidence"].append("unbound evidence")
            with self.assertRaisesRegex(ValueError, "scenario evidence"):
                runner.validate_checked_record(
                    tampered,
                    core_archive,
                    core_metadata,
                    ROOT / "qualifications" / "core-v1.json",
                    linear_archive,
                    linear_metadata,
                    SOURCE_COMMIT,
                )

        self.assertEqual(
            record["qualificationDriverSha256"],
            runner.qualification_driver_sha256(ROOT),
        )

    def test_durable_qualification_artifacts_contain_no_plan_or_ticket_ids(self) -> None:
        durable_paths = [ROOT / "README.md"]
        for root_name in (
            "agent_skills",
            "docs",
            "qualifications",
            "schemas",
            "scripts",
            "skills",
            "tests",
        ):
            durable_paths.extend(
                path
                for path in (ROOT / root_name).rglob("*")
                if path.is_file() and path.suffix in {".json", ".md", ".py"}
            )
        plan_unit = re.compile(r"\bU\d+[A-Za-z]?\b")
        ticket_id = re.compile(r"\b[A-Z]\d{2,}-\d+\b")

        for path in durable_paths:
            content = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIsNone(plan_unit.search(content))
                self.assertIsNone(ticket_id.search(content))


if __name__ == "__main__":
    unittest.main()
