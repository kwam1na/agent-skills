from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    def test_only_a_set_tag_publishes(self) -> None:
        self.assertRegex(self.workflow, r'(?m)^on:\n  push:\n    tags: \["\*-v\[0-9\]\*"\]\n\n')
        self.assertNotIn("branches:", self.workflow)

    def test_the_tag_is_annotated_and_merged_before_anything_is_published(self) -> None:
        guards = ('test "$(git cat-file -t "refs/tags/$TAG")" = tag',
                  'git merge-base --is-ancestor "refs/tags/$TAG^{commit}" origin/main')
        publish = self.workflow.index("gh release create")
        for guard in guards:
            with self.subTest(guard=guard):
                self.assertLess(self.workflow.index(guard), publish)

    def test_every_archive_is_verified_and_an_existing_release_is_never_replaced(self) -> None:
        publish = self.workflow.index("gh release create")
        self.assertLess(self.workflow.index("scripts/build-release.py verify"), publish)
        self.assertLess(self.workflow.index('if gh release view "$TAG"'), publish)
        self.assertNotIn("--clobber", self.workflow)
        self.assertNotIn("gh release upload", self.workflow)
        self.assertNotIn("gh release delete", self.workflow)


if __name__ == "__main__":
    unittest.main()
