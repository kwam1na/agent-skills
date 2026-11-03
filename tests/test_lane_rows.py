"""A lane's row stays true: the queue writes it when it merges or stops a candidate, and the lane's own word wins after."""

from __future__ import annotations

from unittest import mock

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
