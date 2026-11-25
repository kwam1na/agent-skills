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
        document = record()
        planned(document, "APP-1")["noChange"] = {"evidence": "the criteria hold", "baseHead": "a" * 40,
                                                  "handedAt": "2026-11-17T11:40:00Z"}
        [wait] = of(document, "APP-2")
        self.assertEqual(wait["words"], "Waits for APP-1 to land. It waits for the lead to accept it.")
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

    def test_a_stopped_story_is_held_by_the_lane_it_is_now_assigned_to(self) -> None:
        document = record()
        queue(document, "APP-1", "stopped", assignedLane="lane-3")
        self.assertEqual(of(document, "APP-3"), [], "lane 3's own work holds nothing from lane 3")
        document = record()
        queue(document, "APP-1", "stopped", assignedLane="lane-1")
        document["state"]["laneOwnership"]["lane-1"].append("skills/render.py")
        row(document, "lane-1").update(state="blocked")
        [wait] = of(document, "APP-3")
        self.assertEqual((wait["on"], wait["needsSomeone"]), ("APP-1", True))
        self.assertEqual(wait["words"], "Waits for APP-1 to land. Lane 1 holds skills/render.py for it.")

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


class CarryTests(unittest.TestCase):
    """A story's `after` and `files` go with it into the queue at hand-off, by the forge's merge the reconcile sweep
    recovers, and back to the planned work on a rejection, so what waits on it still does until it lands."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        state = record()["state"]
        state["meta"]["handoffsNeedAcceptance"] = True
        (self.directory / state_tool.STATE_FILE).write_text(state_tool.dumps(state), encoding="utf-8")
        (self.directory / state_tool.JOURNAL_FILE).touch()

    def shown(self) -> dict:
        return {"state": state_tool.read_state(self.directory), "journal": state_tool.read_journal(self.directory)}

    def test_a_hand_off_carries_them_into_the_queue_and_a_rejection_carries_them_back(self) -> None:
        state_tool.handoff(self.directory, lane="lane-2", subject="APP-1", head="c" * 40, change_request="cr-1")
        [queued] = self.shown()["state"]["queue"]
        self.assertEqual((queued["subject"], queued["files"]), ("APP-1", ["skills/render.py", "skills/views/card.js"]))
        self.assertNotIn("after", queued)
        self.assertEqual(shape(of(self.shown(), "APP-3")), [("files", "APP-1")])
        state_tool.reject_handoff(self.directory, "APP-1", "not yet")
        back = planned(self.shown(), "APP-1")
        self.assertEqual(back["files"], ["skills/render.py", "skills/views/card.js"])
        self.assertEqual(shape(of(self.shown(), "APP-3")), [("files", "APP-1")])

    def test_a_planned_storys_after_rides_into_the_queue_too(self) -> None:
        state_tool.handoff(self.directory, lane="lane-1", subject="APP-2", head="c" * 40, change_request="cr-2")
        queued = next(item for item in self.shown()["state"]["queue"] if item["subject"] == "APP-2")
        self.assertEqual((queued["after"], queued["files"]), (["APP-1"], ["skills/waits.py"]))

    def test_a_merge_the_sweep_recovers_carries_them_into_the_queue(self) -> None:
        state = record()["state"]
        snapshot = {"source": "forge", "items": [{"subject": "APP-1", "state": "merged", "head": "c" * 40}]}
        lead = state_tool._plan(state, [snapshot], [])[0]
        queued = next(item for item in lead["queue"] if item["subject"] == "APP-1")
        self.assertEqual(queued["files"], ["skills/render.py", "skills/views/card.js"])


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

    def test_an_idle_lanes_wait_names_its_blocker_for_a_card_to_draw(self) -> None:
        # The card groups a lane's waits by its next story: each names the story it waits on, that story's lane, whether the
        # blocker is in the merge queue, its phase in one word from the table, and the files a hold names.
        [order] = of(record(), "lane-1")
        self.assertEqual({key: order.get(key) for key in ("blocker", "onLane", "queued", "phase", "files")},
                         {"blocker": "APP-1", "onLane": "lane-2", "queued": False, "phase": "reviewing", "files": None})
        [files] = of(record(), "lane-3")
        self.assertEqual({key: files.get(key) for key in ("blocker", "onLane", "queued", "phase", "files")},
                         {"blocker": "APP-1", "onLane": "lane-2", "queued": False, "phase": "reviewing",
                          "files": ["skills/render.py"]})
        for stage, fields, queued, phase in (("queued", {}, True, "queued"), ("syncing", {}, True, "syncing"),
                                             ("checking", {}, True, "checking"),
                                             ("queued", {"pending": True}, False, "handed"),
                                             ("stopped", {}, False, "stopped")):
            with self.subTest(stage=stage, fields=fields):
                document = record()
                queue(document, "APP-1", stage, **fields)
                [wait] = of(document, "lane-1")
                self.assertEqual((wait["queued"], wait["phase"]), (queued, phase))
        document = record()
        planned(document, "APP-1")["stage"] = "building"
        [wait] = of(document, "lane-1")
        self.assertEqual(wait["phase"], "building")

    def test_a_waiting_lane_with_no_story_of_its_own_left_says_only_what_it_waits_for(self) -> None:
        document = record()
        row(document, "lane-3").update(state="waiting", waitingOn="APP-1")
        merge(document, "APP-1")
        self.assertEqual(of(document, "lane-3"), [], "the landing it waited for is the end of the wait")
        document = record()
        planned(document, "APP-3")["lane"] = "lane-1"
        row(document, "lane-3").update(state="waiting", waitingOn="APP-1")
        [wait] = of(document, "lane-3")
        self.assertEqual((wait["cause"], wait["words"]), ("lane", "Lane 3 waits for APP-1 to land."))

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
        """The items as the close and the friction note read them; the facts a row is drawn from are NeedsYouRowTests'."""
        listed = [dict(item) for item in items]
        added = run_delivery.waits_needing(SimpleNamespace(release=PRODUCT_ROOT), document, listed)
        return [{name: item[name] for name in ("holding", "source", "clears", "text")} for item in listed + added]

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

    def test_a_free_story_others_wait_on_carries_their_count(self) -> None:
        document = record()
        planned(document, "APP-1")["stage"] = "planned"
        row(document, "lane-2")["state"] = "idle"
        listed = self.needs(document, [])
        self.assertIn(("APP-1 is free to start. Lane 2 is idle. 2 pieces wait on it.", False),
                      [(item["text"], item["holding"]) for item in listed])

    def test_free_to_start_is_listed_and_holds_nothing(self) -> None:
        document = record()
        merge(document, "APP-1")
        listed = self.needs(document, [])
        self.assertEqual([(item["text"], item["holding"]) for item in listed],
                         [("APP-2 is free to start. Lane 1 is idle.", False), ("APP-3 is free to start. Lane 3 is idle.", False)])


class NeedsYouRowTests(unittest.TestCase):
    """Each "Needs you" item carries the facts its row is drawn from, who must act, and its row's words from the table:
    a headline, a why, what clears it and who acts. Its text, which the close and the friction note read, is unchanged."""

    RELEASE = SimpleNamespace(release=PRODUCT_ROOT)

    def said(self, document: dict, items: list[dict]) -> list[dict]:
        return run_delivery.say_needs(self.RELEASE, document, [dict(item) for item in items])

    def test_a_blocked_lane_names_who_its_question_waits_on_and_the_story_it_is_on(self) -> None:
        document = record()
        row(document, "lane-2").update(state="blocked", focus="which API", waitingOn="lead")
        row(document, "lane-1").update(state="blocked", focus="the copy", waitingOn="operator")
        row(document, "lane-3").update(state="blocked", focus="its schema", waitingOn="lane-2")
        items = {item["lane"]: item for item in run_delivery.lane_needs(document["state"])}
        self.assertEqual({lane: item["who"] for lane, item in items.items()},
                         {"lane-1": "operator", "lane-2": "lead", "lane-3": "lane-2"})
        for name in ("user", "You"):
            row(document, "lane-1")["waitingOn"] = name
            self.assertEqual(run_delivery.lane_needs(document["state"])[0]["who"], "operator", name)
        # A stopped piece is on the lane it was assigned to, whatever lane it now names.
        queue(document, "APP-3", "stopped", lane=None, assignedLane="lane-3")
        self.assertEqual(run_delivery.lane_story(document["state"], "lane-3")["subject"], "APP-3")
        self.assertEqual(items["lane-2"]["subject"], "APP-1")
        self.assertNotIn("subject", items["lane-1"])
        self.assertEqual(items["lane-2"]["text"], "lane-2 is blocked: which API")
        [said] = self.said(document, [items["lane-2"]])
        self.assertEqual(said["said"], {"headline": "Lane 2 is blocked on APP-1", "why": "which API",
                                        "clears": "Clears when Lane 2's state changes.", "who": "The lead"})
        self.assertEqual((said["state"], said["since"]), ("review", "2026-11-17T10:00:00Z"))
        self.assertNotIn("fields", said)
        lane_three = self.said(document, [items["lane-3"]])[0]["said"]
        self.assertEqual(lane_three["who"], "Lane 2")

    def test_a_story_that_waits_carries_its_blocker_with_that_storys_lane_and_state(self) -> None:
        document = record()
        [said] = self.said(document, [{"holding": False, "source": "s", "text": "t", "clears": "c", "kind": "free",
                                       "who": "lead", "subject": "APP-2"}])
        self.assertEqual((said["lane"], said["state"]), ("lane-1", "planned"))
        self.assertEqual(said["blocker"], {"subject": "APP-1", "state": "review", "lane": "lane-2"})

    def test_what_the_record_itself_shows_needs_someone_becomes_items_that_hold_nothing(self) -> None:
        document = record()
        queue(document, "APP-1", "stopped", checks=[{"id": "e2e", "title": "end to end", "state": "failed"}])
        stop = journal(document, "candidate.stopped", "APP-1", lane="lane-2")
        meta = document["state"]["meta"]
        meta["hold"] = {"reason": "the base is broken", "at": "2026-11-17T11:00:00Z"}
        meta["sourceReports"] = [{"subject": "APP-3", "note": "its ticket moved"}]
        meta["sources"] = {"tracker": {"status": "stale", "reason": "it timed out", "lastReadAt": "2026-11-17T11:30:00Z"},
                           "forge": {"status": "fresh"}}
        items = run_delivery.record_needs(document, [])
        self.assertEqual([item["kind"] for item in items], ["paused", "stopped", "report", "stale"])
        self.assertFalse(any(item["holding"] for item in items))
        said = {item["kind"]: item for item in self.said(document, items)}
        self.assertEqual(said["stopped"]["said"]["headline"], "APP-1 was stopped")
        self.assertEqual(said["stopped"]["said"]["why"], "end to end failed")
        self.assertEqual((said["stopped"]["since"], said["stopped"]["source"]), (stop["at"], f"journal entry {stop['id']} (candidate.stopped)"))
        self.assertEqual(said["paused"]["said"]["why"], "the base is broken")
        self.assertEqual(said["report"]["said"]["headline"], "A report for the lead on APP-3")
        self.assertEqual(said["stale"]["said"], {"headline": "The tracker is not answering", "why": "it timed out",
                                                 "clears": "Clears when the tracker answers again.", "who": "The lead"})
        # A stop with no failed check is said in the story's words for its entry.
        planned(document, "APP-4")
        queue(document, "APP-4", "stopped")
        quiet = journal(document, "candidate.stopped", "APP-4", {"reason": "check_failed", "detail": "Its review is for an older head.",
                                                                  "stage": "checking"}, lane="lane-2")
        [item] = [entry for entry in self.said(document, run_delivery.record_needs(document, [])) if entry.get("subject") == "APP-4"]
        self.assertEqual(item["said"]["why"], run_delivery.entry_said(quiet))
        self.assertTrue(item["said"]["why"])
        # The queue's own hold already says serving is paused, so the record's hold adds nothing beside it.
        self.assertNotIn("paused", [item["kind"] for item in run_delivery.record_needs(document, [{"kind": "runnerHold"}])])

    def needs_you(self, document: dict) -> list[dict]:
        """needs_you itself on a record, with nothing outside the record answering: no tracker, no fullHealth run, a
        listener running, and no log or resolution on disk."""
        here = SimpleNamespace(release=PRODUCT_ROOT, state=Path("/nonexistent"), flow_log=Path("/nonexistent"),
                               delivery="APP", forge={})
        with mock.patch.multiple(run_delivery, tracker_conf=lambda c: {}, base_head=lambda c: None,
                                 full_health_running=lambda c: None, read_log=lambda *a, **k: [],
                                 lead_listener=lambda *a, **k: {"pid": 1}, only_full_health_breaks=lambda journal: False), \
                mock.patch.object(run_delivery.tracker_publisher, "needs_you", lambda *a, **k: []):
            return run_delivery.needs_you(here, document)

    def test_a_stop_with_no_failed_check_and_a_break_are_said_in_their_entries_story_words_as_written_or_reworded(self) -> None:
        for detail in ("Its review is for an older head.", "Other words."):
            document = record()
            queue(document, "APP-1", "stopped")
            stop = journal(document, "candidate.stopped", "APP-1", {"reason": "check_failed", "detail": detail, "stage": "checking"},
                           lane="lane-2")
            broken = journal(document, "base.broken", None, {"source": "queue", "detail": detail})
            said = {item["kind"]: item["said"]["why"] for item in self.needs_you(document)}
            self.assertEqual((said["stopped"], said["baseBroken"]),
                             (f"{run_delivery.entry_said(stop)} 2 pieces wait on it.", run_delivery.entry_said(broken)[:300]), detail)
            self.assertTrue(detail in said["stopped"] and detail in said["baseBroken"], said)

    def test_a_stopped_story_others_wait_on_is_one_item_carrying_their_count_in_its_why(self) -> None:
        document = record()
        queue(document, "APP-1", "stopped")
        items = run_delivery.record_needs(document, [])
        items += run_delivery.waits_needing(self.RELEASE, document, items)
        [said] = self.said(document, items)
        self.assertEqual(said["said"]["why"], "2 pieces wait on it.")
        self.assertNotIn("waiting", said)

    def test_every_kind_needs_you_gives_is_said_by_the_table_with_its_own_headline(self) -> None:
        source = (PRODUCT_ROOT / "skills/run-delivery/scripts/run_delivery.py").read_text(encoding="utf-8")
        body = source[source.index("def tracker_item("):source.index("NEEDS_FILE = ")]
        # The regrant items a landing leaves, which needs_you lists, are made beside the settle that marks them.
        body += source[source.index("def regrant_needs("):source.index("def change_ownership(")]
        # Each kind, with who it names, as needs_you and its helpers write it.
        found = re.findall(r'"kind": "(\w+)"(?: if \w+ else "(\w+)")?,(?:\s*\n\s*)? ?(?:"who": "(\w+)")?', body)
        kinds = {kind for pair in found for kind in pair[:2] if kind}
        table = json.loads(WORDS.read_text(encoding="utf-8"))["needsYou"]["templates"]
        for name in ("headline", "why", "clears"):
            self.assertEqual(kinds - set(table[name]["cases"]), set(), name)
        self.assertEqual(kinds, set(table["headline"]["cases"]))
        self.assertEqual({pair[0]: pair[2] for pair in found if pair[2] and pair[0] == "drafts"}, {"drafts": "operator"})
        # The items about one story name it, so their rows draw its stop: the merge not accepted, and a blocker on a story.
        self.assertRegex(body, r'"kind": "unaccepted",\s*"who": "lead", "subject": item\["subject"\]')
        self.assertRegex(body, r'"kind": "runnerHold" if runners else "blocker", "who": "lead"[^\n]*\n\s*\*\*\(\{"subject": entry\["subject"\]\} if entry\.get\("subject"\) else \{\}\)')
        # Each kind's headline, from the facts its producer gives it.
        document = record()
        given = {
            "trackerLag": ({"detail": "The tracker publisher hasn't posted journal entries 4"}, {}, "The tracker is behind the record"),
            "trackerLedger": ({"detail": "APP-1: the move isn't offered"}, {}, "A tracker update needs a hand"),
            "healthRunning": ({"head": "a" * 40}, {}, "The full health run is under way at aaaaaaa"),
            "healthFailed": ({"head": "b" * 40, "detail": "1 failed"}, {}, "The full health run failed at bbbbbbb"),
            "healthFlaky": ({"head": "c" * 40, "tests": ["t.T.test_a"]}, {}, "Flaky tests failed the full health run at ccccccc"),
            "baseBroken": ({"detail": "Main is red"}, {}, "The main line is broken"),
            "baseHealth": ({"detail": "Main is red"}, {}, "The main line is broken"),
            "baseFocused": ({"detail": "Main is red", "source": "main"}, {}, "The main line is broken, under the focused posture"),
            "unaccepted": ({}, {"subject": "APP-1"}, "APP-1 merged before it was accepted"),
            "runnerHold": ({"entry": 7, "detail": "a break"}, {}, "The queue holds serving"),
            "blocker": ({"entry": 7, "detail": "the API"}, {"subject": "APP-1"}, "Blocker 7 on APP-1"),
            "listener": ({"detail": "Lane 1 can't reach the lead."}, {}, "The lead isn't listening"),
            "flapping": ({"name": "publisher", "log": "p.log"}, {}, "The supervisor's publisher won't stay up"),
            "drafts": ({"subjects": ["APP-1", "APP-2"]}, {}, "APP-1 and APP-2 wait to be marked ready"),
            "laneBlocked": ({"focus": "which API"}, {"lane": "lane-2", "subject": "APP-1"}, "Lane 2 is blocked on APP-1"),
            "laneAsks": ({"focus": "which API"}, {"lane": "lane-2"}, "Lane 2 waits on the lead"),
            "regrant": ({"path": "src/a.py", "detail": "a landed hunk crosses the span's edge"}, {"lane": "lane-2"},
                        "Lane 2's lines in src/a.py need a regrant"),
            "laneOutside": ({"path": "src/a.py", "detail": "It changed lines 5-7, outside its span."}, {"lane": "lane-2"},
                            "Lane 2 changed src/a.py outside what it holds"),
            "laneUnreadable": ({"branch": "claude/APP-2", "detail": "no common history"}, {"lane": "lane-2"},
                               "Lane 2's branch claude/APP-2 can't be read"),
            "stopped": ({"detail": "e2e failed"}, {"subject": "APP-1"}, "APP-1 was stopped"),
            "free": ({}, {"subject": "APP-2", "lane": "lane-1"}, "APP-2 is free to start"),
            "sibling": ({"on": "ZZZ-1"}, {"subject": "APP-4"}, "APP-4 comes after ZZZ-1, which no sibling lists"),
            "paused": ({"reason": "a break"}, {}, "Serving is paused"),
            "report": ({"about": "APP-3", "detail": "moved"}, {}, "A report for the lead on APP-3"),
            "stale": ({"name": "tracker", "reason": "timed out"}, {}, "The tracker is not answering"),
            "compounding": ({"stub": "flow-log/APP.friction.md"}, {}, "The compound-learning decision is not recorded"),
            "feedback": ({"entry": 9, "anchor": "## Decisions", "detail": "Not yet."}, {}, "Feedback on ## Decisions stops approval"),
        }
        self.assertEqual(set(given), kinds)
        items = [{"holding": False, "source": "s", "text": "t", "clears": "c", "kind": kind, "who": "lead", "fields": fields, **facts}
                 for kind, (fields, facts, _) in given.items()]
        said = self.said(document, items)
        self.assertEqual({item["kind"]: item["said"]["headline"] for item in said}, {kind: words for kind, (_, _, words) in given.items()})
        self.assertTrue(all(item["said"]["clears"] for item in said))

    def test_the_list_opens_with_one_sentence_counting_whom_its_items_wait_on_and_what_holds_the_finish(self) -> None:
        summary = lambda *items: run_delivery.needs_summary(self.RELEASE, [{"holding": held, "who": who} for held, who in items])
        self.assertEqual(summary(), "Nothing needs anyone, and nothing is holding up the finish.")
        self.assertEqual(summary((True, "lead")), "1 thing needs the lead, and it's holding up the finish.")
        self.assertEqual(summary((False, "lead"), (False, "lead")), "2 things need the lead. Nothing is holding up the finish.")
        self.assertEqual(summary((True, "lead"), (False, "operator"), (True, "lane-2")),
                         "1 thing needs you, 1 thing needs the lead and 1 thing needs Lane 2, and 2 of them are holding up the finish.")
        self.assertEqual(summary((True, "lead"), (False, "lead")), "2 things need the lead, and 1 of them is holding up the finish.")
        self.assertEqual(summary((True, "lead"), (True, "operator")),
                         "1 thing needs you and 1 thing needs the lead, and all of them are holding up the finish.")
        self.assertEqual(run_delivery.needs_words(self.RELEASE),
                         {"heading": "Waiting on someone", "holding": "Holding up the finish", "age": "for {age}"})


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

    def test_a_wait_the_record_holds_stays_a_blocker_when_the_tracker_calls_its_target_completed(self) -> None:
        document = record()
        planned(document, "APP-2")["lane"] = None
        snapshot = {"items": [{"subject": "APP-1", "state": "completed"}, {"subject": "APP-2", "state": "backlog"}]}
        entry = self.ranked(document, "lane-1", snapshot)["APP-2"]
        self.assertEqual((entry["blocked"], shape(entry["waits"])), (True, [("order", "APP-1")]))
        self.assertIn("blocked by APP-1", entry["reason"])

    def test_with_a_snapshot_it_still_adds_the_trackers_blocked_by(self) -> None:
        document = record()
        planned(document, "APP-4")["lane"] = None
        snapshot = {"items": [{"subject": "APP-4", "state": "backlog", "blockedBy": ["APP-9"]},
                              {"subject": "APP-9", "state": "started"}]}
        entry = self.ranked(document, "lane-1", snapshot)["APP-4"]
        self.assertTrue(entry["blocked"])
        self.assertIn("blocked by APP-9", entry["reason"])


class SiblingWaitTests(unittest.TestCase):
    """An `after` key that names another repository's story: a sibling delivery's, or, in a record with no siblings,
    nothing this record can see."""

    def ranked(self, document: dict, lane: str, snapshot: dict | None = None) -> dict:
        return {entry["subject"]: entry for entry in state_tool.next_for_lane(document["state"], lane, snapshot, None)}

    def test_without_siblings_an_after_key_outside_the_record_raises_no_wait_and_next_offers_the_story(self) -> None:
        """Scenario 18, pinned before any change: today's behaviour for every record without siblings."""
        document = record()
        planned(document, "APP-4")["lane"] = None
        planned(document, "APP-4")["after"] = ["MOB-7"]
        self.assertNotIn("siblings", document["state"])
        self.assertEqual(of(document, "APP-4"), [])
        entry = self.ranked(document, "lane-1")["APP-4"]
        self.assertEqual((entry["blocked"], entry["waits"]), (False, []))
        # A record whose last sibling was removed keeps an empty list, and behaves as one that never had any.
        document["state"]["siblings"] = []
        self.assertEqual(state_tool.state_errors(document["state"]), [])
        self.assertEqual(of(document, "APP-4"), [])
        self.assertFalse(self.ranked(document, "lane-1")["APP-4"]["blocked"])

    # -- a record with siblings ------------------------------------------------------------------------------------

    LINK = "https://views.example.com/d/" + "a" * 64
    READ_AT = "2026-11-18T10:05:00Z"

    def sibling(self, name: str = "mobile", outcome: str | None = "read", stage: str = "building",
                subject: str = "MOB-7") -> dict:
        row = {"name": name, "link": f"https://{name}.example.com/d/{'a' * 64}", "addedAt": "2026-11-18T10:00:00Z"}
        if outcome is not None:
            row.update(lastOutcome=outcome, lastAttemptAt="2026-11-18T10:20:00Z", readAt=self.READ_AT,
                       stories=[{"subject": subject, "title": "The mobile story", "stage": stage}])
        return row

    def after_sibling(self, *rows: dict, key: str = "MOB-7") -> dict:
        """The fixture record with APP-4, unassigned, after ``key``, and these siblings."""
        document = record()
        planned(document, "APP-4")["lane"] = None
        planned(document, "APP-4")["after"] = [key]
        document["state"]["meta"]["trackerParent"] = "EPIC-1"
        document["state"]["siblings"] = list(rows)
        self.assertEqual(state_tool.state_errors(document["state"]), [])
        return document

    def needs(self, document: dict) -> list[dict]:
        return run_delivery.waits_needing(SimpleNamespace(release=PRODUCT_ROOT), document, [])

    def test_a_story_after_a_siblings_unlanded_story_waits_and_next_doesnt_offer_it(self) -> None:
        """Scenario 19."""
        [wait] = of(self.after_sibling(self.sibling()), "APP-4")
        self.assertEqual({key: wait[key] for key in ("on", "cause", "stage", "frees", "needsSomeone", "words")},
                         {"on": "MOB-7", "cause": "sibling", "stage": "building", "frees": "landed",
                          "needsSomeone": False, "words": "Waits for MOB-7 to land in mobile. Building."})
        entry = self.ranked(self.after_sibling(self.sibling()), "lane-1")["APP-4"]
        self.assertEqual((entry["blocked"], shape(entry["waits"])), (True, [("sibling", "MOB-7")]))
        self.assertIn("blocked by MOB-7", entry["reason"])
        self.assertEqual(self.needs(self.after_sibling(self.sibling())), [])

    def test_a_fresh_read_showing_it_landed_frees_the_story_and_wakes_the_lead_with_no_lead_command(self) -> None:
        """Scenarios 20 and 21, through reconcile on a record on disk: two stale reads keep the wait, saying when the
        sibling was last read; a fresh read lands it, and the lead's wait returns on sibling.landed."""
        with tempfile.TemporaryDirectory(prefix="sibling-wait-") as folder:
            directory = Path(folder) / "record"
            document = self.after_sibling()
            directory.mkdir()
            (directory / "state.json").write_text(json.dumps(document["state"]))
            # The fixture's journal is a reader's sketch, not a writer's: the record on disk starts with none.
            (directory / "journal.jsonl").write_text("")
            state_tool.set_sibling(directory, name="mobile", link=self.LINK)

            def apply(stage: str, outcome: str = "read", age: float = 0) -> None:
                read_at = (datetime.datetime.now(datetime.timezone.utc)
                           - datetime.timedelta(seconds=age)).strftime("%Y-%m-%dT%H:%M:%SZ")
                entry = {"name": "mobile", "link": self.LINK, "readAt": read_at, "outcome": outcome,
                         "trackerParent": "EPIC-1", "stale": outcome == "stale", "terminal": False,
                         "stories": [{"subject": "MOB-7", "title": "The mobile story", "stage": stage}]}
                path = Path(folder) / "siblings.json"
                path.write_text(json.dumps({"schemaVersion": "sibling-snapshot/1", "takenAt": read_at, "siblings": [entry]}))
                state_tool.reconcile(directory, [path])

            def shown() -> dict:
                return {"state": state_tool.read_state(directory), "journal": state_tool.read_journal(directory)}

            apply("building")
            for stale in (dict(outcome="stale"), dict(age=2000)):
                with self.subTest(stale=stale):
                    apply("merged", **stale)
                    began = shown()["state"]["siblings"][0]["lastAttemptAt"]
                    [wait] = of(shown(), "APP-4")
                    self.assertEqual(wait["words"], f"Waits for MOB-7 to land in mobile. Stale since {began}.")
                    self.assertTrue(self.ranked(shown(), "lane-1")["APP-4"]["blocked"])
            after = shown()["journal"][-1]["id"]
            apply("merged")
            self.assertEqual(of(shown(), "APP-4"), [])
            entry = self.ranked(shown(), "lane-1")["APP-4"]
            self.assertEqual((entry["blocked"], entry["waits"]), (False, []))
            code, woke = state_tool.wait_for_lead(directory, after=after, timeout=5, poll=0.01,
                                                   read_machine=lambda _: {"cores": 16, "load5": 1.0, "diskFreeBytes": 500 * 2**30, "swapUsedBytes": 2**30},
                                                   sleep=lambda _: None)
            self.assertEqual(code, 0, woke)
            self.assertIn("sibling.landed", [event["kind"] for event in woke["events"]])
            # No lead command ran: the journal holds no entry of the lead's since the sibling was named.
            self.assertEqual([entry["kind"] for entry in shown()["journal"][-1:]], ["sibling.landed"])

    def test_a_key_no_sibling_holds_needs_the_lead_only_when_every_same_epic_sibling_is_read(self) -> None:
        """Scenario 22."""
        cases = {
            "read": ((self.sibling(),), True, "Waits for ZZZ-1, which no story here or sibling lists: add the sibling, "
                                              "or fix the key."),
            "unavailable": ((self.sibling(outcome="unavailable"),), False,
                            "Waits for ZZZ-1, which no story here holds; mobile hasn't been read yet."),
            "never read": ((self.sibling(outcome=None),), False,
                           "Waits for ZZZ-1, which no story here holds; mobile hasn't been read yet."),
            "read, beside one not fresh": ((self.sibling(), self.sibling("web", outcome="unavailable", subject="WEB-1")),
                                           False, "Waits for ZZZ-1, which no story here holds; web hasn't been read yet."),
            "read, beside another epic's": ((self.sibling(), self.sibling("web", outcome="other-epic", subject="ZZZ-1")),
                                            True, "Waits for ZZZ-1, which no story here or sibling lists: add the "
                                                  "sibling, or fix the key."),
        }
        for name, (rows, needed, words) in cases.items():
            with self.subTest(case=name):
                document = self.after_sibling(*rows, key="ZZZ-1")
                [wait] = of(document, "APP-4")
                self.assertEqual((wait["cause"], wait["on"], wait["needsSomeone"], wait["words"]),
                                 ("sibling", "ZZZ-1", needed, words))
                self.assertTrue(self.ranked(document, "lane-1")["APP-4"]["blocked"])
                listed = [item for item in self.needs(document) if item.get("kind") == "sibling"]
                self.assertEqual([(item["subject"], item["text"]) for item in listed],
                                 [("APP-4", words)] if needed else [])
        said = run_delivery.say_needs(SimpleNamespace(release=PRODUCT_ROOT), self.after_sibling(self.sibling(), key="ZZZ-1"),
                                      [item for item in self.needs(self.after_sibling(self.sibling(), key="ZZZ-1"))
                                       if item.get("kind") == "sibling"])
        self.assertEqual(said[0]["said"]["headline"], "APP-4 comes after ZZZ-1, which no sibling lists")

    def test_the_trackers_blocker_and_the_siblings_read_must_both_say_done(self) -> None:
        """Scenario 23."""
        for stage in ("merged", "settled"):
            with self.subTest(stage=stage):
                done = self.after_sibling(self.sibling(stage=stage))
                self.assertEqual(of(done, "APP-4"), [])
                self.assertFalse(self.ranked(done, "lane-1")["APP-4"]["blocked"])
        landed = self.after_sibling(self.sibling(stage="merged"))
        snapshot = {"items": [{"subject": "APP-4", "state": "backlog", "blockedBy": ["MOB-7"]},
                              {"subject": "MOB-7", "state": "started"}]}
        self.assertTrue(self.ranked(landed, "lane-1", snapshot)["APP-4"]["blocked"])
        snapshot["items"][1]["state"] = "completed"
        self.assertFalse(self.ranked(landed, "lane-1", snapshot)["APP-4"]["blocked"])
        # The tracker done, the sibling not: still blocked.
        self.assertTrue(self.ranked(self.after_sibling(self.sibling()), "lane-1", snapshot)["APP-4"]["blocked"])

    def test_the_plans_order_in_the_hints_is_met_once_the_siblings_story_reads_landed(self) -> None:
        """Scenario 20 with lead-hints' shape: the hints name every after key as a blocker."""
        hints = {"items": {"APP-4": {"blockers": ["MOB-7"]}}}
        ranked = lambda document, snapshot: {entry["subject"]: entry for entry in
                                             state_tool.next_for_lane(document["state"], "lane-1", snapshot, hints)}
        landed, building = self.after_sibling(self.sibling(stage="merged")), self.after_sibling(self.sibling())
        for stage in ("merged", "settled"):
            done = self.after_sibling(self.sibling(stage=stage))
            for snapshot in (None, {"items": [{"subject": "APP-4", "state": "backlog"}]},
                             {"items": [{"subject": "APP-4", "state": "backlog"}, {"subject": "MOB-7", "state": "started"}]}):
                with self.subTest(stage=stage, snapshot=snapshot):
                    self.assertFalse(ranked(done, snapshot)["APP-4"]["blocked"], ranked(done, snapshot)["APP-4"]["reason"])
                    self.assertTrue(ranked(building, snapshot)["APP-4"]["blocked"])
        # The tracker's blockedBy still has to say done itself (Decision 13).
        blocked_by = {"items": [{"subject": "APP-4", "state": "backlog", "blockedBy": ["MOB-7"]},
                                {"subject": "MOB-7", "state": "started"}]}
        self.assertTrue(ranked(landed, blocked_by)["APP-4"]["blocked"])
        # Another epic's sibling frees nothing, though it shows the story landed: here the unlisted wait holds it.
        self.assertTrue(ranked(self.after_sibling(dict(self.sibling(stage="merged"), lastOutcome="other-epic")),
                               None)["APP-4"]["blocked"])

    def test_a_hints_blocker_only_another_epics_sibling_shows_landed_stays_open(self) -> None:
        """No after key names it, so no wait does: next alone decides, and another epic's read frees nothing."""
        document = self.after_sibling(dict(self.sibling(stage="merged"), lastOutcome="other-epic"))
        planned(document, "APP-4").pop("after")
        self.assertEqual(of(document, "APP-4"), [])
        [entry] = [entry for entry in state_tool.next_for_lane(document["state"], "lane-1", None,
                                                              {"items": {"APP-4": {"blockers": ["MOB-7"]}}})
                   if entry["subject"] == "APP-4"]
        self.assertTrue(entry["blocked"], entry["reason"])

    def test_a_wait_on_a_sibling_not_fresh_says_since_when_its_outcome_began(self) -> None:
        """Read fresh and unchanged for two hours, then unavailable: the wait gives the time it turned unavailable,
        never the time of the last read that changed it. A wait on a fresh sibling says nothing about time."""
        row = self.sibling()
        row.update(readAt="2026-11-18T10:05:00Z", lastOutcome="unavailable", lastAttemptAt="2026-11-18T12:05:00Z")
        [wait] = of(self.after_sibling(row), "APP-4")
        self.assertEqual(wait["words"], "Waits for MOB-7 to land in mobile. Couldn't be read since 2026-11-18T12:05:00Z.")
        self.assertNotIn("10:05", wait["words"])
        for outcome, said in (("stale", "Stale since"), ("incompatible", "Needs a compatible snapshot since")):
            with self.subTest(outcome=outcome):
                [wait] = of(self.after_sibling(dict(row, lastOutcome=outcome)), "APP-4")
                self.assertEqual(wait["words"], f"Waits for MOB-7 to land in mobile. {said} 2026-11-18T12:05:00Z.")
        # A row edited by hand, with stories but no outcome or attempt: the sentence falls back to readAt.
        bare = self.sibling()
        del bare["lastOutcome"], bare["lastAttemptAt"]
        [wait] = of(self.after_sibling(bare), "APP-4")
        self.assertEqual(wait["words"], f"Waits for MOB-7 to land in mobile. Not read fresh since {self.READ_AT}.")
        [fresh] = of(self.after_sibling(self.sibling()), "APP-4")
        self.assertEqual(fresh["words"], "Waits for MOB-7 to land in mobile. Building.")
        self.assertNotRegex(fresh["words"], r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

    def test_an_idle_lane_next_on_the_story_says_it_waits_on_the_sibling(self) -> None:
        document = self.after_sibling(self.sibling())
        planned(document, "APP-4")["lane"] = "lane-1"
        planned(document, "APP-2")["lane"] = "lane-3"
        lane = [wait for wait in state_tool.waits(document) if wait["subject"] == "lane-1"]
        self.assertEqual([(wait["cause"], wait["words"]) for wait in lane],
                         [("sibling", "Lane 1 is next on APP-4, which waits for MOB-7 to land in mobile.")])


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
        self.assertEqual(set(table["waits"]["templates"]), {"order", "sibling", "files", "claim", "slot", "review", "held"})
        self.assertEqual(set(table["lanes"]["templates"]), {"order", "sibling", "files", "claim", "held", "waiting"})
        self.assertEqual(set(table["needsYou"]["templates"]), {"count", "free", "stopped", "headline", "why", "clears", "who",
                                                               "whom", "part", "summary", "heading", "holding", "age",
                                                               "leftOpen"})
        self.assertEqual(state_tool.wait_said(table, "needsYou", "count", {"count": 1}), "1 piece waits on it.")
        self.assertEqual(state_tool.wait_said(table, "needsYou", "count", {"count": 3}), "3 pieces wait on it.")

    def test_every_cause_a_wait_gives_is_one_the_table_orders(self) -> None:
        self.assertEqual(state_tool.WAIT_CAUSES, ("order", "sibling", "files", "lane", "claim", "slot", "review", "held"))


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
FIXED = ("Waits for", "before it finishes", "Held by the lead", "is next on", "Waited on by", "free to start", "pieces wait on it",
         "piece waits on it", "was stopped.", "can't reach the lead", "watching again")
MARK = re.compile(r"⟦([a-zA-Z]+\.[a-zA-Z]+(?:\.[a-zA-Z0-9]+)?)⟧")


class MarkerTests(unittest.TestCase):
    """With each template marked, every wait sentence on a view carries its template's marker, and no view shows one
    without: the views print the derivation's words and build no sentence of their own."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.copy = Path(folder.name) / "wait-words.json"
        self.copy.write_text(json.dumps(marked(json.loads(WORDS.read_text(encoding="utf-8"))), ensure_ascii=False),
                             encoding="utf-8")
        # Every loaded copy of the state tool, so a module another test loaded first reads the marked table too.
        copies = {id(module): module for module in (state_tool, views.review_state, *list(sys.modules.values()))
                  if getattr(module, "WAIT_WORDS_FILE", None) is not None and hasattr(module, "load_wait_words")}
        for module in copies.values():
            patcher = mock.patch.object(module, "WAIT_WORDS_FILE", self.copy)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.document = record()
        journal(self.document, "story.held", "APP-4", {"text": "the budget"})
        row(self.document, "lane-2")["state"] = "idle"
        # A lane waiting on a story before it finishes its own.
        self.document["state"]["lanes"].append({"id": "lane-4", "title": "Spare", "focus": "", "state": "waiting",
                                                "waitingOn": "APP-1", "since": "2026-11-17T10:00:00Z",
                                                "waitingFrom": {"state": "working", "at": "2026-11-17T11:00:00Z"}})
        self.document["state"]["planned"].append({"subject": "APP-5", "title": "Spare work", "lane": "lane-4",
                                                  "stage": "building"})

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
                                   "lanes.waiting", "holding.story"})
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

    def test_every_word_of_a_needs_you_row_and_of_the_list_is_the_tables(self) -> None:
        document = self.document
        queue(document, "APP-1", "stopped")
        row(document, "lane-3").update(state="blocked", focus="its schema", waitingOn="operator")
        release = SimpleNamespace(release=PRODUCT_ROOT)
        items = run_delivery.lane_needs(document["state"]) + run_delivery.record_needs(document, [])
        said = run_delivery.say_needs(release, document, items + run_delivery.waits_needing(release, document, items))
        self.assertEqual({item["kind"] for item in said}, {"laneBlocked", "stopped"})
        for item in said:
            for name, words in item["said"].items():
                self.assertRegex(words, rf"^⟦needsYou\.{name}(\.[a-zA-Z0-9]+)?⟧", (item["kind"], name))
        self.assertRegex(run_delivery.needs_summary(release, said), r"^⟦needsYou\.summary\.one⟧ ⟦needsYou\.part\.1⟧ 1 thing needs ⟦needsYou\.whom\.operator⟧ you")
        self.assertEqual(run_delivery.needs_words(release), {"heading": "⟦needsYou.heading⟧ Waiting on someone",
                                                             "holding": "⟦needsYou.holding⟧ Holding up the finish",
                                                             "age": "⟦needsYou.age⟧ for {age}"})


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
