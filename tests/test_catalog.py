from __future__ import annotations

import copy
import unittest

from agent_skills.catalog import dependency_findings, resolve_profile


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


if __name__ == "__main__":
    unittest.main()
