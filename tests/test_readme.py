from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReadmeLifecycleTests(unittest.TestCase):
    def test_named_qualification_artifacts_exist(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        paths = set(re.findall(r"(?<![\w/])qualifications/[\w./-]+", readme))
        self.assertTrue(paths, "README must name its qualification evidence")
        for relative in sorted(paths):
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative).exists(), relative)

    def test_the_current_linear_evidence_is_named_once_it_is_earned(self) -> None:
        # While the linear set is pending, its attestation and record don't exist yet, so the README can't name their
        # checked paths; once the set is earned it must, so the existence check above covers them again.
        from tests.qualification_pending_support import pending

        if pending("linear") is not None:
            self.skipTest(pending("linear"))
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in ("linear-lead-selected-checks-v1.attestation.json", "linear-lead-selected-checks-v1.json"):
            with self.subTest(name=name):
                self.assertIn(f"qualifications/product/{name}", readme)

    def test_public_lifecycle_guidance_covers_session_refresh_and_first_removal(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("start a fresh agent session", readme)
        self.assertIn("rollback --maintenance", readme)
        self.assertIn("remove --maintenance", readme)
        self.assertIn("PYTHONPATH=.agent-skills/current", readme)
        self.assertIn("python3 -B -m agent_skills.cli", readme)
        self.assertIn('$env:PYTHONPATH = ".agent-skills/current"', readme)
        self.assertIn("python -B -m agent_skills.cli", readme)
        self.assertIn("return a first installation to `absent`", readme)


if __name__ == "__main__":
    unittest.main()
