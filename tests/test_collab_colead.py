"""Lane and queue scopes on a shared record: who writes each lane's rows and queue elements, the runner only where the
seat holds the queue, the queue's serve only there, and the queue's handover.

An unshared record is characterized first: the runner's writes, a lane's hand-off and the lead's acceptance stay as they
are. The shared cases use the partitions tests' fixture, and, for the queue's own runs, the merge-queue tests' adopter.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests import test_collab_partitions as partitions
from tests import test_merge_queue as mq

state_tool, collab, KEY = partitions.state_tool, partitions.collab, partitions.KEY
HEAD, OTHER = "4e66a0571b2c3d4e5f60718293a4b5c6d7e8f901", "9f3c2b1a0e9d8c7b6a5f4e3d2c1b0a9f8e7d6c5b"


def lane_row(lane: str) -> dict:
    return {"id": lane, "title": lane.title(), "focus": "Idle", "state": "idle", "waitingOn": None,
            "since": "2026-11-24T00:00:00Z"}


def planned(subject: str, lane: str = "lane-1") -> dict:
    return {"subject": subject, "title": f"Story {subject}", "lane": lane, "stage": "planned"}


class UnsharedQueueCharacterizationTests(unittest.TestCase):
    """What an unshared record lets the runner, a lane and the lead write today, which scopes leave as it is."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name) / "record"
        state_tool.init(self.directory, "Unshared", seed={"planned": [planned("APP-1")]})
        state = state_tool.read_state(self.directory)
        state_tool.patch(self.directory, writer="lead", version=state["version"], changes={"lanes": [lane_row("lane-1")]})

    def state(self) -> dict:
        return state_tool.read_state(self.directory)

    def test_a_hand_off_queues_the_story_at_its_starting_values_and_the_acceptance_frees_it(self) -> None:
        state_tool.patch(self.directory, writer="lead", version=self.state()["version"],
                         changes={"meta": {"handoffsNeedAcceptance": True}})
        state_tool.handoff(self.directory, lane="lane-1", subject="APP-1", head=HEAD, change_request="cr-1")
        item = next(entry for entry in self.state()["queue"] if entry["subject"] == "APP-1")
        self.assertEqual({key: item[key] for key in state_tool.QUEUE_START}, state_tool.QUEUE_START)
        self.assertTrue(item.get("pending"))
        self.assertEqual(self.state().get("planned"), [])
        state_tool.accept_handoff(self.directory, "APP-1")
        item = next(entry for entry in self.state()["queue"] if entry["subject"] == "APP-1")
        self.assertEqual((item["stage"], item.get("pending")), ("queued", None))
        kinds = [entry["kind"] for entry in state_tool.read_journal(self.directory)]
        self.assertEqual(kinds[-2:], ["candidate.handed", "handoff.accepted"])

    def test_the_runner_writes_the_queues_fields_and_journals_and_the_lead_may_not_write_them(self) -> None:
        state_tool.handoff(self.directory, lane="lane-1", subject="APP-1", head=HEAD, change_request="cr-1")
        written = state_tool.patch(self.directory, writer="runner", version=self.state()["version"],
                                   selector="queue[subject=APP-1]", changes={"stage": "checking"})
        self.assertEqual(next(entry for entry in written["queue"])["stage"], "checking")
        self.assertEqual(state_tool.append_journal(self.directory, kind="check.started", subject="APP-1", data={},
                                                   writer="runner")["kind"], "check.started")
        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.patch(self.directory, writer="lead", version=self.state()["version"],
                             selector="queue[subject=APP-1]", changes={"stage": "merged"})
        self.assertEqual(refused.exception.code, "field_not_owned")

    def test_a_lane_writes_its_own_rows_focus_through_the_tool(self) -> None:
        written = state_tool.set_focus(self.directory, "lane-1", "Building APP-1", state="working")
        row = next(entry for entry in written["lanes"] if entry["id"] == "lane-1")
        self.assertEqual((row["focus"], row["state"]), ("Building APP-1", "working"))


class LaneScopeFixture(partitions.SharedFixture):
    """a opens the record and admits b; b holds lane-1 and a keeps lane-2, the queue and delivery."""

    def setUp(self) -> None:
        super().setUp()
        self.first = self.open()
        self.second = self.join(self.b)
        self.a.patch({"lanes": [lane_row("lane-1"), lane_row("lane-2")],
                      "planned": [planned("APP-1", "lane-1"), planned("APP-2", "lane-2")]})
        self.give("lane:lane-1", self.b)

    def refused(self, write, machine, code: str = "scope.not_held") -> state_tool.StateError:
        before = machine.surfaces(self.remote)
        with self.assertRaises(state_tool.StateError) as refused:
            write()
        self.assertEqual(refused.exception.code, code)
        self.assertEqual(machine.surfaces(self.remote), before)
        return refused.exception

    def element(self, machine, array: str, key: str, name: str) -> dict | None:
        return next((item for item in machine.state().get(array, []) if item.get(key) == name), None)


class LaneAndRunnerTests(LaneScopeFixture):
    """Each lead writes its own lane's rows and queue elements; the runner writes only where its seat holds the queue."""

    def test_each_lead_writes_its_own_lanes_rows_and_not_the_others(self) -> None:
        state_tool.set_focus(self.b.directory, "lane-1", "Building APP-1", state="working")
        said = self.refused(lambda: state_tool.set_focus(self.b.directory, "lane-2", "Not b's"), self.b)
        self.assertIn(f"lane:lane-2 by seat {self.first}", str(said))
        self.refused(lambda: state_tool.patch(self.a.directory, writer="lead", version=self.a.state()["version"],
                                              selector="planned[subject=APP-1]", changes={"title": "a's"}), self.a)
        state_tool.patch(self.a.directory, writer="lead", version=self.a.state()["version"],
                         selector="planned[subject=APP-2]", changes={"title": "a's own"})
        self.exchange(self.a, self.b)
        self.assertEqual(self.element(self.a, "lanes", "id", "lane-1")["focus"], "Building APP-1")
        self.assertEqual(self.element(self.b, "planned", "subject", "APP-2")["title"], "a's own")

    def test_a_runner_write_where_the_seat_does_not_hold_the_queue_is_refused_and_the_holders_applies(self) -> None:
        state_tool.handoff(self.b.directory, lane="lane-1", subject="APP-1", head=HEAD, change_request="cr-1")
        self.b.push()
        self.a.take()
        runner = lambda machine: state_tool.patch(machine.directory, writer="runner", version=machine.state()["version"],
                                                  selector="queue[subject=APP-1]", changes={"stage": "checking"})
        said = self.refused(lambda: runner(self.b), self.b)
        self.assertIn(f"seat {self.first}", str(said))
        self.refused(lambda: state_tool.append_journal(self.b.directory, kind="check.started", subject="APP-1",
                                                       data={}, writer="runner"), self.b)
        self.assertEqual(self.element(self.a, "queue", "subject", "APP-1")["stage"], "queued")
        self.assertEqual(self.element(runner(self.a) and self.a, "queue", "subject", "APP-1")["stage"], "checking")

    def test_a_lanes_write_of_a_queue_field_away_from_its_start_is_refused_and_its_peers_ignore_it(self) -> None:
        state_tool.handoff(self.b.directory, lane="lane-1", subject="APP-1", head=HEAD, change_request="cr-1")
        self.b.push()
        self.a.take()
        # Locally only the runner writes a queue element's runner fields, and b's runner may not run at all.
        for change in ({"stage": "merged"}, {"checks": [{"id": "always", "state": "passed"}]}):
            with self.subTest(change=change):
                self.refused(lambda: state_tool.patch(self.b.directory, writer="lead", version=self.b.state()["version"],
                                                      selector="queue[subject=APP-1]", changes=change), self.b,
                             "field_not_owned")
                self.refused(lambda: state_tool.patch(self.b.directory, writer="runner", version=self.b.state()["version"],
                                                      selector="queue[subject=APP-1]", changes=change), self.b)
        seen = {seat: count for seat, count in state_tool.seat_counts(self.b.journal()).items() if seat != self.second}
        own = max(entry["origin"]["seq"] for entry in self.b.journal() if entry["origin"]["seat"] == self.second)
        self.plant(self.second, [{"path": "queue[subject=APP-1]/stage", "value": "merged",
                                  "origin": {"seat": self.second, "seq": own, "seen": seen}}])
        self.a.take()
        self.assertEqual(self.element(self.a, "queue", "subject", "APP-1")["stage"], "queued")

    def test_a_hand_off_acceptance_or_rejection_of_another_lanes_story_is_refused_and_ones_own_is_applied(self) -> None:
        self.a.patch({"meta": {"handoffsNeedAcceptance": True}})
        self.a.push()
        self.b.take()
        self.refused(lambda: state_tool.handoff(self.b.directory, lane="lane-2", subject="APP-2", head=HEAD,
                                                change_request="cr-2"), self.b)
        state_tool.handoff(self.a.directory, lane="lane-2", subject="APP-2", head=HEAD, change_request="cr-2")
        self.a.push()
        self.b.take()
        self.refused(lambda: state_tool.accept_handoff(self.b.directory, "APP-2"), self.b)
        self.refused(lambda: state_tool.reject_handoff(self.b.directory, "APP-2", "Not b's."), self.b)
        state_tool.handoff(self.b.directory, lane="lane-1", subject="APP-1", head=HEAD, change_request="cr-1")
        self.assertEqual(state_tool.accept_handoff(self.b.directory, "APP-1")["stage"], "queued")
        # Once b passes lane-1 on, its lane session's later hand-off is refused on b before anything is written.
        collab.give_scope(self.b.directory, "lane:lane-1", self.first)
        self.refused(lambda: state_tool.handoff(self.b.directory, lane="lane-1", subject="APP-1", head=OTHER,
                                                change_request="cr-1"), self.b)

    def test_a_no_change_hand_off_of_another_lanes_story_and_its_acceptance_are_refused(self) -> None:
        self.a.patch({"meta": {"handoffsNeedAcceptance": True}})
        self.a.push()
        self.b.take()
        self.refused(lambda: state_tool.no_change_handoff(self.b.directory, lane="lane-2", subject="APP-2",
                                                          base_head=HEAD, evidence="It already holds."), self.b)
        state_tool.no_change_handoff(self.a.directory, lane="lane-2", subject="APP-2", base_head=HEAD,
                                     evidence="It already holds.")
        self.a.push()
        self.b.take()
        self.refused(lambda: state_tool.accept_handoff(self.b.directory, "APP-2"), self.b)
        self.assertEqual(state_tool.accept_handoff(self.a.directory, "APP-2")["stage"], "settled")

    def test_a_lanes_start_value_on_an_element_the_queue_has_moved_on_is_ignored(self) -> None:
        state_tool.handoff(self.b.directory, lane="lane-1", subject="APP-1", head=HEAD, change_request="cr-1")
        self.b.push()
        self.a.take()
        own = max(entry["origin"]["seq"] for entry in self.b.journal() if entry["origin"]["seat"] == self.second)
        seen = {seat: count for seat, count in state_tool.seat_counts(self.b.journal()).items() if seat != self.second}
        for stage in ("merging", "merged"):
            with self.subTest(stage=stage):
                state_tool.patch(self.a.directory, writer="runner", version=self.a.state()["version"],
                                 selector="queue[subject=APP-1]", changes={"stage": stage})
                # It names a's value as the one it replaces, so only the stage rule can keep a's.
                held = json.loads((self.a.directory / state_tool.FIELD_ORIGINS_FILE).read_text())["queue[subject=APP-1]/stage"]
                replaces = {"seat": held["seat"], "seq": held["seq"], "digest": state_tool.value_digest(stage)}
                self.plant(self.second, [{"path": "queue[subject=APP-1]/stage", "value": "queued", "replaces": replaces,
                                          "origin": {"seat": self.second, "seq": own, "seen": seen}}])
                self.a.take()
                self.assertEqual(self.element(self.a, "queue", "subject", "APP-1")["stage"], stage)

    def test_a_hand_off_that_ends_a_failed_run_puts_the_queue_holders_checking_row_back_at_its_start(self) -> None:
        for failing in ("failed", "timed_out"):
            with self.subTest(failing=failing):
                if failing != "failed":
                    self.setUp()
                self.superseded_over(failing)

    def superseded_over(self, failing: str) -> None:
        state_tool.handoff(self.b.directory, lane="lane-1", subject="APP-1", head=HEAD, change_request="cr-1")
        self.b.push()
        self.a.take()
        running = [{"id": "always", "state": failing}, {"id": "full", "state": "running"}]
        state_tool.patch(self.a.directory, writer="runner", version=self.a.state()["version"],
                         selector="queue[subject=APP-1]", changes={"stage": "checking", "checks": running})
        self.a.push()
        self.b.take()
        # b's acceptance of the hand-off at a new head, as _take_handed writes it over the failed run.
        origins = json.loads((self.a.directory / state_tool.FIELD_ORIGINS_FILE).read_text())
        held = origins["queue[subject=APP-1]/stage"]
        own = max(entry["origin"]["seq"] for entry in self.b.journal() if entry["origin"]["seat"] == self.second)
        seen = {seat: count for seat, count in state_tool.seat_counts(self.b.journal()).items() if seat != self.second}
        origin = {"seat": self.second, "seq": own, "seen": seen}
        self.plant(self.second, [
            {"path": "queue[subject=APP-1]/head", "value": OTHER, "origin": origin},
            {"path": "queue[subject=APP-1]/stage", "value": "queued", "origin": origin,
             "replaces": {"seat": held["seat"], "seq": held["seq"], "digest": state_tool.value_digest("checking")}},
            {"path": "queue[subject=APP-1]/checks", "origin": origin,
             "value": [{"id": "always", "state": failing}, {"id": "full", "state": "superseded"}],
             "replaces": {"seat": origins["queue[subject=APP-1]/checks"]["seat"],
                          "seq": origins["queue[subject=APP-1]/checks"]["seq"], "digest": state_tool.value_digest(running)}}])
        self.a.take()
        item = self.element(self.a, "queue", "subject", "APP-1")
        self.assertEqual((item["stage"], item["head"], item["checks"][1]["state"]), ("queued", OTHER, "superseded"))
        # Past the boundary: a reset over a checking run with no failed check, or at the same head, is the queue's.
        for checks, head in (([{"id": "always", "state": "passed"}, {"id": "full", "state": "running"}], "a new head"),
                             (running, "the same head")):
            with self.subTest(past=head):
                state_tool.patch(self.a.directory, writer="runner", version=self.a.state()["version"],
                                 selector="queue[subject=APP-1]", changes={"stage": "checking", "checks": checks})
                origins = json.loads((self.a.directory / state_tool.FIELD_ORIGINS_FILE).read_text())
                held = origins["queue[subject=APP-1]/stage"]
                own = max(entry["origin"]["seq"] for entry in self.b.journal() if entry["origin"]["seat"] == self.second)
                origin = {"seat": self.second, "seq": own, "seen": seen}
                moved = [{"path": "queue[subject=APP-1]/head", "value": HEAD, "origin": origin}] if head == "a new head" else []
                self.plant(self.second, [*moved, {
                    "path": "queue[subject=APP-1]/stage", "value": "queued", "origin": origin,
                    "replaces": {"seat": held["seat"], "seq": held["seq"], "digest": state_tool.value_digest("checking")}}])
                self.a.take()
                self.assertEqual(self.element(self.a, "queue", "subject", "APP-1")["stage"], "checking")
        # Marking a finished check, or one the run never had, is not the run ending: it is the queue's to write.
        if failing != "failed":
            return
        self.assertFalse(collab._superseding({"checks": running}, [{"id": "always", "state": "passed"},
                                                                  {"id": "full", "state": "superseded"}]))
        self.assertFalse(collab._superseding({"checks": running}, [*running, {"id": "lint", "state": "superseded"}]))

    def test_the_delivery_holders_adoption_of_a_missed_hand_off_reaches_the_queue_holder_at_its_start(self) -> None:
        collab.give_scope(self.a.directory, "delivery", self.second)
        self.a.push()
        self.b.take()
        item = {"order": 1, "subject": "APP-2", "title": "Story APP-2", "lane": "lane-2", "stage": "queued",
                "head": HEAD, "syncedHead": None, "leadInputs": [], "reportedRounds": 1, "runId": None, "checks": []}
        # As b's reconcile adopts a hand-off it missed: the planned story becomes a queue element at its start.
        state_tool.patch(self.b.directory, writer="lead", version=self.b.state()["version"],
                         changes={"planned": [planned("APP-1", "lane-1")], "queue": [item]})
        self.b.push()
        self.a.take()
        adopted = self.element(self.a, "queue", "subject", "APP-2")
        self.assertEqual({key: adopted[key] for key in state_tool.QUEUE_START}, state_tool.QUEUE_START)
        self.assertIsNone(self.element(self.a, "planned", "subject", "APP-2"))

    def test_a_reuse_grant_recorded_on_a_names_its_entry_by_origin_on_b_once_b_takes_the_queue(self) -> None:
        record = {"check": "full", "definition": "sha256:" + "d" * 64, "head": HEAD, "tree": "e" * 40,
                  "result": "passed", "where": "runner"}
        state_tool.handoff(self.a.directory, lane="lane-2", subject="APP-2", head=HEAD, change_request="cr-2",
                           evidence=[record])
        check = {"id": "parser", "argv": ["python3", "-m", "pytest", "tests/parser"], "cwd": ".",
                 "reasons": ["the parser changed"], "prerequisites": []}
        binding = {"selection": {"lead": True}, "alwaysRun": [{**check, "id": "always"}],
                   "fallback": [{**check, "id": "fallback"}], "profiles": {"default": {"environment": {}}},
                   "checkTimeoutCeiling": 600}
        granted = state_tool.select_checks(self.a.directory, "APP-2", binding=binding, head=HEAD, checks=[check],
                                           reason="only the parser changed", reuse=["full"], why="docs only since")
        grant = next(entry for entry in self.a.journal() if entry["id"] == granted["leadSelection"]["reuse"]["decision"])
        self.b.decide("Only on b, so b's local ids run ahead of a's.")
        collab.give_scope(self.a.directory, "queue", self.second)
        self.exchange(self.a, self.b)
        self.assertEqual(state_tool.holder(self.b.journal(), "queue"), self.second)
        on_b = self.element(self.b, "queue", "subject", "APP-2")["leadSelection"]["reuse"]["decision"]
        self.assertEqual(on_b, self.b.local_id(grant["origin"]["seat"], grant["origin"]["seq"]))
        self.assertNotEqual(on_b, grant["id"])
        self.assertEqual(next(entry for entry in self.b.journal() if entry["id"] == on_b)["kind"], "decision.recorded")

    def test_holders_and_refusals_are_the_same_whatever_the_labels(self) -> None:
        def seen() -> tuple:
            journal = self.b.journal()
            try:
                state_tool.set_focus(self.b.directory, "lane-2", "Not b's")
                refusal = None
            except state_tool.StateError as error:
                refusal = error.code
            return tuple(state_tool.holder(journal, scope) for scope in ("delivery", "queue", "lane:lane-1", "lane:lane-2")) \
                + (refusal,)
        before = seen()
        self.assertEqual(before, (self.first, self.first, self.second, self.first, "scope.not_held"))
        for first_label, second_label in (("B", "A"), ("Same", "Same")):
            with self.subTest(labels=(first_label, second_label)):
                collab.label_seat(self.a.directory, first_label)
                collab.label_seat(self.b.directory, second_label)
                self.exchange(self.a, self.b)
                self.assertEqual(seen(), before)


class HandoverTests(LaneScopeFixture):
    """The queue passes on only once it is idle: no check running and no merge in progress."""

    def runner(self, **changes) -> dict:
        return state_tool.patch(self.a.directory, writer="runner", version=self.a.state()["version"], changes=changes)

    def hand_over(self, wait: float = 0.05) -> dict:
        return collab.give_scope(self.a.directory, "queue", self.second, wait=wait, poll=0.01)

    def test_a_handover_while_a_check_runs_waits_then_refuses_naming_the_check(self) -> None:
        running = {"subject": "APP-2", "title": "Story APP-2", "startedAt": "2026-11-24T00:00:00Z", "steps": []}
        self.runner(meta={"checking": {"now": running, "next": []}})
        said = self.refused(self.hand_over, self.a, "scope.handover_busy")
        self.assertIn("the check of APP-2 (Story APP-2) is running", str(said))
        self.assertEqual(state_tool.holder(self.a.journal(), "queue"), self.first)

    def test_a_handover_while_a_merge_is_in_progress_waits_then_refuses_naming_the_merge(self) -> None:
        state_tool.handoff(self.a.directory, lane="lane-2", subject="APP-2", head=HEAD, change_request="cr-2")
        state_tool.patch(self.a.directory, writer="runner", version=self.a.state()["version"],
                         selector="queue[subject=APP-2]", changes={"stage": "merging"})
        said = self.refused(self.hand_over, self.a, "scope.handover_busy")
        self.assertIn("the merge of APP-2 is in progress", str(said))

    def test_a_handover_while_a_turn_holds_the_queue_lock_waits_for_it(self) -> None:
        with state_tool.file_lock(self.a.directory / state_tool.QUEUE_LOCK):
            said = self.refused(self.hand_over, self.a, "scope.handover_busy")
        self.assertIn("a turn of the queue is running", str(said))

    def test_a_queue_that_turns_idle_within_the_wait_is_handed_over(self) -> None:
        running = {"subject": "APP-2", "title": "Story APP-2", "startedAt": "2026-11-24T00:00:00Z", "steps": []}
        self.runner(meta={"checking": {"now": running, "next": []}})
        slept = []

        def finish(_: float) -> None:
            if not slept:
                state_tool.patch(self.a.directory, writer="runner", version=self.a.state()["version"],
                                 assignments={"/meta/checking/now": None})
            slept.append(True)

        moved = collab.give_scope(self.a.directory, "queue", self.second, wait=5, poll=0.01, sleep=finish)
        self.assertEqual((moved["kind"], len(slept) >= 1), ("scope.transferred", True))
        self.assertEqual([entry["kind"] for entry in self.a.journal()[-2:]], ["queue.stopped", "scope.transferred"])

    def test_scope_give_takes_its_wait(self) -> None:
        running = {"subject": "APP-2", "title": "Story APP-2", "startedAt": "2026-11-24T00:00:00Z", "steps": []}
        self.runner(meta={"checking": {"now": running, "next": []}})
        ran = subprocess.run([sys.executable, "-B", str(partitions.TOOL), "--dir", str(self.a.directory), "scope", "give",
                              "--scope", "queue", "--to", self.second, "--wait", "0.1"],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual((ran.returncode, json.loads(ran.stdout)["code"]), (1, "scope.handover_busy"))

    def test_the_supervisor_starts_no_serve_where_the_seat_does_not_hold_the_queue(self) -> None:
        spec = importlib.util.spec_from_file_location("run_delivery_for_colead",
                                                      partitions.PRODUCT_ROOT / "skills/run-delivery/scripts/run_delivery.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def argv(machine) -> list[str] | None:
            c = types.SimpleNamespace(release=partitions.PRODUCT_ROOT, state=machine.directory, root=machine.repo,
                                      remote="origin", base_branch="main", flow_raw={},
                                      shelf=types.SimpleNamespace(json=lambda name: {"state": {}}))
            return module.Supervisor(c, once=True).child_argv("serve")
        self.assertIsNone(argv(self.b))
        self.assertIn("serve", argv(self.a))

    def test_an_idle_handover_journals_the_queue_stopped_before_the_transfer(self) -> None:
        moved = self.hand_over()
        stopped, transferred = self.a.journal()[-2:]
        self.assertEqual((stopped["kind"], stopped["data"]), ("queue.stopped", {"seat": self.first}))
        self.assertEqual((transferred["id"], transferred["kind"], transferred["data"]),
                         (moved["id"], "scope.transferred", {"scope": "queue", "from": self.first, "to": self.second}))
        self.assertEqual(state_tool.holder(self.a.journal(), "queue"), self.second)


class QueueAcrossSeatsTests(mq.MergeQueueTestCase):
    """The queue runs on the machine whose seat holds it, over hand-offs another seat's lane accepts."""

    def setUp(self) -> None:
        super().setUp()
        cleared = mock.patch.dict(os.environ, partitions.own_git_environment(), clear=True)
        cleared.start()
        self.addCleanup(cleared.stop)
        self.adopter = self.adopter_for_seats()
        self.a = partitions.Machine(self.adopter.operator)
        self.b = partitions.Machine(partitions.clone(self.adopter.remote, self.directory / "b", name="B"))
        base = {"ref": "main", "head": partitions.git(self.a.repo, "rev-parse", "HEAD")}
        collab.open_record(self.a.directory, key=KEY, headline="Shared", base=base)
        self.first = self.a.seat()
        self.a.push()
        self.b.take()
        collab.request_seat(self.b.directory)
        self.b.push()
        self.a.take()
        collab.admit_seat(self.a.directory, self.b.seat())
        self.second = self.b.seat()
        self.a.patch({"lanes": [lane_row("lane-1")], "planned": [planned("feature", "lane-1")],
                      "meta": {"handoffsNeedAcceptance": True}})
        collab.give_scope(self.a.directory, "lane:lane-1", self.second)
        self.a.push()
        self.b.take()

    def adopter_for_seats(self) -> "mq.Adopter":
        adopter = self.adopter()
        partitions.git(adopter.operator, "config", "user.name", "A")
        partitions.git(adopter.operator, "config", "user.email", "a@example.invalid")
        return adopter

    def runner(self) -> "mq.queue.Runner":
        return mq.queue.Runner(self.adopter.operator, self.a.directory, remote="origin", base="main")

    def item(self, machine, subject: str = "feature") -> dict:
        return next(entry for entry in machine.state()["queue"] if entry["subject"] == subject)

    def test_bs_accepted_hand_off_reaches_as_queue_at_its_start_without_its_planned_story_and_merges(self) -> None:
        head = self.adopter.lane_commit("feature", {"src/app.txt": "app two\n"})
        state_tool.handoff(self.b.directory, lane="lane-1", subject="feature", head=head, change_request="cr-1")
        state_tool.accept_handoff(self.b.directory, "feature")
        self.b.push()
        self.a.take()
        item = self.item(self.a)
        self.assertEqual((item["stage"], item.get("pending"), self.a.state().get("planned")), ("queued", None, []))
        results = self.runner().run_command("run", ["feature"])
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document())
        self.assertEqual(self.item(self.a)["stage"], "merged")
        self.a.push()
        self.b.take()
        self.assertEqual(self.item(self.b)["stage"], "merged")

    def test_a_re_hand_off_after_the_queue_sent_the_story_back_resets_its_start_and_the_queue_runs_it_again(self) -> None:
        head = self.adopter.lane_commit("feature", {"src/app.txt": "app two\n"})
        state_tool.handoff(self.b.directory, lane="lane-1", subject="feature", head=head, change_request="cr-1")
        state_tool.accept_handoff(self.b.directory, "feature")
        self.b.push()
        self.a.take()
        # a's queue sends it back: its run stops it.
        state_tool.patch(self.a.directory, writer="runner", version=self.a.state()["version"],
                         selector="queue[subject=feature]", changes={"stage": "stopped", "syncedHead": head})
        self.a.push()
        self.b.take()
        again = self.adopter.lane_push("feature", {"src/app.txt": "app three\n"})
        state_tool.handoff(self.b.directory, lane="lane-1", subject="feature", head=again, change_request="cr-1")
        state_tool.accept_handoff(self.b.directory, "feature")
        self.b.push()
        self.a.take()
        item = self.item(self.a)
        self.assertEqual(({key: item[key] for key in state_tool.QUEUE_START}, item["head"]), (state_tool.QUEUE_START, again))
        results = self.runner().run_command("run", ["feature"])
        self.assertEqual(results[-1].document()["outcome"], "merged", results[-1].document())

    def test_after_a_handover_the_old_machines_serve_ends_and_the_new_ones_serves(self) -> None:
        handed = []

        def hand_over(_: float) -> None:
            if not handed:
                handed.append(collab.give_scope(self.a.directory, "queue", self.second, wait=1, poll=0.01))

        with self.assertRaises(mq.queue.delivery_state.StateError) as ended:
            mq.queue.serve(self.adopter.operator, self.a.directory, remote="origin", base="main", poll=0,
                           sleep=hand_over)
        self.assertEqual((ended.exception.code, len(handed)), ("queue.not_held", 1))
        self.a.push()
        self.b.take()
        self.assertEqual(mq.queue.serve(self.b.repo, self.b.directory, remote="origin", base="main",
                                        until_idle=True, poll=0), [])

    def test_a_handover_between_serves_passes_ends_serve_at_its_next_turn_having_taken_nothing(self) -> None:
        head = self.adopter.lane_commit("feature", {"src/app.txt": "app two\n"})
        state_tool.handoff(self.b.directory, lane="lane-1", subject="feature", head=head, change_request="cr-1")
        state_tool.accept_handoff(self.b.directory, "feature")
        self.b.push()
        self.a.take()
        before = len(self.a.journal())

        def hand_over() -> None:
            # Between the pass that found work and the turn that takes it.
            if state_tool.holder(self.a.journal(), "queue") == self.first:
                collab.give_scope(self.a.directory, "queue", self.second, wait=1, poll=0.01)

        results = []
        with self.assertRaises(mq.queue.delivery_state.StateError) as ended:
            mq.queue.serve(self.adopter.operator, self.a.directory, remote="origin", base="main", poll=0,
                           before_turn=hand_over, on_result=results.append)
        self.assertEqual(ended.exception.code, "queue.not_held")
        # The turn took nothing: no result, no queue ref for the candidate, and the record as the lane left it.
        self.assertEqual((results, self.adopter.queued("feature")), ([], None))
        self.assertEqual(self.item(self.a)["stage"], "queued")
        self.assertEqual([entry["kind"] for entry in self.a.journal()[before:]], ["queue.stopped", "scope.transferred"])

    def test_serve_on_the_command_line_refuses_with_one_json_line(self) -> None:
        for extra in ([], ["--until-idle"]):
            with self.subTest(extra=extra):
                ran = subprocess.run([sys.executable, "-B", str(partitions.SCRIPTS / "merge_queue.py"), "--repo",
                                      str(self.b.repo), "--state", str(self.b.directory), "--remote", "origin",
                                      "--base", "main", "serve", "--poll", "0", *extra],
                                     capture_output=True, text=True, timeout=120)
                self.assertEqual(ran.returncode, 1, ran.stderr)
                self.assertEqual(json.loads(ran.stdout.strip().splitlines()[-1])["code"], "queue.not_held")

    def test_serve_refuses_where_the_seat_does_not_hold_the_queue(self) -> None:
        with self.assertRaises(mq.queue.delivery_state.StateError) as refused:
            mq.queue.serve(self.b.repo, self.b.directory, remote="origin", base="main", until_idle=True, poll=0)
        self.assertEqual(refused.exception.code, "queue.not_held")
        self.assertEqual(mq.queue.serve(self.adopter.operator, self.a.directory, remote="origin", base="main",
                                        until_idle=True, poll=0), [])


if __name__ == "__main__":
    unittest.main()
