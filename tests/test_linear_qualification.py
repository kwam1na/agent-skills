from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_skills.release import build_release, verify_release


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qualify-linear.py"
ATTESTATION = ROOT / "qualifications" / "linear-v1.attestation.json"
RECORD = ROOT / "qualifications" / "linear-v1.json"


def _load_runner():
    spec = importlib.util.spec_from_file_location("qualify_linear", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _host_attestation() -> dict:
    event_specs = (
        ("search", "miss", ["primary"]),
        ("search", "miss", ["dependency"]),
        ("create", "success", ["primary"]),
        ("create", "success", ["dependency"]),
        ("read", "success", ["primary"]),
        ("read", "success", ["dependency"]),
        ("update", "success", ["primary"]),
        ("update", "success", ["dependency"]),
        ("relations", "success", ["primary", "dependency"]),
        ("read", "relation-observed", ["primary", "dependency"]),
        ("reconciliation", "unique-match", ["primary"]),
        ("update", "canceled", ["primary"]),
        ("update", "canceled", ["dependency"]),
        ("read", "cleanup-confirmed", ["primary"]),
        ("read", "cleanup-confirmed", ["dependency"]),
    )
    return {
        "auditReferences": [
            "linear-audit:11111111111111111111111111111111",
            "linear-audit:22222222222222222222222222222222",
        ],
        "collector": {
            "kind": "trusted-host-side",
            "transport": "host-native-linear-mcp",
        },
        "entities": [
            {
                "auditReference": "linear-audit:11111111111111111111111111111111",
                "finalStatus": "canceled",
                "operationMarker": "linear-operation:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "role": "primary",
            },
            {
                "auditReference": "linear-audit:22222222222222222222222222222222",
                "finalStatus": "canceled",
                "operationMarker": "linear-operation:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "role": "dependency",
            },
        ],
        "events": [
            {
                "eventReference": f"linear-event:{index:032x}",
                "operation": operation,
                "outcome": outcome,
                "roles": roles,
                "sequence": index,
            }
            for index, (operation, outcome, roles) in enumerate(event_specs, start=1)
        ],
        "receipt": {
            "externalSystem": "linear",
            "reference": "linear-receipt:cccccccccccccccccccccccccccccccc",
        },
        "schemaVersion": "agent-skills-linear-host-attestation/1",
        "sessionReference": "linear-session:dddddddddddddddddddddddddddddddd",
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
        attestation: dict,
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        core_archive, core_metadata, linear_archive, linear_metadata = (
            self._build_candidates(directory)
        )
        attestation_path = directory / "attestation.json"
        attestation_path.write_text(_canonical_json(attestation), encoding="utf-8")
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
                "--attestation",
                str(attestation_path),
                "--record",
                str(record_path),
            ],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, record_path, attestation_path

    def test_exact_linear_qualification_is_redacted_and_binds_retained_attestation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            secret = "live-connector-secret-must-not-leak"
            environment = {**os.environ, "QUALIFICATION_SECRET_SENTINEL": secret}
            with mock.patch.dict(os.environ, environment, clear=True):
                result, record_path, attestation_path = self._run_qualification(
                    directory,
                    _host_attestation(),
                )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(set(record["candidate"]), {"core", "linear"})
            self.assertEqual(record["candidate"]["core"]["profile"], "core")
            self.assertEqual(record["candidate"]["linear"]["profile"], "linear")
            self.assertRegex(
                record["candidate"]["core"]["sourceTreeSha256"], r"^[a-f0-9]{64}$"
            )
            self.assertRegex(
                record["candidate"]["linear"]["sourceTreeSha256"], r"^[a-f0-9]{64}$"
            )
            self.assertEqual(
                record["attestationSha256"],
                _load_runner().digest_bytes(attestation_path.read_bytes()),
            )
            self.assertEqual(
                record["receiptReference"],
                _host_attestation()["receipt"]["reference"],
            )
            self.assertEqual(record["summary"]["result"], "passed")
            serialized = record_path.read_text(encoding="utf-8")
            self.assertNotIn(str(directory), serialized)
            self.assertNotIn(secret, serialized)
            self.assertNotIn("rawHostPayload", serialized)
            self.assertNotIn("rawIdempotencyKey", serialized)

    def test_attestation_rejects_duplicate_references_and_broken_operation_causality(
        self,
    ) -> None:
        runner = _load_runner()
        invalid_documents = []

        duplicate_audit = _host_attestation()
        duplicate_audit["auditReferences"][1] = duplicate_audit["auditReferences"][0]
        invalid_documents.append(duplicate_audit)

        duplicate_event = _host_attestation()
        duplicate_event["events"][1]["eventReference"] = duplicate_event["events"][0][
            "eventReference"
        ]
        invalid_documents.append(duplicate_event)

        create_before_search = _host_attestation()
        create_before_search["events"][0]["operation"] = "create"
        invalid_documents.append(create_before_search)

        repeated_create = _host_attestation()
        repeated_create["events"][10]["operation"] = "create"
        invalid_documents.append(repeated_create)

        missing_cleanup = _host_attestation()
        missing_cleanup["events"][-1]["outcome"] = "success"
        invalid_documents.append(missing_cleanup)

        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaisesRegex(ValueError, "linear_qualification.attestation"):
                    runner.validate_attestation(document)

    def test_attestation_must_be_canonical_json(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "attestation.json"
            path.write_text(json.dumps(_host_attestation()), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "canonical"):
                runner.load_attestation(path)

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
        self.assertTrue(all(case["action"] for case in matrix))
        self.assertTrue(
            all(
                case["repeatMutation"] is False
                for case in matrix
                if case["input"].startswith("mutation-")
            )
        )

    def test_transport_sensor_inspects_supplied_candidate_and_rejects_each_category(
        self,
    ) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            core_archive, core_metadata, linear_archive, linear_metadata = (
                self._build_candidates(directory)
            )
            core_candidate = directory / "core-candidate"
            linear_candidate = directory / "linear-candidate"
            verify_release(core_archive, core_metadata, extract_to=core_candidate)
            verify_release(linear_archive, linear_metadata, extract_to=linear_candidate)
            contract = json.loads(
                (
                    linear_candidate
                    / "skills/linear-tracker-adapter/references/adapter-contract.json"
                ).read_text(encoding="utf-8")
            )
            runner.assert_candidate_transport_boundary(
                linear_candidate,
                core_candidate,
                contract,
            )

            for category, markers in contract["transportBoundary"][
                "forbiddenActionMarkers"
            ].items():
                with self.subTest(category=category):
                    mutated = directory / f"mutated-{category}"
                    shutil.copytree(linear_candidate, mutated)
                    skill = mutated / "skills/linear-tracker-adapter/SKILL.md"
                    skill.write_text(
                        skill.read_text(encoding="utf-8") + f"\n{markers[0]}\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        runner.QualificationFailure,
                        category,
                    ):
                        runner.assert_candidate_transport_boundary(
                            mutated,
                            core_candidate,
                            contract,
                        )

    def test_checked_record_matches_current_driver_attestation_and_release_identities(
        self,
    ) -> None:
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
                ATTESTATION,
            )
            tampered = copy.deepcopy(record)
            tampered["scenarios"][0]["evidence"].append("unbound evidence")
            with self.assertRaisesRegex(ValueError, "exact record mismatch"):
                runner.validate_checked_record(
                    tampered,
                    core_archive,
                    core_metadata,
                    ROOT / "qualifications" / "core-v1.json",
                    linear_archive,
                    linear_metadata,
                    ATTESTATION,
                )

            tampered_attestation = Path(temporary) / "tampered-attestation.json"
            attestation = json.loads(ATTESTATION.read_text(encoding="utf-8"))
            attestation["sessionReference"] = (
                "linear-session:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
            )
            tampered_attestation.write_text(
                _canonical_json(attestation),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "attestation mismatch"):
                runner.validate_checked_record(
                    record,
                    core_archive,
                    core_metadata,
                    ROOT / "qualifications" / "core-v1.json",
                    linear_archive,
                    linear_metadata,
                    tampered_attestation,
                )

    def test_durable_corpus_contains_no_plan_or_ticket_ids(self) -> None:
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
