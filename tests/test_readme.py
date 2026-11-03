from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("bootstrap", "core", "jira", "linear", "run-delivery")


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def _section(heading: str) -> str:
    text = _readme()
    marker = f"\n## {heading}\n"
    start = text.index(marker) + len(marker)
    end = text.find("\n## ", start)
    return text[start:] if end == -1 else text[start:end]


def _checked_in_sets() -> set[str]:
    """Every release set with an archive under `qualifications/product/`, whichever came last."""
    sets = set()
    for archive in (ROOT / "qualifications/product").glob("*.zip"):
        for profile in PROFILES:
            if archive.stem.startswith(f"{profile}-"):
                sets.add(archive.stem[len(profile) + 1 :])
    return sets


class ReadmeLifecycleTests(unittest.TestCase):
    def test_named_qualification_artifacts_exist(self) -> None:
        readme = _readme()
        paths = set(re.findall(r"(?<![\w/])qualifications/[\w./-]+", readme))
        self.assertTrue(paths, "README must name its qualification evidence")
        for relative in sorted(paths):
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative).exists(), relative)

    def test_it_points_at_history(self) -> None:
        readme = _readme()
        self.assertIn("`qualifications/history/`", readme)
        self.assertIn("`qualifications/product/`", readme)

    def test_it_names_no_release_set(self) -> None:
        sets = _checked_in_sets()
        self.assertGreater(len(sets), 10)
        readme = _readme()
        for name in sorted(sets):
            with self.subTest(name=name):
                # A set called `run-delivery-v1` is not named by the release id `run-delivery-v1.2.3`.
                self.assertIsNone(re.search(re.escape(name) + r"(?!\.\d)", readme))

    def test_it_describes_no_current_set_and_no_live_run(self) -> None:
        readme = " ".join(_readme().split())
        for phrase in (
            "current product set",
            "current checked",
            "current workflow-only record",
            "--collector-binding",
            "--attestation",
            "auditReferences",
            "collector input",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, readme)

    def test_the_install_section_downloads_latest_and_verifies_the_signer(self) -> None:
        section = _section("Getting a release")
        self.assertRegex(section, r"gh release download --repo kwam1na/agent-skills\b")
        self.assertIn("--signer-workflow kwam1na/agent-skills/.github/workflows/release.yml", section)
        self.assertIn("gh attestation verify", section)
        self.assertIn("carry no attestation", " ".join(section.split()))

    def test_the_install_section_warns_about_releases_before_the_first_version_tag(self) -> None:
        section = " ".join(_section("Getting a release").split())
        for phrase in (
            "Releases before the first version tag, which is what latest resolves to until then, carry a 0.7.7"
            " runtime without the installed-data protocol",
            "an adopter whose installed runtime has `installed-data-proof/1` must not apply them",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)

    def test_public_lifecycle_guidance_covers_session_refresh_and_first_removal(self) -> None:
        readme = _readme()

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
