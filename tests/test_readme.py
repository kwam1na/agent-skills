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
