from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from jsonschema import Draft202012Validator

from agent_skills.release import build_release, verify_release
from agent_skills.validate import canonical_json, validate_corpus


ROOT = Path(__file__).resolve().parents[1]
LEGACY_REVISION = "342850073464a2c9b6198d32d11475f5fa5cbe53"
ORIGINS = json.loads((ROOT / "tests/fixtures/provenance/origins.json").read_text())
LEGACY_LOCK = json.loads((ROOT / "tests/fixtures/provenance/legacy-lock.json").read_text())


def migrate(document: dict) -> dict:
    result = copy.deepcopy(document)
    result["schemaVersion"] = "agent-skills-provenance/2"
    for entry in result["entries"]:
        entry["source"].setdefault("kind", "repository-source")
        if entry["source"]["kind"] == "repository-source":
            entry["source"].setdefault("repository", result["sourceBaseline"]["repository"])
    return result


class ProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "corpus"
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        self.path = self.root / "provenance.lock.json"
        self.original = json.loads(self.path.read_text())
        self.document = migrate(self.original)

    def findings(self) -> tuple:
        self.path.write_text(canonical_json(self.document), encoding="utf-8")
        return validate_corpus(self.root).findings

    def test_migration_preserves_existing_origins_and_outputs(self) -> None:
        migrated = migrate(LEGACY_LOCK)
        self.assertEqual(migrated["sourceBaseline"], LEGACY_LOCK["sourceBaseline"])
        live = {entry["id"]: entry for entry in self.document["entries"]}
        for before, after in zip(LEGACY_LOCK["entries"], migrated["entries"]):
            self.assertEqual(after["outputs"], before["outputs"])
            for key, value in before["source"].items():
                self.assertEqual(after["source"][key], value)
                self.assertEqual(live[before["id"]]["source"][key], value)
        self.assertEqual(self.findings(), ())

    def test_migration_preserves_already_versioned_independent_origins(self) -> None:
        self.assertEqual(migrate(self.original), self.original)
        self.assertEqual(migrate(self.document), self.document)

    def test_different_immutable_origins_are_independent(self) -> None:
        unchanged = copy.deepcopy(self.document["entries"][1:])
        self.document["entries"][0]["source"] = copy.deepcopy(ORIGINS["repository"])
        self.assertEqual(self.findings(), ())
        self.assertEqual(self.document["entries"][1:], unchanged)
        self.assertEqual(self.document["sourceBaseline"]["revision"], LEGACY_REVISION)

    def test_independent_origin_retains_output_binding_not_athena_derivation(self) -> None:
        entry = self.document["entries"][0]
        entry["owner"] = "fixture-owner"
        entry["source"] = copy.deepcopy(ORIGINS["independent"])
        entry["modification"] = {"status": "independently-authored", "reason": "Fixture only."}
        self.assertEqual(self.findings(), ())
        entry["outputs"][0]["sha256"] = "0" * 64
        self.assertIn("provenance.digest", {item.rule_id for item in self.findings()})

    def test_repository_source_requires_immutable_complete_origin(self) -> None:
        valid = copy.deepcopy(ORIGINS["repository"])
        mutations = [
            ("revision", value) for value in ("main", "HEAD", "v1.0.0", "abc123", "", None)
        ] + [(key, None) for key in ("repository", "path", "sha256")]
        mutations += [("path", value) for value in ("../escape", " ", ".")]
        mutations += [("sha256", "wrong"), ("repository", " ")]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                source = copy.deepcopy(valid)
                source[key] = value
                self.document["entries"][0]["source"] = source
                self.assertIn("provenance.coverage", {item.rule_id for item in self.findings()})

    def test_version_two_modification_shape_matches_schema(self) -> None:
        validator = Draft202012Validator(json.loads(
            (ROOT / "schemas/provenance.schema.json").read_text()))
        valid = copy.deepcopy(self.document)
        for kind in ("repository-source", "independent-origin"):
            index = next(i for i, entry in enumerate(valid["entries"])
                         if entry["source"]["kind"] == kind)
            status = valid["entries"][index]["modification"]["status"]
            for modification in (
                None, {}, [],
                {"status": status},
                {"status": status, "reason": " "},
                {"status": status, "reason": False},
                {"status": "invented", "reason": "Fixture"},
                {"status": [], "reason": "Fixture"},
                {"status": status, "reason": "Fixture", "extra": True},
            ):
                self.document = copy.deepcopy(valid)
                if modification is None:
                    del self.document["entries"][index]["modification"]
                else:
                    self.document["entries"][index]["modification"] = modification
                with self.subTest(kind=kind, modification=modification):
                    self.assertFalse(validator.is_valid(self.document))
                    self.assertIn("provenance.coverage", {item.rule_id for item in self.findings()})

    def test_schema_and_runtime_reject_newline_tainted_identities(self) -> None:
        schema = json.loads((ROOT / "schemas/provenance.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        valid = copy.deepcopy(self.document)
        self.assertTrue(validator.is_valid(valid))
        for location, field, rule in (
            ("source", "revision", "provenance.coverage"),
            ("source", "sha256", "provenance.coverage"),
            ("output", "sha256", "provenance.digest"),
        ):
            with self.subTest(location=location, field=field):
                self.document = copy.deepcopy(valid)
                entry = self.document["entries"][0]
                target = entry["source"] if location == "source" else entry["outputs"][0]
                target[field] += "\n"
                self.assertFalse(validator.is_valid(self.document))
                self.assertIn(rule, {item.rule_id for item in self.findings()})

    def test_legacy_schema_rejects_newline_tainted_source_digest(self) -> None:
        schema = json.loads((ROOT / "schemas/provenance.schema.json").read_text())
        validator = Draft202012Validator(schema)
        legacy = copy.deepcopy(LEGACY_LOCK)
        self.assertTrue(validator.is_valid(legacy))
        legacy["entries"][0]["source"]["sha256"] += "\n"
        self.assertFalse(validator.is_valid(legacy))

    def test_independent_origin_requires_rationale_and_matching_owner_approval(self) -> None:
        entry = self.document["entries"][0]
        entry["owner"] = "fixture-owner"
        entry["modification"] = {"status": "independently-authored", "reason": "Fixture only."}
        for source in (
            {"kind": "independent-origin"},
            {**ORIGINS["independent"], "rationale": " "},
            {**ORIGINS["independent"], "ownerApproval": {}},
            {**ORIGINS["independent"], "ownerApproval": {"owner": "other", "reference": "ref"}},
            {**ORIGINS["independent"], "ownerApproval": {"owner": "fixture-owner", "reference": " "}},
            {**ORIGINS["independent"], "revision": LEGACY_REVISION},
        ):
            with self.subTest(source=source):
                entry["source"] = source
                self.assertIn("provenance.coverage", {item.rule_id for item in self.findings()})

    def test_unknown_origin_fields_and_kinds_reject(self) -> None:
        for source in (
            {**ORIGINS["repository"], "mutableBranch": "main"},
            {**ORIGINS["repository"], "kind": "invented"},
            {key: value for key, value in ORIGINS["repository"].items() if key != "kind"},
        ):
            with self.subTest(source=source):
                self.document["entries"][0]["source"] = source
                self.assertIn("provenance.coverage", {item.rule_id for item in self.findings()})

    def test_origin_kind_cannot_contradict_authorship_or_omit_outputs(self) -> None:
        entry = self.document["entries"][0]
        entry["source"] = copy.deepcopy(ORIGINS["repository"])
        entry["modification"] = {"status": "independently-authored", "reason": "Contradiction fixture."}
        self.assertIn("provenance.coverage", {item.rule_id for item in self.findings()})
        entry["source"] = copy.deepcopy(ORIGINS["independent"])
        entry["owner"] = "fixture-owner"
        entry["outputs"] = []
        self.assertIn("provenance.coverage", {item.rule_id for item in self.findings()})

    def test_legacy_version_rejects_new_kind_and_changed_baseline(self) -> None:
        self.document["schemaVersion"] = "agent-skills-provenance/1"
        self.document["entries"][0]["source"] = copy.deepcopy(ORIGINS["repository"])
        self.assertTrue(self.findings())

    def test_legacy_release_metadata_verifies_and_extracts_after_migration(self) -> None:
        self.document["schemaVersion"] = "agent-skills-provenance/1"
        for entry in self.document["entries"]:
            if entry["source"].get("kind") == "independent-origin":
                # This is a constructed legacy-format compatibility fixture,
                # not a claim that newly authored production material existed
                # in the retained baseline archive.
                entry["source"] = {
                    "baselineSourceId": "synthetic-legacy-fixture",
                    "path": "fixture/SKILL.md",
                    "revision": LEGACY_REVISION,
                    "sha256": "0" * 64,
                }
                entry["modification"] = {
                    "status": "modified-derivative",
                    "reason": "Synthetic legacy-format compatibility fixture only.",
                }
            entry["source"].pop("kind", None)
            entry["source"].pop("repository", None)
        self.assertEqual(self.findings(), ())
        archive = Path(self.temporary.name) / "legacy.zip"
        metadata = Path(self.temporary.name) / "legacy.release.json"
        build_release(self.root, archive, metadata, release_id="legacy-provenance", profile_id="core")
        before = archive.read_bytes()
        verified = verify_release(archive, metadata, extract_to=Path(self.temporary.name) / "extracted")
        self.assertEqual(verified.release_id, "legacy-provenance")
        self.assertEqual(archive.read_bytes(), before)
        with zipfile.ZipFile(archive) as retained:
            self.assertEqual(json.loads(retained.read("provenance.lock.json"))["schemaVersion"], "agent-skills-provenance/1")


if __name__ == "__main__":
    unittest.main()
