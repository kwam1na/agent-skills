"""A recovered merge retains source time separately from its later observation."""
import unittest
import datetime
import json
from pathlib import Path
from unittest import mock

from tests import test_orchestration_state as fixtures

state_tool, queue_item, HEAD_A, entry = fixtures.state_tool, fixtures.queue_item, fixtures.HEAD_A, fixtures.entry


class MergeOccurrenceTests(unittest.TestCase):
    setUp = fixtures.ReconcileTests.setUp
    snapshot = fixtures.ReconcileTests.snapshot
    lead = fixtures.ReconcileTests.lead
    bytes = fixtures.ReconcileTests.bytes

    def forge(self, **fields):
        return Path(self.snapshot("forge", [{"subject": "work-a", "state": "merged", "head": HEAD_A,
                                              "mergedAt": "2026-10-20T10:00:00Z", **fields}]))

    def test_delayed_recovery_keeps_occurrence_and_observation_separate(self):
        self.lead({"queue": [queue_item("work-a")]})
        state_tool.reconcile(self.directory, [self.forge()])
        merged = state_tool.read_journal(self.directory)[-1]
        self.assertEqual(merged["occurredAt"], "2026-10-20T10:00:00Z")
        self.assertGreater(merged["at"], merged["occurredAt"])
        self.assertEqual(state_tool.show(self.directory)["stats"]["work-a"]["endedAt"], merged["occurredAt"])

    def test_already_merged_without_event_is_recovered_once_without_rewriting_state(self):
        self.lead({"queue": [queue_item("work-a")]})
        state_tool.patch(self.directory, writer="runner", version=state_tool.read_state(self.directory)["version"],
                         selector="queue[subject=work-a]", changes={"stage": "merged"})
        before = self.bytes()[0]
        forge = self.forge()
        self.assertTrue(state_tool.reconcile(self.directory, [forge])["drift"])
        self.assertEqual(self.bytes()[0], before)
        recovered = self.bytes()
        self.assertFalse(state_tool.reconcile(self.directory, [forge])["drift"])
        self.assertEqual(self.bytes(), recovered)
        self.assertEqual(len(state_tool.read_journal(self.directory)), 1)

    def test_terminal_candidate_is_not_relinked_to_another_change_request(self):
        self.lead({"queue": [queue_item("work-a", changeRequest="cr-original", runId="run-original")]})
        state_tool.patch(self.directory, writer="runner", version=state_tool.read_state(self.directory)["version"],
                         selector="queue[subject=work-a]", changes={"stage": "merged"})
        before = self.bytes()
        for stage in ("draft", "open", "closed", "merged"):
            with self.subTest(stage=stage):
                fields = {"subject": "work-a", "state": stage, "changeRequest": "cr-follow-on", "head": HEAD_A}
                if stage == "merged":
                    fields["mergedAt"] = "2026-10-20T10:00:00Z"
                result = state_tool.reconcile(self.directory, [Path(self.snapshot("forge", [fields]))])
                self.assertFalse(result["drift"])
                self.assertIn("change request differs", result["reports"][0]["note"])
                self.assertEqual(self.bytes(), before)
                self.assertFalse((self.directory / state_tool.RECOVERY_FILE).exists())

    def test_missing_source_time_is_unknown_but_normal_runner_entries_keep_their_time(self):
        self.lead({"queue": [queue_item("work-a")]})
        forge = Path(self.snapshot("forge", [{"subject": "work-a", "state": "merged", "head": HEAD_A}]))
        state_tool.reconcile(self.directory, [forge])
        merged = state_tool.read_journal(self.directory)[-1]
        self.assertIn("occurredAt", merged)
        self.assertIsNone(merged["occurredAt"])
        self.assertIsNone(state_tool.show(self.directory)["stats"]["work-a"]["endedAt"])
        normal = entry(1, "2026-10-20T10:00:00Z", "candidate.merged", "work-a")
        stats = state_tool.delivery_stats({"queue": [queue_item("work-a")]}, [normal])["work-a"]
        self.assertEqual(stats["endedAt"], normal["at"])
        self.assertIsNone(stats["totalSeconds"])

    def test_latest_landing_uses_occurrence_order_and_journal_times_are_validated(self):
        earlier = entry(1, "2026-10-20T12:00:00Z", "candidate.merged", "work-a")
        recovered = entry(2, "2026-10-20T13:00:00Z", "candidate.merged", "work-b",
                          occurredAt="2026-10-20T10:00:00Z")
        self.assertEqual(state_tool.latest_merge([earlier, recovered]), earlier)
        self.assertEqual(state_tool.journal_errors([earlier, recovered]), [])
        for value in ("bad", "2026-02-30T10:00:00Z", "2026-10-20T14:00:00Z", 123):
            with self.subTest(value=value):
                self.assertTrue(state_tool.journal_errors([earlier, {**recovered, "occurredAt": value}]))

    def test_invalid_source_times_are_refused_before_writes(self):
        self.lead({"queue": [queue_item("work-a")]})
        before = self.bytes()
        for value in ("bad", "2026-02-30T10:00:00Z", "2099-01-01T00:00:00Z", 123, None):
            with self.subTest(value=value), self.assertRaises(state_tool.StateError):
                state_tool.reconcile(self.directory, [self.forge(mergedAt=value)])
            self.assertEqual(self.bytes(), before)

    def test_crash_recovery_retains_source_time(self):
        self.lead({"queue": [queue_item("work-a")]})
        forge = self.forge()
        with mock.patch.object(state_tool, "_journal_recovery", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                state_tool.reconcile(self.directory, [forge])
        state_tool.reconcile(self.directory, [forge])
        self.assertEqual(state_tool.read_journal(self.directory)[-1]["occurredAt"], "2026-10-20T10:00:00Z")
        self.assertFalse(state_tool.reconcile(self.directory, [forge])["drift"])

    def test_late_handoff_does_not_invent_wait_or_check_durations(self):
        journal = [entry(1, "2026-10-20T09:00:00Z", "lane.assigned", "work-a"),
                   entry(2, "2026-10-20T11:00:00Z", "candidate.handed", "work-a"),
                   entry(3, "2026-10-20T11:10:00Z", "check.started", "work-a"),
                   entry(4, "2026-10-20T12:00:00Z", "candidate.merged", "work-a",
                         occurredAt="2026-10-20T10:00:00Z")]
        stats = state_tool.delivery_stats({"queue": [queue_item("work-a")]}, journal)["work-a"]
        self.assertEqual(stats["endedAt"], "2026-10-20T10:00:00Z")
        self.assertEqual(stats["totalSeconds"], 3600)
        self.assertIsNone(stats["waitSeconds"])
        self.assertIsNone(stats["checkToMergeSeconds"])

    def test_future_occurrence_inside_snapshot_skew_refuses_before_any_write(self):
        self.lead({"queue": [queue_item("work-a")]})
        before = self.bytes()
        future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        snapshot = {"source": "forge", "takenAt": future, "items": [
            {"subject": "work-a", "state": "merged", "head": HEAD_A, "mergedAt": future}]}
        path = self.root / "future.json"
        path.write_text(json.dumps(snapshot))
        with self.assertRaises(state_tool.StateError):
            state_tool.reconcile(self.directory, [path])
        self.assertEqual(self.bytes(), before)
        self.assertFalse((self.directory / state_tool.RECOVERY_FILE).exists())

    def test_already_merged_conflicting_head_is_reported_without_writes(self):
        self.lead({"queue": [queue_item("work-a")]})
        state_tool.patch(self.directory, writer="runner", version=state_tool.read_state(self.directory)["version"],
                         selector="queue[subject=work-a]", changes={"stage": "merged"})
        before = self.bytes()
        result = state_tool.reconcile(self.directory, [self.forge(head=fixtures.HEAD_B, changeRequest="different-pr")])
        self.assertFalse(result["drift"])
        self.assertIn("head", result["reports"][0]["note"])
        self.assertEqual(self.bytes(), before)
        self.assertFalse((self.directory / state_tool.RECOVERY_FILE).exists())

    def test_missing_source_head_uses_synced_identity_through_crash(self):
        self.lead({"queue": [queue_item("work-a")]})
        state_tool.patch(self.directory, writer="runner", version=state_tool.read_state(self.directory)["version"],
                         selector="queue[subject=work-a]", changes={"stage": "merged", "syncedHead": fixtures.HEAD_B})
        forge = Path(self.snapshot("forge", [{"subject": "work-a", "state": "merged", "mergedAt": "2026-10-20T10:00:00Z"}]))
        with mock.patch.object(state_tool, "_journal_recovery", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                state_tool.reconcile(self.directory, [forge])
        unrelated = Path(self.snapshot("tracker", []))
        state_tool.reconcile(self.directory, [unrelated])
        merged = state_tool.read_journal(self.directory)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["occurredAt"], "2026-10-20T10:00:00Z")
        self.assertFalse(state_tool.reconcile(self.directory, [forge])["drift"])

    def test_late_real_handoff_append_never_duplicates_recovered_merge(self):
        self.lead({"lanes": [{"id": "lane-1", "title": "Lane", "state": "working", "focus": "work-a",
                              "since": state_tool.now(), "waitingOn": None}],
                   "queue": [queue_item("work-a", changeRequest="pr-1")]})
        forge = self.forge(changeRequest="pr-1")
        append = state_tool.append_journal
        def interleave(directory, **kwargs):
            if kwargs["kind"] == "candidate.handed":
                state_tool.reconcile(directory, [forge])
            return append(directory, **kwargs)
        with mock.patch.object(state_tool, "append_journal", side_effect=interleave):
            state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head=HEAD_A, change_request="pr-1")
        before = self.bytes()
        self.assertFalse(state_tool.reconcile(self.directory, [forge])["drift"])
        self.assertEqual(self.bytes(), before)
        shown = state_tool.show(self.directory)
        self.assertEqual(len([e for e in shown["journal"] if e["kind"] == "candidate.merged"]), 1)
        self.assertEqual(shown["stats"]["work-a"]["endedAt"], "2026-10-20T10:00:00Z")
        state_tool.patch(self.directory, writer="runner", version=state_tool.read_state(self.directory)["version"],
                         selector="queue[subject=work-a]", changes={"stage": "stopped"})
        state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head=fixtures.HEAD_B, change_request="pr-2")
        later = self.forge(head=fixtures.HEAD_B, changeRequest="pr-2", mergedAt="2026-10-20T11:00:00Z")
        state_tool.reconcile(self.directory, [later])
        self.assertEqual(len([e for e in state_tool.read_journal(self.directory) if e["kind"] == "candidate.merged"]), 2)
        self.assertFalse(state_tool.reconcile(self.directory, [later])["drift"])
