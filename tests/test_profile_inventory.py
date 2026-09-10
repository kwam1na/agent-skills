from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_skills.release import build_release, verify_release


ROOT = Path(__file__).resolve().parents[1]
LINEAR_SKILLS = {"create-linear-ticket", "execute-linear-ticket", "linear-tracker-adapter"}


class ProfileInventoryTests(unittest.TestCase):
    def test_linear_skills_have_scenarios_for_both_hosts(self) -> None:
        inventory = json.loads((ROOT / "validation-inventory.json").read_text())
        scenarios = [row for row in inventory["scenarios"] if row["profile"] == "linear"]
        self.assertEqual({row["skill"] for row in scenarios}, LINEAR_SKILLS)
        for row in scenarios:
            with self.subTest(skill=row["skill"]):
                self.assertEqual(set(row["hosts"]), {"codex", "claude-code"})

    def test_every_profile_archive_validates_with_its_own_inventory(self) -> None:
        for profile in ("bootstrap", "core", "linear"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                archive = directory / "candidate.zip"
                metadata = directory / "candidate.release.json"
                extracted = directory / "standalone"
                build_release(ROOT, archive, metadata, release_id="profile-inventory-v1", profile_id=profile)
                verify_release(archive, metadata, extract_to=extracted)
                environment = dict(os.environ)
                environment.pop("PYTHONPATH", None)
                result = subprocess.run(
                    [sys.executable, "-B", "-m", "agent_skills.validate", "--root", "."],
                    cwd=extracted,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                inventory = json.loads((extracted / "validation-inventory.json").read_text())
                self.assertEqual(inventory["expectedScenarioCount"], len(inventory["scenarios"]))
                if profile == "linear":
                    self.assertTrue(LINEAR_SKILLS <= {row["skill"] for row in inventory["scenarios"]})
