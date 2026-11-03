"""A lane's row stays true: the queue writes it when it merges or stops a candidate, and the lane's own word wins after."""

from __future__ import annotations

import json
from typing import Callable
from unittest import mock

from tests import test_merge_queue
from tests.test_merge_queue import MergeQueueTestCase, queue, state_tool


def lane_entry(lane: str = "lane-1", focus: str = "feature: building", state: str = "working") -> dict:
    return {"id": lane, "title": lane, "focus": focus, "state": state, "waitingOn": None, "since": "2026-11-03T00:00:00Z"}


class LaneRowCase(MergeQueueTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.record = self.adopter()

    def lanes(self, *entries: dict) -> None:
        self.record.lead_write(lambda state: state_tool.patch(
            self.record.state, writer="lead", version=state["version"], changes={"lanes": list(entries)}))

    def row(self, lane: str = "lane-1") -> dict:
        return next(entry for entry in state_tool.read_state(self.record.state)["lanes"] if entry["id"] == lane)

    def handed(self, subject: str = "feature") -> None:
        state_tool.append_journal(self.record.state, kind="candidate.handed", subject=subject, lane="lane-1", summary="handed off")


class TheQueueWritesTheRow(LaneRowCase):
    def test_a_merge_leaves_a_lane_with_nothing_waiting_idle_naming_what_landed(self) -> None:
        self.lanes(lane_entry())
        self.candidate(self.record)
        results = self.run_queue(self.record, "feature")
        self.assertEqual(results[-1].outcome, "merged")
        row = self.row()
        self.assertEqual((row["focus"], row["state"], row["waitingOn"]), ("feature merged", "idle", None))
        self.assertNotEqual(row["since"], "2026-11-03T00:00:00Z")
        self.assertIn("focusAt", row)

    def test_a_stop_says_why_and_that_the_lead_decides(self) -> None:
        self.lanes(lane_entry())
        self.candidate(self.record, {"tests/test_new.txt": "FAIL\n"})
        self.assertStopped(self.run_queue(self.record, "feature"), "check_failed")
        row = self.row()
        self.assertEqual((row["focus"], row["state"], row["waitingOn"]), ("feature stopped: check_failed", "idle", "lead"))

    def test_the_row_and_the_line_move_in_one_write(self) -> None:
        for name, files, stage in (("feature", {"src/app.txt": "app two\n"}, "merged"), ("broken", {"tests/test_new.txt": "FAIL\n"}, "stopped")):
            with self.subTest(stage):
                self.fresh()
                self.record = self.adopter()
                self.lanes(lane_entry(focus=f"{name}: building"))
                self.candidate(self.record, files, name=name)
                written = []
                patch = state_tool.patch

                def spy(*arguments, **options):
                    written.append(patch(*arguments, **options))
                    return written[-1]

                with mock.patch.object(queue.delivery_state, "patch", spy):
                    self.run_queue(self.record, name)
                landed = [state for state in written if state["queue"][0]["stage"] == stage]
                self.assertTrue(landed)
                self.assertIn(stage, landed[0]["lanes"][0]["focus"])

    def test_an_interrupted_run_writes_the_row_too(self) -> None:
        self.lanes(lane_entry())
        self.candidate(self.record)
        self.record.lead_write(lambda state: state_tool.patch(
            self.record.state, writer="runner", version=state["version"], selector="queue[subject=feature]", assignments={"/stage": "syncing"}))
        self.run_queue(self.record)
        self.assertEqual(self.record.item("feature")["stage"], "stopped")
        self.assertEqual(self.row()["focus"], "feature stopped: interrupted")

    def test_a_refusal_serve_stops_writes_the_row_too(self) -> None:
        self.lanes(lane_entry())
        head = self.candidate(self.record)
        runner = self.record.runner()
        runner.write(build=queue._refused_stop(self.record.state, "feature", head, "candidate_moved"))
        self.assertEqual(self.record.item("feature")["stage"], "stopped")
        self.assertEqual(self.row()["focus"], "feature stopped: candidate_moved")

    def test_a_refusal_that_stops_nothing_leaves_the_row(self) -> None:
        self.lanes(lane_entry(focus="still building"))
        self.candidate(self.record)
        document = self.run_queue(self.record, "no-such-subject")[-1].document()
        self.assertEqual(document["outcome"], "refused")
        self.assertEqual(self.row()["focus"], "still building")

    def test_another_lanes_row_is_left_alone(self) -> None:
        self.lanes(lane_entry(), lane_entry("lane-2", "other work"))
        self.candidate(self.record)
        before = self.row("lane-2")
        self.run_queue(self.record, "feature")
        self.assertEqual(self.row("lane-2"), before)


class TwoCandidatesFromOneLane(LaneRowCase):
    def both(self) -> None:
        self.candidate(self.record)
        self.handed()
        self.candidate(self.record, {"src/lib.txt": "lib two\n"}, name="second")
        self.handed("second")

    def test_the_queues_own_words_arent_the_lane_moving_on(self) -> None:
        self.lanes(lane_entry(focus="feature: review done", state="handed"))
        self.both()
        self.run_queue(self.record, "feature")
        self.assertEqual((self.row()["focus"], self.row()["state"]), ("feature merged", "assigned"))
        self.run_queue(self.record, "second")
        self.assertEqual((self.row()["focus"], self.row()["state"], self.row()["waitingOn"]), ("second merged", "idle", None))

    def test_a_batch_rests_the_lane_too(self) -> None:
        self.lanes(lane_entry(focus="feature: review done", state="handed"))
        self.both()
        self.record.runner().run_command("batch", ["feature", "second"])
        self.assertEqual((self.row()["state"], self.row()["waitingOn"]), ("idle", None))
        self.assertIn("merged", self.row()["focus"])

    def test_the_queue_saying_one_stopped_stays_while_it_is_open(self) -> None:
        self.lanes(lane_entry(focus="feature: building"))
        self.candidate(self.record, {"tests/test_new.txt": "FAIL\n"})
        self.candidate(self.record, {"src/lib.txt": "lib two\n"}, name="second")
        self.run_queue(self.record, "feature")
        self.assertEqual(self.row()["focus"], "feature stopped: check_failed")
        self.run_queue(self.record, "second")
        self.assertEqual((self.row()["focus"], self.row()["waitingOn"]), ("feature stopped: check_failed", "lead"))

    def test_a_merged_earlier_candidate_is_no_assignment(self) -> None:
        self.lanes(lane_entry(focus="feature: building"))
        self.candidate(self.record)
        self.run_queue(self.record, "feature")
        state_tool.set_focus(self.record.state, "lane-1", "second: building", state="working")
        self.candidate(self.record, {"src/lib.txt": "lib two\n"}, name="second")
        self.run_queue(self.record, "second")
        self.assertEqual((self.row()["focus"], self.row()["state"]), ("second merged", "idle"))


class AfterAStop(LaneRowCase):
    def stop(self) -> str:
        self.lanes(lane_entry())
        head = self.candidate(self.record, {"tests/test_new.txt": "FAIL\n"})
        self.run_queue(self.record, "feature")
        self.assertEqual(self.row()["waitingOn"], "lead")
        return head

    def test_the_lanes_focus_ends_what_the_queue_said_it_waits_on(self) -> None:
        self.stop()
        state_tool.set_focus(self.record.state, "lane-1", "fixing the failing test", state="working")
        row = self.row()
        self.assertEqual((row["focus"], row["state"], row["waitingOn"]), ("fixing the failing test", "working", None))
        self.assertNotIn("focusBy", row)

    def test_a_lanes_raw_patch_ends_it_too(self) -> None:
        self.stop()
        state = state_tool.read_state(self.record.state)
        state_tool.patch(self.record.state, writer="lane", lane="lane-1", version=state["version"], selector="lanes[id=lane-1]",
                         changes={"focus": "fixing it"})
        self.assertEqual((self.row()["focus"], self.row()["waitingOn"]), ("fixing it", None))

    def test_a_focus_can_say_it_still_waits_on_the_same_one(self) -> None:
        self.stop()
        state_tool.set_focus(self.record.state, "lane-1", "asking the lead", waiting_on="lead")
        self.assertEqual(self.row()["waitingOn"], "lead")

    def test_a_focus_can_say_it_still_waits(self) -> None:
        self.stop()
        state_tool.set_focus(self.record.state, "lane-1", "asking", state="blocked", waiting_on="the lead's answer")
        self.assertEqual(self.row()["waitingOn"], "the lead's answer")

    def test_a_lanes_own_waiting_on_survives_a_focus_that_doesnt_mention_it(self) -> None:
        self.lanes(lane_entry())
        state_tool.set_focus(self.record.state, "lane-1", "asking", state="blocked", waiting_on="the lead's answer")
        state_tool.set_focus(self.record.state, "lane-1", "still asking")
        self.assertEqual(self.row()["waitingOn"], "the lead's answer")

    def test_a_new_hand_off_ends_the_queues_words(self) -> None:
        self.stop()
        head = self.record.lane_commit("feature", {"tests/test_new.txt": "ok\n"})
        state_tool.handoff(self.record.state, lane="lane-1", subject="feature", head=head, change_request="c1", rounds=1)
        row = self.row()
        self.assertEqual((row["focus"], row["state"], row["waitingOn"]), ("", "handed", None))
        self.assertNotIn("focusAt", row)
        self.assertNotIn("focusBy", row)
        self.assertIn("feature", state_tool.lane_focus(state_tool.read_state(self.record.state))["lane-1"])

    def test_a_new_hand_off_leaves_a_lanes_own_words(self) -> None:
        self.stop()
        state_tool.set_focus(self.record.state, "lane-1", "feature fixed", state="working")
        head = self.record.lane_commit("feature", {"tests/test_new.txt": "ok\n"})
        state_tool.handoff(self.record.state, lane="lane-1", subject="feature", head=head, change_request="c1", rounds=1)
        self.assertEqual(self.row()["focus"], "feature fixed")


class ABlockedLane(LaneRowCase):
    def test_with_other_work_open_keeps_its_question(self) -> None:
        self.lanes({**lane_entry(focus="second: blocked until feature lands", state="blocked"), "waitingOn": "feature landing"})
        self.candidate(self.record)
        self.candidate(self.record, {"src/lib.txt": "lib two\n"}, name="second")
        before = self.row()
        self.run_queue(self.record, "feature")
        self.assertEqual(self.row(), before)

    def test_that_has_spoken_since_its_hand_off_keeps_its_row(self) -> None:
        self.lanes(lane_entry())
        self.candidate(self.record)
        self.handed()
        state_tool.set_focus(self.record.state, "lane-1", "a question", state="blocked", waiting_on="the lead")
        before = self.row()
        self.run_queue(self.record, "feature")
        self.assertEqual(self.row(), before)


class TheNameMatch(LaneRowCase):
    def spoken_since(self, words: str, subject: str = "feature") -> None:
        self.lanes(lane_entry())
        self.candidate(self.record, name=subject)
        self.handed(subject)
        state_tool.set_focus(self.record.state, "lane-1", words, state="working")
        self.run_queue(self.record, subject)

    def test_a_longer_name_before_it_is_not_the_candidate(self) -> None:
        self.spoken_since("pre-feature: building")
        self.assertEqual(self.row()["focus"], "pre-feature: building")

    def test_a_name_is_matched_as_written_not_as_a_pattern(self) -> None:
        self.spoken_since("fxature: building", "f.ature")
        self.assertEqual(self.row()["focus"], "fxature: building")

    def test_at_the_same_moment_as_the_hand_off_the_lane_counts_as_having_spoken(self) -> None:
        self.lanes(lane_entry())
        self.candidate(self.record)
        with mock.patch.object(state_tool, "now", return_value="2031-01-01T00:00:00.000000Z"):
            self.handed()
            state_tool.set_focus(self.record.state, "lane-1", "another ticket", state="working")
        self.run_queue(self.record, "feature")
        self.assertEqual(self.row()["focus"], "another ticket")

    def test_an_unreadable_journal_keeps_the_row_and_still_writes_the_stage(self) -> None:
        self.lanes(lane_entry(focus="other words"))
        self.candidate(self.record)
        state = state_tool.read_state(self.record.state)
        with mock.patch.object(state_tool, "read_journal", side_effect=state_tool.StateError("journal_invalid", "unreadable")):
            rows = state_tool.lane_rows_after(self.record.state, state, "feature")
        self.assertEqual(rows, state["lanes"])


class RoutesOtherThanTheMerge(LaneRowCase):
    """Each other way the queue or the record moves a stage."""

    # Taken from the class, not imported, so its own tests aren't run again here.
    break_checkout = test_merge_queue.ServeTests.break_checkout
    serve = test_merge_queue.ServeTests.serve

    def forge_says_merged(self, head: str) -> None:
        snapshot = self.directory / "forge.json"
        snapshot.write_text(json.dumps({"takenAt": state_tool.now(), "source": "forge",
                                        "items": [{"subject": "feature", "state": "merged", "changeRequest": "change 7", "head": head}]}))
        state_tool.reconcile(self.record.state, [snapshot])

    def test_a_merge_recorded_from_the_forge_writes_the_row(self) -> None:
        self.lanes(lane_entry(state="handed"))
        head = self.candidate(self.record)
        self.forge_says_merged(head)
        self.assertEqual(self.record.item("feature")["stage"], "merged")
        self.assertEqual((self.row()["focus"], self.row()["state"]), ("feature merged", "idle"))

    def test_a_merge_recovered_after_an_interruption_replaces_the_queues_stop(self) -> None:
        self.lanes(lane_entry())
        head = self.candidate(self.record)
        self.record.lead_write(lambda state: state_tool.patch(
            self.record.state, writer="runner", version=state["version"], selector="queue[subject=feature]", assignments={"/stage": "merging"}))
        self.record.runner().recover()
        self.assertEqual(self.row()["focus"], "feature stopped: interrupted")
        self.forge_says_merged(head)
        self.assertEqual(self.record.item("feature")["stage"], "merged")
        self.assertEqual((self.row()["focus"], self.row()["state"], self.row()["waitingOn"]), ("feature merged", "idle", None))

    def test_a_candidate_put_back_after_a_setup_break_no_longer_says_it_stopped(self) -> None:
        self.lanes(lane_entry(state="handed"))
        self.candidate(self.record)
        restore: list[Callable[[], None]] = []

        def break_once(runner, subject) -> None:
            if not restore:
                restore.append(self.break_checkout(self.record))

        self.serve(self.record, seams={"after_sync": break_once})
        self.assertEqual(self.record.item("feature")["stage"], "queued")
        row = self.row()
        self.assertEqual((row["focus"], row["state"], row["waitingOn"]), ("", "handed", None))

    def test_a_batch_put_back_when_the_base_moved_no_longer_says_it_stopped(self) -> None:
        self.lanes(lane_entry(focus="feature: review done", state="handed"))
        self.candidate(self.record)
        self.candidate(self.record, {"src/lib.txt": "lib two\n"}, name="second")

        def push_base(runner, subject) -> None:
            if subject == "feature":
                self.record.advance_base({"src/other.txt": "other\n"})

        results = self.record.runner(seams={"before_batch_landing": push_base}).run_command("batch", ["feature", "second"])
        documents = {result.document()["candidate"]: result.document() for result in results}
        self.assertEqual(documents["feature"]["stop"]["reason"], "batch_tree_mismatch")
        self.assertEqual(self.record.item("feature")["stage"], "queued")
        row = self.row()
        self.assertEqual((row["focus"], row["state"], row["waitingOn"]), ("", "handed", None))

    def queues_words(self, words: str, state: str = "idle") -> dict:
        self.lanes(lane_entry(state=state))
        head = self.candidate(self.record)
        current = state_tool.read_state(self.record.state)
        state_tool.patch(self.record.state, writer="runner", version=current["version"],
                         changes={"lanes": [{**current["lanes"][0], "focus": words}]})
        runner = self.record.runner()
        runner.set_item("feature", stage="stopped")
        before = self.row()
        runner.write(build=queue._requeue("feature", head))
        return before

    def test_a_put_back_moves_the_time_the_state_changed(self) -> None:
        before = self.queues_words("feature stopped: runner_error")
        self.assertEqual(self.row()["state"], "handed")
        self.assertNotEqual(self.row()["since"], before["since"])

    def test_a_put_back_leaves_the_queues_words_about_another_candidate(self) -> None:
        before = self.queues_words("second merged")
        self.assertEqual(self.row(), before)

    def test_a_put_back_leaves_a_lanes_own_words_that_read_the_same(self) -> None:
        self.lanes(lane_entry(focus="feature stopped: I will look at it", state="working"))
        head = self.candidate(self.record)
        runner = self.record.runner()
        runner.set_item("feature", stage="stopped")
        before = self.row()
        runner.write(build=queue._requeue("feature", head))
        self.assertEqual(self.row(), before)

    def test_serve_says_why_it_stopped_a_refused_candidate(self) -> None:
        self.lanes(lane_entry(state="handed"))
        head = self.record.lane_commit("feature", {"src/app.txt": "app two\n"})
        self.record.enqueue("feature", head, lead_inputs=["no-such-input"])
        results = self.serve(self.record)
        reason = results[-1].document()["stop"]["reason"]
        self.assertEqual(self.record.item("feature")["stage"], "stopped")
        self.assertEqual(self.row()["focus"], f"feature stopped: {reason}")


class TheLanesOwnWordWins(LaneRowCase):
    def test_a_focus_after_the_queues_update_takes_over(self) -> None:
        self.lanes(lane_entry())
        self.candidate(self.record)
        self.run_queue(self.record, "feature")
        written = self.row()["focusAt"]
        state_tool.set_focus(self.record.state, "lane-1", "next: reading the ticket", state="working")
        row = self.row()
        self.assertEqual((row["focus"], row["state"]), ("next: reading the ticket", "working"))
        self.assertGreater(row["focusAt"], written)

    def test_a_lane_that_moved_on_after_its_hand_off_keeps_its_row(self) -> None:
        self.lanes(lane_entry(focus="handed off"))
        self.candidate(self.record)
        self.handed()
        state_tool.set_focus(self.record.state, "lane-1", "another ticket: building", state="working")
        before = self.row()
        self.run_queue(self.record, "feature")
        self.assertEqual(self.row(), before)

    def test_a_row_still_naming_the_candidate_is_updated_though_it_was_written_after_the_hand_off(self) -> None:
        self.lanes(lane_entry())
        self.candidate(self.record)
        self.handed()
        state_tool.set_focus(self.record.state, "lane-1", "feature handed off; waiting", state="working")
        self.run_queue(self.record, "feature")
        self.assertEqual(self.row()["focus"], "feature merged")

    def test_a_waiting_row_that_names_nothing_is_updated_once_the_lane_has_handed_off(self) -> None:
        self.lanes(lane_entry())
        self.candidate(self.record)
        self.handed()
        state_tool.set_focus(self.record.state, "lane-1", "waiting for the lead", state="handed")
        self.run_queue(self.record, "feature")
        self.assertEqual(self.row()["focus"], "feature merged")

    def test_a_name_inside_a_longer_name_is_not_the_candidate(self) -> None:
        self.lanes(lane_entry(focus="feature-two: building"))
        self.candidate(self.record)
        self.handed()
        state_tool.set_focus(self.record.state, "lane-1", "feature-two: building", state="working")
        self.run_queue(self.record, "feature")
        self.assertEqual(self.row()["focus"], "feature-two: building")


class AnAssignmentStillWaiting(LaneRowCase):
    def test_a_lane_with_more_queued_keeps_its_state_and_says_what_landed(self) -> None:
        self.lanes(lane_entry())
        self.candidate(self.record)
        self.candidate(self.record, {"src/lib.txt": "lib two\n"}, name="second")
        self.run_queue(self.record, "feature")
        row = self.row()
        self.assertEqual((row["focus"], row["state"]), ("feature merged", "working"))

    def test_a_handed_lane_with_planned_work_reads_as_assigned(self) -> None:
        self.lanes(lane_entry(state="handed"))
        self.candidate(self.record)
        self.record.lead_write(lambda state: state_tool.patch(
            self.record.state, writer="lead", version=state["version"],
            changes={"planned": [{"subject": "next", "title": "Next", "lane": "lane-1", "stage": "planned"}]}))
        self.run_queue(self.record, "feature")
        self.assertEqual(self.row()["state"], "assigned")

    def test_a_row_about_the_other_work_stays(self) -> None:
        self.lanes(lane_entry(focus="second: building"))
        self.candidate(self.record)
        self.candidate(self.record, {"src/lib.txt": "lib two\n"}, name="second")
        self.run_queue(self.record, "feature")
        self.assertEqual(self.row()["focus"], "second: building")


class FieldOwnership(LaneRowCase):
    def refused(self, changes: dict) -> None:
        state = state_tool.read_state(self.record.state)
        with self.assertRaises(state_tool.StateError) as refusal:
            state_tool.patch(self.record.state, writer="runner", version=state["version"], changes=changes)
        self.assertEqual(refusal.exception.code, "field_not_owned")

    def test_the_runner_writes_only_a_rows_words_and_state(self) -> None:
        self.lanes(lane_entry())
        self.refused({"lanes": [{**lane_entry(), "title": "renamed"}]})
        self.refused({"lanes": []})
        self.refused({"lanes": [lane_entry(), lane_entry("lane-2")]})
        state = state_tool.read_state(self.record.state)
        written = state_tool.patch(self.record.state, writer="runner", version=state["version"],
                                   changes={"lanes": [{**self.row(), "focus": "said", "state": "idle", "waitingOn": None}]})
        self.assertEqual(written["lanes"][0]["focus"], "said")

    def test_a_lead_writing_a_strange_lane_id_is_refused_cleanly(self) -> None:
        state = state_tool.read_state(self.record.state)
        with self.assertRaises(state_tool.StateError) as refusal:
            state_tool.patch(self.record.state, writer="lead", version=state["version"], changes={"lanes": [{"id": ["x"]}]})
        self.assertEqual(refusal.exception.code, "patch_invalid")

    def test_who_said_it_is_the_tools_alone(self) -> None:
        self.lanes(lane_entry())
        self.refused({"lanes": [{**self.row(), "focusBy": "runner"}]})
        state = state_tool.read_state(self.record.state)
        written = state_tool.patch(self.record.state, writer="lead", version=state["version"],
                                   changes={"lanes": [{**self.row(), "focusBy": "runner"}]})
        self.assertNotIn("focusBy", written["lanes"][0])
        state = state_tool.read_state(self.record.state)
        written = state_tool.patch(self.record.state, writer="runner", version=state["version"],
                                   changes={"lanes": [{**self.row(), "focus": "said"}]})
        self.assertEqual(written["lanes"][0]["focusBy"], "runner")
        state = state_tool.read_state(self.record.state)
        written = state_tool.patch(self.record.state, writer="lead", version=state["version"],
                                   changes={"lanes": [{**self.row(), "focus": "the lead said"}]})
        self.assertNotIn("focusBy", written["lanes"][0])

    def test_the_time_is_the_tools_alone(self) -> None:
        self.lanes({**lane_entry(), "focusAt": "2020-01-01T00:00:00Z"})
        row = self.row()
        self.assertNotEqual(row["focusAt"], "2020-01-01T00:00:00Z")
        # Nobody moves it by writing it, and a write that leaves the words alone leaves it alone.
        state = state_tool.read_state(self.record.state)
        written = state_tool.patch(self.record.state, writer="lead", version=state["version"],
                                   changes={"lanes": [{**row, "focusAt": "2020-01-01T00:00:00Z", "title": "renamed"}]})
        self.assertEqual(written["lanes"][0]["focusAt"], row["focusAt"])
        state = state_tool.read_state(self.record.state)
        with self.assertRaises(state_tool.StateError) as refusal:
            state_tool.patch(self.record.state, writer="lane", lane="lane-1", version=state["version"], selector="lanes[id=lane-1]",
                             changes={"focusAt": "2020-01-01T00:00:00Z"})
        self.assertEqual(refusal.exception.code, "writer_not_owned")

    def test_a_lane_saying_the_same_words_again_is_still_a_lane_speaking(self) -> None:
        self.lanes(lane_entry())
        before = self.row()["focusAt"]
        state_tool.set_focus(self.record.state, "lane-1", "feature: building")
        self.assertGreater(self.row()["focusAt"], before)

    def test_a_lane_changing_only_its_state_doesnt_move_the_time(self) -> None:
        self.lanes(lane_entry())
        before = self.row()["focusAt"]
        state = state_tool.read_state(self.record.state)
        state_tool.patch(self.record.state, writer="lane", lane="lane-1", version=state["version"], selector="lanes[id=lane-1]",
                         changes={"state": "reviewing"})
        self.assertEqual(self.row()["focusAt"], before)

    def test_an_existing_record_with_no_time_reads_and_takes_one_when_the_words_change(self) -> None:
        self.lanes(lane_entry())
        path = self.record.state / "state.json"
        state = state_tool.read_state(self.record.state)
        del state["lanes"][0]["focusAt"]
        path.write_text(state_tool.dumps(state), encoding="utf-8")
        self.assertNotIn("focusAt", self.row())
        self.candidate(self.record)
        self.assertNotIn("focusAt", self.row())
        self.run_queue(self.record, "feature")
        self.assertIn("focusAt", self.row())
