"""Lane and queue scopes on a shared record: who writes each lane's rows and queue elements, the runner only where the
seat holds the queue, the queue's serve only there, and the queue's handover.

An unshared record is characterized first: the runner's writes, a lane's hand-off and the lead's acceptance stay as they
are. The shared cases use the partitions tests' fixture, and, for the queue's own runs, the merge-queue tests' adopter.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import test_collab_partitions as partitions

state_tool, collab = partitions.state_tool, partitions.collab
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


if __name__ == "__main__":
    unittest.main()
