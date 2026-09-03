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
import zipfile
from pathlib import Path
from unittest import mock

from agent_skills.release import build_release, verify_release


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qualify-linear.py"
ATTESTATION = ROOT / "qualifications" / "linear-v2.attestation.json"
RECORD = ROOT / "qualifications" / "linear-v2.json"
BINDING = ROOT / "qualifications" / "linear-v2.collector-binding.json"


def _load_runner():
    spec = importlib.util.spec_from_file_location("qualify_linear", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _collector_binding() -> dict:
    """Synthetic test-only input; never qualification evidence for a release."""
    return {
        "schemaVersion": "agent-skills-linear-collector-binding/1",
        "releases": {
            profile: {
                "archiveSha256": "a" * 64,
                "metadataSha256": "b" * 64,
                "profile": profile,
                "releaseId": f"{profile}-v1",
            }
            for profile in ("core", "linear")
        },
        "graphSha256": "c" * 64,
        "subjectRef": {"schemaVersion": "workflow-subject-ref/1", "opaque": "test subject"},
        "candidateRef": {"schemaVersion": "workflow-candidate-ref/1", "opaque": "test candidate"},
    }


def _host_attestation(binding: dict | None = None) -> dict:
    """Synthetic test-only attestation, not a retained host collection."""
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
        "collectorBinding": copy.deepcopy(binding if binding is not None else _collector_binding()),
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
        "schemaVersion": "agent-skills-linear-host-attestation/2",
        "sessionReference": "linear-session:dddddddddddddddddddddddddddddddd",
    }


class LinearQualificationTests(unittest.TestCase):
    def test_bound_attestation_compares_every_field_with_independent_input(self) -> None:
        runner = _load_runner()
        binding = _collector_binding()
        self.assertIn("liveEvidence", runner.validate_attestation(_host_attestation(), binding))
        paths = [
            ("releases", profile, field)
            for profile in ("core", "linear")
            for field in ("archiveSha256", "metadataSha256", "profile", "releaseId")
        ] + [("graphSha256",), ("subjectRef", "opaque"), ("candidateRef", "opaque")]
        for path in paths:
            attestation = _host_attestation()
            target = attestation["collectorBinding"]
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = "d" * 64 if path[-1].endswith("Sha256") else "different"
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "binding"):
                runner.validate_attestation(attestation, binding)

    def test_old_unbound_attestation_is_rejected(self) -> None:
        legacy = _host_attestation()
        legacy.pop("collectorBinding")
        legacy["schemaVersion"] = "agent-skills-linear-host-attestation/1"
        with self.assertRaisesRegex(ValueError, "attestation"):
            _load_runner().validate_attestation(legacy, _collector_binding())

    def test_binding_and_attestation_hostile_shapes_fail_closed(self) -> None:
        runner = _load_runner()
        malformed = [None, [], "", {}, {**_collector_binding(), "extra": "forbidden"}]
        for path, replacement in (
            (("schemaVersion",), "agent-skills-linear-collector-binding/0"),
            (("releases",), []),
            (("releases", "core"), []),
            (("releases", "core", "archiveSha256"), "A" * 64),
            (("releases", "linear", "releaseId"), " "),
            (("graphSha256",), True),
            (("subjectRef",), {"schemaVersion": "workflow-subject-ref/1", "opaque": "", "extra": "x"}),
            (("subjectRef", "schemaVersion"), "workflow-subject-ref/2"),
            (("candidateRef", "schemaVersion"), "workflow-candidate-ref/2"),
            (("subjectRef", "opaque"), []),
            (("candidateRef", "opaque"), " \n"),
        ):
            binding = _collector_binding()
            target = binding
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = replacement
            malformed.append(binding)
        for binding in malformed:
            with self.subTest(binding=binding), self.assertRaisesRegex(ValueError, "collector_binding"):
                runner.validate_collector_binding(binding)
        for change in ("audit", "sequence", "collector", "receipt", "redaction"):
            attestation = _host_attestation()
            if change == "audit":
                attestation["auditReferences"][0] = []
            elif change == "sequence":
                attestation["events"][0]["sequence"] = True
            elif change == "collector":
                attestation["collector"] = {"kind": "unavailable", "transport": "host-native-linear-mcp"}
            elif change == "receipt":
                attestation["receipt"] = None
            else:
                attestation["collectorBinding"]["subjectRef"]["opaque"] = "/Users/private/subject"
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "attestation"):
                runner.validate_attestation(attestation, attestation["collectorBinding"])

    def test_collector_input_must_exist_be_canonical_and_independently_supplied(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "binding.json"
            with self.assertRaisesRegex(ValueError, "unavailable input"):
                runner.load_collector_binding(path)
            binding = _collector_binding()
            for contents in (
                json.dumps(binding),
                _canonical_json(binding).replace('"graphSha256":', '"graphSha256":"' + "d" * 64 + '","graphSha256":'),
            ):
                path.write_text(contents, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "canonical"):
                    runner.load_collector_binding(path)
            path.write_text(_canonical_json(binding), encoding="utf-8")
            self.assertEqual(runner.load_collector_binding(path), binding)
            with self.assertRaisesRegex(ValueError, "collector_binding"):
                runner.validate_attestation(_host_attestation(), None)
            with self.assertRaisesRegex(ValueError, "unavailable input"):
                runner.load_attestation(Path(temporary) / "unavailable.json", binding)

    def test_candidate_binding_checks_actual_release_fields_and_both_graphs(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            paths = self._build_candidates(directory)
            core_root, linear_root = directory / "core", directory / "linear"
            core = verify_release(paths[0], paths[1], extract_to=core_root)
            linear = verify_release(paths[2], paths[3], extract_to=linear_root)
            binding = self._binding_for_candidates(*paths)
            runner.validate_candidate_binding(binding, core, linear, core_root, linear_root)
            for profile in ("core", "linear"):
                for field in ("archiveSha256", "metadataSha256", "profile", "releaseId"):
                    wrong = copy.deepcopy(binding)
                    wrong["releases"][profile][field] = "d" * 64 if field.endswith("Sha256") else "wrong"
                    with self.subTest(profile=profile, field=field), self.assertRaisesRegex(ValueError, "collector_binding"):
                        runner.validate_candidate_binding(wrong, core, linear, core_root, linear_root)
            for root in (core_root, linear_root):
                graph = root / runner.GRAPH_PATH
                original = graph.read_bytes()
                graph.write_bytes(original + b"\n")
                with self.subTest(root=root.name), self.assertRaisesRegex(ValueError, "graph mismatch"):
                    runner.validate_candidate_binding(binding, core, linear, core_root, linear_root)
                graph.unlink()
                with self.assertRaisesRegex(ValueError, "graph unavailable"):
                    runner.validate_candidate_binding(binding, core, linear, core_root, linear_root)
                graph.write_bytes(original)

    def test_core_record_binding_rejects_stale_release_graph_and_failed_summary(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._build_candidates(Path(temporary))
            core = verify_release(paths[0], paths[1])
            binding = self._binding_for_candidates(*paths)
            record = {"release": binding["releases"]["core"], "workflowGraphSha256": binding["graphSha256"], "summary": {"result": "passed"}}
            for path in [("release", key) for key in record["release"]] + [("workflowGraphSha256",), ("summary", "result")]:
                wrong = copy.deepcopy(record)
                target = wrong
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = "wrong"
                with self.subTest(path=path), self.assertRaises(runner.QualificationFailure):
                    runner._core_binding(runner.Checkpoint("core-qualification-binding", "trusted-checked-qualification"), core, wrong, binding["graphSha256"])

    def test_anti_vacuity_rejects_missing_reordered_and_empty_passing_evidence(self) -> None:
        runner = _load_runner()
        scenarios = [
            {"id": scenario, "result": "passed", "assertionCount": 1, "evidence": ["asserted"], "evidenceSource": "test-only"}
            for scenario in runner.REQUIRED_SCENARIOS
        ]
        malformed = [[], scenarios[:-1], list(reversed(scenarios))]
        for key, value in (("assertionCount", 0), ("assertionCount", True), ("assertionCount", 2), ("evidence", []), ("evidence", "asserted"), ("evidence", [""]), ("evidenceSource", ""), ("result", "skipped"), ("result", [])):
            wrong = copy.deepcopy(scenarios)
            wrong[0][key] = value
            malformed.append(wrong)
        for wrong in malformed:
            with self.subTest(scenarios=wrong), self.assertRaisesRegex(ValueError, "anti_vacuity"):
                runner._finalize_record({}, "a" * 64, "b" * 64, "linear-receipt:" + "c" * 32, wrong, _collector_binding(), "d" * 64)

    def test_cli_removes_stale_success_after_unavailable_collection_or_failure(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            record_path = directory / "record.json"
            arguments = [value for name in ("core-archive", "core-metadata", "core-record", "linear-archive", "linear-metadata", "attestation", "collector-binding") for value in (f"--{name}", str(directory / name))]
            arguments.extend(("--record", str(record_path)))
            for error in (ValueError("collector unavailable"), OSError("receipt unavailable")):
                record_path.write_text(_canonical_json({"summary": {"result": "passed"}}), encoding="utf-8")
                with mock.patch.object(runner, "qualify", side_effect=error):
                    self.assertEqual(runner.main(arguments), 1)
                self.assertFalse(record_path.exists())

    def test_exact_offline_record_shape_and_tampering_are_checked(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result, record_path, attestation_path = self._run_qualification(directory, _host_attestation())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(set(record), {
                "attestationSha256", "candidate", "collectorBinding", "collectorBindingSha256",
                "coreQualificationSha256", "proofBoundary", "receiptReference", "redaction",
                "scenarios", "schemaVersion", "summary",
            })
            paths = (
                directory / "core.zip", directory / "core.release.json", directory / "core-record.json",
                directory / "linear.zip", directory / "linear.release.json", attestation_path,
                directory / "collector-binding.json",
            )
            runner.validate_checked_record(record, *paths)
            changes = [
                ("collectorBindingSha256", "d" * 64),
                ("coreQualificationSha256", "d" * 64),
                ("schemaVersion", "agent-skills-linear-qualification/1"),
                ("extra", True),
            ]
            for key, value in changes:
                wrong = copy.deepcopy(record)
                wrong[key] = value
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, "exact record mismatch"):
                    runner.validate_checked_record(wrong, *paths)
            wrong = copy.deepcopy(record)
            wrong["collectorBinding"]["candidateRef"]["opaque"] = "stale candidate"
            with self.assertRaisesRegex(ValueError, "exact record mismatch"):
                runner.validate_checked_record(wrong, *paths)
            wrong = copy.deepcopy(record)
            wrong["scenarios"][0]["evidence"].append("invented evidence")
            with self.assertRaisesRegex(ValueError, "exact record mismatch"):
                runner.validate_checked_record(wrong, *paths)
            failed_core = json.loads(paths[2].read_text(encoding="utf-8"))
            failed_core["summary"]["result"] = "failed"
            paths[2].write_text(_canonical_json(failed_core), encoding="utf-8")
            failed = runner.qualify(*paths)
            self.assertEqual(failed["summary"]["result"], "failed")
            with self.assertRaisesRegex(ValueError, "exact record mismatch"):
                runner.validate_checked_record(failed, *paths)

    def test_cli_failure_preserves_inputs_and_never_reuses_old_success(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result, record_path, attestation_path = self._run_qualification(directory, _host_attestation())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            paths = {
                "core-archive": directory / "core.zip", "core-metadata": directory / "core.release.json",
                "core-record": directory / "core-record.json", "linear-archive": directory / "linear.zip",
                "linear-metadata": directory / "linear.release.json", "attestation": attestation_path,
                "collector-binding": directory / "collector-binding.json", "record": record_path,
            }
            arguments = [value for name, path in paths.items() for value in (f"--{name}", str(path))]
            binding_bytes = paths["collector-binding"].read_bytes()
            aliased = arguments[:-2] + ["--record", str(paths["collector-binding"])]
            self.assertEqual(runner.main(aliased), 1)
            self.assertEqual(paths["collector-binding"].read_bytes(), binding_bytes)
            paths["collector-binding"].unlink()
            self.assertEqual(runner.main(arguments), 1)
            self.assertFalse(record_path.exists())
            paths["collector-binding"].write_bytes(binding_bytes)
            attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
            attestation["collector"] = None
            attestation_path.write_text(_canonical_json(attestation), encoding="utf-8")
            record_path.write_text(_canonical_json({"summary": {"result": "passed"}}), encoding="utf-8")
            self.assertEqual(runner.main(arguments), 1)
            self.assertFalse(record_path.exists())

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
            release_id="linear-v2",
            profile_id="linear",
        )
        return core_archive, core_metadata, linear_archive, linear_metadata

    def test_linear_cannot_inherit_qualification_for_different_shared_core(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            core_archive, core_metadata, linear_archive, linear_metadata = self._build_candidates(directory)
            divergent = directory / "divergent-linear"
            verify_release(linear_archive, linear_metadata, extract_to=divergent)
            diagnosis = divergent / "agent_skills/diagnosis.py"
            original = diagnosis.read_text(encoding="utf-8")
            self.assertIn("elif gaps:", original)
            diagnosis.write_text(original.replace("elif gaps:", "elif True:"), encoding="utf-8")
            changed_archive = directory / "changed-linear.zip"
            changed_metadata = directory / "changed-linear.release.json"
            build_release(divergent, changed_archive, changed_metadata,
                          release_id="linear-v2", profile_id="linear")
            verify_release(changed_archive, changed_metadata)
            with mock.patch.object(self, "_build_candidates", return_value=(
                core_archive, core_metadata, changed_archive, changed_metadata,
            )):
                outcome, record, _ = self._run_qualification(directory, _host_attestation())
            self.assertEqual(outcome.returncode, 1, outcome.stdout + outcome.stderr)
            self.assertFalse(record.exists())

    def _run_qualification(
        self,
        directory: Path,
        attestation: dict,
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        core_archive, core_metadata, linear_archive, linear_metadata = (
            self._build_candidates(directory)
        )
        binding = self._binding_for_candidates(core_archive, core_metadata, linear_archive, linear_metadata)
        binding_path = directory / "collector-binding.json"
        binding_path.write_text(_canonical_json(binding), encoding="utf-8")
        attestation = copy.deepcopy(attestation)
        attestation["collectorBinding"] = copy.deepcopy(binding)
        core_record = directory / "core-record.json"
        # Synthetic unit-test fixture, not checked qualification evidence.
        core_record.write_text(_canonical_json({
            "release": binding["releases"]["core"],
            "workflowGraphSha256": binding["graphSha256"],
            "summary": {"result": "passed"},
        }), encoding="utf-8")
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
                str(core_record),
                "--linear-archive",
                str(linear_archive),
                "--linear-metadata",
                str(linear_metadata),
                "--attestation",
                str(attestation_path),
                "--collector-binding",
                str(binding_path),
                "--record",
                str(record_path),
            ],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, record_path, attestation_path

    def _binding_for_candidates(
        self, core_archive: Path, core_metadata: Path, linear_archive: Path, linear_metadata: Path,
    ) -> dict:
        binding = _collector_binding()
        for archive, metadata in ((core_archive, core_metadata), (linear_archive, linear_metadata)):
            release = verify_release(archive, metadata)
            binding["releases"][release.profile_id] = {
                "archiveSha256": release.archive_sha256,
                "metadataSha256": release.metadata_sha256,
                "profile": release.profile_id,
                "releaseId": release.release_id,
            }
        with zipfile.ZipFile(core_archive) as archive:
            binding["graphSha256"] = _load_runner().digest_bytes(archive.read("workflows/delivery-v1.json"))
        return binding

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
            self.assertNotIn("auditReferences", record)
            self.assertNotIn("qualificationDriverSha256", record)
            self.assertEqual(record["schemaVersion"], "agent-skills-linear-qualification/2")
            self.assertEqual(record["collectorBinding"], json.loads(attestation_path.read_text())["collectorBinding"])
            self.assertEqual(record["collectorBindingSha256"], _load_runner().digest_bytes((directory / "collector-binding.json").read_bytes()))
            self.assertTrue(
                all("checkpointSha256" not in scenario for scenario in record["scenarios"])
            )
            self.assertEqual(record["summary"]["result"], "passed")
            serialized = record_path.read_text(encoding="utf-8")
            self.assertNotIn(str(directory), serialized)
            self.assertNotIn(secret, serialized)
            self.assertNotIn("rawHostPayload", serialized)
            self.assertNotIn("rawIdempotencyKey", serialized)

    def test_install_discovery_is_hermetic_without_preexisting_host_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            empty_home = directory / "empty-home"
            empty_home.mkdir()
            environment = {
                **os.environ,
                "AGENT_SKILLS_EXECUTION_CONTEXT": "pull-request",
                "CLAUDE_CONFIG_DIR": str(empty_home / "claude"),
                "CODEX_HOME": str(empty_home / "codex"),
                "GITHUB_EVENT_NAME": "pull_request",
                "HOME": str(empty_home),
                "XDG_CONFIG_HOME": str(empty_home / "xdg"),
            }
            with mock.patch.dict(os.environ, environment, clear=True):
                result, record_path, _ = self._run_qualification(
                    directory,
                    _host_attestation(),
                )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            scenario = next(
                scenario
                for scenario in record["scenarios"]
                if scenario["id"] == "linear-install-and-host-discovery"
            )
            self.assertEqual(scenario["result"], "passed")
            self.assertIn(
                "qualification-owned roots isolate both host projections",
                scenario["evidence"],
            )
            self.assertNotIn(str(empty_home), record_path.read_text(encoding="utf-8"))
            self.assertFalse((empty_home / "codex").exists())
            self.assertFalse((empty_home / "claude").exists())

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
                    runner.validate_attestation(document, _collector_binding())

    def test_attestation_must_be_canonical_json(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "attestation.json"
            path.write_text(json.dumps(_host_attestation()), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "canonical"):
                runner.load_attestation(path, _collector_binding())

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

    def test_source_tree_digest_uses_platform_neutral_posix_path_order(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "candidate"
            skill = root / "adapter" / "SKILL.md"
            reference = root / "adapter" / "references" / "contract.json"
            manifest = root / "release-manifest.json"
            skill.parent.mkdir(parents=True)
            reference.parent.mkdir(parents=True)
            skill.write_bytes(b"skill\n")
            reference.write_bytes(b"reference\n")
            manifest.write_bytes(b"ignored\n")

            expected = runner._digest(
                [
                    {
                        "path": "adapter/SKILL.md",
                        "sha256": runner.digest_bytes(b"skill\n"),
                    },
                    {
                        "path": "adapter/references/contract.json",
                        "sha256": runner.digest_bytes(b"reference\n"),
                    },
                ]
            )

            self.assertEqual(runner.source_tree_sha256(root), expected)

    def test_transport_sensor_inspects_only_candidate_delta_and_rejects_real_actions(
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
            evidence = runner.inspect_candidate_transport_boundary(
                linear_candidate,
                core_candidate,
                contract,
            )
            self.assertIn("host-native MCP capability set remains complete", evidence)

            harmless = directory / "harmless-prose"
            shutil.copytree(linear_candidate, harmless)
            skill = harmless / "skills/linear-tracker-adapter/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8")
                + "\nDiscuss credential, OAuth, and subprocess boundaries without executing them.\n",
                encoding="utf-8",
            )
            notes = harmless / "skills/linear-tracker-adapter/boundary_notes.py"
            notes.write_text(
                "# Never use api.linear.app or @linear/sdk as an alternate transport.\n",
                encoding="utf-8",
            )
            extension = harmless / "skills/linear-tracker-adapter/extension.js"
            extension.write_text(
                "export const normalizeReference = value => value.trim();\n",
                encoding="utf-8",
            )
            inactive_transport = (
                harmless / "skills/linear-tracker-adapter/inactive_transport.js"
            )
            inactive_transport.write_text(
                'const transport = require("node:https");\n'
                'const documented = "https://api.linear.app/graphql";\n'
                "export { documented, transport };\n",
                encoding="utf-8",
            )
            runner.inspect_candidate_transport_boundary(harmless, core_candidate, contract)

            hostile = (
                (
                    "python-direct-endpoint",
                    "direct-linear-endpoint",
                    "skills/linear-tracker-adapter/transport.py",
                    'import requests\nrequests.post("https://api.linear.app/graphql")\n',
                ),
                (
                    "node-require-alias",
                    "direct-linear-endpoint",
                    "skills/linear-tracker-adapter/transport.cjs",
                    'const transport = require("node:https");\n'
                    'transport.request("https://api.linear.app/graphql");\n',
                ),
                (
                    "node-import-alias",
                    "direct-linear-endpoint",
                    "skills/linear-tracker-adapter/transport.mjs",
                    'import { request as send } from "node:https";\n'
                    'send("https://api.linear.app/graphql");\n',
                ),
                (
                    "node-destructured-require",
                    "direct-linear-endpoint",
                    "skills/linear-tracker-adapter/transport.js",
                    'const { get: fetchLinear } = require("https");\n'
                    'fetchLinear("https://api.linear.app/graphql");\n',
                ),
                (
                    "node-member-assigned-request",
                    "direct-linear-endpoint",
                    "skills/linear-tracker-adapter/member-request.js",
                    'const https = require("node:https");\n'
                    "const dispatchLinear = https.request;\n"
                    'dispatchLinear("https://api.linear.app/graphql");\n',
                ),
                (
                    "node-member-assigned-get",
                    "direct-linear-endpoint",
                    "skills/linear-tracker-adapter/member-get.mjs",
                    'import https from "node:https";\n'
                    "const fetchLinear = https.get;\n"
                    'fetchLinear("https://api.linear.app/graphql");\n',
                ),
                (
                    "node-combined-import",
                    "direct-linear-endpoint",
                    "skills/linear-tracker-adapter/combined-import.mjs",
                    'import https, { request as issueLinear } from "node:https";\n'
                    'issueLinear("https://api.linear.app/graphql");\n',
                ),
                (
                    "linear-sdk-import",
                    "linear-sdk",
                    "skills/linear-tracker-adapter/transport.ts",
                    'import { LinearClient } from "@linear/sdk";\nnew LinearClient();\n',
                ),
                (
                    "shell-command",
                    "command-transport",
                    "skills/linear-tracker-adapter/transport.sh",
                    "curl https://example.invalid/provider\n",
                ),
            )
            for case_name, category, relative, source in hostile:
                with self.subTest(case=case_name):
                    mutated = directory / f"mutated-{case_name}"
                    shutil.copytree(linear_candidate, mutated)
                    target = mutated / relative
                    target.write_text(source, encoding="utf-8")
                    with self.assertRaisesRegex(
                        runner.QualificationFailure,
                        category,
                    ):
                        runner.inspect_candidate_transport_boundary(
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
                BINDING,
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
                    BINDING,
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
                    BINDING,
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
                and not any(path.is_relative_to(ROOT / "docs" / kind) for kind in ("plans", "delivery"))
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
