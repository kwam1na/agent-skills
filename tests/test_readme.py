from __future__ import annotations

import json
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


def _profile(name: str) -> dict:
    return json.loads((ROOT / "profiles" / f"{name}.json").read_text(encoding="utf-8"))


def _intro() -> str:
    """The README's overview, before its first second-level heading, whitespace-normalized."""
    text = _readme()
    return " ".join(text[: text.index("\n## ")].split())


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

    def test_the_install_section_downloads_latest_and_checks_the_digest(self) -> None:
        section = _section("Getting a release")
        self.assertRegex(section, r"gh release download --repo kwam1na/agent-skills\b")
        self.assertIn("shasum -a 256 run-delivery-*.zip", section)
        self.assertIn("Releases carry no build-provenance attestation", " ".join(section.split()))
        self.assertNotIn("--signer-workflow", section)

    def test_the_posture_link_reaches_its_section_of_the_guide(self) -> None:
        self.assertIn("docs/product-generation.md#declaring-a-delivery-posture", _readme())
        guide = (Path(__file__).resolve().parents[1] / "docs/product-generation.md").read_text(encoding="utf-8")
        self.assertIn("\n## Declaring a delivery posture\n", guide)

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



class ReadmeKnowledgeLibraryTests(unittest.TestCase):
    """The README describes the knowledge library as the profile descriptions do."""

    CORE_SENTENCE = (
        "Workflows may consult an optional, read-only knowledge library after the repository's own solution notes, "
        "and report that it was not consulted when none is configured."
    )

    def test_the_core_paragraph_carries_the_core_profiles_sentence(self) -> None:
        self.assertIn(self.CORE_SENTENCE, " ".join(_profile("core")["description"].split()))
        intro = _intro()
        self.assertIn(self.CORE_SENTENCE, intro)
        # It sits in the core paragraph, after the compounding decision and before bootstrap guidance.
        self.assertIn(
            "a compounding decision with a default home for solution notes. " + self.CORE_SENTENCE
            + " Bootstrap guidance is opt-in.",
            intro,
        )

    def test_the_run_delivery_paragraph_names_the_adapter_and_its_setup(self) -> None:
        intro = _intro()
        for phrase in (
            "It also ships `hive-knowledge-adapter`, which maps that knowledge library to the host's hive-mined "
            "server, configured by the repository's `.agents/knowledge-properties.json`; without that file, or on "
            "a host other than macOS, where hive-mined does not run, the library is not consulted.",
            "The adoption guide's [knowledge library section]"
            "(skills/run-delivery/references/adopting.md#a-knowledge-library-optional) sets it up.",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, intro)
        guide = (ROOT / "skills/run-delivery/references/adopting.md").read_text(encoding="utf-8")
        self.assertIn("\n### A knowledge library (optional)\n", guide)

    def test_the_archives_the_readme_says_ship_the_adapter_are_the_ones_that_do(self) -> None:
        intro = _intro()
        self.assertIn(
            "The `run-delivery`, Linear and Jira archives carry the adapter; `core`, `bootstrap` and "
            "`delivery-views` do not, so there the library is absent.",
            intro,
        )
        adapter = "skills/hive-knowledge-adapter/SKILL.md"
        for name, ships in (("run-delivery", True), ("linear", True), ("jira", True),
                            ("core", False), ("bootstrap", False), ("delivery-views", False)):
            with self.subTest(profile=name):
                self.assertEqual(adapter in _profile(name).get("expectedFiles", []), ships)


if __name__ == "__main__":
    unittest.main()
