"""A lane is always a session of its own, and a host that cannot provide one stops."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = "skills/orchestrate-work/SKILL.md"
GUIDE = "skills/orchestrate-work/references/orchestration-guide.md"
RULING = "never a subagent, and never the lead working in its own session"
STOP = "no lane starts"
OPERATOR_CHOICE = (
    "brings it to the operator, who may choose to deliver the tickets one at a time through `$execute-work`"
)
LENSES = "Subagents remain how a lane realizes its review lenses"
HOST_BLOCKER = "- a host that cannot start or message sessions, so no lane can start;"
OLD_FALLBACK = (
    "may also take tickets",
    "degraded mode",
    "lanes as subagents",
    "lane as a subagent",
    "a subagent or the lead itself",
    "lanes in sequence",
    "or in sequence",
    "undelivered messages in its own notes",
    "whatever the host offers for sessions",
)


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _document(relative: str) -> str:
    return _normalized((ROOT / relative).read_text(encoding="utf-8"))


def _degraded_behavior() -> str:
    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    entry = next(skill for skill in catalog["skills"] if skill["id"] == "orchestrate-work")
    return _normalized(entry["degradedBehavior"])


def _operator_list(text: str) -> str:
    start = text.index("The operator is brought in only for:")
    end = text.index(". ", text.index("standing direction settles it", start))
    return text[start:end]


class LaneRulingTests(unittest.TestCase):
    def test_the_skill_and_the_guide_hold_the_ruling(self) -> None:
        for relative in (SKILL, GUIDE):
            text = _document(relative)
            for phrase in (RULING, STOP, OPERATOR_CHOICE, LENSES):
                with self.subTest(path=relative, phrase=phrase):
                    self.assertIn(phrase, text)

    def test_the_host_blocker_is_on_both_operator_lists(self) -> None:
        for relative in (SKILL, GUIDE):
            with self.subTest(path=relative):
                self.assertIn(HOST_BLOCKER, _operator_list(_document(relative)))

    def test_the_lane_role_states_the_ruling(self) -> None:
        self.assertIn(f"**Lane.** A session of its own, always: {RULING}.", _document(SKILL))

    def test_the_guide_section_stops_rather_than_falling_back(self) -> None:
        text = _document(GUIDE)
        start = text.index("### Without a session capability")
        section = text[start:text.index("### Leases", start)]
        for phrase in (STOP, RULING, OPERATOR_CHOICE, LENSES, "`blocker.recorded`"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)

    def test_the_catalog_states_the_ruling(self) -> None:
        degraded = _degraded_behavior()
        self.assertIn(STOP, degraded)
        self.assertIn("one at a time through $execute-work", degraded)

    def test_the_old_fallback_is_gone(self) -> None:
        for name, text in ((SKILL, _document(SKILL)), (GUIDE, _document(GUIDE)), ("catalog", _degraded_behavior())):
            for phrase in OLD_FALLBACK:
                with self.subTest(path=name, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_a_lane_still_realizes_its_lenses_as_subagents(self) -> None:
        self.assertIn("Review with two lenses as subagents", _document(GUIDE))


if __name__ == "__main__":
    unittest.main()
