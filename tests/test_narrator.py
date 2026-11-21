from __future__ import annotations

import calendar
import contextlib
import copy
import datetime
import importlib.util
import io
import json
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

import agent_skills


ROOT = Path(__file__).resolve().parents[1]
# The product under test: this repository, or the extracted release candidate that
# qualification puts first on the path, so its shipped scripts are the ones exercised.
PRODUCT_ROOT = Path(agent_skills.__file__).resolve().parents[1]
SKILL = PRODUCT_ROOT / "skills/orchestrate-work"
SCRIPTS = SKILL / "scripts"
STORY_SCHEMA = SKILL / "references/story.schema.json"
REGISTRY = SKILL / "references/journal-kinds.json"
FIXTURES = ROOT / "tests/fixtures/narrator"
GOLDEN = FIXTURES / "last-delivery/headline.md"
ALTITUDES = ("headline", "story", "everything")


def _load(name: str):
    # Importing the skill's modules here must not leave bytecode beside them.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


narrator = _load("narrator")
state_tool = _load("delivery_state")


def fixture(name: str) -> tuple[list[dict], dict | None]:
    folder = FIXTURES / name
    journal = [json.loads(line) for line in (folder / "journal.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    state = folder / "state.json"
    return journal, json.loads(state.read_text(encoding="utf-8")) if state.is_file() else None


def plan_of(name: str) -> dict | None:
    plan = FIXTURES / name / "plan.json"
    return json.loads(plan.read_text(encoding="utf-8")) if plan.is_file() else None


def told(name: str) -> tuple[dict, list[dict]]:
    journal, state = fixture(name)
    return narrator.narrate(journal, state=state, plan=plan_of(name)), journal


def instant(entry: dict) -> float:
    """An entry's instant in seconds: its occurredAt when it has one, else its at."""
    text = entry.get("occurredAt") or entry["at"]
    whole, _, fraction = text[:-1].partition(".")
    return calendar.timegm(time.strptime(whole, "%Y-%m-%dT%H:%M:%S")) + (float("0." + fraction) if fraction else 0.0)


def by_kind(journal: list[dict], kind: str, subject: object = ...) -> list[int]:
    return [entry["id"] for entry in journal if entry["kind"] == kind and (subject is ... or entry["subject"] == subject)]


def beats_at(story: dict, altitude: str) -> list[dict]:
    return [beat for beat in story["beats"] if altitude in beat["altitudes"]]


def beat_of(story: dict, entry: int, altitude: str = "everything") -> dict:
    [beat] = [beat for beat in beats_at(story, altitude) if entry in beat["entries"] and beat["form"] != "thread"]
    return beat


def clock_time(text: str, first_day: str) -> str:
    """A story instant as the golden reads it, in UTC: "3:07 pm", with the day when it is not the first."""
    parsed = time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
    shown = time.strftime("%I:%M %p", parsed).lstrip("0").lower()
    return shown if text[:10] == first_day else shown + time.strftime(", %b %d", parsed).replace(" 0", " ")


def headline_markdown(story: dict) -> str:
    """The headline altitude of a story, as a reader who wasn't there reads it: the digest, then each chapter in turn
    with the beats a stakeholder reads, and each setback with when it opened and closed and what it cost."""
    first_day = story["digest"]["startedAt"][:10]
    threads = {thread["id"]: thread for thread in story["threads"]}
    beats = {beat["id"]: beat for beat in story["beats"]}
    digest = story["digest"]["words"]
    lines = [f"**{digest['head']}**" + (f" {digest['line']}" if digest["line"] else ""), ""]
    for chapter in story["chapters"]:
        words = chapter["words"]
        head = f"**{words['head']}** · {clock_time(chapter['at'], first_day)}."
        if chapter.get("pieceTitle"):
            head += f" {chapter['pieceTitle']}."
        lines.append(head + (f" {words['line']}" if words["line"] else ""))
        for standing in words["standing"]:
            lines.append(f"- {standing['text']}" + (f", until {clock_time(standing['until'], first_day)}." if standing["until"] else ""))
        for beat in (beats[name] for name in chapter["beats"]):
            if "headline" not in beat["altitudes"] or beat["kind"] == "candidate.merged":
                continue
            when = clock_time(beat["at"], first_day)
            if beat["form"] == "thread":
                thread = threads[beat["thread"]]
                when += (f" to {clock_time(thread['closedAt'], first_day)}, {narrator.journal_words.duration(thread['cost'])}"
                         if thread["state"] == "closed" else ", still standing")
            lines.append(f"- **{beat['words']['head']}** · {when}." + (f" {beat['words']['line']}" if beat["words"]["line"] else ""))
        lines.append("")
    return "\n".join(lines)


def raising(*_args, **_kwargs):
    raise AssertionError("the narrator read the clock")


class RaisingDatetime(datetime.datetime):
    now = classmethod(raising)
    utcnow = classmethod(raising)
    today = classmethod(raising)


@contextlib.contextmanager
def no_clock():
    with contextlib.ExitStack() as stack:
        for name in ("time", "time_ns", "monotonic", "monotonic_ns", "perf_counter", "localtime", "gmtime", "ctime", "asctime"):
            stack.enter_context(mock.patch.object(time, name, raising))
        stack.enter_context(mock.patch.object(datetime, "datetime", RaisingDatetime))
        yield


SCHEMA = Draft202012Validator(json.loads(STORY_SCHEMA.read_text(encoding="utf-8")))
NAMES = sorted(folder.name for folder in FIXTURES.iterdir() if (folder / "journal.jsonl").is_file())


class FixtureTests(unittest.TestCase):
    """What holds for every fixture: its journal is in the new shape, and its story is well formed."""

    def test_the_fixtures_are_the_scenarios_the_plan_names(self) -> None:
        self.assertEqual(NAMES, sorted([
            "last-delivery", "just-started", "standing-break", "overlapping-breaks", "uncaused-stop", "stop-then-handoff",
            "two-answers", "break-stops-a-piece", "failed-check-first", "recovered-merge", "bookkeeping-in-a-thread",
            "decision-on-a-break", "answered-bookkeeping", "requeued-stop", "batch-of-three", "bookkeeping-run",
            "thread-at-story", "never-landed", "v26-3670"]))

    def test_every_fixture_is_a_journal_of_facts_the_state_tool_accepts(self) -> None:
        for name in NAMES:
            with self.subTest(name=name):
                journal, _ = fixture(name)
                self.assertEqual(state_tool.journal_errors(journal), [])
                self.assertTrue(all("data" in entry and "summary" not in entry for entry in journal))

    def test_every_story_validates_against_its_schema(self) -> None:
        for name in NAMES:
            with self.subTest(name=name):
                story, _ = told(name)
                self.assertEqual([error.message for error in SCHEMA.iter_errors(story)], [])
                self.assertEqual(story["schemaVersion"], "story/1")

    def test_every_fixture_tells_the_same_story_with_the_clock_taken_away(self) -> None:
        for name in NAMES:
            with self.subTest(name=name):
                journal, state = fixture(name)
                expected = narrator.narrate(copy.deepcopy(journal), state=copy.deepcopy(state), plan=plan_of(name))
                with no_clock():
                    self.assertRaises(AssertionError, time.time)
                    self.assertRaises(AssertionError, datetime.datetime.now)
                    story = narrator.narrate(journal, state=state, plan=plan_of(name))
                self.assertEqual(story, expected)
                # Nothing it was given is changed.
                self.assertEqual((journal, state), fixture(name))

    def test_every_duration_runs_between_two_entries_and_an_open_record_has_none_to_now(self) -> None:
        for name in NAMES:
            with self.subTest(name=name):
                story, journal = told(name)
                entries = {entry["id"]: entry for entry in journal}
                for thread in story["threads"]:
                    if thread["state"] == "closed":
                        # It ends at its closing entry, or, for a stop journalled after its story's landing happened, at
                        # its own opening, so no cost runs backwards.
                        ended = max(instant(entries[thread["closing"]]), instant(entries[thread["opening"]]))
                        self.assertEqual(narrator._seconds(thread["closedAt"]), ended)
                        self.assertEqual(thread["cost"], round(ended - instant(entries[thread["opening"]])))
                    else:
                        self.assertIsNone(thread["closing"])
                        self.assertIsNone(thread["cost"])
                        self.assertIsNone(thread["closedAt"])
                digest = story["digest"]
                # Counted by the ledger: a row's cost covers its threads' spans, each overlap once.
                self.assertEqual(digest["setbacks"]["cost"], sum(row["cost"] for row in story["ledger"] if row["state"] == "closed"))
                for row in story["ledger"]:
                    costs = [thread["cost"] for thread in story["threads"] if thread["id"] in row["threads"]]
                    if row["state"] == "closed":
                        self.assertLessEqual(max(costs), row["cost"])
                        self.assertLessEqual(row["cost"], sum(costs))
                if story["state"] == "open":
                    self.assertIsNone(digest["closedAt"])
                    self.assertIsNone(digest["took"])
                else:
                    closing = entries[by_kind(journal, "delivery.closed")[-1]]
                    self.assertEqual(digest["took"], round(instant(closing) - instant(journal[0])))

    def test_every_beat_names_its_entries_and_every_entry_is_told_once_in_full(self) -> None:
        for name in NAMES:
            with self.subTest(name=name):
                story, journal = told(name)
                ids = [entry["id"] for entry in journal]
                everything = [entry for beat in beats_at(story, "everything") for entry in beat["entries"]]
                self.assertEqual(sorted(everything), ids)
                self.assertTrue(all(beat["entries"] for beat in story["beats"]))
                self.assertFalse([beat for beat in story["beats"] if beat["form"] == "thread" and "everything" in beat["altitudes"]])
                # Each chapter holds its entries, in order, and the chapters hold every entry once.
                self.assertEqual(sorted(entry for chapter in story["chapters"] for entry in chapter["entries"]), ids)
                for chapter in story["chapters"]:
                    self.assertEqual({entry for name_ in chapter["beats"] for entry in next(b for b in story["beats"] if b["id"] == name_)["entries"]
                                      if next(b for b in story["beats"] if b["id"] == name_)["form"] != "thread"}, set(chapter["entries"]))

    def test_every_entry_a_thread_holds_names_that_thread(self) -> None:
        for name in NAMES:
            with self.subTest(name=name):
                story, _ = told(name)
                held = [entry for thread in story["threads"] for entry in thread["entries"]]
                self.assertEqual(len(held), len(set(held)))
                for thread in story["threads"]:
                    for entry in thread["entries"]:
                        self.assertEqual(beat_of(story, entry)["thread"], thread["id"])
                    [beat] = [beat for beat in story["beats"] if beat["form"] == "thread" and beat["thread"] == thread["id"]]
                    self.assertEqual(beat["altitudes"], ["headline"])
                    self.assertEqual(beat["at"], thread["openedAt"])
                    self.assertTrue(set(beat["entries"]) <= set(thread["entries"]))
                    self.assertIn(thread["opening"], beat["entries"])
                for beat in story["beats"]:
                    if beat["thread"] is not None and beat["form"] != "thread":
                        self.assertTrue(set(beat["entries"]) <= set(next(t for t in story["threads"] if t["id"] == beat["thread"])["entries"]))

    def test_the_schema_refuses_a_story_with_a_missing_or_an_unknown_member(self) -> None:
        story, _ = told("standing-break")
        self.assertTrue(story["digest"]["setbacks"]["standing"])
        # A chapter's line for a setback opened earlier and ended inside it: B, stopped before A landed, ends at its own.
        journal = Journal()
        journal.started()
        journal.add(5, "candidate.stopped", {"reason": "interrupted", "detail": "interrupted"}, subject="B")
        journal.add(10, "candidate.merged", {"head": "c" * 40}, subject="A")
        journal.add(20, "candidate.merged", {"head": "d" * 40}, subject="B")
        carried = journal.told()
        self.assertTrue(carried["chapters"][2]["words"]["standing"])
        changes = []
        for name, reach in {"carried setback": lambda s: s["chapters"][2]["words"]["standing"][0]}.items():
            for change in (lambda s, reach=reach: reach(s).update(summary="A sentence"), lambda s, reach=reach: reach(s).pop(next(iter(reach(s))))):
                broken = copy.deepcopy(carried)
                change(broken)
                with self.subTest(change=name):
                    self.assertTrue(list(SCHEMA.iter_errors(broken)))
        objects = {"story": lambda s: s, "digest": lambda s: s["digest"], "digest words": lambda s: s["digest"]["words"],
                   "setbacks": lambda s: s["digest"]["setbacks"], "standing": lambda s: s["digest"]["setbacks"]["standing"][0],
                   "chapter": lambda s: s["chapters"][0], "chapter words": lambda s: s["chapters"][0]["words"],
                   "ledger row": lambda s: s["ledger"][0], "ledger row chapters": lambda s: s["ledger"][0]["chapters"],
                   "ledger row words": lambda s: s["ledger"][0]["words"],
                   "beat": lambda s: s["beats"][0], "beat words": lambda s: s["beats"][0]["words"], "thread": lambda s: s["threads"][0],
                   "thread words": lambda s: s["threads"][0]["words"], "thread parts": lambda s: s["threads"][0]["told"]}
        changes = []
        for name, reach in objects.items():
            changes.append((f"{name} with an unknown member", lambda s, reach=reach: reach(s).update(summary="A sentence")))
            changes.append((f"{name} missing a member", lambda s, reach=reach: reach(s).pop(next(iter(reach(s))))))
        changes += [("a beat of an unknown form", lambda s: s["beats"][0].update(form="card")),
                    ("an unknown altitude", lambda s: s["beats"][0].update(altitudes=["headlines"])),
                    ("a thread of an unknown state", lambda s: s["threads"][0].update(state="stuck")),
                    ("a thread ended by something unknown", lambda s: s["threads"][0].update(endedBy="the lead")),
                    ("a ledger row of an unknown state", lambda s: s["ledger"][0].update(state="stuck")),
                    ("a ledger row of no threads", lambda s: s["ledger"][0].update(threads=[])),
                    ("a chapter naming an unknown row", lambda s: s["chapters"][0]["setbacks"].append("t1")),
                    ("a cost in words", lambda s: s["threads"][0].update(cost="38m")),
                    ("a story of an unknown state", lambda s: s.update(state="paused"))]
        self.assertEqual(sum(1 for name, _ in changes if "missing" in name), len(objects))
        for name, change in changes:
            broken = copy.deepcopy(story)
            change(broken)
            with self.subTest(change=name):
                self.assertTrue(list(SCHEMA.iter_errors(broken)))


class ScenarioTests(unittest.TestCase):
    """Each narrator scenario of the plan, from its fixture."""

    def test_last_delivery_has_a_start_six_landings_and_a_close_and_two_setbacks_both_closed(self) -> None:
        story, journal = told("last-delivery")
        # The record's own pieces, by the ids it keeps, are kept beside it.
        expected = json.loads((FIXTURES / "last-delivery/expected.json").read_text(encoding="utf-8"))
        self.assertEqual([chapter["form"] for chapter in story["chapters"]], ["start"] + ["landing"] * 6 + ["finish"])
        self.assertEqual([chapter["subject"] for chapter in story["chapters"][1:-1]], expected["landings"])
        self.assertEqual([chapter["words"]["head"] for chapter in (story["chapters"][0], story["chapters"][-1])], ["Getting started", "Finished"])
        stop = by_kind(journal, "candidate.stopped", expected["stopped"])[0]
        brk = by_kind(journal, "base.broken")[0]
        self.assertEqual([(thread["opening"], thread["state"]) for thread in story["threads"]], [(stop, "closed"), (brk, "closed")])
        handed_again = [entry for entry in journal if entry["kind"] == "candidate.handed" and stop in entry.get("answers", [])]
        self.assertEqual(story["threads"][0]["closing"], handed_again[0]["id"])
        self.assertEqual(story["threads"][1]["closing"], by_kind(journal, "base.repaired")[0])
        self.assertEqual(story["threads"][1]["heldUp"], expected["heldUp"])
        digest = story["digest"]
        self.assertEqual((digest["landed"], digest["pieces"], digest["setbacks"]["closed"], digest["setbacks"]["standing"], digest["decisions"]),
                         (6, 6, 2, [], 6))
        self.assertEqual(digest["setbacks"]["cost"], story["threads"][0]["cost"] + story["threads"][1]["cost"])
        self.assertEqual(story["state"], "closed")

    def test_last_delivery_reads_as_its_golden_headline_story(self) -> None:
        story, _ = told("last-delivery")
        self.assertEqual(headline_markdown(story), GOLDEN.read_text(encoding="utf-8"))

    def test_a_setback_that_ended_inside_a_later_chapter_is_told_with_when_it_ended(self) -> None:
        story, journal = told("last-delivery")
        [stopped] = [thread for thread in story["threads"] if thread["opening"] == by_kind(journal, "candidate.stopped")[0]]
        [carried] = [chapter for chapter in story["chapters"] if stopped["id"] in chapter["standing"]]
        self.assertEqual(carried["subject"], journal[stopped["opening"] - 1]["subject"])
        self.assertEqual(carried["words"]["standing"], [{"thread": stopped["id"], "text": f"{carried['subject']} was stopped",
                                                          "until": stopped["closedAt"]}])
        self.assertLess(stopped["closedAt"], carried["at"])

    def test_finish_lines_are_told_by_their_labels_and_lines_met_together_are_one_beat(self) -> None:
        story, journal = told("last-delivery")
        met = by_kind(journal, "finish.met")
        [beat] = [beat for beat in story["beats"] if set(met) & set(beat["entries"])]
        self.assertEqual((beat["form"], beat["entries"], beat["altitudes"]), ("finish", met, list(ALTITUDES)))
        self.assertEqual(beat["words"], {"head": "Every finish line was met", "line": "Every story, Local suite, Integration and Main CI"})
        story, journal = told("never-landed")
        [single] = [beat for beat in story["beats"] if beat["entries"] == by_kind(journal, "finish.met")]
        self.assertEqual((single["form"], single["words"]), ("entry", {"head": "A finish line was met", "line": "Every story lands"}))
        # Two of three lines met at once, by their plan labels; an id is never shown.
        journal, state = fixture("never-landed")
        journal.insert(len(journal) - 1, {**journal[-2], "id": len(journal), "data": {"finish": "release"}})
        journal[-1]["id"] = len(journal)
        plan = {"finish": [{"id": "stories", "label": "Every story"}, {"id": "release", "label": "Released"}, {"id": "docs", "label": "Docs"}]}
        [both] = [beat for beat in narrator.narrate(journal, state=state, plan=plan)["beats"] if beat["form"] == "finish"]
        self.assertEqual(both["words"], {"head": "Two finish lines were met", "line": "Every story and Released"})
        # One step past "every": as many lines as the plan's with one the plan doesn't hold, or every line with one met twice.
        for finishes, plan_ in ((["stories", "release", "elsewhere"], plan),
                                (["stories", "release", "stories"], {"finish": plan["finish"][:2]})):
            with self.subTest(finishes=finishes):
                journal, state = fixture("never-landed")
                last = journal.pop()
                met = journal.pop()
                for finish in finishes:
                    journal.append({**met, "id": len(journal) + 1, "data": {"finish": finish}})
                journal.append({**last, "id": len(journal) + 1})
                [three] = [beat for beat in narrator.narrate(journal, state=state, plan=plan_)["beats"] if beat["form"] == "finish"]
                self.assertEqual(three["words"]["head"], "Three finish lines were met")

    def test_no_story_shows_a_finish_lines_id(self) -> None:
        for name in NAMES:
            with self.subTest(name=name):
                story, journal = told(name)
                ids = {entry["data"]["finish"] for entry in journal if entry["kind"] == "finish.met"}
                shown = json.dumps([beat["words"] for beat in story["beats"]] + [chapter["words"] for chapter in story["chapters"]])
                self.assertFalse([finish for finish in ids if re.search(rf"(?<![\w-]){re.escape(finish)}(?![\w-])", shown)])

    def test_just_started_is_one_chapter_and_nothing_has_landed(self) -> None:
        story, journal = told("just-started")
        self.assertEqual([(chapter["form"], chapter["entries"]) for chapter in story["chapters"]], [("start", [journal[0]["id"]])])
        self.assertEqual((story["digest"]["landed"], story["digest"]["pieces"]), (0, 2))
        self.assertEqual(story["digest"]["words"]["head"], "Nothing has landed yet.")
        self.assertEqual((story["state"], story["threads"]), ("open", []))

    def test_standing_break_is_an_open_thread_counted_as_standing_and_told_once_not_in_every_later_chapter(self) -> None:
        story, journal = told("standing-break")
        brk = journal[by_kind(journal, "base.broken")[0] - 1]
        [thread] = story["threads"]
        self.assertEqual((thread["state"], thread["openedAt"], thread["closing"], thread["cost"]), ("open", brk["at"], None, None))
        self.assertEqual(story["digest"]["setbacks"], {"closed": 0, "cost": 0, "standing": [{"thread": thread["id"], "openedAt": brk["at"]}]})
        [row] = story["ledger"]
        self.assertEqual((row["kind"], row["state"], row["since"], row["threads"], row["chapters"]),
                         ("base.broken", "open", brk["at"], [thread["id"]], {"opened": "c1", "ended": None}))
        # The ledger tells it once: only the chapter it opened in names it, and no later chapter repeats it.
        self.assertEqual([chapter["setbacks"] for chapter in story["chapters"]], [[row["id"]], [], [], []])
        self.assertEqual([chapter["standing"] for chapter in story["chapters"]], [[], [], [], []])
        self.assertEqual([chapter["words"]["standing"] for chapter in story["chapters"]], [[], [], [], []])
        self.assertEqual(story["chapters"][-1]["words"]["head"], "Finishing")
        self.assertIn("still stand", story["digest"]["words"]["line"])

    def test_overlapping_breaks_are_two_threads_each_with_only_its_own_entries(self) -> None:
        story, journal = told("overlapping-breaks")
        first, second = by_kind(journal, "base.broken")
        repair_second, repair_first = by_kind(journal, "base.repaired")
        chain = by_kind(journal, "check.started") + by_kind(journal, "check.failed") + by_kind(journal, "base.rerun.started") + by_kind(journal, "base.rerun.finished")
        self.assertEqual([(thread["opening"], thread["closing"], sorted(thread["entries"])) for thread in story["threads"]],
                         [(first, repair_first, sorted([first, repair_first])), (second, repair_second, sorted(chain + [second, repair_second]))])

    def test_uncaused_stop_opens_a_thread_alone(self) -> None:
        story, journal = told("uncaused-stop")
        [stop] = by_kind(journal, "candidate.stopped")
        self.assertEqual([(thread["opening"], thread["entries"], thread["cause"], thread["state"]) for thread in story["threads"]],
                         [(stop, [stop], [], "open")])

    def test_stop_then_handoff_closes_at_the_new_handoff(self) -> None:
        story, journal = told("stop-then-handoff")
        [stop] = by_kind(journal, "candidate.stopped")
        handed = by_kind(journal, "candidate.handed")[1]
        [thread] = story["threads"]
        entries = {entry["id"]: entry for entry in journal}
        self.assertEqual((thread["opening"], thread["closing"], thread["closedAt"]), (stop, handed, entries[handed]["at"]))
        self.assertEqual(thread["cost"], 30 * 60)
        self.assertEqual(thread["cause"], by_kind(journal, "check.failed"))

    def test_two_answers_close_at_the_first_and_cost_runs_to_it(self) -> None:
        story, journal = told("two-answers")
        [stop] = by_kind(journal, "candidate.stopped")
        first = by_kind(journal, "candidate.handed")[1]
        [requeued] = by_kind(journal, "handoff.requeued")
        [thread] = story["threads"]
        self.assertEqual((thread["closing"], thread["cost"]), (first, 10 * 60))
        self.assertIn(requeued, thread["entries"])

    def test_a_stop_a_break_caused_joins_the_break_and_its_handoff_closes_nothing(self) -> None:
        story, journal = told("break-stops-a-piece")
        [brk] = by_kind(journal, "base.broken")
        [stop] = by_kind(journal, "candidate.stopped")
        handed = by_kind(journal, "candidate.handed")[1]
        [repair] = by_kind(journal, "base.repaired")
        [thread] = story["threads"]
        self.assertEqual((thread["opening"], thread["closing"], thread["heldUp"]), (brk, repair, ["A"]))
        self.assertTrue({stop, handed} <= set(thread["entries"]))

    def test_a_failed_check_before_its_stop_is_held_by_the_thread_the_stop_opens(self) -> None:
        story, journal = told("failed-check-first")
        [stop] = by_kind(journal, "candidate.stopped")
        [failed] = by_kind(journal, "check.failed")
        [selected] = by_kind(journal, "check.selected")
        [thread] = story["threads"]
        entries = {entry["id"]: entry for entry in journal}
        self.assertEqual((thread["opening"], thread["openedAt"]), (stop, entries[stop]["at"]))
        self.assertIn(failed, thread["entries"])
        self.assertNotIn(selected, thread["entries"])
        self.assertEqual(thread["closing"], by_kind(journal, "candidate.handed")[1])

    def test_a_recovered_merge_is_placed_told_and_timed_by_when_it_happened(self) -> None:
        story, journal = told("recovered-merge")
        [merge] = [entry for entry in journal if entry["kind"] == "candidate.merged"]
        [assigned_b] = by_kind(journal, "lane.assigned", "B")
        landing = story["chapters"][1]
        self.assertEqual((landing["form"], landing["at"], landing["entries"]), ("landing", merge["occurredAt"], [merge["id"]]))
        self.assertIn(assigned_b, story["chapters"][2]["entries"])
        self.assertTrue(landing["words"]["line"].startswith("2h after it was picked up"))

    def test_bookkeeping_in_a_thread_is_told_only_in_full(self) -> None:
        story, journal = told("bookkeeping-in-a-thread")
        [owned] = by_kind(journal, "ownership.granted")
        [thread] = story["threads"]
        self.assertIn(owned, thread["entries"])
        self.assertEqual(beat_of(story, owned)["altitudes"], ["everything"])
        for altitude in ("headline", "story"):
            self.assertFalse([beat for beat in beats_at(story, altitude) if owned in beat["entries"]])

    def test_a_decision_on_a_break_is_its_own_beat_at_every_altitude(self) -> None:
        story, journal = told("decision-on-a-break")
        [decision] = by_kind(journal, "decision.recorded")
        [thread] = story["threads"]
        beat = beat_of(story, decision)
        self.assertEqual((beat["form"], beat["entries"], beat["altitudes"], beat["thread"]), ("entry", [decision], list(ALTITUDES), thread["id"]))
        self.assertEqual(beat["words"], {"head": "Held every landing until the main line is repaired",
                                         "line": "A piece checked against a broken main line proves nothing."})
        self.assertEqual(story["digest"]["decisions"], 1)

    def test_answered_bookkeeping_opens_no_thread(self) -> None:
        story, journal = told("answered-bookkeeping")
        self.assertEqual(story["threads"], [])
        self.assertTrue(all(beat["thread"] is None for beat in story["beats"]))

    def test_a_requeued_stop_opens_no_thread_and_reads_at_the_story_altitude(self) -> None:
        story, journal = told("requeued-stop")
        [stop] = by_kind(journal, "candidate.stopped")
        self.assertEqual(story["threads"], [])
        beat = beat_of(story, stop)
        self.assertEqual((beat["form"], beat["altitudes"], beat["words"]["head"]), ("entry", ["story", "everything"], "A went back in the queue"))

    def test_a_batch_of_three_is_one_beat_and_a_single_start_its_own(self) -> None:
        story, journal = told("batch-of-three")
        starts = by_kind(journal, "check.started")
        batch = beat_of(story, starts[0])
        self.assertEqual((batch["form"], batch["entries"], batch["altitudes"]), ("batch", starts[:3], ["story", "everything"]))
        self.assertEqual(batch["words"], {"head": "Checks started on three pieces together", "line": "In one batch: A, B and C"})
        single = beat_of(story, starts[3])
        self.assertEqual((single["form"], single["entries"]), ("entry", [starts[3]]))

    def test_a_run_of_bookkeeping_is_one_beat_split_by_what_comes_between(self) -> None:
        story, journal = told("bookkeeping-run")
        kept = [beat for beat in story["beats"] if beat["form"] == "bookkeeping"]
        granted = by_kind(journal, "ownership.granted")
        self.assertEqual([beat["entries"] for beat in kept],
                         [[granted[0], granted[1], by_kind(journal, "plan.updated")[0]], [by_kind(journal, "ownership.revoked")[0], granted[2]]])
        self.assertEqual([beat["altitudes"] for beat in kept], [["everything"], ["everything"]])
        self.assertEqual(kept[0]["words"], {"head": "Bookkeeping", "line": "Files given (2) and Plan rebuilt (1)"})
        self.assertEqual(beat_of(story, by_kind(journal, "lane.assigned")[0], "story")["form"], "entry")

    def test_at_the_story_altitude_each_entry_of_a_thread_is_its_own_beat_naming_it(self) -> None:
        story, journal = told("thread-at-story")
        [thread] = story["threads"]
        self.assertGreater(len(thread["entries"]), 4)
        for entry in thread["entries"]:
            beat = beat_of(story, entry, "story")
            self.assertEqual((beat["form"], beat["entries"], beat["thread"]), ("entry", [entry], thread["id"]))
            self.assertNotIn("headline", beat["altitudes"])

    def test_a_closed_delivery_says_in_its_last_chapter_which_piece_never_landed(self) -> None:
        story, _ = told("never-landed")
        last = story["chapters"][-1]
        self.assertEqual((last["form"], last["words"]["head"], last["notLanded"]), ("finish", "Finished", ["B"]))
        self.assertIn("B never landed.", last["words"]["line"])
        self.assertEqual(story["digest"]["words"]["head"], "One of two pieces landed in 45m.")


def rows_by(story: dict) -> dict:
    """The ledger's rows by (subject, reason)."""
    return {(row["subject"], row["reason"]): row for row in story["ledger"]}


class Journal:
    """A journal written inline, for a case no named scenario holds: each step at a minute after nine."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def add(self, minute: int, kind: str, data: dict | None = None, lane: str | None = "lane-1", subject: str | None = None,
            because: list[int] | None = None, answers: list[int] | None = None) -> int:
        entry = {"id": len(self.entries) + 1, "at": f"2026-11-16T{9 + minute // 60:02d}:{minute % 60:02d}:00Z", "kind": kind,
                 "lane": lane, "subject": subject, "data": data or {}}
        if because:
            entry["because"] = because
        if answers:
            entry["answers"] = answers
        self.entries.append(entry)
        return entry["id"]

    def started(self) -> int:
        return self.add(0, "record.started", {"base": {"ref": "main", "head": "a" * 40}}, lane=None)

    def failed(self, minute: int, subject: str, start: int) -> int:
        return self.add(minute, "check.failed", {"check": "tests.test_one", "timedOut": False, "failing": ["tests.test_one.Case.test_it"],
                                                 "more": 0, "log": "logs/one.log"}, subject=subject, because=[start])

    def stopped(self, minute: int, subject: str, because: list[int]) -> int:
        return self.add(minute, "candidate.stopped", {"reason": "check_failed", "detail": "Its checks failed."}, subject=subject, because=because)

    def handed(self, minute: int, subject: str, answers: list[int] | None = None) -> int:
        return self.add(minute, "candidate.handed", {"head": "b" * 40, "changeRequest": "https://example.test/pull/1", "rounds": 1,
                                                     "checks": ["tests.test_one"]}, subject=subject, answers=answers)

    def told(self, state: dict | None = None, plan: dict | None = None) -> dict:
        self.assertValid()
        return narrator.narrate(self.entries, state=state, plan=plan)

    def assertValid(self) -> None:
        errors = state_tool.journal_errors(self.entries)
        if errors:
            raise AssertionError(errors)


class BoundaryTests(unittest.TestCase):
    """Where a batch, a run of bookkeeping or a thread meets another, and the narrator's other inputs and words."""

    def assertWhole(self, story: dict, journal: Journal) -> None:
        self.assertEqual([error.message for error in SCHEMA.iter_errors(story)], [])
        told_once = sorted(entry for beat in beats_at(story, "everything") for entry in beat["entries"])
        self.assertEqual(told_once, [entry["id"] for entry in journal.entries])
        for thread in story["threads"]:
            for entry in thread["entries"]:
                self.assertEqual(beat_of(story, entry)["thread"], thread["id"])

    def test_bookkeeping_beside_a_thread_is_not_run_together_with_the_threads_own(self) -> None:
        journal = Journal()
        journal.started()
        start = journal.add(1, "check.started", subject="A")
        stop = journal.stopped(3, "A", [journal.failed(2, "A", start)])
        loose = journal.add(4, "plan.updated", {"updated": "2026-11-16T09:04:00Z", "changed": ["units"]}, lane=None)
        held = journal.add(5, "ownership.granted", {"path": "src/fix.py"}, subject="A", because=[stop])
        story = journal.told()
        self.assertWhole(story, journal)
        [thread] = story["threads"]
        self.assertEqual((beat_of(story, loose)["entries"], beat_of(story, loose)["thread"]), ([loose], None))
        self.assertEqual((beat_of(story, held)["entries"], beat_of(story, held)["thread"]), ([held], thread["id"]))

    def test_a_batch_stays_one_beat_when_a_member_fails_and_that_members_start_joins_its_thread(self) -> None:
        for failing in ("A", "B", "C"):
            with self.subTest(failing=failing):
                journal = Journal()
                journal.started()
                starts = {subject: journal.add(10, "check.started", {"batch": ["A", "B", "C"]}, subject=subject) for subject in "ABC"}
                journal.stopped(12, failing, [journal.failed(11, failing, starts[failing])])
                story = journal.told()
                self.assertWhole(story, journal)
                [thread] = story["threads"]
                together = beat_of(story, starts["A" if failing != "A" else "B"])
                self.assertEqual((together["form"], together["thread"]), ("batch", None))
                self.assertEqual(together["entries"], [starts[subject] for subject in "ABC" if subject != failing])
                self.assertEqual(together["words"], {"head": "Checks started on three pieces together", "line": "In one batch: A, B and C"})
                alone = beat_of(story, starts[failing])
                self.assertEqual((alone["entries"], alone["thread"]), ([starts[failing]], thread["id"]))

    def test_a_batch_run_again_is_a_beat_of_its_own_at_its_own_instant(self) -> None:
        journal = Journal()
        journal.started()
        first = [journal.add(10, "check.started", {"batch": ["A", "B"]}, subject=subject) for subject in "AB"]
        finished = journal.add(20, "check.finished", {"batch": ["A", "B"]}, subject="A", because=[first[0]])
        again = [journal.add(50, "check.started", {"batch": ["A", "B"]}, subject=subject) for subject in "AB"]
        story = journal.told()
        self.assertWhole(story, journal)
        self.assertEqual([beat["entries"] for beat in story["beats"] if beat["entries"][0] != 1], [first, [finished], again])
        self.assertEqual([beat["at"] for beat in story["beats"] if beat["form"] == "batch"], ["2026-11-16T09:10:00Z", "2026-11-16T09:50:00Z"])

    def test_a_batch_held_whole_by_one_thread_is_one_beat_naming_it(self) -> None:
        journal = Journal()
        journal.started()
        starts = [journal.add(10, "check.started", {"batch": ["A", "B", "C"]}, subject=subject) for subject in "ABC"]
        failed = journal.add(11, "check.failed", {"check": "tests.test_one", "timedOut": False, "failing": ["tests.test_one.Case.test_it"],
                                                  "more": 0, "log": "logs/one.log"}, subject="A", because=starts)
        stop = journal.stopped(12, "A", [failed])
        later = journal.add(20, "check.started", {"batch": ["A", "B", "C"]}, subject="A", because=[stop])
        story = journal.told()
        self.assertWhole(story, journal)
        [thread] = story["threads"]
        together = beat_of(story, starts[0])
        self.assertEqual((together["form"], together["entries"], together["thread"]), ("batch", starts, thread["id"]))
        self.assertEqual((beat_of(story, later)["entries"], beat_of(story, later)["thread"]), ([later], thread["id"]))

    def test_two_batches_side_by_side_are_two_beats(self) -> None:
        journal = Journal()
        journal.started()
        first = [journal.add(10, "check.started", {"batch": ["A", "B"]}, subject=subject) for subject in "AB"]
        second = [journal.add(11, "check.started", {"batch": ["C", "D"]}, subject=subject) for subject in "CD"]
        story = journal.told()
        self.assertEqual([beat["entries"] for beat in story["beats"] if beat["form"] == "batch"], [first, second])

    def test_a_stop_with_no_problem_behind_it_is_caused_by_what_it_names(self) -> None:
        journal = Journal()
        journal.started()
        start = journal.add(1, "check.started", subject="A")
        stop = journal.stopped(2, "A", [start])
        [thread] = journal.told()["threads"]
        self.assertEqual((thread["opening"], thread["cause"]), (stop, [start]))

    def test_a_blocker_opens_a_thread_its_resolution_closes(self) -> None:
        journal = Journal()
        journal.started()
        blocker = journal.add(5, "blocker.recorded", {"text": "The dev backend is down."}, lane=None)
        resolved = journal.add(25, "blocker.resolved", {"why": "It came back."}, lane=None, answers=[blocker])
        story = journal.told()
        self.assertEqual([(thread["opening"], thread["closing"], thread["cost"]) for thread in story["threads"]], [(blocker, resolved, 20 * 60)])
        self.assertEqual(story["digest"]["words"]["line"], "One setback cost 20m.")

    def test_a_rounds_start_is_a_story_beat_its_lenses_bookkeeping_and_its_reduction_opens_no_thread(self) -> None:
        journal = Journal()
        journal.started()
        journal.add(1, "lane.assigned", {"title": "Work A"}, subject="A")
        start = journal.add(10, "review.started", {"round": 2, "head": "b" * 40, "lenses": ["lens.one", "lens.two"],
                                                   "restart": False, "moved": 3, "why": "Fixes the open P1."}, subject="A")
        back = [journal.add(20, "review.lens.returned", {"round": 2, "lens": lens, "verdict": "approve",
                                                         "findings": {"P0": 0, "P1": 0, "P2": 0, "P3": 0}}, subject="A")
                for lens in ("lens.one", "lens.two")]
        reduced = journal.add(25, "review.round", {"round": 2, "startedAt": "2026-11-16T09:10:00Z", "finishedAt": "2026-11-16T09:25:00Z",
                                                   "verdicts": {"lens.one": "approve", "lens.two": "approve"},
                                                   "findings": {"P0": 0, "P1": 0, "P2": 0, "P3": 0}}, subject="A", answers=[start])
        story = journal.told()
        self.assertWhole(story, journal)
        self.assertEqual(story["threads"], [])
        beat = beat_of(story, start)
        self.assertEqual((beat["form"], beat["entries"], beat["altitudes"]), ("entry", [start], ["story", "everything"]))
        self.assertEqual(beat["words"], {"head": "Review round 2 started on A",
                                         "line": "At bbbbbbb, read by lens.one and lens.two, 3 commits since the last round. Fixes the open P1."})
        # The lenses' returns are one run of bookkeeping, told only in full; the reduced round is a beat of its own.
        returns = beat_of(story, back[0])
        self.assertEqual((returns["form"], returns["entries"], returns["altitudes"]), ("bookkeeping", back, ["everything"]))
        self.assertEqual(beat_of(story, reduced)["entries"], [reduced])

    def test_work_after_the_close_reopens_the_delivery(self) -> None:
        journal = Journal()
        journal.started()
        journal.add(5, "delivery.closed")
        journal.add(6, "lane.assigned", {"title": "Work A"}, subject="A")
        story = journal.told()
        self.assertEqual((story["state"], story["digest"]["closedAt"], story["digest"]["took"]), ("open", None, None))
        self.assertEqual(story["chapters"][-1]["words"]["head"], "Getting started")

    def test_a_piece_is_timed_from_its_first_pick_up_and_a_setback_closed_as_a_chapter_begins_does_not_stand(self) -> None:
        journal = Journal()
        journal.started()
        journal.add(1, "lane.assigned", {"title": "Work A"}, subject="A")
        journal.add(30, "lane.assigned", {"title": "Work A"}, lane="lane-2", subject="A")
        start = journal.add(31, "check.started", subject="B")
        stop = journal.stopped(40, "B", [journal.failed(35, "B", start)])
        journal.add(61, "candidate.merged", {"head": "c" * 40}, subject="A")
        journal.handed(61, "B", answers=[stop])
        journal.add(70, "candidate.merged", {"head": "d" * 40}, subject="B")
        story = journal.told()
        landing_a, landing_b = story["chapters"][1:3]
        self.assertEqual(landing_a["words"]["line"], "1h after it was picked up.")
        # Closed as its chapter begins: told there once, as ended, never as still standing.
        [thread] = story["threads"]
        self.assertEqual(landing_b["words"]["standing"], [{"thread": thread["id"], "text": "B was stopped", "until": thread["closedAt"]}])

    def test_a_setback_that_ends_as_its_carried_chapter_ends_is_told_as_ended(self) -> None:
        journal = Journal()
        journal.started()
        start = journal.add(1, "check.started", subject="B")
        stop = journal.stopped(10, "B", [journal.failed(5, "B", start)])
        journal.add(20, "candidate.merged", {"head": "c" * 40}, subject="A")
        journal.handed(25, "B", answers=[stop])
        journal.add(25, "candidate.merged", {"head": "d" * 40}, subject="C")
        story = journal.told()
        [thread] = story["threads"]
        self.assertEqual(story["chapters"][2]["words"]["standing"], [{"thread": thread["id"], "text": "B was stopped", "until": thread["closedAt"]}])

    def test_the_plan_adds_pieces_and_titles_and_leaves_out_what_it_removed(self) -> None:
        journal = Journal()
        journal.started()
        journal.add(1, "lane.assigned", {"title": "Work A"}, subject="A")
        journal.add(30, "candidate.merged", {"head": "c" * 40}, subject="A")
        plan = {"units": [{"subject": "A", "title": "Retry card payments once"}, {"subject": "C", "title": "Work C"},
                          {"subject": "D", "title": "Work D", "removed": True}]}
        story = journal.told(plan=plan)
        self.assertEqual((story["digest"]["landed"], story["digest"]["pieces"]), (1, 2))
        self.assertEqual(story["chapters"][1]["pieceTitle"], "Retry card payments once")
        self.assertEqual(story["digest"]["words"]["head"], "One of two pieces landed so far.")
        self.assertEqual(journal.told()["chapters"][1]["pieceTitle"], "Work A")

    def test_the_narrators_own_words_for_the_start_the_finish_and_the_digest(self) -> None:
        story, _ = told("just-started")
        self.assertEqual(story["digest"]["words"], {"head": "Nothing has landed yet.", "line": "No setbacks."})
        self.assertEqual(story["chapters"][0]["words"], {"head": "Getting started", "line": "", "standing": []})
        story, _ = told("never-landed")
        self.assertEqual(story["chapters"][-1]["words"], {"head": "Finished", "line": "Every finish line was met. B never landed.", "standing": []})
        self.assertEqual(story["chapters"][0]["words"]["line"], "Lane 1 picked up A and B.")
        story, _ = told("standing-break")
        self.assertEqual(story["chapters"][-1]["words"]["standing"], [])
        self.assertEqual(story["digest"]["words"], {"head": "Two of three pieces landed so far.", "line": "One setback still stands."})
        journal, state = fixture("never-landed")
        state["finish"].append({"id": "release", "met": False, "text": "It is released"})
        self.assertEqual(narrator.narrate(journal, state=state)["chapters"][-1]["words"]["line"],
                         "One of two finish lines were met. B never landed.")

    def test_a_record_the_story_cannot_be_told_from_is_refused(self) -> None:
        journal = Journal()
        journal.started()
        for change in (lambda entries: entries[0].update(kind="nothing.known"), lambda entries: entries[0].update(data=["not", "facts"]),
                       lambda entries: entries[0].update(at="yesterday"), lambda entries: entries[0].pop("id")):
            broken = copy.deepcopy(journal.entries)
            change(broken)
            with self.subTest(entry=json.dumps(broken[0])[:60]):
                with self.assertRaises(narrator.NarratorError):
                    narrator.narrate(broken)


class ReadingTests(unittest.TestCase):
    """What a view needs to draw a beat without wording it: each kind's explanation, a run of bookkeeping's own
    entries in their words, and a setback's parts, each a sentence of its own."""

    def test_each_beat_carries_its_kinds_explanation(self) -> None:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for name in NAMES:
            with self.subTest(name=name):
                story, journal = told(name)
                entries = {entry["id"]: entry for entry in journal}
                for beat in story["beats"]:
                    if beat["form"] == "bookkeeping":
                        self.assertIsNone(beat["explain"])
                    elif beat["form"] == "thread":
                        opening = next(t for t in story["threads"] if t["id"] == beat["thread"])["opening"]
                        self.assertEqual(beat["explain"], registry["kinds"][entries[opening]["kind"]]["explain"])
                    else:
                        self.assertEqual(beat["explain"], registry["kinds"][entries[beat["entries"][0]]["kind"]]["explain"])

    def test_a_run_of_bookkeeping_lists_its_entries_in_their_own_words(self) -> None:
        story, journal = told("bookkeeping-run")
        first = next(beat for beat in story["beats"] if beat["form"] == "bookkeeping")
        self.assertEqual(first["items"], [
            {"entry": first["entries"][0], "words": {"head": "src/a.py was given to Lane 1", "line": ""}},
            {"entry": first["entries"][1], "words": {"head": "src/b.py was given to Lane 2", "line": ""}},
            {"entry": first["entries"][2], "words": {"head": "The plan was rebuilt", "line": "Changed: units"}}])
        self.assertTrue(all(beat["items"] == [] for beat in story["beats"] if beat["form"] != "bookkeeping"))

    def test_a_setback_is_told_in_its_parts(self) -> None:
        story, _ = told("last-delivery")
        stopped, broke = story["threads"]
        self.assertEqual({key: stopped["told"][key] for key in ("cause", "heldUp")}, {"cause": "tests.test_orchestration_state failed.", "heldUp": None})
        self.assertEqual(stopped["told"]["said"], "The guide named a file the skill doesn't ship, and a test holds the guide to the files it names.")
        self.assertTrue(stopped["told"]["ending"].startswith("It ended when lane 2 handed off"))
        expected = json.loads((FIXTURES / "last-delivery/expected.json").read_text(encoding="utf-8"))
        self.assertEqual(broke["told"]["heldUp"], f"It held up {expected['heldUp'][0]}.")
        self.assertEqual(broke["told"]["ending"], "It ended when the main line was repaired.")
        [standing] = told("standing-break")[0]["threads"]
        self.assertEqual((standing["told"]["cause"], standing["told"]["ending"]), (None, "It still stands."))
        # With nothing before it, a setback's own line is why it happened: a blocker's is the whole of what blocked the work.
        self.assertTrue(standing["told"]["said"].startswith("The full run failed"))
        journal = [
            {"id": 1, "at": "2026-11-16T09:00:00Z", "kind": "record.started", "lane": None, "subject": None, "data": {"base": {"ref": "main", "head": "a" * 40}}},
            {"id": 2, "at": "2026-11-16T09:10:00Z", "kind": "blocker.recorded", "lane": "lane-1", "subject": "A", "data": {"text": "The staging database is down"}},
        ]
        [blocked] = narrator.narrate(journal)["threads"]
        self.assertEqual(blocked["told"], {"cause": None, "said": "The staging database is down.", "heldUp": None, "ending": "It still stands."})

    def test_the_story_says_what_each_tone_means_and_a_setback_is_named_as_its_sheet_is(self) -> None:
        story, _ = told("last-delivery")
        self.assertEqual(story["tones"], [{"tone": "landed", "word": "Landed"}, {"tone": "ready", "word": "Ready to check"},
                                          {"tone": "decision", "word": "Decision"}, {"tone": "problem", "word": "Setback"},
                                          {"tone": "note", "word": "Other update"}])
        words = {tone["tone"]: tone["word"] for tone in story["tones"]}
        self.assertTrue(all(words[beat["tone"]] == beat["label"] for beat in story["beats"] if beat["form"] == "thread"))

    def test_the_tones_words_are_the_registrys_and_a_changed_word_changes_the_story(self) -> None:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(registry["tones"], {"landed": "Landed", "ready": "Ready to check", "decision": "Decision",
                                             "problem": "Setback", "note": "Other update"})
        registry["tones"]["problem"] = "Hold-up"
        story = narrator.narrate(fixture("standing-break")[0], registry=registry)
        self.assertIn({"tone": "problem", "word": "Hold-up"}, story["tones"])
        self.assertTrue(all(beat["label"] == "Hold-up" for beat in story["beats"] if beat["form"] == "thread"))
        self.assertTrue(any(beat["form"] == "thread" for beat in story["beats"]))


class OlderShapeTests(unittest.TestCase):
    """A record whose entries carry a sentence, or no facts, is in the older shape: the narrator tells no story of it,
    as the journal itself refuses it, and a kind the registry doesn't list is refused as before."""

    def journal(self) -> list[dict]:
        at = lambda minute: f"2026-11-16T09:{minute:02d}:00Z"
        return [{"id": 1, "at": at(0), "kind": "finish.met", "lane": None, "subject": "stories", "data": {"finish": "stories"}},
                {"id": 2, "at": at(1), "kind": "candidate.merged", "lane": "lane-1", "subject": "A", "data": {"head": "b" * 40}}]

    def test_an_entry_with_a_sentence_or_without_facts_is_refused_as_the_older_shape(self) -> None:
        self.assertEqual(state_tool.journal_errors(self.journal()), [])
        narrator.narrate(self.journal())
        older = [{**self.journal()[0], "summary": "stories: Every story lands"},
                 {key: value for key, value in self.journal()[1].items() if key != "data"}]
        for index, entry in enumerate(older):
            journal = self.journal()
            journal[index] = entry
            with self.subTest(entry=entry):
                with self.assertRaises(narrator.NarratorError) as refused:
                    narrator.narrate(journal)
                self.assertIn("in the older journal shape", str(refused.exception))
                self.assertIn("in the older journal shape", " ".join(state_tool.journal_errors(journal)))

    def test_a_kind_the_registry_does_not_list_is_refused(self) -> None:
        journal = self.journal()
        journal[1]["kind"] = "review.evidence"
        with self.assertRaises(narrator.NarratorError) as refused:
            narrator.narrate(journal)
        self.assertIn("no kind named review.evidence", str(refused.exception))


class RegistryTests(unittest.TestCase):
    """The narrator takes what opens a thread from the registry, where a test may point it at a copy."""

    def test_a_kind_the_registry_says_opens_a_thread_opens_one(self) -> None:
        journal = [
            {"id": 1, "at": "2026-11-16T09:00:00Z", "kind": "record.started", "lane": None, "subject": None,
             "data": {"base": {"ref": "main", "head": "a" * 40}}},
            {"id": 2, "at": "2026-11-16T09:05:00Z", "kind": "serving.held", "lane": None, "subject": None, "data": {"why": "Setup is not done."}},
        ]
        self.assertEqual(narrator.narrate(journal)["threads"], [])
        planted = json.loads(REGISTRY.read_text(encoding="utf-8"))
        planted["kinds"]["serving.held"]["opensThread"] = True
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "journal-kinds.json"
            path.write_text(json.dumps(planted), encoding="utf-8")
            with mock.patch.object(narrator.journal_words, "REGISTRY_FILE", path):
                [thread] = narrator.narrate(journal)["threads"]
        self.assertEqual((thread["opening"], thread["state"]), (2, "open"))


class CommandTests(unittest.TestCase):
    """The command narrates the record document a renderer is given."""

    def test_the_command_prints_the_story_of_the_document_on_stdin(self) -> None:
        journal, state = fixture("stop-then-handoff")
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps({"state": state, "journal": journal}).encode("utf-8")), encoding="utf-8")
        stdout = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(stdout):
            self.assertEqual(narrator.main([]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), narrator.narrate(journal, state=state))

    def test_a_document_that_is_not_a_record_is_refused(self) -> None:
        for given in (b"[]", b"{", json.dumps({"journal": [{"id": 1}]}).encode()):
            stdin = io.TextIOWrapper(io.BytesIO(given), encoding="utf-8")
            stderr = io.StringIO()
            with self.subTest(given=given), mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stderr(stderr), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(narrator.main([]), 2)
                self.assertIn("narrator:", stderr.getvalue())


def _queue_tests():
    # Imported when a row runs, so the rows that read only fixtures never load the queue's harness.
    from tests import test_merge_queue
    return test_merge_queue


class QueueJournalTests(unittest.TestCase):
    """Journals the real queue and state tool write, built as the queue's own tests build them, narrate to one setback."""

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="narrator-queue-")).resolve()
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.mq = _queue_tests()

    def one_closed(self, journal: list[dict], opening: dict, closing: dict) -> None:
        """The story has one setback, opened and closed by these entries, costing the time between their instants."""
        def instant(entry: dict) -> datetime.datetime:
            return datetime.datetime.fromisoformat(entry.get("occurredAt") or entry["at"])

        story = narrator.narrate(journal)
        self.assertEqual([(thread["opening"], thread["closing"], thread["state"]) for thread in story["threads"]],
                         [(opening["id"], closing["id"], "closed")])
        cost = round((instant(closing) - instant(opening)).total_seconds())
        self.assertEqual(story["threads"][0]["cost"], cost)
        self.assertEqual(story["digest"]["setbacks"], {"closed": 1, "cost": cost, "standing": []})

    def test_a_stop_the_lane_answers_with_a_new_hand_off_is_one_setback_closed_at_the_hand_off(self) -> None:
        mq = self.mq
        case = mq.MergeQueueTestCase()
        adopter = mq.Adopter(self.directory, None, "normal")
        case.candidate(adopter, {"tests/test_own.txt": "FAIL\n"})
        case.assertStopped(case.run_queue(adopter, "feature"), "check_failed")
        fixed = adopter.lane_commit("feature", {"tests/test_own.txt": "pass\n"})
        mq.StoppedBaseRerunTests.hand_off_again(adopter, fixed)
        case.run_queue(adopter, "feature")
        self.assertEqual(adopter.item("feature")["stage"], "merged")
        journal = mq.state_tool.read_journal(adopter.state)
        [stop] = [entry for entry in journal if entry["kind"] == "candidate.stopped"]
        [handed] = [entry for entry in journal if entry["kind"] == "candidate.handed"]
        self.assertEqual(handed.get("answers"), [stop["id"]])
        self.one_closed(journal, stop, handed)

    def test_a_break_a_rerun_declared_then_its_repair_is_one_setback_closed_at_the_repair(self) -> None:
        mq = self.mq
        case = mq.BaseRepairTests()
        adopter = mq.Adopter(self.directory, {"postSync": []}, "normal")
        adopter.advance_base(case.BROKEN)
        case.break_with(adopter, "first")
        case.candidate(adopter, {"src/app.txt": "app fixed\n"}, name="repair")
        mq.state_tool.name_repair(adopter.state, "repair")
        case.serve(adopter)
        journal = mq.state_tool.read_journal(adopter.state)
        [finished] = [entry for entry in journal if entry["kind"] == "base.rerun.finished"]
        [broken] = [entry for entry in journal if entry["kind"] == "base.broken"]
        [repaired] = [entry for entry in journal if entry["kind"] == "base.repaired"]
        self.assertEqual((broken.get("because"), repaired.get("answers")), ([finished["id"]], [broken["id"]]))
        self.assertEqual(adopter.item("first")["stage"], "merged")
        self.one_closed(journal, broken, repaired)



class LedgerTests(unittest.TestCase):
    """A landing ends its own story's stops, and the ledger tells each setback once, grouped by story and reason."""

    def assertValid(self, story: dict) -> None:
        self.assertEqual([error.message for error in SCHEMA.iter_errors(story)], [])

    def stops(self, journal: Journal, minutes: list[int], subject: str = "A", reason: str = "interrupted") -> list[int]:
        return [journal.add(minute, "candidate.stopped", {"reason": reason, "detail": f"stopped: {reason}"}, subject=subject)
                for minute in minutes]

    def test_a_story_stopped_then_landed_has_its_row_ended_at_the_landing_and_nothing_stands(self) -> None:
        journal = Journal()
        journal.started()
        [stop] = self.stops(journal, [10])
        merged = journal.add(30, "candidate.merged", {"head": "c" * 40}, subject="A")
        story = journal.told()
        self.assertValid(story)
        [thread] = story["threads"]
        self.assertEqual((thread["state"], thread["closing"], thread["closedAt"], thread["cost"], thread["endedBy"]),
                         ("closed", merged, journal.entries[merged - 1]["at"], 20 * 60, "landing"))
        self.assertEqual(thread["told"]["ending"], "It ended when A landed.")
        [row] = story["ledger"]
        self.assertEqual((row["subject"], row["reason"], row["count"], row["state"], row["since"], row["endedAt"], row["endedBy"],
                          row["ending"], row["cost"]),
                         ("A", "interrupted", 1, "closed", None, journal.entries[merged - 1]["at"], merged, "landing", 20 * 60))
        self.assertEqual(row["words"], {"head": "A was stopped.", "line": "It ended when A landed."})
        self.assertEqual(story["digest"]["setbacks"]["standing"], [])
        self.assertNotIn("still stand", story["digest"]["words"]["line"])

    def test_a_stop_whose_story_has_not_landed_still_stands(self) -> None:
        journal = Journal()
        journal.started()
        [stop] = self.stops(journal, [10], subject="B")
        journal.add(30, "candidate.merged", {"head": "c" * 40}, subject="A")
        story = journal.told()
        [row] = story["ledger"]
        self.assertEqual((row["subject"], row["state"], row["since"], row["endedAt"], row["cost"]),
                         ("B", "open", journal.entries[stop - 1]["at"], None, None))
        self.assertEqual((row["waitingOn"], row["words"]), (None, {"head": "B was stopped.", "line": "It still stands."}))
        self.assertEqual(story["digest"]["words"]["line"], "One setback still stands.")
        # Who it waits on, where the record says: the lane the stopped piece is queued in.
        state = {"queue": [{"subject": "B", "lane": "lane-2"}], "lanes": [{"id": "lane-2", "waitingOn": "lead"}]}
        [row] = journal.told(state=state)["ledger"]
        self.assertEqual((row["waitingOn"], row["words"]["line"]), ("lead", "It still stands. It waits on lead."))
        # Only a standing row waits: once B lands, its row waits on no one, whatever the record still queues.
        journal.add(40, "candidate.merged", {"head": "e" * 40}, subject="B")
        [row] = journal.told(state=state)["ledger"]
        self.assertEqual((row["state"], row["waitingOn"]), ("closed", None))

    def test_six_stops_with_one_reason_are_one_row_and_two_reasons_two(self) -> None:
        journal = Journal()
        journal.started()
        repeated = self.stops(journal, [10, 11, 12, 13, 14, 15])
        [failed] = self.stops(journal, [16], reason="check_failed")
        story = journal.told()
        self.assertValid(story)
        rows = rows_by(story)
        self.assertEqual(sorted(rows), [("A", "check_failed"), ("A", "interrupted")])
        row = rows[("A", "interrupted")]
        self.assertEqual((row["count"], len(row["threads"]), row["firstOpenedAt"], row["lastOpenedAt"], row["state"]),
                         (6, 6, journal.entries[repeated[0] - 1]["at"], journal.entries[repeated[-1] - 1]["at"], "open"))
        self.assertEqual(rows[("A", "check_failed")]["count"], 1)
        self.assertEqual(row["words"]["head"], "A was stopped 6 times.")
        # A standing row stands since its oldest standing thread, and standing rows read newest first.
        self.assertEqual(row["since"], journal.entries[repeated[0] - 1]["at"])
        self.assertEqual([(entry["subject"], entry["reason"]) for entry in story["ledger"]], [("A", "check_failed"), ("A", "interrupted")])
        self.assertEqual(story["digest"]["words"]["line"], "Two setbacks still stand.")

    def test_a_stop_a_hand_off_answers_keeps_that_ending_and_cost_after_its_story_lands(self) -> None:
        journal = Journal()
        journal.started()
        start = journal.add(1, "check.started", subject="A")
        stop = journal.stopped(10, "A", [journal.failed(5, "A", start)])
        handed = journal.handed(25, "A", answers=[stop])
        journal.add(40, "candidate.merged", {"head": "c" * 40}, subject="A")
        story = journal.told()
        [thread] = story["threads"]
        self.assertEqual((thread["closing"], thread["cost"], thread["endedBy"]), (handed, 15 * 60, "answer"))
        [row] = story["ledger"]
        self.assertEqual((row["endedBy"], row["ending"], row["cost"]), (handed, "answer", 15 * 60))
        self.assertEqual(story["digest"]["words"]["line"], "One setback cost 15m.")

    def test_a_base_break_and_a_blocker_keep_their_own_endings_and_a_landing_ends_no_break(self) -> None:
        journal = Journal()
        journal.started()
        broke = journal.add(5, "base.broken", {"source": "queue", "reason": "check_failed"}, lane=None)
        blocker = journal.add(6, "blocker.recorded", {"text": "The dev backend is down."}, lane=None)
        resolved = journal.add(20, "blocker.resolved", {"why": "It came back."}, lane=None, answers=[blocker])
        journal.add(30, "candidate.merged", {"head": "c" * 40}, subject="A")
        journal.add(31, "candidate.merged", {"head": "d" * 40}, subject="B")
        story = journal.told()
        self.assertValid(story)
        by_opening = {thread["opening"]: thread for thread in story["threads"]}
        self.assertEqual((by_opening[broke]["state"], by_opening[broke]["endedBy"]), ("open", None))
        self.assertEqual((by_opening[blocker]["closing"], by_opening[blocker]["endedBy"]), (resolved, "answer"))
        self.assertEqual([row["state"] for row in story["ledger"]], ["open", "closed"])

    def test_a_landing_ends_no_blocker_on_its_own_story(self) -> None:
        journal = Journal()
        journal.started()
        blocker = journal.add(5, "blocker.recorded", {"text": "A's dev backend is down."}, lane=None, subject="A")
        self.stops(journal, [10])
        journal.add(30, "candidate.merged", {"head": "c" * 40}, subject="A")
        story = journal.told()
        self.assertValid(story)
        by_opening = {thread["opening"]: thread for thread in story["threads"]}
        self.assertEqual((by_opening[blocker]["state"], by_opening[blocker]["endedBy"], by_opening[blocker]["closing"]), ("open", None, None))
        rows = {row["kind"]: row for row in story["ledger"]}
        self.assertEqual((rows["blocker.recorded"]["state"], rows["candidate.stopped"]["state"]), ("open", "closed"))

    def test_a_row_costs_the_time_its_stops_held_together_counting_overlaps_once(self) -> None:
        # Two copies of one stop, open at once, then ended by the landing: 20m, not 35m.
        journal = Journal()
        journal.started()
        self.stops(journal, [10, 15])
        journal.add(30, "candidate.merged", {"head": "c" * 40}, subject="A")
        story = journal.told()
        [row] = story["ledger"]
        self.assertEqual((row["cost"], story["digest"]["setbacks"]["cost"]), (20 * 60, 20 * 60))
        self.assertEqual(story["digest"]["words"]["line"], "One setback cost 20m.")
        # Two that never overlap: their own spans, 2m and 5m, with the gap between them not counted.
        journal = Journal()
        journal.started()
        start = journal.add(1, "check.started", subject="B")
        first = journal.stopped(10, "B", [journal.failed(9, "B", start)])
        journal.handed(12, "B", answers=[first])
        again = journal.add(15, "check.started", subject="B")
        second = journal.stopped(20, "B", [journal.failed(19, "B", again)])
        journal.handed(25, "B", answers=[second])
        [row] = journal.told()["ledger"]
        self.assertEqual(row["cost"], 7 * 60)

    def test_a_row_ends_with_the_thread_that_ended_last(self) -> None:
        # Both of B's stops open before A lands. The later one a hand-off answers; the earlier one only B's landing ends,
        # later still, so the row ends with the thread that opened first.
        journal = Journal()
        journal.started()
        [late] = self.stops(journal, [3], subject="B", reason="check_failed")
        start = journal.add(4, "check.started", subject="B")
        first = journal.stopped(6, "B", [journal.failed(5, "B", start)])
        handed = journal.handed(8, "B", answers=[first])
        journal.add(10, "candidate.merged", {"head": "c" * 40}, subject="A")
        merged = journal.add(20, "candidate.merged", {"head": "d" * 40}, subject="B")
        story = journal.told()
        self.assertValid(story)
        [row] = story["ledger"]
        ended = next(thread for thread in story["threads"] if thread["opening"] == late)
        self.assertEqual((row["count"], row["endedAt"], row["endedBy"], row["ending"], row["chapters"]),
                         (2, journal.entries[merged - 1]["at"], merged, "landing", {"opened": "c1", "ended": "c3"}))
        self.assertNotEqual(row["endedBy"], handed)
        self.assertEqual(story["chapters"][2]["standing"], [ended["id"]])

    def test_a_row_ends_in_the_chapter_of_its_last_thread_not_its_first(self) -> None:
        # B's first stop is answered before A lands; its second, opened then too, stands until B lands after C.
        journal = Journal()
        journal.started()
        start = journal.add(1, "check.started", subject="B")
        first = journal.stopped(3, "B", [journal.failed(2, "B", start)])
        journal.handed(5, "B", answers=[first])
        [standing] = self.stops(journal, [7], subject="B", reason="check_failed")
        journal.add(10, "candidate.merged", {"head": "c" * 40}, subject="A")
        journal.add(15, "candidate.merged", {"head": "d" * 40}, subject="C")
        journal.add(20, "candidate.merged", {"head": "e" * 40}, subject="B")
        story = journal.told()
        [row] = story["ledger"]
        ended = next(thread for thread in story["threads"] if thread["opening"] == standing)
        self.assertEqual(row["chapters"], {"opened": "c1", "ended": "c4"})
        self.assertEqual([chapter["standing"] for chapter in story["chapters"]], [[], [], [], [ended["id"]]])

    def test_a_chapter_carries_the_thread_that_ended_last_though_a_later_one_opened_there(self) -> None:
        # B's first stop stands from before A's landing until B lands; a second, opened and answered after A landed,
        # is told by its own beat. The chapter where B lands carries the first, which stood across A's landing.
        journal = Journal()
        journal.started()
        [held] = self.stops(journal, [3], subject="B", reason="check_failed")
        journal.add(10, "candidate.merged", {"head": "c" * 40}, subject="A")
        start = journal.add(11, "check.started", subject="B")
        second = journal.stopped(13, "B", [journal.failed(12, "B", start)])
        journal.handed(15, "B", answers=[second])
        journal.add(20, "candidate.merged", {"head": "d" * 40}, subject="B")
        story = journal.told()
        self.assertValid(story)
        [row] = story["ledger"]
        first = next(thread for thread in story["threads"] if thread["opening"] == held)
        self.assertEqual(row["chapters"], {"opened": "c1", "ended": "c3"})
        self.assertEqual(story["chapters"][2]["standing"], [first["id"]])

    def test_a_landing_ends_only_its_own_storys_stops(self) -> None:
        journal = Journal()
        journal.started()
        self.stops(journal, [10], subject="A")
        self.stops(journal, [11], subject="B")
        journal.add(30, "candidate.merged", {"head": "c" * 40}, subject="A")
        rows = rows_by(journal.told())
        self.assertEqual((rows[("A", "interrupted")]["state"], rows[("B", "interrupted")]["state"]), ("closed", "open"))

    def test_a_stop_journalled_after_its_storys_landing_ends_there_at_no_cost(self) -> None:
        # A recovered merge happened before the queue journalled the stops it didn't know were stale.
        journal = Journal()
        journal.started()
        stop = self.stops(journal, [20])[0]
        merged = journal.add(25, "candidate.merged", {"head": "c" * 40}, subject="A")
        journal.entries[merged - 1]["occurredAt"] = "2026-11-16T09:15:00Z"
        story = journal.told()
        self.assertValid(story)
        [thread] = story["threads"]
        self.assertEqual((thread["closing"], thread["closedAt"], thread["cost"], thread["endedBy"]),
                         (merged, journal.entries[stop - 1]["at"], 0, "landing"))

    def test_a_no_change_settlement_ends_its_storys_stops_as_a_landing_does(self) -> None:
        journal = Journal()
        journal.started()
        self.stops(journal, [10])
        journal.add(20, "candidate.handed", {"head": "b" * 40, "checks": [], "noChange": True}, subject="A")
        settled = journal.add(30, "handoff.accepted", {"settled": True}, lane=None, subject="A")
        [row] = journal.told()["ledger"]
        self.assertEqual((row["state"], row["endedBy"], row["ending"], row["cost"]), ("closed", settled, "landing", 20 * 60))
        journal = Journal()
        journal.started()
        self.stops(journal, [10])
        journal.add(20, "candidate.handed", {"head": "b" * 40, "checks": [], "noChange": True}, subject="A")
        journal.add(30, "handoff.accepted", {"settled": False}, lane=None, subject="A")
        self.assertEqual(journal.told()["ledger"][0]["state"], "open")
        # Settled with no acceptance: the record holds the planned piece as settled, and its no-change hand-off lands it.
        journal = Journal()
        journal.started()
        self.stops(journal, [10])
        handed = journal.add(20, "candidate.handed", {"head": "b" * 40, "checks": [], "noChange": True}, subject="A")
        [row] = journal.told(state={"planned": [{"subject": "A", "stage": "settled"}]})["ledger"]
        self.assertEqual((row["state"], row["endedBy"], row["ending"], row["cost"]), ("closed", handed, "landing", 10 * 60))
        self.assertEqual(journal.told()["ledger"][0]["state"], "open")

    def test_a_chapter_lists_only_the_rows_that_opened_or_ended_in_it(self) -> None:
        journal = Journal()
        journal.started()
        self.stops(journal, [10], subject="B")
        journal.add(20, "candidate.merged", {"head": "c" * 40}, subject="A")
        journal.add(30, "candidate.merged", {"head": "d" * 40}, subject="C")
        self.stops(journal, [35], subject="D")
        journal.add(40, "candidate.merged", {"head": "e" * 40}, subject="B")
        journal.add(50, "candidate.merged", {"head": "f" * 40}, subject="E")
        story = journal.told()
        self.assertValid(story)
        rows = rows_by(story)
        b, d = rows[("B", "interrupted")], rows[("D", "interrupted")]
        self.assertEqual([chapter["setbacks"] for chapter in story["chapters"]], [[b["id"]], [], [], [d["id"], b["id"]], []])
        self.assertEqual((b["chapters"], d["chapters"]), ({"opened": "c1", "ended": "c4"}, {"opened": "c4", "ended": None}))
        # No chapter carries a setback that is still standing from an earlier one, and one ended inside it is told once.
        self.assertEqual([chapter["standing"] for chapter in story["chapters"]], [[], [], [], [b["threads"][-1]], []])
        self.assertEqual(story["chapters"][3]["words"]["standing"],
                         [{"thread": b["threads"][-1], "text": "B was stopped", "until": b["endedAt"]}])

    def test_a_row_whose_later_thread_opens_and_ends_in_a_later_chapter_is_told_there_once(self) -> None:
        # B is stopped and answered before A lands, then stopped and answered again before B lands: the second stop is
        # told in its own chapter by its own beat, and nothing is carried there as if it stood across A's landing.
        journal = Journal()
        journal.started()
        start = journal.add(1, "check.started", subject="B")
        first = journal.stopped(5, "B", [journal.failed(4, "B", start)])
        journal.handed(8, "B", answers=[first])
        journal.add(10, "candidate.merged", {"head": "c" * 40}, subject="A")
        again = journal.add(12, "check.started", subject="B")
        second = journal.stopped(15, "B", [journal.failed(14, "B", again)])
        journal.handed(18, "B", answers=[second])
        journal.add(20, "candidate.merged", {"head": "d" * 40}, subject="B")
        story = journal.told()
        self.assertValid(story)
        [row] = story["ledger"]
        self.assertEqual((row["count"], row["chapters"]), (2, {"opened": "c1", "ended": "c3"}))
        self.assertEqual([chapter["standing"] for chapter in story["chapters"]], [[], [], []])
        self.assertEqual([chapter["words"]["standing"] for chapter in story["chapters"]], [[], [], []])
        self.assertEqual([chapter["setbacks"] for chapter in story["chapters"]], [[row["id"]], [], [row["id"]]])

    def test_v26_3670s_own_record_has_its_stale_stops_ended_at_their_landings(self) -> None:
        story, journal = told("v26-3670")
        self.assertValid(story)
        rows = rows_by(story)
        stale = {subject: rows[(subject, "interrupted")] for subject in ("V26-3666", "V26-3667")}
        landed = {subject: by_kind(journal, "candidate.merged", subject)[0] for subject in stale}
        for subject, row in stale.items():
            with self.subTest(subject=subject):
                self.assertEqual((row["state"], row["ending"], row["endedBy"]), ("closed", "landing", landed[subject]))
                self.assertEqual(sorted(row["threads"]),
                                 sorted(thread["id"] for thread in story["threads"]
                                        if journal[thread["opening"] - 1]["subject"] == subject))
        self.assertEqual((stale["V26-3666"]["count"], stale["V26-3667"]["count"]), (5, 7))
        failed = rows[("V26-3668", "check_failed")]
        self.assertEqual((failed["state"], failed["ending"], failed["count"]), ("closed", "answer", 1))
        self.assertGreater(failed["cost"], 0)
        self.assertEqual(story["digest"]["setbacks"]["standing"], [])
        self.assertNotIn("still stand", story["digest"]["words"]["line"])
        self.assertEqual(sum(1 for row in story["ledger"] if row["state"] == "open"), 0)
        # Each row is named by at most two chapters, the one it opened in and the one it ended in, never ending before it
        # opened: a stale stop ends, clamped to its own opening, in its own chapter rather than its landing's.
        index = {chapter["id"]: number for number, chapter in enumerate(story["chapters"])}
        for row in story["ledger"]:
            named = [chapter["id"] for chapter in story["chapters"] if row["id"] in chapter["setbacks"]]
            self.assertEqual(sorted(named), sorted({row["chapters"]["opened"], row["chapters"]["ended"]} - {None}))
            with self.subTest(row=row["id"]):
                self.assertLessEqual(index[row["chapters"]["opened"]], index[row["chapters"]["ended"]])
                ended = story["chapters"][index[row["chapters"]["ended"]]]
                self.assertLessEqual(narrator._seconds(row["endedAt"]), narrator._seconds(ended["at"]))


if __name__ == "__main__":
    unittest.main()
