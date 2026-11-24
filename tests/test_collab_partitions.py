"""Seats, per-seat partitions and the causal ingest of a shared record.

An unshared record is characterized first: its journal validation, its links, ``wait`` and the writer pin stay as
they are while a shared record adds seats, partitions and origins. The multi-machine cases build their own bare
remote and clones through ``tests/collab_remotes.py`` and observe a peer only by reading its record's files.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_skills

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = Path(agent_skills.__file__).resolve().parents[1]
SKILL = PRODUCT_ROOT / "skills/orchestrate-work"
SCRIPTS = SKILL / "scripts"
TOOL = SCRIPTS / "delivery_state.py"
MACHINE = {"cores": 16, "load5": 1.0, "diskFreeBytes": 500 * 2**30, "swapUsedBytes": 2**30}
LEDGER = {"capacity": 4, "used": 0, "running": [], "waiting": [], "overrides": []}


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


state_tool = _load("delivery_state")


def wait(directory: Path, after: int | None) -> tuple[int, dict]:
    return state_tool.wait_for_lead(directory, after=after, timeout=0, poll=0, read_machine=lambda _: MACHINE,
                                    read_ledger=lambda: LEDGER, sleep=lambda _: None)


def files(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in directory.iterdir()
            if path.is_file() and path.name in (state_tool.STATE_FILE, state_tool.JOURNAL_FILE)}


class UnsharedRecordCharacterizationTests(unittest.TestCase):
    """What an unshared record does today, which a shared record must leave exactly as it is."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name) / "record"
        state_tool.init(self.directory, "Unshared")

    def decide(self, **links: list[int]) -> dict:
        return state_tool.append_journal(self.directory, kind="decision.recorded",
                                         data={"text": "Take the left lane.", "why": "A fixture."}, **links)

    def test_journal_ids_run_from_one_and_an_entry_carries_no_origin(self) -> None:
        first, second = self.decide(), self.decide(because=[1])
        self.assertEqual((first["id"], second["id"]), (1, 2))
        self.assertNotIn("origin", first)
        self.assertNotIn("collab", state_tool.read_state(self.directory)["meta"])
        entries = state_tool.read_journal(self.directory)
        self.assertEqual(state_tool.journal_errors(entries), [])
        self.assertIn("entry 2: ids must run in sequence from 1",
                      state_tool.journal_errors([entries[0], dict(entries[1], id=3)]))
        self.assertIn("entry 1/because: entry 1 is not an earlier entry of this journal",
                      state_tool.journal_errors([dict(entries[1], id=1), dict(entries[0], id=2)]))

    def test_because_and_answers_name_earlier_entries_and_answers_only_the_kinds_the_registry_lists(self) -> None:
        self.decide()
        before = files(self.directory)
        for links, said in (({"because": [2]}, "/because: entry 2 is not an earlier entry of this journal"),
                            ({"because": [1, 1]}, "/because: names an entry twice")):
            with self.subTest(links=links):
                with self.assertRaises(state_tool.StateError) as refused:
                    self.decide(**links)
                self.assertEqual(refused.exception.code, "entry_invalid")
                self.assertIn(said, str(refused.exception))
                self.assertEqual(files(self.directory), before)
        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.append_journal(self.directory, kind="blocker.resolved", data={"why": "Done."}, answers=[1])
        self.assertIn("/answers: a blocker.resolved does not answer a decision.recorded", str(refused.exception))
        blocker = state_tool.append_journal(self.directory, kind="blocker.recorded", data={"text": "Stuck."})
        resolved = state_tool.append_journal(self.directory, kind="blocker.resolved", data={"why": "Done."},
                                             answers=[blocker["id"]])
        self.assertEqual(resolved["answers"], [2])

    def test_wait_after_wakes_on_a_later_entry_of_a_wait_kind_only(self) -> None:
        self.decide()
        code, quiet = wait(self.directory, 0)
        self.assertEqual((code, quiet["woke"], quiet["events"]), (3, "expired", []))
        state_tool.append_journal(self.directory, kind="blocker.recorded", data={"text": "Stuck."})
        code, woke = wait(self.directory, 1)
        self.assertEqual((code, woke["woke"], [event["id"] for event in woke["events"]]), (0, "event", [2]))
        self.assertEqual(wait(self.directory, 2)[1]["woke"], "expired")

    def test_the_writer_pin_refuses_another_generation_and_leaves_the_record(self) -> None:
        first, second = ({"releaseId": "test-old", "archiveSha256": "a" * 64},
                         {"releaseId": "test-new", "archiveSha256": "b" * 64})
        with mock.patch.object(state_tool, "writer_generation", return_value=first):
            state_tool.upgrade_writer(self.directory, expected="unpinned", reason="Pin the fixture")
        before = files(self.directory)
        with mock.patch.object(state_tool, "writer_generation", return_value=second):
            with self.assertRaises(state_tool.StateError) as refused:
                self.decide()
        self.assertEqual(refused.exception.code, "writer_generation_mismatch")
        self.assertEqual(files(self.directory), before)
        with mock.patch.object(state_tool, "writer_generation", return_value=first):
            self.assertEqual(self.decide()["id"], 1)


if __name__ == "__main__":
    unittest.main()
