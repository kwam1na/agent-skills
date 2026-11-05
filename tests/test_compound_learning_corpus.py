"""`compound-learning` says where a learning goes and what a note holds, and leaves machinery out."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = "skills/compound-learning/SKILL.md"
KERNEL = "skills/compound-delivery-kernel/SKILL.md"
BUG_CATEGORIES = (
    "build-errors",
    "test-failures",
    "runtime-errors",
    "performance-issues",
    "database-issues",
    "security-issues",
    "ui-bugs",
    "integration-issues",
    "logic-errors",
)
KNOWLEDGE_CATEGORIES = (
    "best-practices",
    "documentation-gaps",
    "workflow-issues",
    "developer-experience",
    "architecture-patterns",
    "design-patterns",
    "tooling-decisions",
    "conventions",
)
SHARED_FIELDS = ("title", "date", "category", "module", "problem_type", "severity", "tags")
BUG_FIELDS = ("symptoms", "root_cause", "resolution_type")
KNOWLEDGE_FIELDS = ("applies_when",)
BUG_SECTIONS = "Problem, Symptoms, What Didn't Work, Solution, Why This Works, Prevention, Related Issues"
KNOWLEDGE_SECTIONS = "Problem, Solution, Why This Matters, Prevention, Examples, Related"
# Notes written before their sections were checked, each owed its sections by an open follow-up item.
AWAITING_SECTIONS = frozenset({
    "delivery-posture-close-out-readers-share-bytes-2026-11-04.md",
    "delivery-posture-harness-read-from-the-base-2026-11-04.md",
    "fake-trackers-list-moves-as-the-tracker-does-2026-11-03.md",
    "prove-each-gate-condition-alone-2026-11-03.md",
    "stopping-a-recorded-pid-2026-11-03.md",
    "show-a-derived-list-without-storing-it-2026-11-03.md",
})
# The contract this workflow gave up, in the words it used and in others for the same thing.
OLD_CONTRACT = re.compile(
    r"storage path|document schema|knowledge format|writes? none|writes? nothing|writing durable state"
    r"|repository-owned handoff|never writes|does not write",
    re.IGNORECASE,
)
OLD_REASON = re.compile(r"remain overlays|caller-owned decision", re.IGNORECASE)
# What stays out as machinery.
MACHINERY = re.compile(
    r"subagent|sub-agent|session history|auto memory|auto-memory|compound:check|delivery_diff_fingerprint"
    r"|validate-frontmatter|frontmatter validator|lightweight mode|full mode|phase \d|refresh skill"
    r"|compound-refresh|`component`|rails",
    re.IGNORECASE,
)


def _headings(note: str) -> list[str]:
    """A note's second-level headings, in order, outside its frontmatter."""
    return re.findall(r"(?m)^## (.+?)\s*$", note.split("---\n", 2)[2])


def _text(relative: str = SKILL) -> str:
    return " ".join((ROOT / relative).read_text(encoding="utf-8").split())


def _section(heading: str) -> str:
    text = (ROOT / SKILL).read_text(encoding="utf-8")
    marker = f"\n## {heading}\n"
    start = text.index(marker) + len(marker)
    end = text.find("\n## ", start)
    return " ".join((text[start:] if end == -1 else text[start:end]).split())


def _track(section: str, name: str) -> str:
    start = section.index(f"**{name} track")
    end = section.find("**", section.index("**", start + 2) + 2)
    return section[start:] if end == -1 else section[start:end]


class CompoundLearningCorpusTests(unittest.TestCase):
    def test_the_note_has_a_home_and_the_repository_can_change_it(self) -> None:
        section = _section("Solution Notes")
        self.assertIn(
            "Write the note at `docs/solutions/<category>/<slug>-<YYYY-MM-DD>.md`, unless the repository's"
            " instructions declare another location or format, and create the category folder if it is missing",
            section,
        )

    def test_this_repositorys_notes_sit_in_their_categories_with_their_frontmatter(self) -> None:
        notes = sorted((ROOT / "docs/solutions").rglob("*.md"))
        self.assertTrue(notes)
        for note in notes:
            with self.subTest(note=note.name):
                self.assertEqual(note.parent.parent, ROOT / "docs/solutions")
                category = note.parent.name
                self.assertIn(category, (*BUG_CATEGORIES, *KNOWLEDGE_CATEGORIES))
                self.assertRegex(note.name, r"-\d{4}-\d{2}-\d{2}\.md$")
                text = note.read_text(encoding="utf-8")
                head = text.split("---\n")[1]
                self.assertIn(f"category: {category}\n", head)
                for field in SHARED_FIELDS:
                    self.assertRegex(head, rf"(?m)^{field}:")
                # `problem_type` is the category's singular form, such as `build_error` for `build-errors`.
                self.assertIn(f"problem_type: {category[:-1].replace('-', '_')}\n", head)
                bug = category in BUG_CATEGORIES
                for field in BUG_FIELDS if bug else KNOWLEDGE_FIELDS:
                    self.assertRegex(head, rf"(?m)^{field}:")
                if note.name in AWAITING_SECTIONS:
                    continue
                self.assertEqual(_headings(text), (BUG_SECTIONS if bug else KNOWLEDGE_SECTIONS).split(", "))

    def test_a_note_awaiting_its_sections_is_still_here_and_still_lacks_them(self) -> None:
        """The list only shrinks: a note brought up to format leaves it, so the sections check covers it again."""
        notes = {note.name: note for note in (ROOT / "docs/solutions").rglob("*.md")}
        for name in sorted(AWAITING_SECTIONS):
            with self.subTest(note=name):
                self.assertIn(name, notes)
                expected = BUG_SECTIONS if notes[name].parent.name in BUG_CATEGORIES else KNOWLEDGE_SECTIONS
                self.assertNotEqual(_headings(notes[name].read_text(encoding="utf-8")), expected.split(", "))

    def test_each_track_lists_exactly_its_categories(self) -> None:
        section = _section("Solution Notes")
        for name, categories, others in (
            ("Bug", BUG_CATEGORIES, KNOWLEDGE_CATEGORIES),
            ("Knowledge", KNOWLEDGE_CATEGORIES, BUG_CATEGORIES),
        ):
            track = _track(section, name)
            for category in categories:
                with self.subTest(track=name, category=category):
                    self.assertIn(f"`{category}`", track)
            for category in others:
                with self.subTest(track=name, absent=category):
                    self.assertNotIn(f"`{category}`", track)

    def test_each_track_names_its_frontmatter_and_its_sections_in_order(self) -> None:
        section = _section("Solution Notes")
        for field in SHARED_FIELDS:
            with self.subTest(field=field):
                self.assertIn(f"`{field}`", section)
        bug, knowledge = _track(section, "Bug"), _track(section, "Knowledge")
        for field in BUG_FIELDS:
            with self.subTest(track="bug", field=field):
                self.assertIn(f"`{field}`", bug)
                self.assertNotIn(f"`{field}`", knowledge)
        for field in KNOWLEDGE_FIELDS:
            with self.subTest(track="knowledge", field=field):
                self.assertIn(f"`{field}`", knowledge)
                self.assertNotIn(f"`{field}`", bug)
        self.assertIn(f"Sections, in order: {BUG_SECTIONS}.", bug)
        self.assertIn(f"Sections, in order: {KNOWLEDGE_SECTIONS}.", knowledge)

    def test_the_instructions_hold(self) -> None:
        text = _text()
        for phrase in (
            "Search `docs/solutions/` first",
            "If an existing note covers the same problem, root cause and solution, update it in place and add"
            " `last_updated`, because two notes on one problem drift",
            "A related note from another angle means a new note",
            "One file per learning",
            "Quote YAML array items that start with a reserved character",
            "Decide before the final review-and-merge loop, while the context is fresh",
            "A learning that changes how agents deliver across repositories goes to a skill",
            "A concrete missing sensor or tooling gap goes to a follow-up item",
            "Record no durable learning only when the change is local, obvious and unlikely to recur",
            "says, in one line, that `docs/solutions/` exists, how it is organised, and when it is relevant",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_a_learning_after_the_final_loop_has_a_path_and_none_lands_unreviewed(self) -> None:
        text = _text()
        for phrase in (
            "so a note is part of what gets reviewed",
            "Where the caller's sequence names compounding after review, that step confirms and records the decision",
            "A learning first seen there goes through review with its note while a round and an open change remain"
            " to carry it",
            "Where none does, such as at an exhausted review bound or at close-out, it goes to a follow-up item, which"
            " carries the note through a review of its own",
            "No note lands unreviewed",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_a_repositorys_declared_store_wins_throughout(self) -> None:
        text = _text()
        self.assertIn("A repository's own declared location and format win over the ones here", text)
        self.assertIn("Where the repository declares another store, read it for `docs/solutions/` throughout", text)

    def test_a_caller_limited_to_listed_files_hands_the_learning_on(self) -> None:
        self.assertIn(
            "A caller limited to listed files, such as a lane, hands the learning through its handoff to whoever owns"
            " the store rather than writing it",
            _text(),
        )

    def test_a_change_with_no_reusable_reason_has_an_outcome(self) -> None:
        text = _text()
        self.assertIn(
            "Record no durable learning only when the change is local, obvious and unlikely to recur, and return"
            " `not-required` with that reason",
            text,
        )
        self.assertIn("A change that is not all three has a reusable reason to name", text)

    def test_the_kernel_makes_the_timing_reachable(self) -> None:
        self.assertIn(
            "Decide the compounding response before the final review-and-merge loop, so a note is part of the"
            " reviewed candidate",
            _text(KERNEL),
        )

    def test_the_provenance_reason_states_the_new_contract(self) -> None:
        lock = json.loads((ROOT / "provenance.lock.json").read_text(encoding="utf-8"))
        entry = next(item for item in lock["entries"] if item["id"] == "compound-learning")
        reason = entry["modification"]["reason"]
        self.assertIsNone(OLD_REASON.search(reason))
        self.assertIn("unless a repository declares its own", reason)

    def test_the_search_comes_before_the_write(self) -> None:
        section = _section("Solution Notes")
        self.assertLess(section.index("Search `docs/solutions/` first"), section.index("Write the note at"))

    def test_the_agent_writes_the_note_and_the_event_records_the_decision_only(self) -> None:
        text = _text()
        self.assertIn("The agent following this workflow writes the note", text)
        self.assertIn("`compounding.recorded` records the decision only", text)

    def test_the_old_contract_is_gone(self) -> None:
        self.assertIsNone(OLD_CONTRACT.search(_text()))

    def test_tracker_and_transport_stay_the_repositorys(self) -> None:
        self.assertIn("This workflow does not choose a tracker or transport", _text())

    def test_the_machinery_stays_out(self) -> None:
        self.assertIsNone(MACHINERY.search(_text()))

    def test_the_kernel_offers_a_note_among_the_durable_responses(self) -> None:
        self.assertIn("save a solution note", _text(KERNEL))


if __name__ == "__main__":
    unittest.main()
