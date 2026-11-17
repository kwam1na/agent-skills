"""What everything waits on: the derivation, its sentences, and every view that prints them.

The waits are derived from the record alone, in-process, from the fixture record under ``tests/fixtures/waits/``
and copies changed one fact at a time. Each case proves the wait, and the event that frees it.
"""

from __future__ import annotations

import builtins
import copy
import datetime
import importlib.util
import io
import json
import os
import re
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import agent_skills
from tests.delivery_views_fixtures import config


ROOT = Path(__file__).resolve().parents[1]
# The product under test: this repository, or the extracted release candidate that qualification puts first on the
# path, so its shipped scripts are the ones exercised.
PRODUCT_ROOT = Path(agent_skills.__file__).resolve().parents[1]
SKILL = PRODUCT_ROOT / "skills/orchestrate-work"
SCRIPTS = SKILL / "scripts"
WORDS = SKILL / "references/wait-words.json"
FIXTURE = ROOT / "tests/fixtures/waits/record.json"


def _load(name: str, path: Path):
    # Importing the skill's modules here must not leave bytecode beside them.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(path.parent))
        sys.dont_write_bytecode = previous


state_tool = _load("delivery_state", SCRIPTS / "delivery_state.py")
render_state = _load("render_state", SCRIPTS / "render_state.py")
run_delivery = _load("run_delivery", PRODUCT_ROOT / "skills/run-delivery/scripts/run_delivery.py")
views = _load("delivery_views_render", PRODUCT_ROOT / "skills/delivery-views/scripts/render.py")


def record() -> dict:
    """A fresh copy of the fixture record: APP-1 in review in lane 2, holding skills/render.py; APP-2 planned after
    it in lane 1; APP-3 planned in lane 3, naming skills/render.py; APP-4 planned in lane 2. Lanes 1 and 3 are idle."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def planned(document: dict, subject: str) -> dict:
    return next(item for item in document["state"]["planned"] if item["subject"] == subject)


def merge(document: dict, subject: str) -> None:
    """The story leaves the planned work and lands through the queue, as the runner leaves it."""
    item = planned(document, subject)
    document["state"]["planned"].remove(item)
    document["state"]["queue"].append({
        "order": len(document["state"]["queue"]) + 1, "subject": subject, "title": item["title"], "lane": item["lane"],
        "stage": "merged", "head": "b" * 40, "syncedHead": None, "leadInputs": [], "reportedRounds": 2,
        "runId": None, "checks": [], **{name: item[name] for name in ("after", "files") if name in item}})


def queue(document: dict, subject: str, stage: str, **fields: object) -> None:
    item = planned(document, subject)
    document["state"]["planned"].remove(item)
    document["state"]["queue"].append({
        "order": len(document["state"]["queue"]) + 1, "subject": subject, "title": item["title"], "lane": item["lane"],
        "stage": stage, "head": "b" * 40, "syncedHead": None, "leadInputs": [], "reportedRounds": 2,
        "runId": None, "checks": [], **{name: item[name] for name in ("after", "files") if name in item}, **fields})


def journal(document: dict, kind: str, subject: str, data: dict | None = None, lane: str | None = None) -> dict:
    entry = {"id": len(document["journal"]) + 1, "at": f"2026-11-17T12:{len(document['journal']):02d}:00Z",
             "kind": kind, "lane": lane, "subject": subject, "data": data or {}}
    document["journal"].append(entry)
    return entry


def row(document: dict, lane: str) -> dict:
    return next(entry for entry in document["state"]["lanes"] if entry["id"] == lane)


def of(document: dict, subject: str, **given) -> list[dict]:
    return [wait for wait in state_tool.waits(document, **given) if wait["subject"] == subject]


def shape(waits: list[dict]) -> list[tuple]:
    return [(wait["cause"], wait["on"]) for wait in waits]


class FixtureTests(unittest.TestCase):
    def test_the_fixture_is_a_record_the_state_tool_accepts(self) -> None:
        self.assertEqual(state_tool.state_errors(record()["state"]), [])


class OrderTests(unittest.TestCase):
    """c1: a planned story whose after keys have not all landed says which it waits on, and where that one stands."""

    def test_a_story_ordered_after_one_in_review_names_it_and_its_round_and_needs_no_one(self) -> None:
        [wait] = of(record(), "APP-2")
        self.assertEqual({key: wait[key] for key in ("on", "cause", "stage", "frees", "needsSomeone")},
                         {"on": "APP-1", "cause": "order", "stage": "review", "frees": "landed", "needsSomeone": False})
        self.assertEqual(wait["words"], "Waits for APP-1 to land. It is in review, round 2.")
        self.assertNotIn("since", wait)

    def test_the_wait_ends_when_the_story_it_names_merges(self) -> None:
        document = record()
        merge(document, "APP-1")
        self.assertEqual(of(document, "APP-2"), [])

    def test_the_wait_ends_when_the_story_it_names_settles_instead(self) -> None:
        document = record()
        planned(document, "APP-1")["stage"] = "settled"
        self.assertEqual(of(document, "APP-2"), [])

    def test_with_two_after_keys_landing_the_first_leaves_one_wait_on_the_second(self) -> None:
        document = record()
        planned(document, "APP-2")["after"] = ["APP-1", "APP-4"]
        self.assertEqual(shape(of(document, "APP-2")), [("order", "APP-1"), ("order", "APP-4")])
        merge(document, "APP-1")
        [wait] = of(document, "APP-2")
        self.assertEqual((wait["on"], wait["stage"], wait["words"]),
                         ("APP-4", "planned", "Waits for APP-4 to land. It hasn't started."))

    def test_the_sentence_says_where_the_story_stands_in_the_queue(self) -> None:
        for stage, fields, said in (("queued", {"pending": True}, "It waits for the lead to accept it."),
                                    ("queued", {}, "It is in the queue."),
                                    ("checking", {}, "It is being checked."),
                                    ("stopped", {}, "It was stopped.")):
            with self.subTest(stage=stage, fields=fields):
                document = record()
                queue(document, "APP-1", stage, **fields)
                [wait] = of(document, "APP-2")
                self.assertEqual((wait["stage"], wait["words"]), (stage, f"Waits for APP-1 to land. {said}"))

    def test_a_story_already_started_waits_on_nothing(self) -> None:
        document = record()
        planned(document, "APP-2")["stage"] = "building"
        self.assertEqual(of(document, "APP-2"), [])

    def test_a_story_without_after_or_files_gets_no_order_or_files_wait_and_no_error(self) -> None:
        document = record()
        for subject in ("APP-2", "APP-3"):
            planned(document, subject).pop("after", None)
            planned(document, subject).pop("files", None)
        self.assertEqual([wait for wait in state_tool.waits(document) if wait["cause"] in ("order", "files")], [])


class FilesTests(unittest.TestCase):
    """c2: a planned story whose files another lane holds says which lane, and for which story."""

    def test_another_lanes_grant_and_its_unlanded_story_naming_the_file_give_a_wait_on_that_story(self) -> None:
        [wait] = of(record(), "APP-3")
        self.assertEqual({key: wait[key] for key in ("on", "cause", "stage", "frees", "needsSomeone")},
                         {"on": "APP-1", "cause": "files", "stage": "review", "frees": "landed", "needsSomeone": False})
        self.assertEqual(wait["words"], "Waits for APP-1 to land. Lane 2 holds skills/render.py for it.")

    def test_the_wait_ends_when_that_story_merges(self) -> None:
        document = record()
        merge(document, "APP-1")
        self.assertEqual(of(document, "APP-3"), [])

    def test_the_wait_ends_when_the_lead_moves_the_file_to_the_storys_own_lane(self) -> None:
        document = record()
        ownership = document["state"]["laneOwnership"]
        ownership["lane-2"].remove("skills/render.py")
        ownership["lane-3"].append("skills/render.py")
        self.assertEqual(of(document, "APP-3"), [])

    def test_a_queued_story_still_holds_the_files_it_names_until_it_lands(self) -> None:
        document = record()
        queue(document, "APP-1", "checking")
        self.assertEqual(shape(of(document, "APP-3")), [("files", "APP-1")])

    def test_a_grant_on_paths_the_story_does_not_name_gives_no_wait(self) -> None:
        document = record()
        planned(document, "APP-3")["files"] = ["skills/peers.py", "skills/other.py"]
        self.assertEqual(of(document, "APP-3"), [])

    def test_a_grant_to_the_storys_own_lane_gives_no_wait(self) -> None:
        document = record()
        planned(document, "APP-4")["files"] = ["skills/render.py"]
        self.assertEqual(of(document, "APP-4"), [])

    def test_a_grant_whose_story_naming_the_file_has_merged_gives_no_wait_though_it_still_stands(self) -> None:
        document = record()
        merge(document, "APP-1")
        self.assertIn("skills/render.py", document["state"]["laneOwnership"]["lane-2"])
        self.assertEqual(of(document, "APP-3"), [])

    def test_a_region_grant_gives_no_wait(self) -> None:
        document = record()
        document["state"]["laneOwnership"]["lane-2"].remove("skills/render.py")
        document["state"]["laneRegions"] = [{"path": "skills/render.py", "lane": "lane-2", "start": 1, "end": 40,
                                             "baseHead": "a" * 40}]
        self.assertEqual(of(document, "APP-3"), [])

    def test_a_glob_grant_holds_the_files_under_it(self) -> None:
        document = record()
        planned(document, "APP-3")["files"] = ["skills/views/card.js"]
        [wait] = of(document, "APP-3")
        self.assertEqual(wait["words"], "Waits for APP-1 to land. Lane 2 holds skills/views/card.js for it.")

    def test_a_story_waiting_for_both_reasons_gives_both_in_the_tables_order(self) -> None:
        document = record()
        planned(document, "APP-3")["after"] = ["APP-4"]
        self.assertEqual(shape(of(document, "APP-3")), [("order", "APP-4"), ("files", "APP-1")])


class HeldTests(unittest.TestCase):
    """c3: the lead can record why a story waits, and the reason is shown with it."""

    def test_a_hold_gives_a_held_wait_with_the_leads_text_and_since_when(self) -> None:
        document = record()
        held = journal(document, "story.held", "APP-4", {"text": "waits for the operator's call on the budget."})
        [wait] = of(document, "APP-4")
        self.assertEqual(wait, {"subject": "APP-4", "on": "lead", "cause": "held", "stage": "held", "frees": "assigned",
                                "since": held["at"], "needsSomeone": False,
                                "words": "Held by the lead: waits for the operator's call on the budget."})

    def test_assigning_the_story_ends_the_hold(self) -> None:
        document = record()
        journal(document, "story.held", "APP-4", {"text": "the budget"})
        journal(document, "lane.assigned", "APP-4", {"title": "Let a lane wait"}, lane="lane-2")
        self.assertEqual(of(document, "APP-4"), [])

    def test_clearing_the_hold_ends_it(self) -> None:
        document = record()
        journal(document, "story.held", "APP-4", {"text": "the budget"})
        journal(document, "story.unheld", "APP-4")
        self.assertEqual(of(document, "APP-4"), [])

    def test_a_held_story_with_an_unlanded_after_key_gives_both_in_the_tables_order(self) -> None:
        document = record()
        journal(document, "story.held", "APP-2", {"text": "the budget"})
        self.assertEqual(shape(of(document, "APP-2")), [("order", "APP-1"), ("held", "lead")])


class NeedsSomeoneTests(unittest.TestCase):
    """A wait needs someone only when what frees it does: it is stopped, blocked on the lead, or in "Needs you"."""

    def test_a_wait_on_a_stopped_story_needs_someone(self) -> None:
        document = record()
        queue(document, "APP-1", "stopped")
        self.assertEqual([wait["needsSomeone"] for wait in of(document, "APP-2")], [True])

    def test_a_wait_on_a_story_whose_lane_is_blocked_on_the_lead_needs_someone(self) -> None:
        for fields in ({"state": "blocked"}, {"waitingOn": "lead"}):
            with self.subTest(fields=fields):
                document = record()
                row(document, "lane-2").update(fields)
                self.assertEqual([wait["needsSomeone"] for wait in of(document, "APP-2")], [True])

    def test_a_wait_on_a_story_already_in_needs_you_needs_someone(self) -> None:
        self.assertEqual([wait["needsSomeone"] for wait in of(record(), "APP-2", needing={"APP-1"})], [True])

    def test_a_wait_on_a_moving_story_needs_no_one(self) -> None:
        for stage in ("queued", "checking", "merging"):
            with self.subTest(stage=stage):
                document = record()
                queue(document, "APP-1", stage)
                self.assertEqual([wait["needsSomeone"] for wait in of(document, "APP-2")], [False])


class LaneTests(unittest.TestCase):
    """c4: an idle lane whose next story waits is shown as waiting, for the same reason, not as stalled."""

    def test_an_idle_lane_waits_for_its_next_storys_reason(self) -> None:
        [wait] = of(record(), "lane-1")
        self.assertEqual({key: wait[key] for key in ("on", "cause", "stage", "frees", "needsSomeone")},
                         {"on": "APP-2", "cause": "order", "stage": "planned", "frees": "landed", "needsSomeone": False})
        self.assertEqual(wait["words"], "Lane 1 is next on APP-2, which waits for APP-1 to land.")
        [files] = of(record(), "lane-3")
        self.assertEqual(files["words"], "Lane 3 is next on APP-3, which waits for APP-1 to land. "
                                         "Lane 2 holds skills/render.py for it.")

    def test_a_lane_at_work_says_nothing_of_its_next_story(self) -> None:
        document = record()
        row(document, "lane-1")["state"] = "working"
        self.assertEqual(of(document, "lane-1"), [])

    def test_the_lanes_wait_ends_with_its_storys(self) -> None:
        document = record()
        merge(document, "APP-1")
        self.assertEqual(of(document, "lane-1"), [])
        self.assertEqual(of(document, "lane-3"), [])

    def test_a_lane_whose_next_story_is_held_says_so(self) -> None:
        document = record()
        journal(document, "story.held", "APP-3", {"text": "the budget"})
        self.assertEqual([wait["words"] for wait in of(document, "lane-3")],
                         ["Lane 3 is next on APP-3, which waits for APP-1 to land. Lane 2 holds skills/render.py for it.",
                          "Lane 3 is next on APP-3, which the lead holds: the budget"])


class HoldingTests(unittest.TestCase):
    """On the thing that holds: who waits on it."""

    def test_the_story_others_wait_on_names_them_and_lanes_are_not_counted(self) -> None:
        document = record()
        self.assertEqual(state_tool.waited_on(document, state_tool.waits(document)),
                         [{"subject": "APP-1", "by": ["APP-2", "APP-3"], "words": "Waited on by APP-2 and APP-3."}])

    def test_a_hold_names_no_story_as_holding(self) -> None:
        document = record()
        merge(document, "APP-1")
        journal(document, "story.held", "APP-4", {"text": "the budget"})
        self.assertEqual(state_tool.waited_on(document, state_tool.waits(document)), [])


class FreeToStartTests(unittest.TestCase):
    """A planned story nothing holds any more, on an idle lane, is free to start."""

    def free(self, document: dict) -> list[str]:
        return [item["subject"] for item in state_tool.free_to_start(document, state_tool.waits(document))]

    def test_a_story_with_no_wait_left_on_an_idle_lane_is_free_to_start(self) -> None:
        document = record()
        merge(document, "APP-1")
        self.assertEqual(state_tool.free_to_start(document, state_tool.waits(document)),
                         [{"subject": "APP-2", "lane": "lane-1", "words": "APP-2 is free to start. Lane 1 is idle."},
                          {"subject": "APP-3", "lane": "lane-3", "words": "APP-3 is free to start. Lane 3 is idle."}])

    def test_it_is_not_free_while_its_lane_is_busy_it_is_held_or_it_still_waits(self) -> None:
        document = record()
        self.assertEqual(self.free(document), [])  # each still waits
        merge(document, "APP-1")
        row(document, "lane-1")["state"] = "working"
        journal(document, "story.held", "APP-3", {"text": "the budget"})
        self.assertEqual(self.free(document), [])

    def test_the_idle_lane_an_acceptance_left_waiting_on_its_story_still_counts_as_idle(self) -> None:
        document = record()
        merge(document, "APP-1")
        row(document, "lane-1").update(state="idle", waitingOn="APP-9")
        self.assertIn("APP-2", self.free(document))
        planned(document, "APP-2")["after"] = ["APP-4"]
        self.assertNotIn("APP-2", self.free(document))


class NeedsYouTests(unittest.TestCase):
    """The supervisor's "Needs you" list gains a wait only when someone must act, and never holds the close for it."""

    def needs(self, document: dict, items: list[dict]) -> list[dict]:
        listed = [dict(item) for item in items]
        added = run_delivery.waits_needing(SimpleNamespace(release=PRODUCT_ROOT), document, listed)
        return listed + added

    def test_a_stopped_blocker_is_one_item_carrying_how_many_wait_on_it_and_holds_nothing(self) -> None:
        document = record()
        queue(document, "APP-1", "stopped")
        self.assertEqual(self.needs(document, []), [
            {"holding": False, "source": "APP-1's waits", "clears": "APP-1 put back, landing or settling",
             "text": "APP-1 was stopped. 2 pieces wait on it."}])

    def test_a_blocker_whose_lane_is_blocked_on_the_lead_gains_the_count_on_the_lanes_item(self) -> None:
        document = record()
        row(document, "lane-2").update(state="blocked", focus="which API")
        lane = {"holding": True, "source": "lane-2's state", "clears": "the lane's state changing",
                "text": "lane-2 is blocked: which API"}
        self.assertEqual(self.needs(document, [lane]), [{**lane, "text": "lane-2 is blocked: which API 2 pieces wait on it."}])

    def test_a_blocker_already_in_needs_you_gains_the_count_on_its_own_item(self) -> None:
        document = record()
        blocker = journal(document, "blocker.recorded", "APP-1", {"text": "the API is down"})
        planned(document, "APP-3")["files"] = ["skills/peers.py"]
        item = {"holding": True, "source": f"journal entry {blocker['id']} (blocker.recorded)", "clears": "x",
                "text": "Blocker 5 (APP-1): the API is down."}
        self.assertEqual(self.needs(document, [item])[0], {**item, "text": "Blocker 5 (APP-1): the API is down. 1 piece waits on it."})
        # APP-3 names no file another lane holds now, so on its idle lane it is free to start.
        self.assertEqual([entry["text"] for entry in self.needs(document, [item])[1:]],
                         ["APP-3 is free to start. Lane 3 is idle."])

    def test_a_wait_on_a_moving_story_adds_nothing(self) -> None:
        self.assertEqual(self.needs(record(), []), [])

    def test_free_to_start_is_listed_and_holds_nothing(self) -> None:
        document = record()
        merge(document, "APP-1")
        listed = self.needs(document, [])
        self.assertEqual([(item["text"], item["holding"]) for item in listed],
                         [("APP-2 is free to start. Lane 1 is idle.", False), ("APP-3 is free to start. Lane 3 is idle.", False)])


class ListeningTests(unittest.TestCase):
    """While no lead-wait runs, "Needs you" says who can't reach the lead, from the table, with no command in it."""

    def said(self, lanes: list[tuple[str, str]], queue: list[dict] | None = None) -> dict:
        state = {"lanes": [{"id": lane, "state": state} for lane, state in lanes], "queue": queue or []}
        return run_delivery.listening_words(SimpleNamespace(release=PRODUCT_ROOT), state)

    def test_one_lane_and_several_lanes_cant_reach_the_lead(self) -> None:
        self.assertEqual(self.said([("lane-1", "idle"), ("lane-2", "working")]),
                         {"text": "Lane 2 can't reach the lead. A hand-off or question from it will wait unseen until the "
                                  "lead is watching again.", "clears": "the lead is watching again"})
        self.assertEqual(self.said([("lane-1", "working"), ("lane-2", "reviewing")])["text"],
                         "Lane 1 and lane 2 can't reach the lead. A hand-off or question from them will wait unseen "
                         "until the lead is watching again.")
        self.assertTrue(self.said([("lane-1", "working"), ("lane-2", "reviewing"), ("lane-3", "blocked")])["text"]
                        .startswith("Lane 1, lane 2 and lane 3 can't reach the lead."))

    def test_a_lane_with_a_name_is_called_by_it_and_with_no_lane_at_work_it_names_the_queued_piece(self) -> None:
        self.assertTrue(self.said([("api", "working")])["text"].startswith("Api can't reach the lead."))
        self.assertEqual(self.said([("lane-1", "idle")], [{"subject": "APP-1", "stage": "queued"}])["text"],
                         "A piece in the queue can't reach the lead. If it lands or stops, nothing will tell the lead "
                         "until it is watching again.")

    def test_the_item_names_no_command(self) -> None:
        said = self.said([("lane-1", "working")])
        for words in ("lead-wait", "run_delivery", "python"):
            self.assertNotIn(words, said["text"] + said["clears"])


class NextTests(unittest.TestCase):
    """`next` calls a story blocked for the same cause and on that `waits` gives it."""

    def ranked(self, document: dict, lane: str, snapshot: dict | None = None) -> dict:
        return {entry["subject"]: entry for entry in state_tool.next_for_lane(document["state"], lane, snapshot, None)}

    def test_with_no_snapshot_an_order_wait_and_a_files_wait_each_block_and_no_wait_does_not(self) -> None:
        document = record()
        for subject in ("APP-2", "APP-3", "APP-4"):
            planned(document, subject)["lane"] = None
        ranked = self.ranked(document, "lane-1")
        derived = {wait["subject"]: shape([wait]) for wait in state_tool.waits(document) if wait["subject"].startswith("APP")}
        for subject in ("APP-2", "APP-3"):
            self.assertEqual((ranked[subject]["blocked"], shape(ranked[subject]["waits"])), (True, derived[subject]))
        self.assertEqual(derived, {"APP-2": [("order", "APP-1")], "APP-3": [("files", "APP-1")]})
        self.assertEqual((ranked["APP-4"]["blocked"], ranked["APP-4"]["waits"]), (False, []))
        self.assertTrue(ranked["APP-4"]["listed"])

    def test_with_a_snapshot_it_still_adds_the_trackers_blocked_by(self) -> None:
        document = record()
        planned(document, "APP-4")["lane"] = None
        snapshot = {"items": [{"subject": "APP-4", "state": "backlog", "blockedBy": ["APP-9"]},
                              {"subject": "APP-9", "state": "started"}]}
        entry = self.ranked(document, "lane-1", snapshot)["APP-4"]
        self.assertTrue(entry["blocked"])
        self.assertIn("blocked by APP-9", entry["reason"])


class PurityTests(unittest.TestCase):
    """The same record and table always give the same waits: no clock, no network, no other file."""

    def test_with_the_clock_the_network_and_every_other_open_raising_the_same_record_gives_the_same_waits_twice(self):
        document = record()
        journal(document, "story.held", "APP-4", {"text": "the budget"})
        table = str(state_tool.WAIT_WORDS_FILE)
        real_open, real_io_open = builtins.open, io.open

        def guarded(opener):
            def opened(file, *args, **kwargs):
                if str(file) != table:
                    raise AssertionError(f"waits opened {file}")
                return opener(file, *args, **kwargs)
            return opened

        def refused(*args, **kwargs):
            raise AssertionError("waits read the clock or the network")

        expected = state_tool.waits(record() | {"journal": document["journal"]})
        with mock.patch.object(builtins, "open", guarded(real_open)), mock.patch.object(io, "open", guarded(real_io_open)), \
                mock.patch.object(os, "open", refused), mock.patch.object(time, "time", refused), \
                mock.patch.object(time, "monotonic", refused), mock.patch.object(socket, "socket", refused), \
                mock.patch.object(state_tool, "now", refused), \
                mock.patch.object(state_tool.datetime, "datetime", SimpleNamespace(now=refused, utcnow=refused,
                                                                                    fromisoformat=refused)):
            first = state_tool.waits(document)
            second = state_tool.waits(document)
        self.assertEqual(first, second)
        self.assertEqual(first, expected)
        self.assertTrue(first)


class TableTests(unittest.TestCase):
    """Every sentence comes from one table, and every view prints it as it is."""

    def test_the_table_is_canonical_json_and_every_template_fills(self) -> None:
        raw = WORDS.read_text(encoding="utf-8")
        table = json.loads(raw)
        self.assertEqual(json.dumps(table, indent=2, ensure_ascii=False) + "\n", raw)
        self.assertEqual(table["schemaVersion"], "wait-words/1")
        for group in ("stages", "waits", "lanes", "holding", "needsYou", "listening"):
            self.assertIn("templates", table[group])
        self.assertEqual(set(table["waits"]["templates"]), {"order", "files", "held"})
        self.assertEqual(set(table["lanes"]["templates"]), {"order", "files", "held"})
        self.assertEqual(set(table["needsYou"]["templates"]), {"count", "free", "stopped"})
        self.assertEqual(state_tool.wait_said(table, "needsYou", "count", {"count": 1}), "1 piece waits on it.")
        self.assertEqual(state_tool.wait_said(table, "needsYou", "count", {"count": 3}), "3 pieces wait on it.")

    def test_every_cause_a_wait_gives_is_one_the_table_orders(self) -> None:
        self.assertEqual(state_tool.WAIT_CAUSES, ("order", "files", "lane", "claim", "slot", "review", "held"))


def marked(table: dict) -> dict:
    """The table with every template string carrying its own marker, ⟦group.name[.case]⟧, ahead of its words."""
    copied = copy.deepcopy(table)
    for group, value in copied.items():
        if not isinstance(value, dict) or "templates" not in value:
            continue
        for name, template in value["templates"].items():
            if isinstance(template, dict):
                template["cases"] = {case: f"⟦{group}.{name}.{case}⟧ {words}" for case, words in template["cases"].items()}
                template["otherwise"] = f"⟦{group}.{name}⟧ {template['otherwise']}"
            else:
                value["templates"][name] = f"⟦{group}.{name}⟧ {template}"
    return copied


# The fixed words of the real templates, each of which a view may show only inside a marked sentence.
FIXED = ("Waits for", "Held by the lead", "is next on", "Waited on by", "free to start", "pieces wait on it",
         "piece waits on it", "was stopped.", "can't reach the lead", "watching again")
MARK = re.compile(r"⟦([a-zA-Z]+\.[a-zA-Z]+(?:\.[a-z0-9]+)?)⟧")


class MarkerTests(unittest.TestCase):
    """With each template marked, every wait sentence on a view carries its template's marker, and no view shows one
    without: the views print the derivation's words and build no sentence of their own."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.copy = Path(folder.name) / "wait-words.json"
        self.copy.write_text(json.dumps(marked(json.loads(WORDS.read_text(encoding="utf-8"))), ensure_ascii=False),
                             encoding="utf-8")
        for module in (state_tool, views.review_state):
            patcher = mock.patch.object(module, "WAIT_WORDS_FILE", self.copy)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.document = record()
        journal(self.document, "story.held", "APP-4", {"text": "the budget"})
        row(self.document, "lane-2")["state"] = "idle"

    def check(self, shown: str, sentences: list[str]) -> None:
        """Every sentence is shown whole, carrying its marker; what is left once they are taken out holds no wait words."""
        rest = shown
        for sentence in sentences:
            self.assertRegex(sentence, MARK)
            self.assertIn(sentence, shown)
            rest = rest.replace(sentence, "")
        for words in FIXED:
            self.assertNotIn(words, rest)

    def sentences(self, document: dict) -> list[str]:
        return [wait["words"] for name in ("waits", "waitedOn") for wait in document[name]]

    def test_every_template_this_record_reaches_carries_its_own_marker(self) -> None:
        document = render_state.with_waits(self.document)
        reached = {MARK.search(words).group(1) for words in self.sentences(document)}
        self.assertEqual(reached, {"waits.order", "waits.files", "waits.held", "lanes.order", "lanes.files", "lanes.held",
                                   "holding.story"})
        stage = next(wait["words"] for wait in document["waits"] if wait["cause"] == "order" and wait["subject"] == "APP-2")
        self.assertIn("⟦stages.where.review⟧", stage)

    def test_the_markdown_view_shows_every_wait_sentence_with_its_marker_and_words_none_itself(self) -> None:
        document = render_state.with_waits(self.document)
        self.check(render_state.render_markdown(self.document), self.sentences(document))

    def test_the_built_in_page_carries_every_wait_sentence_with_its_marker(self) -> None:
        document = render_state.with_waits(self.document)
        page = render_state.render_static_page(self.document)
        embedded = re.search(r'<script type="application/json" id="delivery-state">(.*?)</script>', page, re.S).group(1)
        shown = json.loads(embedded)
        self.assertEqual(shown["waits"], document["waits"])
        self.assertEqual(shown["waitedOn"], document["waitedOn"])
        # The page's own script holds no wait sentence: it prints the embedded words.
        script = page[page.index("<script>"):] if "<script>" in page else page
        for words in FIXED:
            self.assertNotIn(words, script.replace(embedded, ""))

    def test_the_live_page_carries_every_wait_sentence_with_its_marker(self) -> None:
        data = views.line_map_data(self.document, {}, config(), theme_key="t", history=None)
        self.assertEqual(self.sentences(data["document"]), self.sentences(render_state.with_waits(self.document)))
        script = (PRODUCT_ROOT / "skills/delivery-views/assets/line-map.js").read_text(encoding="utf-8")
        for words in FIXED:
            self.assertNotIn(words, script)

    def test_needs_you_says_its_forms_from_the_table(self) -> None:
        document = self.document
        merge(document, "APP-1")
        planned(document, "APP-4")["after"] = []
        row(document, "lane-2")["state"] = "working"
        added = run_delivery.waits_needing(SimpleNamespace(release=PRODUCT_ROOT), document, [])
        self.assertTrue(added)
        for item in added:
            self.assertRegex(item["text"], r"⟦needsYou\.free⟧")
        for lanes, queued, marker in (([("lane-1", "working")], [], "listening.text.1"),
                                      ([("lane-1", "working"), ("lane-2", "working")], [], "listening.text"),
                                      ([], [{"subject": "APP-1", "stage": "queued"}], "listening.text.0")):
            listening = run_delivery.listening_words(SimpleNamespace(release=PRODUCT_ROOT), {
                "lanes": [{"id": lane, "state": state} for lane, state in lanes], "queue": queued})
            self.assertRegex(listening["text"], rf"^⟦{re.escape(marker)}⟧ ")
            self.assertRegex(listening["clears"], r"^⟦listening\.clears⟧ ")
        stopped = record()
        queue(stopped, "APP-1", "stopped")
        [item] = run_delivery.waits_needing(SimpleNamespace(release=PRODUCT_ROOT), stopped, [])
        self.assertRegex(item["text"], r"^⟦needsYou\.stopped⟧ APP-1 was stopped\. ⟦needsYou\.count⟧ 2 pieces wait on it\.$")


class WrapperTests(unittest.TestCase):
    """The wrapper every view and the live stream read carries the waits beside the story."""

    def test_the_wrapper_adds_the_waits_and_who_waits_on_what(self) -> None:
        document = render_state.with_waits(record())
        self.assertEqual(document["waits"], state_tool.waits(record()))
        self.assertEqual(document["waitedOn"], state_tool.waited_on(record(), document["waits"]))

    def test_a_record_that_waits_on_nothing_reads_as_it_did(self) -> None:
        document = record()
        merge(document, "APP-1")
        self.assertEqual(render_state.with_waits(document), document)
        self.assertEqual(views.with_waits(document), document)

    def test_a_wait_is_gone_from_every_view_once_what_frees_it_happens(self) -> None:
        document = record()
        before = render_state.render_markdown(document)
        self.assertIn("Waits for APP-1 to land. It is in review, round 2.", before)
        self.assertIn("Lane 1 is next on APP-2", before)
        merge(document, "APP-1")
        after = render_state.render_markdown(document)
        for words in FIXED:
            self.assertNotIn(words, after)


if __name__ == "__main__":
    unittest.main()
