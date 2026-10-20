"""Every product release stays within the archive entry limit installed products enforce.

An installed product refuses to update to a release with more than
``MAX_ENTRIES`` archive entries, so a release over the limit could never be
installed by an existing adopter. A product release carries the profile's
workflow files, the release manifest and the runtime files.
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from agent_skills import release
from agent_skills.release import MAX_ENTRIES, RUNTIME_FILES, build_release


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("bootstrap", "core", "linear")


def product_entries(root: Path, profile: str) -> int:
    """How many archive entries the profile's product release would hold."""
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / "release.zip"
        build_release(root, archive, Path(temporary) / "release.json", release_id="entry-budget",
                      profile_id=profile)
        with zipfile.ZipFile(archive) as opened:
            workflow_entries = len(opened.infolist())
    return workflow_entries + len(RUNTIME_FILES)


def budget_problem(profile: str, entries: int) -> str | None:
    if entries <= MAX_ENTRIES:
        return None
    return (
        f"the {profile} product would hold {entries} archive entries, {entries - MAX_ENTRIES} over the "
        f"{MAX_ENTRIES} an installed product accepts; consolidate files rather than raise the limit"
    )


class EntryBudgetTests(unittest.TestCase):
    def test_every_product_release_fits_the_installed_entry_limit(self) -> None:
        for profile in PROFILES:
            entries = product_entries(ROOT, profile)
            with self.subTest(profile=profile, entries=entries, remaining=MAX_ENTRIES - entries):
                self.assertIsNone(budget_problem(profile, entries))

    def test_the_budget_fails_with_the_overrun_named_when_a_change_goes_over(self) -> None:
        original = release._release_view
        baseline = product_entries(ROOT, "linear")
        extra = MAX_ENTRIES - baseline + 1

        def padded(root: Path, profile_id: str) -> dict[str, bytes]:
            files = original(root, profile_id)
            for index in range(extra):
                files[f"skills/padding/file-{index}.txt"] = b"x"
            return files

        with mock.patch.object(release, "_release_view", padded):
            entries = product_entries(ROOT, "linear")
        self.assertEqual(entries, MAX_ENTRIES + 1)
        self.assertEqual(
            budget_problem("linear", entries),
            f"the linear product would hold {MAX_ENTRIES + 1} archive entries, 1 over the {MAX_ENTRIES} "
            "an installed product accepts; consolidate files rather than raise the limit",
        )
        self.assertIsNone(budget_problem("linear", MAX_ENTRIES))

    def test_the_count_matches_a_product_built_with_its_runtime(self) -> None:
        # The checked product archives carry the runtime: their entry count is
        # what this budget models, workflow entries plus the runtime files.
        product = ROOT / "qualifications/product/linear-concurrent-delivery-v1.zip"
        with zipfile.ZipFile(product) as opened:
            names = [info.filename for info in opened.infolist()]
        runtime = {name.removeprefix("runtime/") for name in names if name.startswith("runtime/")}
        self.assertEqual(runtime, set(RUNTIME_FILES))
        self.assertLessEqual(len(names), MAX_ENTRIES)


if __name__ == "__main__":
    unittest.main()
