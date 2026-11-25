from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from agent_skills.catalog import dependency_findings, resolve_profile
from agent_skills.release import _release_view
from agent_skills.validate import sentence_findings, sentence_text

ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skills = {
            "core": {"dependencies": []},
            "worker": {
                "dependencies": [{"id": "core", "requirement": "required"}]
            },
        }
        self.profiles = {
            "core": {"includes": [], "members": ["core"]},
            "complete": {"includes": ["core"], "members": ["worker"]},
        }

    def test_profile_resolution_is_exact_and_sorted(self) -> None:
        self.assertEqual(
            resolve_profile("complete", self.profiles), ["core", "worker"]
        )

    def test_profile_include_cycle_rejects(self) -> None:
        profiles = copy.deepcopy(self.profiles)
        profiles["core"]["includes"] = ["complete"]
        with self.assertRaisesRegex(ValueError, "profile include cycle"):
            resolve_profile("complete", profiles)

    def test_dependency_failures_report_together(self) -> None:
        skills = copy.deepcopy(self.skills)
        skills["worker"]["dependencies"] = [
            {"id": "missing", "requirement": "required"},
            {"id": "worker", "requirement": "required"},
        ]
        findings = dependency_findings(skills, self.profiles)
        rules = {finding.rule_id for finding in findings}
        self.assertEqual(
            rules,
            {"dependency.cycle", "dependency.reference", "dependency.required_closure"},
        )


class SentenceListTests(unittest.TestCase):
    """A `degradedBehavior` or profile `description` kept as one sentence per line renders as one string."""

    def test_a_list_renders_joined_with_single_spaces_and_a_string_is_kept(self) -> None:
        self.assertEqual(sentence_text(["One sentence.", "Two sentences."]), "One sentence. Two sentences.")
        self.assertEqual(sentence_text("Already one string."), "Already one string.")

    def test_the_string_and_list_forms_pass_and_bad_ones_are_named(self) -> None:
        self.assertEqual(sentence_findings("A string."), [])
        self.assertEqual(sentence_findings(["A sentence.", "Another one."]), [])
        self.assertEqual(sentence_findings([]), ["sentence list must not be empty"])
        self.assertEqual(sentence_findings(7), ["must be a string or a list of sentences"])
        self.assertEqual(
            sentence_findings(["", "\tTab.", "Trailing. ", "Line\nbreak.", "Return\rhere.", 3]),
            ["sentence 0 must not be empty", "sentence 1 has leading or trailing whitespace",
             "sentence 2 has leading or trailing whitespace", "sentence 3 holds a line break",
             "sentence 4 holds a line break", "sentence 5 must be a string"],
        )

    def test_the_archive_carries_each_list_as_its_joined_string(self) -> None:
        source = {skill["id"]: skill for skill in
                  json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))["skills"]}
        files = _release_view(ROOT, "linear")
        archived = json.loads(files["catalog.json"])["skills"]
        self.assertGreater(len(archived), 10)
        for skill in archived:
            if "degradedBehavior" not in skill:
                continue
            with self.subTest(skill=skill["id"]):
                sentences = source[skill["id"]]["degradedBehavior"]
                self.assertIsInstance(sentences, list)
                self.assertEqual(skill["degradedBehavior"], " ".join(sentences))
        profiles = [name for name in files if name.startswith("profiles/")]
        self.assertGreater(len(profiles), 1)
        for name in profiles:
            with self.subTest(profile=name):
                sentences = json.loads((ROOT / name).read_text(encoding="utf-8"))["description"]
                self.assertIsInstance(sentences, list)
                self.assertEqual(json.loads(files[name])["description"], " ".join(sentences))


if __name__ == "__main__":
    unittest.main()
