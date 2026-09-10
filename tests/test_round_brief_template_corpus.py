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
ROUND_STATEMENT = re.compile(
    r"^- `([^`]+)`: (.*?)(?=\n- `|\n\n)", re.MULTILINE | re.DOTALL
)

REQUIRED_SKILL_PHRASES = (
    "The brief is [the round brief template]"
    "(references/round-brief-template.md) filled in, not composed",
    "An executor realizing a lens fills the template's members rather than"
    " composing the brief's shape from this text",
)

SKILL_MEMBER_PHRASES = {
    "Binding tuple": "Obtain the trusted comparison context from the harness",
    "Round and bound": "The brief uses exactly one of these round statements",
    "Round kind and scope": (
        "The brief also states the round kind and its resulting scope"
    ),
    "Fewest-rounds mandate": "The brief carries the mandate",
    "Carry-forward entries": (
        "For a verification round the caller supplies each lens"
    ),
    "Candidate reference resolutions": (
        "The caller also supplies, for each of the two `candidateRef` values"
    ),
    "Charter": (
        "Its `Charter` member contains the selected charter verbatim inside the brief"
    ),
    "Result shape": (
        "`review-lens-result/1`: `outcome`, `findings`, `evidence`"
    ),
    "Observation binding and selected output": (
        "Every selected lens runs in its own clean checkout of the prepared"
        " candidate tree"
    ),
}

REQUIRED_MEMBERS = tuple(SKILL_MEMBER_PHRASES)
ROUND_STATEMENTS = ("ordinary counted", "grace", "reopened", "unbounded")

REQUIRED_TEMPLATE_PHRASES = (
    "Fill every member below",
    "`review-lens-result/1`",
    "`lateFindings`",
    "round k of N",
    "round N+1 with declared bound N",
    "reopened round k",
    "unbounded round k",
    "supplied verbatim",
)

MEMBER_BODY_PHRASES = {
    "Binding tuple": (
        "prepared candidate tree SHA",
        "Opaque references are reproduced exactly",
    ),
    "Round and bound": (
        "Use exactly one of these statements",
        "round N+1 with declared bound N",
    ),
    "Round kind and scope": (
        "assessment round",
        "verification round",
    ),
    "Fewest-rounds mandate": (
        "conscious effort to find everything the lens can in this round",
        "Round N, the last counted round",
    ),
    "Carry-forward entries": (
        "every prior round in which this lens was selected",
        "result-less state where the acquisition failed",
        "most recent result-carrying entry",
    ),
    "Candidate reference resolutions": (
        "For each of the two `candidateRef` values the delta spans",
        "matches each pair by exact text",
    ),
    "Charter": (
        "The selected lens's charter, supplied verbatim inside this brief",
    ),
    "Result shape": (
        "reviewed tree SHA and the successful start and end identity checks",
        "When this lens was realized by the host convention",
    ),
    "Observation binding and selected output": (
        "own clean checkout of the prepared candidate tree",
        "Repeat both checks after the review",
    ),
}


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.index(marker) + len(marker)
    end = text.find("\n## ", start)
    return text[start:] if end == -1 else text[start:end]


def _round_statements(text: str, heading: str) -> dict[str, str]:
    return {
        label: " ".join(body.split())
        for label, body in ROUND_STATEMENT.findall(_section(text, heading))
    }


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
        normalized_skill = " ".join(_read(SKILL).split())
        sentence = MEMBER_SENTENCE.search(normalized_skill + " ")
        self.assertIsNotNone(sentence, "the skill text names no template members")
        named = QUOTED_MEMBER.findall(sentence.group(1))
        self.assertTrue(named, "the member sentence quotes no member")
        self.assertEqual(len(named), len(set(named)), named)
        self.assertEqual(set(named), set(REQUIRED_MEMBERS))

        shipped = TEMPLATE_MEMBER.findall(_read(TEMPLATE))
        self.assertTrue(shipped, "the template declares no member heading")
        self.assertEqual(len(shipped), len(set(shipped)), shipped)
        self.assertEqual(set(shipped), set(REQUIRED_MEMBERS))

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

        for member, phrase in SKILL_MEMBER_PHRASES.items():
            with self.subTest(member=member, phrase=phrase):
                self.assertIn(phrase, normalized_skill)

    def test_round_statement_sets_match_in_both_directions(self) -> None:
        skill_statements = _round_statements(_read(SKILL), "The round brief")
        template_statements = _round_statements(_read(TEMPLATE), "Round and bound")
        self.assertEqual(tuple(skill_statements), ROUND_STATEMENTS)
        self.assertEqual(tuple(template_statements), ROUND_STATEMENTS)
        self.assertEqual(skill_statements, template_statements)

    def test_each_required_member_carries_its_normative_body(self) -> None:
        template = _read(TEMPLATE)
        matches = list(TEMPLATE_MEMBER.finditer(template))
        bodies: dict[str, str] = {}
        for index, match in enumerate(matches):
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(template)
            )
            bodies[match.group(1)] = " ".join(template[match.end() : end].split())

        self.assertEqual(set(MEMBER_BODY_PHRASES), set(REQUIRED_MEMBERS))
        for member, phrases in MEMBER_BODY_PHRASES.items():
            for phrase in phrases:
                with self.subTest(member=member, phrase=phrase):
                    self.assertIn(phrase, bodies[member])


if __name__ == "__main__":
    unittest.main()
