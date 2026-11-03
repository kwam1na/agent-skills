"""Every product release stays within the archive entry limit installed products enforce.

An installed product refuses to update to a release with more than
``MAX_ENTRIES`` archive entries, so a release over the limit could never be
installed by an existing adopter. A product release carries the profile's
workflow files, the release manifest and the runtime files.

The limit can be raised only through a stepping-stone release. An installed
product verifies an incoming release against its own limit, so a release that
raises the limit must itself fit the old limit, and adopters install it before
any release that exceeds the old limit.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from agent_skills import release
from agent_skills.release import MAX_ENTRIES, RUNTIME_FILES, build_release


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("bootstrap", "core", "delivery-views", "linear", "run-delivery")


def workflow_entries(root: Path, profile: str) -> int:
    """How many archive entries the profile's release holds without a runtime."""
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / "release.zip"
        build_release(root, archive, Path(temporary) / "release.json", release_id="entry-budget",
                      profile_id=profile)
        with zipfile.ZipFile(archive) as opened:
            return len(opened.infolist())


def product_entries(root: Path, profile: str) -> int:
    """How many archive entries the profile's product release would hold."""
    return workflow_entries(root, profile) + len(RUNTIME_FILES)


def budget_problem(profile: str, entries: int) -> str | None:
    if entries <= MAX_ENTRIES:
        return None
    return (
        f"the {profile} product would hold {entries} archive entries, {entries - MAX_ENTRIES} over the "
        f"{MAX_ENTRIES} an installed product accepts; raise the limit only through a stepping-stone "
        "release that itself fits the old limit and that adopters install before any release that "
        "exceeds it"
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
            "an installed product accepts; raise the limit only through a stepping-stone release that "
            "itself fits the old limit and that adopters install before any release that exceeds it",
        )
        self.assertIsNone(budget_problem("linear", MAX_ENTRIES))

    def test_a_product_is_its_workflow_entries_plus_every_runtime_file(self) -> None:
        # A product built from the tree shows the shape the budget models: its
        # runtime files are exactly the runtime set, and everything else is
        # the workflow release.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            view = release._release_view(ROOT, "linear")
            runtime_directory = directory / "runtime"
            runtime_directory.mkdir()
            for name in RUNTIME_FILES:
                (runtime_directory / name).write_bytes((ROOT / "product-runtime" / name).read_bytes())
            descriptor = runtime_directory / "runtime.json"
            stored = json.loads(descriptor.read_bytes())["workflowContentSha256"]
            descriptor.write_bytes(descriptor.read_bytes().replace(
                stored.encode(), release._path_digest(sorted(view), view).encode(),
            ))
            product = directory / "linear.zip"
            build_release(ROOT, product, directory / "linear.json", release_id="entry-budget",
                          profile_id="linear", runtime_directory=runtime_directory)
            with zipfile.ZipFile(product) as opened:
                names = [info.filename for info in opened.infolist()]
        runtime = [name for name in names if name.startswith("runtime/")]
        self.assertEqual({name.removeprefix("runtime/") for name in runtime}, set(RUNTIME_FILES))
        self.assertEqual(len(names), workflow_entries(ROOT, "linear") + len(RUNTIME_FILES))
        # The budget counts the same way for the current tree.
        for profile in PROFILES:
            with self.subTest(profile=profile):
                self.assertEqual(product_entries(ROOT, profile),
                                 workflow_entries(ROOT, profile) + len(RUNTIME_FILES))
        self.assertEqual(len(RUNTIME_FILES), 11)


if __name__ == "__main__":
    unittest.main()
