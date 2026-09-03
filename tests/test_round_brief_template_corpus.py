from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SKILL = "skills/obtain-review/SKILL.md"
TEMPLATE = "skills/obtain-review/references/round-brief-template.md"

MEMBER_SENTENCE = re.compile(r"its members are (.+?)\.\s")
QUOTED_MEMBER = re.compile(r"`([^`]+)`")
TEMPLATE_MEMBER = re.compile(r"^## (.+)$", re.MULTILINE)

REQUIRED_SKILL_PHRASES = (
    "The brief is [the round brief template]"
    "(references/round-brief-template.md) filled in, not composed",
    "An executor realizing a lens fills the template's members rather than"
    " composing the brief's shape from this text",
)

REQUIRED_TEMPLATE_PHRASES = (
    "Fill every member below",
    "`review-lens-result/1`",
    "`lateFindings`",
    "round k of N",
    "the grace round obtained at the bound",
    "the loop is unbounded",
    "supplied verbatim",
)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class RoundBriefTemplateCorpusTests(unittest.TestCase):
    """Pin the shipped round brief template against the skill text that names it."""

    def test_template_ships_beside_the_skill(self) -> None:
        self.assertTrue((ROOT / TEMPLATE).is_file(), TEMPLATE)

    def test_skill_text_points_at_the_template(self) -> None:
        normalized = " ".join(_read(SKILL).split())
        for phrase in REQUIRED_SKILL_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, normalized)

    def test_template_carries_its_declared_phrases(self) -> None:
        normalized = " ".join(_read(TEMPLATE).split())
        for phrase in REQUIRED_TEMPLATE_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, normalized)

    def test_member_sets_match_in_both_directions(self) -> None:
        sentence = MEMBER_SENTENCE.search(" ".join(_read(SKILL).split()) + " ")
        self.assertIsNotNone(sentence, "the skill text names no template members")
        named = QUOTED_MEMBER.findall(sentence.group(1))
        self.assertTrue(named, "the member sentence quotes no member")
        self.assertEqual(len(named), len(set(named)), named)

        shipped = TEMPLATE_MEMBER.findall(_read(TEMPLATE))
        self.assertTrue(shipped, "the template declares no member heading")
        self.assertEqual(len(shipped), len(set(shipped)), shipped)

        self.assertEqual(
            set(named) - set(shipped),
            set(),
            "the skill text names a member the template omits",
        )
        self.assertEqual(
            set(shipped) - set(named),
            set(),
            "the template ships a member the skill text does not name",
        )


if __name__ == "__main__":
    unittest.main()
