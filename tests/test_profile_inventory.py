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
JIRA_SKILLS = {"create-jira-ticket", "execute-jira-ticket", "jira-tracker-adapter"}
TRACKER_SKILLS = {"linear": LINEAR_SKILLS, "jira": JIRA_SKILLS}


PROFILES = ("bootstrap", "core", "delivery-views", "jira", "linear", "run-delivery")
KNOWLEDGE_ADAPTER_FILES = (
    "skills/hive-knowledge-adapter/SKILL.md",
    "skills/hive-knowledge-adapter/references/adapter-contract.json",
)
# The read-only knowledge adapter rides in the run-delivery closure and nowhere else.
KNOWLEDGE_ADAPTER_PROFILES = {"jira", "linear", "run-delivery"}


class ProfileInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """Each profile's archive, built and verified once for the whole class."""
        holder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(holder.cleanup)
        cls.directory = Path(holder.name)
        cls.archives: dict[str, tuple[Path, Path]] = {}
        for profile in PROFILES:
            cls.archives[profile] = cls.build(profile, profile)
            verify_release(*cls.archives[profile], extract_to=cls.extracted(profile))

    @classmethod
    def build(cls, profile: str, stem: str) -> tuple[Path, Path]:
        archive, metadata = cls.directory / f"{stem}.zip", cls.directory / f"{stem}.release.json"
        build_release(ROOT, archive, metadata, release_id="profile-inventory-v1", profile_id=profile)
        return archive, metadata

    @classmethod
    def extracted(cls, profile: str) -> Path:
        return cls.directory / f"{profile}-standalone"

    def skills(self, profile: str) -> set[str]:
        return {path.name for path in (self.extracted(profile) / "skills").iterdir()}

    def test_tracker_skills_have_scenarios_for_both_hosts(self) -> None:
        inventory = json.loads((ROOT / "validation-inventory.json").read_text())
        for profile, skills in TRACKER_SKILLS.items():
            scenarios = [row for row in inventory["scenarios"] if row["profile"] == profile]
            self.assertEqual({row["skill"] for row in scenarios}, skills)
            for row in scenarios:
                with self.subTest(profile=profile, skill=row["skill"]):
                    self.assertEqual(set(row["hosts"]), {"codex", "claude-code"})

    def test_tracker_profiles_build_on_run_delivery_and_the_rest_stay_as_they_were(self) -> None:
        run_delivery = self.skills("run-delivery")
        self.assertIn("run-delivery", run_delivery)
        self.assertLessEqual(self.skills("delivery-views"), run_delivery)
        self.assertNotIn("run-delivery", self.skills("delivery-views"))
        for profile, tracker in TRACKER_SKILLS.items():
            with self.subTest(profile=profile):
                self.assertEqual(self.skills(profile), run_delivery | tracker)
        self.assertEqual(self.skills("bootstrap"), {"bootstrap-agent-harness"})

    def test_only_the_run_delivery_closure_ships_the_knowledge_adapter(self) -> None:
        for profile in PROFILES:
            for shipped in KNOWLEDGE_ADAPTER_FILES:
                with self.subTest(profile=profile, path=shipped):
                    self.assertEqual(
                        (self.extracted(profile) / shipped).is_file(), profile in KNOWLEDGE_ADAPTER_PROFILES
                    )

    def test_the_run_delivery_archive_is_deterministic(self) -> None:
        archive, metadata = self.build("run-delivery", "run-delivery-again")
        first, first_metadata = self.archives["run-delivery"]
        self.assertEqual(archive.read_bytes(), first.read_bytes())
        self.assertEqual(metadata.read_bytes(), first_metadata.read_bytes())

    def test_every_profile_archive_validates_with_its_own_inventory(self) -> None:
        for profile in PROFILES:
            with self.subTest(profile=profile):
                extracted = self.extracted(profile)
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
                if profile in TRACKER_SKILLS:
                    self.assertTrue(TRACKER_SKILLS[profile] <= {row["skill"] for row in inventory["scenarios"]})


if __name__ == "__main__":
    unittest.main()
