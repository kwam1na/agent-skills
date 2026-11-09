from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_skills.validate import safe_relative_path, validate_corpus
from corpus_copy import SharedCorpus, copy_corpus
from quiet_git import quieten


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


MISSING = b"\0missing\0"


def tree_digest(root: Path) -> str:
    """The checkout's own files: what git tracks and what it would add, never what it ignores.

    Ignored paths hold live state (a running delivery's .orchestration/, environments), which other processes may
    write while a test runs, so they are never read. A listed path that is gone is recorded as missing.
    """
    listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root,
                            check=True, capture_output=True).stdout
    digest = hashlib.sha256()
    for relative in sorted(set(name.decode() for name in listed.split(b"\0") if name)):
        path = root / relative
        digest.update(relative.encode() + b"\0")
        digest.update(path.read_bytes() if path.is_file() else MISSING)
    return digest.hexdigest()


class CorpusValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        holder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(holder.cleanup)
        cls.corpus = SharedCorpus(Path(holder.name) / "corpus")

    def copied_corpus(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        """The class's one copy, as it first was, and a folder of this test's own beside it."""
        self.addCleanup(self.corpus.restored)
        return tempfile.TemporaryDirectory(), self.corpus.restored()

    def assert_stable_and_read_only(self, root: Path, validate=None) -> None:
        validate = validate or validate_corpus
        before = tree_digest(root)
        first = validate(root)
        second = validate(root)
        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual(first.findings, ())
        self.assertGreater(len(first.rules_checked), 0)
        self.assertGreater(len(first.scan_roots), 0)
        self.assertEqual(tree_digest(root), before, "validation wrote a file of the checkout")

    def test_valid_corpus_is_stable_and_read_only(self) -> None:
        self.assert_stable_and_read_only(ROOT)

    def test_structure_reference_and_dependency_findings_aggregate(self) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        skill = root / "skills" / "compound-delivery-kernel" / "SKILL.md"
        skill.write_text("# missing frontmatter\n[missing](references/nope.md)\n", encoding="utf-8")
        catalog_path = root / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["skills"][1]["dependencies"] = [
            {"id": "missing-skill", "requirement": "required"}
        ]
        write_json(catalog_path, catalog)

        findings = validate_corpus(root).findings
        rules = {finding.rule_id for finding in findings}
        self.assertTrue(
            {"skill.frontmatter", "skill.reference", "dependency.reference"} <= rules
        )

    def test_null_dependencies_report_without_crashing(self) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        catalog_path = root / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["skills"][1]["dependencies"] = None
        write_json(catalog_path, catalog)

        result = validate_corpus(root)
        self.assertIn("dependency.reference", {finding.rule_id for finding in result.findings})

    def test_missing_skill_deep_reference_and_future_schema_reject(self) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        missing = root / "skills" / "deliver-work" / "SKILL.md"
        missing.unlink()
        core = root / "skills" / "compound-delivery-kernel"
        (core / "references").mkdir()
        (core / "references" / "one.md").write_text("[two](two.md)\n", encoding="utf-8")
        (core / "references" / "two.md").write_text("[three](three.md)\n", encoding="utf-8")
        (core / "references" / "three.md").write_text("end\n", encoding="utf-8")
        skill = core / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\n[one](references/one.md)\n", encoding="utf-8")
        profile_path = root / "profiles" / "core.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["schemaVersion"] = "agent-skills-profile/2"
        write_json(profile_path, profile)

        rules = {finding.rule_id for finding in validate_corpus(root).findings}
        self.assertTrue({"skill.structure", "skill.reference", "schema.supported"} <= rules)

    def test_path_escape_absolute_case_collision_and_external_symlink_reject(self) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        catalog_path = root / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        duplicate = copy = dict(catalog["skills"][1])
        duplicate["id"] = "Compound-Delivery-Kernel"
        duplicate["path"] = "../outside/SKILL.md"
        catalog["skills"].append(duplicate)
        catalog["skills"][0]["path"] = "/absolute/SKILL.md"
        write_json(catalog_path, catalog)
        outside = Path(temporary.name) / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        os.symlink(outside, root / "skills" / "compound-delivery-kernel" / "escape.md")

        rules = {finding.rule_id for finding in validate_corpus(root).findings}
        self.assertTrue({"path.safe", "path.case_unique", "path.symlink_contained"} <= rules)

    def test_windows_drive_path_rejects_on_every_host(self) -> None:
        self.assertFalse(safe_relative_path("C:/escape.txt"))
        self.assertFalse(safe_relative_path("C:\\escape.txt"))

    def test_case_fold_profile_id_collision_rejects(self) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        source = root / "profiles" / "core.json"
        duplicate = json.loads(source.read_text(encoding="utf-8"))
        duplicate["id"] = "Core"
        write_json(root / "profiles" / "collision.json", duplicate)

        findings = validate_corpus(root).findings
        self.assertIn("path.case_unique", {finding.rule_id for finding in findings})

    def test_core_leakage_has_positive_and_negative_controls(self) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        skill = root / "skills" / "compound-delivery-kernel" / "SKILL.md"
        original = skill.read_text(encoding="utf-8")
        tracking_ticket = "".join(("V", "26", "-", "999"))
        skill.write_text(
            original + f"\nPrivate command: {tracking_ticket} and /Users/example/work.\n",
            encoding="utf-8",
        )
        rules = {finding.rule_id for finding in validate_corpus(root).findings}
        self.assertIn("core.leakage", rules)

        skill.write_text(
            original + "\nPublic standard: JSON Schema draft 2020-12.\n",
            encoding="utf-8",
        )
        self.assertNotIn(
            "core.leakage", {finding.rule_id for finding in validate_corpus(root).findings}
        )

    def test_each_core_leakage_class_rejects(self) -> None:
        tracking_ticket = "".join(("V", "26", "-", "999"))
        literals = (
            "Athena policy",
            tracking_ticket,
            "/Users/example/work",
            "https://athena-os.app/private",
            "https://linear.app/v26-labs/issue/example",
            "MCP transport",
            "HTTP request",
            "SDK client",
            "OAuth flow",
            "credential store",
            "api_key=abcdefgh",
            r"C:\Users\example\repo\SKILL.md",
        )
        for literal in literals:
            with self.subTest(literal=literal):
                temporary, root = self.copied_corpus()
                self.addCleanup(temporary.cleanup)
                skill = root / "skills" / "compound-delivery-kernel" / "SKILL.md"
                skill.write_text(skill.read_text(encoding="utf-8") + f"\n{literal}\n", encoding="utf-8")
                self.assertIn(
                    "core.leakage",
                    {finding.rule_id for finding in validate_corpus(root).findings},
                )

    def test_undeclared_core_host_extension_rejects(self) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        relative = "skills/compound-delivery-kernel/agents/unknown.yaml"
        extension = root / relative
        extension.parent.mkdir()
        extension.write_text("interface: unknown\n", encoding="utf-8")
        provenance_path = root / "provenance.lock.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        entry = next(item for item in provenance["entries"] if item["id"] == "compound-delivery-kernel")
        entry["outputs"].append({"path": relative, "sha256": hashlib.sha256(extension.read_bytes()).hexdigest()})
        entry["outputs"].sort(key=lambda item: item["path"])
        write_json(provenance_path, provenance)
        profile_path = root / "profiles" / "core.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["expectedFiles"].append(relative)
        profile["expectedFiles"].sort()
        write_json(profile_path, profile)

        self.assertIn(
            "core.leakage",
            {finding.rule_id for finding in validate_corpus(root).findings},
        )

    def test_escaping_skills_root_symlink_rejects(self) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        external = Path(temporary.name) / "external-skills"
        shutil.copytree(root / "skills", external)
        shutil.rmtree(root / "skills")
        os.symlink(external, root / "skills")

        findings = validate_corpus(root).findings
        self.assertIn("path.symlink_contained", {finding.rule_id for finding in findings})

    def test_empty_scan_root_and_disabled_rule_fail_anti_vacuity(self) -> None:
        for mutation in ("empty-root", "disabled-rule", "missing-scenario", "missing-fixture"):
            with self.subTest(mutation=mutation):
                temporary, root = self.copied_corpus()
                self.addCleanup(temporary.cleanup)
                inventory_path = root / "validation-inventory.json"
                inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
                if mutation == "empty-root":
                    inventory["scanRoots"] = []
                elif mutation == "disabled-rule":
                    inventory["rules"][0]["enabled"] = False
                elif mutation == "missing-scenario":
                    inventory["scenarios"] = []
                else:
                    (root / "tests" / "fixtures" / "invalid-corpora" / "coverage.json").unlink()
                write_json(inventory_path, inventory)
                rules = {finding.rule_id for finding in validate_corpus(root).findings}
                self.assertIn("inventory.anti_vacuity", rules)

    def test_scan_root_mapping_is_exact(self) -> None:
        for mutation in ("missing", "duplicate", "substituted"):
            with self.subTest(mutation=mutation):
                temporary, root = self.copied_corpus()
                self.addCleanup(temporary.cleanup)
                inventory_path = root / "validation-inventory.json"
                inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
                if mutation == "missing":
                    inventory["scanRoots"].pop()
                elif mutation == "duplicate":
                    inventory["scanRoots"].append(dict(inventory["scanRoots"][0]))
                else:
                    inventory["scanRoots"][0]["path"] = "README.md"
                write_json(inventory_path, inventory)
                rules = {finding.rule_id for finding in validate_corpus(root).findings}
                self.assertIn("inventory.anti_vacuity", rules)

    def test_missing_classification_and_invalid_distribution_reject(self) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        provenance_path = root / "provenance.lock.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance["entries"][0]["classification"] = ""
        provenance["entries"][1]["distribution"] = "internal-maybe"
        write_json(provenance_path, provenance)

        findings = validate_corpus(root).findings
        messages = {finding.message for finding in findings}
        self.assertIn("missing classification: bootstrap-agent-harness", messages)
        self.assertIn("invalid distribution: compound-delivery-kernel", messages)

    def test_malformed_document_shapes_fail_closed(self) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        catalog_path = root / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["skills"][0]["profiles"] = None
        write_json(catalog_path, catalog)
        provenance_path = root / "provenance.lock.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance["entries"][0]["source"] = "not-an-object"
        write_json(provenance_path, provenance)

        result = validate_corpus(root)
        self.assertGreater(len(result.findings), 0)
        self.assertIn("schema.supported", {finding.rule_id for finding in result.findings})

    def test_absent_or_malformed_tracker_properties_reject(self) -> None:
        # Named here rather than read from the validator, so narrowing the
        # validator's own tuples is red.
        mutations = [
            "absent",
            "unreadable",
            "malformed",
            "not-an-object",
            "no-declared-properties",
            "statuses-not-an-object",
            "operation-not-an-object",
            *(
                f"dropped-{name}"
                for name in ("project", "statuses", "team")
            ),
            *(
                f"emptied-{operation}"
                for operation in ("closeOrHandoff", "updateStatus")
            ),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                temporary, root = self.copied_corpus()
                self.addCleanup(temporary.cleanup)
                properties = root / ".agents" / "tracker-properties.json"
                self.assertEqual(validate_corpus(root).findings, ())
                if mutation == "absent":
                    properties.unlink()
                elif mutation == "unreadable":
                    # A read failure that is not FileNotFoundError, so the
                    # wider arm of the read's handler is exercised.
                    properties.unlink()
                    properties.mkdir()
                elif mutation == "malformed":
                    properties.write_text("{", encoding="utf-8")
                elif mutation == "not-an-object":
                    write_json(properties, ["yaegars"])
                else:
                    declared = json.loads(properties.read_text(encoding="utf-8"))
                    if mutation == "no-declared-properties":
                        declared = {}
                    elif mutation == "statuses-not-an-object":
                        declared["statuses"] = ["Done"]
                    elif mutation == "operation-not-an-object":
                        # A declared operation carrying a scalar rather than a
                        # role-to-status map, so the per-operation type guard is
                        # exercised where the outer guard on statuses passes.
                        declared["statuses"]["updateStatus"] = "Todo"
                    elif mutation.startswith("dropped-"):
                        declared.pop(mutation.removeprefix("dropped-"))
                    else:
                        declared["statuses"][mutation.removeprefix("emptied-")] = {}
                    write_json(properties, declared)

                findings = validate_corpus(root).findings
                self.assertEqual(
                    [finding.rule_id for finding in findings], ["tracker.contract"]
                )
                self.assertEqual(
                    findings[0].artifact, ".agents/tracker-properties.json"
                )

    def test_permission_denied_tracker_properties_rejects(self) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        properties = root / ".agents" / "tracker-properties.json"
        properties.chmod(0)
        try:
            try:
                properties.read_text(encoding="utf-8")
            except PermissionError:
                pass
            else:
                self.skipTest("this user can read a mode-000 file")

            findings = validate_corpus(root).findings
            self.assertEqual(
                [finding.rule_id for finding in findings], ["tracker.contract"]
            )
            self.assertEqual(
                findings[0].artifact, ".agents/tracker-properties.json"
            )
        finally:
            properties.chmod(0o600)

    def test_tracker_properties_separate_format_identity_from_consumed_values(self) -> None:
        # schemaVersion identifies the document, not a tracker operation input.
        # The remaining keys are operational properties; do not accumulate
        # unused defaults in the properties document.
        declared = json.loads(
            (ROOT / ".agents" / "tracker-properties.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(declared), {"project", "schemaVersion", "statuses", "team"}
        )
        self.assertEqual(declared["schemaVersion"], "agent-skills-tracker-properties/1")

    def test_release_corpus_without_repository_instructions_needs_no_properties(
        self,
    ) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        (root / ".agents" / "tracker-properties.json").unlink()
        (root / "AGENTS.md").unlink()

        self.assertEqual(validate_corpus(root).findings, ())


    def test_tracker_properties_are_validated_without_repository_instructions(self) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        (root / "AGENTS.md").unlink()
        properties = root / ".agents" / "tracker-properties.json"
        properties.write_text('{"project": "Example"}\n', encoding="utf-8")

        findings = validate_corpus(root).findings

        self.assertEqual([finding.rule_id for finding in findings], ["tracker.contract"])
        self.assertEqual(findings[0].artifact, ".agents/tracker-properties.json")
        self.assertIn("omit statuses, team", findings[0].message)

    def test_unreadable_tracker_properties_without_repository_instructions_reject(self) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        (root / "AGENTS.md").unlink()
        properties = root / ".agents" / "tracker-properties.json"
        properties.unlink()
        properties.mkdir()

        findings = validate_corpus(root).findings

        self.assertEqual([finding.rule_id for finding in findings], ["tracker.contract"])
        self.assertEqual(findings[0].artifact, ".agents/tracker-properties.json")

    def test_valid_tracker_properties_without_repository_instructions_pass(self) -> None:
        temporary, root = self.copied_corpus()
        self.addCleanup(temporary.cleanup)
        (root / "AGENTS.md").unlink()

        self.assertEqual(validate_corpus(root).findings, ())

class ReadOnlyCheckTests(unittest.TestCase):
    """The read-only check sees a write to the checkout's own files, and never reads the live state git ignores."""

    @classmethod
    def setUpClass(cls) -> None:
        holder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(holder.cleanup)
        cls.root = copy_corpus(Path(holder.name) / "checkout")
        subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(cls.root)], check=True, capture_output=True)
        quieten(cls.root)
        subprocess.run(["git", "add", "--all"], cwd=cls.root, check=True, capture_output=True)

    def check(self, write) -> None:
        """The read-only row's own check on the copy, with ``write`` run during each validation."""
        def validating(root: Path):
            write(root)
            return validate_corpus(root)
        CorpusValidationTests.assert_stable_and_read_only(self, self.root, validating)

    def written(self, relative: str, text: str) -> None:
        path = self.root / relative
        before = path.read_bytes() if path.exists() else None
        self.addCleanup(lambda: path.write_bytes(before) if before is not None else path.unlink(missing_ok=True))
        path.parent.mkdir(parents=True, exist_ok=True)
        counter = iter(range(1000))
        self.write = lambda root: (root / relative).write_text(f"{text} {next(counter)}\n", encoding="utf-8")

    def test_an_ignored_live_state_file_changing_during_validation_is_not_a_write(self) -> None:
        relative = ".orchestration/state/state.json"
        self.assertTrue(subprocess.run(["git", "check-ignore", "-q", relative], cwd=self.root).returncode == 0)
        self.written(relative, '{"version":')
        self.check(self.write)

    def test_validation_writing_a_tracked_file_fails_the_check(self) -> None:
        # Outside what validation reads, so the write itself is what the check must see.
        relative = "tests/quiet_git.py"
        self.assertIn(relative.encode(), subprocess.run(["git", "ls-files", "--cached", relative], cwd=self.root,
                                                         check=True, capture_output=True).stdout)
        original = (self.root / relative).read_text(encoding="utf-8")
        self.written(relative, original)
        with self.assertRaisesRegex(AssertionError, "validation wrote a file of the checkout"):
            self.check(self.write)

    def test_validation_adding_a_file_git_would_track_fails_the_check(self) -> None:
        relative = "tests/stray-validation-output.md"
        self.assertNotEqual(subprocess.run(["git", "check-ignore", "-q", relative], cwd=self.root).returncode, 0)
        self.written(relative, "# stray")
        with self.assertRaisesRegex(AssertionError, "validation wrote a file of the checkout"):
            self.check(self.write)

    def test_a_tracked_file_validation_removes_is_recorded_missing(self) -> None:
        relative = "skills/compound-delivery-kernel/SKILL.md"
        path = self.root / relative
        before = tree_digest(self.root)
        original = path.read_bytes()
        self.addCleanup(path.write_bytes, original)
        path.unlink()
        removed = tree_digest(self.root)
        self.assertNotEqual(removed, before)
        path.write_bytes(b"")
        self.assertNotEqual(tree_digest(self.root), removed, "a removed file reads apart from an emptied one")
        path.write_bytes(original)
        self.assertEqual(tree_digest(self.root), before)


if __name__ == "__main__":
    unittest.main()
