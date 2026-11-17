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
        state_tool.append_journal(self.record.state, kind="candidate.handed", subject=subject, lane="lane-1",
                                  data={"head": "a" * 40, "checks": []})


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


class TheLanesKeptWord(LaneRowCase):
    """The queue resting a row keeps the lane's own last word on it, as restedFrom, so the record carries that word
    past the merge whether or not a supervisor pass saw it."""

    def spoke(self, focus: str = "feature: building", state: str = "working") -> dict:
        self.lanes(lane_entry())
        state_tool.set_focus(self.record.state, "lane-1", focus, state=state)
        return self.row()

    def test_a_merge_keeps_the_lanes_own_word_on_the_row_it_rests(self) -> None:
        said = self.spoke()
        self.candidate(self.record)
        self.run_queue(self.record, "feature")
        row = self.row()
        self.assertEqual((row["focus"], row["state"], row.get("focusBy")), ("feature merged", "idle", "runner"))
        self.assertEqual(row["restedFrom"], {"state": "working", "at": said["focusAt"]})

    def test_a_row_without_focus_at_keeps_its_since(self) -> None:
        self.lanes(lane_entry(focus="", state="assigned"))  # no words, so no focusAt: since says when it was set
        self.candidate(self.record)
        self.run_queue(self.record, "feature")
        self.assertEqual(self.row()["restedFrom"], {"state": "assigned", "at": "2026-11-03T00:00:00Z"})

    def test_the_queues_next_word_carries_the_lanes_word_forward(self) -> None:
        said = self.spoke()
        self.candidate(self.record)
        self.candidate(self.record, {"src/lib.txt": "lib two\n"}, name="second")
        self.run_queue(self.record, "feature")
        self.assertEqual(self.row()["focus"], "feature merged")
        self.run_queue(self.record, "second")
        row = self.row()
        self.assertEqual((row["focus"], row["state"]), ("second merged", "idle"))
        self.assertEqual(row["restedFrom"], {"state": "working", "at": said["focusAt"]}, "the queue's own word replaced the lane's")

    def test_the_lanes_next_word_ends_it(self) -> None:
        self.spoke()
        self.candidate(self.record)
        self.run_queue(self.record, "feature")
        state_tool.set_focus(self.record.state, "lane-1", "next: reading the ticket", state="working")
        self.assertNotIn("restedFrom", self.row())

    def test_a_stop_keeps_it_and_a_new_hand_off_ends_it(self) -> None:
        said = self.spoke()
        self.candidate(self.record, {"tests/test_new.txt": "FAIL\n"})
        self.run_queue(self.record, "feature")
        row = self.row()
        self.assertEqual((row["focus"], row["restedFrom"]), ("feature stopped: check_failed", {"state": "working", "at": said["focusAt"]}))
        head = self.record.lane_commit("feature", {"tests/test_new.txt": "ok\n"})
        state_tool.handoff(self.record.state, lane="lane-1", subject="feature", head=head, change_request="c1", rounds=1)
        self.assertNotIn("restedFrom", self.row())

    def test_no_writer_sets_it_by_hand(self) -> None:
        self.spoke()
        self.candidate(self.record)
        self.run_queue(self.record, "feature")
        kept = self.row()["restedFrom"]
        forged = {"state": "working", "at": "2099-01-01T00:00:00Z"}
        for writer in ("runner", "lane"):
            with self.subTest(writer=writer):
                state = state_tool.read_state(self.record.state)
                with self.assertRaises(state_tool.StateError):
                    state_tool.patch(self.record.state, writer=writer, lane="lane-1" if writer == "lane" else None,
                                     version=state["version"], selector="lanes[id=lane-1]", assignments={"/restedFrom": forged})
                self.assertEqual(self.row()["restedFrom"], kept)
        # The lead may rewrite rows, but the tool keeps this field: a forged value doesn't stand.
        self.record.lead_write(lambda state: state_tool.patch(
            self.record.state, writer="lead", version=state["version"], selector="lanes[id=lane-1]",
            assignments={"/restedFrom": forged}))
        self.assertEqual(self.row()["restedFrom"], kept)

    def test_another_lanes_row_has_none(self) -> None:
        self.lanes(lane_entry(), lane_entry("lane-2", focus="other: building"))
        self.candidate(self.record)
        self.run_queue(self.record, "feature")
        self.assertNotIn("restedFrom", self.row("lane-2"))


# ---------------------------------------------------------------------------
# A lane waiting on another story: progress, not a question. In-process, on a copy of the waits fixture, through the
# state tool's own writes and the rows the runner writes when it lands, stops or puts back a piece.

import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

from tests.test_waits import record as waits_record, run_delivery, state_tool as tool  # noqa: E402

HEAD = "c" * 40


class WaitingRecord(unittest.TestCase):
    """The waits fixture as a record on disk: lane 1 and lane 3 idle, lane 2 reviewing APP-1, which lane 3's APP-3
    names files of. Hand-offs need no acceptance unless a case says so."""

    accept = False

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        state = waits_record()["state"]
        state["meta"]["handoffsNeedAcceptance"] = self.accept
        (self.directory / tool.STATE_FILE).write_text(tool.dumps(state), encoding="utf-8")
        (self.directory / tool.JOURNAL_FILE).touch()

    def state(self) -> dict:
        return tool.read_state(self.directory)

    def row(self, lane: str) -> dict:
        return next(entry for entry in self.state()["lanes"] if entry["id"] == lane)

    def focus(self, lane: str, text: str, state: str | None = None, waiting_on: object = ...) -> dict:
        tool.set_focus(self.directory, lane, text, state=state, waiting_on=waiting_on)
        return self.row(lane)

    def working_then_waiting(self, lane: str, on: str, text: str | None = None) -> dict:
        self.focus(lane, "building", state="working")
        return self.focus(lane, text or f"waiting for {on}", state="waiting", waiting_on=on)

    def hand(self, lane: str, subject: str) -> None:
        tool.handoff(self.directory, lane=lane, subject=subject, head=HEAD, change_request=f"cr-{subject}")

    def runner(self, subject: str, stage: str, stopped: str | None = None) -> None:
        """The runner's own write when it lands or stops a piece: the queue and the lanes, in one patch."""
        state = self.state()
        finished = tool.merged_item if stage == "merged" else (lambda item: {**item, "stage": stage})
        queue = [finished(item) if item["subject"] == subject else item for item in state["queue"]]
        tool.patch(self.directory, writer="runner", version=state["version"],
                   changes={"queue": queue, "lanes": tool.lane_rows_after(self.directory, state, subject, stopped=stopped)})

    def put_back(self, subject: str) -> None:
        state = self.state()
        queue = [{**item, "stage": "queued"} if item["subject"] == subject else item for item in state["queue"]]
        tool.patch(self.directory, writer="runner", version=state["version"],
                   changes={"queue": queue, "lanes": tool.lane_rows_put_back(state, subject)})

    def event(self, kind: str, subject: str, data: dict | None = None) -> dict:
        entry = {"id": 99, "at": "2026-11-17T13:00:00Z", "kind": kind, "lane": None, "subject": subject,
                 "data": data or {"head": HEAD}}
        [event] = tool.wait_events([entry], self.state())
        return event

    def wakes(self) -> dict:
        reading = {"cores": 8, "load5": 0.0, "diskFreeBytes": 10 ** 15, "swapUsedBytes": 0}
        code, result = tool.wait_for_lead(self.directory, after=None, timeout=0, poll=0, read_machine=lambda _: reading,
                                 sleep=lambda _: None)
        return result


class ABlockedLaneToday(WaitingRecord):
    """Characterization, pinned before `waiting` was added and kept after it: a `blocked` lane wakes `wait`, is in
    "Needs you" and holds the close. It is not reported quiet: `wait` wakes on it instead, and only lanes assigned,
    working or reviewing are counted quiet."""

    def test_a_blocked_lane_wakes_wait_is_in_needs_you_and_holds_the_close(self) -> None:
        self.focus("lane-3", "which API?", state="blocked", waiting_on="lead")
        result = self.wakes()
        self.assertEqual((result["woke"], [lane["id"] for lane in result["blockedLanes"]]), ("event", ["lane-3"]))
        self.assertEqual([item["source"] for item in run_delivery.lane_needs(self.state())], ["lane-3's state"])
        self.assertIn("lane-3", run_delivery.busy_lanes(self.state()))
        self.assertNotIn("lane-3", [lane["id"] for lane in tool._quiet_lanes(self.state(), 0, 10 ** 12).values()])

    def test_an_unmoved_working_lane_is_counted_quiet(self) -> None:
        self.focus("lane-3", "building", state="working")
        self.assertIn("lane-3", [lane["id"] for lane in tool._quiet_lanes(self.state(), 0, 10 ** 12).values()])


class SettingWaiting(WaitingRecord):
    def test_a_lane_waits_on_a_story_the_record_holds_and_keeps_the_state_it_left(self) -> None:
        row = self.working_then_waiting("lane-3", "APP-1")
        self.assertEqual((row["state"], row["waitingOn"], row["waitingFrom"]["state"]), ("waiting", "APP-1", "working"))
        self.assertIn("at", row["waitingFrom"])

    def test_an_unknown_key_is_refused_naming_the_field_and_so_is_no_key(self) -> None:
        before = (self.directory / tool.STATE_FILE).read_bytes()
        with self.assertRaises(tool.StateError) as unknown:
            self.focus("lane-3", "waiting", state="waiting", waiting_on="APP-99")
        self.assertIn("waitingOn", str(unknown.exception))
        with self.assertRaises(tool.StateError) as missing:
            self.focus("lane-3", "waiting", state="waiting")
        self.assertIn("--waiting-on", str(missing.exception))
        self.assertEqual((self.directory / tool.STATE_FILE).read_bytes(), before)

    def test_a_waiting_lane_wakes_no_wait_is_never_quiet_isnt_in_needs_you_and_holds_no_close(self) -> None:
        self.working_then_waiting("lane-3", "APP-1")
        result = self.wakes()
        self.assertEqual((result["woke"], result["blockedLanes"]), ("expired", []))
        self.assertNotIn("lane-3", [lane["id"] for lane in result["quietLanes"]])
        self.assertNotIn("lane-3", [lane["id"] for lane in tool._quiet_lanes(self.state(), 0, 10 ** 12).values()])
        self.assertEqual(run_delivery.lane_needs(self.state()), [])
        self.assertNotIn("lane-3", run_delivery.busy_lanes(self.state()))

    def test_its_wait_is_said_from_the_table_naming_the_story_it_will_finish(self) -> None:
        self.working_then_waiting("lane-3", "APP-1")
        [wait] = [wait for wait in tool.waits({"state": self.state()}) if wait["subject"] == "lane-3"]
        self.assertEqual({key: wait[key] for key in ("on", "cause", "stage", "frees", "needsSomeone")},
                         {"on": "APP-1", "cause": "lane", "stage": "review", "frees": "landed", "needsSomeone": False})
        self.assertEqual(wait["words"], "Lane 3 waits for APP-1 to land before it finishes APP-3.")
        self.assertEqual(wait["since"], self.row("lane-3")["waitingFrom"]["at"])


class WaitingFromIsCarried(WaitingRecord):
    def test_across_the_lanes_own_focus_the_runner_speaking_over_it_and_the_lead_writing_it(self) -> None:
        self.working_then_waiting("lane-2", "APP-1")
        self.focus("lane-2", "still waiting for APP-1")
        self.assertEqual(self.row("lane-2")["waitingFrom"]["state"], "working")
        state = self.state()
        tool.patch(self.directory, writer="runner", version=state["version"], selector="lanes[id=lane-2]",
                   changes={"focus": "the queue says something"})
        self.assertEqual(self.row("lane-2")["waitingFrom"]["state"], "working")
        state = self.state()
        tool.patch(self.directory, writer="lead", version=state["version"], selector="lanes[id=lane-2]",
                   changes={"focus": "the lead says something"})
        self.assertEqual((self.row("lane-2")["state"], self.row("lane-2")["waitingFrom"]["state"]), ("waiting", "working"))
        self.hand("lane-2", "APP-1")
        # Its own hand-off took it out of waiting; set it again on the handed story, then it lands.
        self.working_then_waiting("lane-2", "APP-1")
        self.runner("APP-1", "merged")
        row = self.row("lane-2")
        self.assertEqual((row["state"], row["waitingOn"], "waitingFrom" in row), ("working", None, False))
        merged = next(item for item in self.state()["queue"] if item["subject"] == "APP-1")
        document = {"state": self.state(), "journal": [{"id": 1, "kind": "candidate.merged", "subject": "APP-1",
                                                        "at": "2026-11-17T13:00:00Z", "lane": "lane-2", "data": {}}]}
        self.assertFalse(run_delivery.lane_moved_off(document, merged), "the returned row still stands at work")


class ALandingReturnsTheLanesItFrees(WaitingRecord):
    def test_a_working_lane_returns_to_working_and_an_idle_one_to_idle_in_the_same_write(self) -> None:
        self.working_then_waiting("lane-3", "APP-1")
        self.focus("lane-1", "waiting for APP-1", state="waiting", waiting_on="APP-1")
        self.hand("lane-2", "APP-1")
        self.runner("APP-1", "merged")
        for lane, back in (("lane-3", "working"), ("lane-1", "idle")):
            row = self.row(lane)
            self.assertEqual((row["state"], row["waitingOn"], "waitingFrom" in row), (back, None, False), lane)
        self.assertEqual(self.event("candidate.merged", "APP-1")["freed"], ["lane-1", "lane-3"])

    def test_a_row_whose_last_focus_doesnt_name_the_story_returns_too(self) -> None:
        self.working_then_waiting("lane-3", "APP-1", text="reading the guide")
        self.hand("lane-2", "APP-1")
        self.runner("APP-1", "merged")
        self.assertEqual(self.row("lane-3")["state"], "working")


class ASettlementReturnsThemToo(WaitingRecord):
    def test_a_no_change_hand_off_that_settles_at_once_frees_the_waiting_lane(self) -> None:
        self.working_then_waiting("lane-3", "APP-1")
        tool.no_change_handoff(self.directory, lane="lane-2", subject="APP-1", base_head=HEAD, evidence="criteria hold")
        row = self.row("lane-3")
        self.assertEqual((row["state"], row["waitingOn"], "waitingFrom" in row), ("working", None, False))
        self.assertEqual(self.event("candidate.handed", "APP-1", {"head": HEAD, "checks": [], "noChange": True})["freed"],
                         ["lane-3"])

    def test_a_lane_waiting_on_its_own_story_with_nothing_else_open_ends_idle(self) -> None:
        self.working_then_waiting("lane-3", "APP-3")
        tool.no_change_handoff(self.directory, lane="lane-3", subject="APP-3", base_head=HEAD, evidence="criteria hold")
        row = self.row("lane-3")
        self.assertEqual((row["state"], row["waitingOn"], "waitingFrom" in row), ("idle", None, False))


class ASettlementTheLeadAccepts(WaitingRecord):
    accept = True

    def test_accepting_a_no_change_hand_off_frees_the_waiting_lane(self) -> None:
        self.working_then_waiting("lane-3", "APP-1")
        tool.no_change_handoff(self.directory, lane="lane-2", subject="APP-1", base_head=HEAD, evidence="criteria hold")
        self.assertEqual(self.row("lane-3")["state"], "waiting", "the hand-off alone settles nothing")
        tool.accept_handoff(self.directory, "APP-1")
        row = self.row("lane-3")
        self.assertEqual((row["state"], row["waitingOn"], "waitingFrom" in row), ("working", None, False))
        self.assertEqual(self.event("handoff.accepted", "APP-1", {"settled": True})["freed"], ["lane-3"])

    def test_a_lane_waiting_on_its_own_accepted_no_change_story_ends_idle(self) -> None:
        self.working_then_waiting("lane-3", "APP-3")
        tool.no_change_handoff(self.directory, lane="lane-3", subject="APP-3", base_head=HEAD, evidence="criteria hold")
        tool.accept_handoff(self.directory, "APP-3")
        row = self.row("lane-3")
        self.assertEqual((row["state"], row["waitingOn"], "waitingFrom" in row), ("idle", None, False))

    def test_after_an_acceptance_the_lane_is_idle_waiting_on_the_accepted_story_until_it_lands(self) -> None:
        self.hand("lane-2", "APP-1")
        self.assertEqual(self.row("lane-2")["state"], "handed")
        tool.accept_handoff(self.directory, "APP-1")
        row = self.row("lane-2")
        self.assertEqual((row["state"], row["waitingOn"]), ("idle", "APP-1"))
        self.assertEqual(run_delivery.lane_needs(self.state()), [])
        self.runner("APP-1", "merged")
        self.assertEqual(self.row("lane-2")["waitingOn"], None)

    def test_a_lane_blocked_asking_the_lead_about_its_hand_off_is_settled_too(self) -> None:
        self.hand("lane-2", "APP-1")
        self.focus("lane-2", "APP-1 is handed: please accept it", state="blocked", waiting_on="lead")
        tool.accept_handoff(self.directory, "APP-1")
        row = self.row("lane-2")
        self.assertEqual((row["state"], row["waitingOn"]), ("idle", "APP-1"))


class AStop(WaitingRecord):
    def test_another_lane_waiting_on_the_stopped_piece_still_waits_and_is_counted(self) -> None:
        self.working_then_waiting("lane-3", "APP-1")
        before = self.row("lane-3")
        self.hand("lane-2", "APP-1")
        self.runner("APP-1", "stopped", stopped="check_failed")
        row = self.row("lane-3")
        self.assertEqual((row["state"], row["waitingOn"], row["waitingFrom"]), ("waiting", "APP-1", before["waitingFrom"]))
        added = run_delivery.waits_needing(SimpleNamespace(release=run_delivery_root()), {"state": self.state(), "journal": []},
                                           [])
        [stopped] = [item for item in added if item["source"] == "APP-1's waits"]
        # APP-2 by order, APP-3 by files, and lane 3 itself.
        self.assertTrue(stopped["text"].endswith("3 pieces wait on it."), stopped)
        self.put_back("APP-1")
        self.runner("APP-1", "merged")
        self.assertEqual(self.row("lane-3")["state"], "working")
        self.assertEqual(self.event("candidate.merged", "APP-1")["freed"], ["lane-3"])

    def test_a_lane_waiting_on_its_own_stopped_story_returns_to_work_waiting_on_the_lead(self) -> None:
        for text in ("waiting for APP-1", "reading the guide"):
            with self.subTest(text=text):
                self.setUp()
                self.hand("lane-2", "APP-1")
                self.working_then_waiting("lane-2", "APP-1", text=text)
                self.runner("APP-1", "stopped", stopped="check_failed")
                row = self.row("lane-2")
                self.assertEqual((row["state"], row["waitingOn"], "waitingFrom" in row), ("working", "lead", False))
                self.put_back("APP-1")
                self.assertEqual(self.row("lane-2")["state"], "handed")


class LeavingWaitingAnotherWay(WaitingRecord):
    """Any other write that takes a row out of `waiting` drops waitingFrom and a waitingOn naming what it waited on."""

    def assertLeft(self, row: dict, state: str) -> None:
        self.assertEqual((row["state"], "waitingFrom" in row), (state, False))
        self.assertNotEqual(row["waitingOn"], "APP-1")

    def test_the_lanes_own_focus_to_working(self) -> None:
        self.working_then_waiting("lane-3", "APP-1")
        self.assertLeft(self.focus("lane-3", "back at it", state="working"), "working")

    def test_handing_its_own_story_with_a_change(self) -> None:
        self.working_then_waiting("lane-3", "APP-1")
        self.hand("lane-3", "APP-3")
        self.assertLeft(self.row("lane-3"), "handed")

    def test_handing_its_own_story_with_no_change(self) -> None:
        self.__class__.accept = True
        self.addCleanup(setattr, self.__class__, "accept", False)
        self.setUp()
        self.working_then_waiting("lane-3", "APP-1")
        tool.no_change_handoff(self.directory, lane="lane-3", subject="APP-3", base_head=HEAD, evidence="criteria hold")
        self.assertLeft(self.row("lane-3"), "handed")

    def test_waiting_again_starts_afresh(self) -> None:
        first = self.working_then_waiting("lane-3", "APP-1")["waitingFrom"]
        self.focus("lane-3", "reviewing now", state="reviewing")
        again = self.focus("lane-3", "waiting again", state="waiting", waiting_on="APP-1")["waitingFrom"]
        self.assertEqual(again["state"], "reviewing")
        self.assertGreaterEqual(again["at"], first["at"])


class EveryViewOrdersLanesById(unittest.TestCase):
    def test_the_markdown_view_lists_lanes_by_id_whatever_order_the_record_keeps(self) -> None:
        from tests.test_waits import render_state
        document = waits_record()
        lanes = document["state"]["lanes"]
        lanes.append({**lanes[0], "id": "lane-10", "title": "Ten"})
        document["state"]["lanes"] = [lanes[3], lanes[1], lanes[2], lanes[0]]
        shown = render_state.render_markdown(document)
        section = shown[shown.index("## Lanes"):shown.index("## Finish line")]
        listed = [line.split(":")[0][2:] for line in section.splitlines() if line.startswith("- lane")]
        self.assertEqual(listed, ["lane-1", "lane-2", "lane-3", "lane-10"])


def run_delivery_root() -> Path:
    return Path(run_delivery.__file__).resolve().parents[3]


from types import SimpleNamespace  # noqa: E402
