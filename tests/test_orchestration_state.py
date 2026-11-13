from __future__ import annotations

import contextlib
import copy
import datetime
import importlib.util
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

import agent_skills
from agent_skills.validate import LEAKAGE_PATTERNS, validate_corpus
from quiet_git import quieten


ROOT = Path(__file__).resolve().parents[1]
# The product under test: this repository, or the extracted release candidate that
# qualification puts first on the path, so its shipped scripts are the ones exercised.
PRODUCT_ROOT = Path(agent_skills.__file__).resolve().parents[1]
SKILL = PRODUCT_ROOT / "skills/orchestrate-work"
SCRIPTS = SKILL / "scripts"
TOOL = SCRIPTS / "delivery_state.py"
SCHEMA_FILE = SKILL / "references/schemas.json"
HEAD_A = "a" * 40
HEAD_B = "b" * 40


def _load(name: str):
    # Importing the skill's modules here must not leave bytecode beside them.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))
        sys.dont_write_bytecode = previous


state_tool = _load("delivery_state")
# The schema validator lives in the state tool.
schema_module = state_tool


def queue_item(subject: str, **fields: object) -> dict:
    item = {
        "order": 1,
        "subject": subject,
        "title": f"Candidate {subject}",
        "lane": "lane-1",
        "stage": "queued",
        "head": HEAD_A,
        "syncedHead": None,
        "leadInputs": [],
        "reportedRounds": 2,
        "runId": None,
        "checks": [],
    }
    item.update(fields)
    return item


# A console that cannot encode most of Unicode, as a Windows pipe defaults to.
NARROW_CONSOLE = {"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}


def run_tool(
    directory: Path, *args: str, stdin: str | bytes | None = None, environment: dict[str, str] | None = None
) -> tuple[int, dict]:
    # Bytes in and out: the tool's contract is UTF-8 on stdin and ASCII JSON on
    # stdout, whatever the console encoding.
    result = subprocess.run(
        [sys.executable, "-B", str(TOOL), "--dir", str(directory), *args],
        input=stdin.encode("utf-8") if isinstance(stdin, str) else stdin,
        capture_output=True,
        env={**os.environ, **(environment or {})},
        check=False,
    )
    # A crash prints nothing on stdout: show why instead of a bare decode error.
    assert result.stdout, result.stderr.decode("utf-8", "replace")
    assert result.stdout.isascii(), result.stdout
    return result.returncode, json.loads(result.stdout.decode("ascii"))


class StateToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "state"
        state_tool.init(self.directory, "Epic underway")

    def state_bytes(self) -> bytes:
        return (self.directory / state_tool.STATE_FILE).read_bytes()

    def lead(self, version: int, changes: object, selector: str | None = None) -> dict:
        return state_tool.patch(
            self.directory, writer="lead", version=version, changes=changes, selector=selector
        )

    def test_init_patch_show_round_trips(self) -> None:
        code, created = run_tool(self.directory.parent / "fresh", "init", "--headline", "First")
        self.assertEqual(code, 0, created)
        self.assertEqual(created["state"]["version"], 1)
        fresh = self.directory.parent / "fresh"
        lane = {"id": "lane-1", "title": "Renderer", "focus": "views", "state": "working", "waitingOn": None,
                "since": "2026-10-20T10:00:00Z"}
        code, patched = run_tool(
            fresh, "patch", "--writer", "lead", "--version", "1",
            "--patch", json.dumps({"lanes": [lane], "queue": [queue_item("pr-1")]}),
        )
        self.assertEqual(code, 0, patched)
        code, shown = run_tool(fresh, "show")
        self.assertEqual(code, 0, shown)
        self.assertEqual(shown["state"], patched["state"])
        self.assertEqual(shown["state"]["version"], 2)
        self.assertEqual(shown["state"]["lanes"], [{**lane, "focusAt": shown["state"]["lanes"][0]["focusAt"]}])
        self.assertEqual(shown["state"]["queue"], [queue_item("pr-1")])
        self.assertEqual(shown["journal"], [])
        self.assertEqual(run_tool(fresh, "validate"), (0, {"ok": True}))

    def test_an_invalid_hold_names_the_required_shape(self) -> None:
        before = self.state_bytes()
        with self.assertRaises(state_tool.StateError) as refused:
            self.lead(1, {"meta": {"hold": {"reason": "pause"}}})
        self.assertIn("{reason, since}", str(refused.exception))
        self.assertEqual(self.state_bytes(), before)

    def test_live_flakes_are_lead_owned_and_validate_test_patterns(self) -> None:
        flake = {"baseHead": HEAD_A, "check": "suite", "tests": ["test_flaky"], "ref": "FX-1"}
        before = self.state_bytes()
        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.patch(self.directory, writer="runner", version=1, changes={"liveFlakes": [flake]})
        self.assertEqual(refused.exception.code, "field_not_owned")
        with self.assertRaisesRegex(state_tool.StateError, "regular expression"):
            self.lead(1, {"liveFlakes": [{**flake, "tests": ["("]}]})
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(self.lead(1, {"liveFlakes": [flake]})["liveFlakes"], [flake])

    def test_init_refuses_to_replace_existing_state(self) -> None:
        before = self.state_bytes()
        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.init(self.directory, "Again")
        self.assertEqual(refused.exception.code, "state_exists")
        self.assertEqual(self.state_bytes(), before)

    def test_stale_version_is_refused_and_nothing_is_written(self) -> None:
        self.lead(1, {"meta": {"headline": "Moved on"}})
        before = self.state_bytes()
        code, refused = run_tool(
            self.directory, "patch", "--writer", "lead", "--version", "1",
            "--patch", json.dumps({"meta": {"headline": "Late"}}),
        )
        self.assertEqual((code, refused["code"]), (1, "version_stale"))
        self.assertEqual(self.state_bytes(), before)

    def test_invalid_patches_are_refused_and_nothing_is_written(self) -> None:
        self.lead(1, {"queue": [queue_item("pr-1")]})
        before = self.state_bytes()
        cases = {
            "not an object": ("lead", ["x"], None, "patch_invalid"),
            "unknown field": ("lead", {"surprise": 1}, None, "patch_invalid"),
            "wrong type": ("lead", {"lanes": {}}, None, "patch_invalid"),
            "bad stage": ("runner", {"stage": "teleported"}, "queue[subject=pr-1]", "patch_invalid"),
            "required field removed": ("lead", {"meta": {"headline": None}}, None, "patch_invalid"),
            "bad head": ("lead", {"title": "x", "head": "not-a-commit"}, "queue[subject=pr-1]", "patch_invalid"),
            "duplicate subject": ("lead", {"queue": [queue_item("pr-1"), queue_item("pr-1")]}, None, "patch_invalid"),
            "selector matches nothing": ("lead", {"title": "x"}, "queue[subject=pr-9]", "selector_unmatched"),
            "selector by the wrong key": ("lead", {"title": "x"}, "queue[id=pr-1]", "selector_invalid"),
            "malformed selector": ("lead", {"title": "x"}, "queue", "selector_invalid"),
            "renaming the selected element": ("lead", {"subject": "pr-2"}, "queue[subject=pr-1]", "patch_invalid"),
            "tool-owned version": ("lead", {"version": 9}, None, "field_not_owned"),
            "tool-owned timestamp": ("lead", {"meta": {"updatedAt": "2026-01-01T00:00:00Z"}}, None, "field_not_owned"),
        }
        for name, (writer, changes, selector, code) in cases.items():
            with self.subTest(name):
                with self.assertRaises(state_tool.StateError) as refused:
                    state_tool.patch(self.directory, writer=writer, version=2, changes=changes, selector=selector)
                self.assertEqual(refused.exception.code, code, str(refused.exception))
                self.assertEqual(self.state_bytes(), before)
        code, refused = run_tool(self.directory, "patch", "--writer", "lead", "--version", "2", "--patch", "{")
        self.assertEqual((code, refused["code"]), (1, "patch_invalid"))
        self.assertEqual(self.state_bytes(), before)

    def test_patch_reads_from_stdin(self) -> None:
        code, patched = run_tool(
            self.directory, "patch", "--writer", "lead", "--version", "1", "--patch", "-",
            stdin=json.dumps({"meta": {"headline": "From stdin"}}),
        )
        self.assertEqual(code, 0, patched)
        self.assertEqual(patched["state"]["meta"]["headline"], "From stdin")

    def test_journal_ids_stay_sequential_across_restarts(self) -> None:
        for number in range(1, 4):
            # Each append is a separate process: nothing is remembered between them.
            code, appended = run_tool(
                self.directory, "journal", "--kind", "decision.recorded", "--summary", f"Decision {number}",
                "--lane", "lane-1",
            )
            self.assertEqual(code, 0, appended)
            self.assertEqual(appended["entry"]["id"], number)
        code, shown = run_tool(self.directory, "show")
        self.assertEqual([entry["id"] for entry in shown["journal"]], [1, 2, 3])
        self.assertEqual(shown["journal"][0]["lane"], "lane-1")
        self.assertIsNone(shown["journal"][0]["subject"])

    def test_a_close_journaled_by_hand_carries_its_started_mark_and_finishes_once(self) -> None:
        run_tool(self.directory, "journal", "--kind", "decision.recorded", "--summary", "Before")
        code, closed = run_tool(self.directory, "journal", "--kind", "delivery.closed", "--summary", "Closed by hand")
        self.assertEqual(code, 0, closed)
        entry = closed["entry"]
        closing = state_tool.read_state(self.directory)["meta"]["closing"]
        self.assertEqual(closing, {"started": {"entry": entry["id"], "at": entry["at"]}})
        # Only the latest close finishes, and only once.
        code, refused = run_tool(self.directory, "close-finished", "--entry", "1")
        self.assertEqual((code, refused["code"]), (1, "close_unknown"))
        code, done = run_tool(self.directory, "close-finished", "--entry", str(entry["id"]))
        self.assertEqual(code, 0, done)
        finished = done["state"]["meta"]["closing"]["finished"]
        self.assertEqual(finished["entry"], entry["id"])
        before = self.state_bytes()
        code, again = run_tool(self.directory, "close-finished", "--entry", str(entry["id"]))
        self.assertEqual(code, 0, again)
        self.assertEqual(self.state_bytes(), before)
        # A later close starts afresh, its finished mark gone until its own archive lands.
        later = state_tool.append_journal(self.directory, kind="delivery.closed", summary="Closed again")
        self.assertEqual(state_tool.read_state(self.directory)["meta"]["closing"], {"started": {"entry": later["id"], "at": later["at"]}})
        with self.assertRaises(state_tool.StateError) as stale:
            state_tool.close_finished(self.directory, entry=entry["id"])
        self.assertEqual(stale.exception.code, "close_unknown")

    def test_only_the_latest_close_finishes_even_when_its_mark_names_an_earlier_one(self) -> None:
        first = state_tool.append_journal(self.directory, kind="delivery.closed", summary="Closed")
        later = state_tool.append_journal(self.directory, kind="delivery.closed", summary="Closed again")
        # As an older tool would leave it: a later close appended with no mark of its own.
        state = state_tool.read_state(self.directory)
        state["meta"]["closing"] = {"started": {"entry": first["id"], "at": first["at"]}}
        (self.directory / state_tool.STATE_FILE).write_text(state_tool.dumps(state), encoding="utf-8")
        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.close_finished(self.directory, entry=first["id"])
        self.assertEqual(refused.exception.code, "close_unknown")
        self.assertNotIn("finished", state_tool.read_state(self.directory)["meta"]["closing"])
        self.assertGreater(later["id"], first["id"])

    def test_a_close_cannot_finish_without_its_started_mark(self) -> None:
        state_tool.append_journal(self.directory, kind="delivery.closed", summary="Closed")
        state = state_tool.read_state(self.directory)
        del state["meta"]["closing"]
        (self.directory / state_tool.STATE_FILE).write_text(state_tool.dumps(state), encoding="utf-8")
        before = self.state_bytes()
        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.close_finished(self.directory, entry=1)
        self.assertEqual(refused.exception.code, "close_unknown")
        self.assertEqual(self.state_bytes(), before)

    def test_unknown_journal_kind_is_refused(self) -> None:
        journal = self.directory / state_tool.JOURNAL_FILE
        before = journal.read_bytes()
        code, refused = run_tool(self.directory, "journal", "--kind", "lane.teleported", "--summary", "No")
        self.assertEqual((code, refused["code"]), (1, "entry_invalid"))
        self.assertEqual(journal.read_bytes(), before)
        with self.assertRaises(state_tool.StateError) as empty:
            state_tool.append_journal(self.directory, kind="decision.recorded", summary="")
        self.assertEqual(empty.exception.code, "entry_invalid")

    def test_journal_summaries_may_carry_any_line_separator(self) -> None:
        summaries = ["two\u2028lines", "two\u2029paragraphs", "next\u0085line", "carriage\rreturn", "new\nline"]
        for summary in summaries:
            code, appended = run_tool(self.directory, "journal", "--kind", "decision.recorded", "--summary", summary)
            self.assertEqual(code, 0, appended)
        code, shown = run_tool(self.directory, "show")
        self.assertEqual(code, 0, shown)
        self.assertEqual([entry["summary"] for entry in shown["journal"]], summaries)
        self.assertEqual([entry["id"] for entry in shown["journal"]], [1, 2, 3, 4, 5])

    def test_output_is_parseable_whatever_the_console_encoding(self) -> None:
        summaries = ["two\u2028lines", "two\u2029paragraphs", "carriage\rreturn", "windows\r\nline", "caf\u00e9 \u2713"]
        for summary in summaries:
            with self.subTest(summary=summary):
                code, appended = run_tool(
                    self.directory, "journal", "--kind", "decision.recorded", "--summary", summary,
                    environment=NARROW_CONSOLE,
                )
                self.assertEqual(code, 0, appended)
                self.assertEqual(appended["entry"]["summary"], summary)
        code, shown = run_tool(self.directory, "show", environment=NARROW_CONSOLE)
        self.assertEqual(code, 0, shown)
        self.assertEqual([entry["summary"] for entry in shown["journal"]], summaries)
        code, refused = run_tool(self.directory, "journal", "--kind", "no.such\u2028kind", "--summary", "x",
                                 environment=NARROW_CONSOLE)
        self.assertEqual((code, refused["code"]), (1, "entry_invalid"))

    def test_stdin_patches_are_read_as_utf8_whatever_the_console_encoding(self) -> None:
        headline = "Caf\u00e9 \u2713 \u2028 done"
        code, patched = run_tool(
            self.directory, "patch", "--writer", "lead", "--version", "1", "--patch", "-",
            stdin=json.dumps({"meta": {"headline": headline}}, ensure_ascii=False), environment=NARROW_CONSOLE,
        )
        self.assertEqual(code, 0, patched)
        self.assertEqual(patched["state"]["meta"]["headline"], headline)
        code, shown = run_tool(self.directory, "show", environment=NARROW_CONSOLE)
        self.assertEqual(shown["state"]["meta"]["headline"], headline)

    def test_undecodable_input_is_refused_as_json(self) -> None:
        code, refused = run_tool(self.directory, "patch", "--writer", "lead", "--version", "1", "--patch", "-",
                                 stdin=b'\xff{"meta":{}}', environment=NARROW_CONSOLE)
        self.assertEqual((code, refused["code"]), (1, "patch_invalid"))
        code, patched = run_tool(self.directory, "patch", "--writer", "lead", "--version", "1", "--patch", "-",
                                 stdin=b'\xef\xbb\xbf{"meta":{"headline":"With a BOM"}}', environment=NARROW_CONSOLE)
        self.assertEqual(code, 0, patched)
        self.assertEqual(patched["state"]["meta"]["headline"], "With a BOM")
        with open(self.directory / state_tool.STATE_FILE, "ab") as stream:
            stream.write(b"\xff")
        code, refused = run_tool(self.directory, "show", environment=NARROW_CONSOLE)
        self.assertEqual((code, refused["code"]), (1, "state_invalid"))

    def test_a_lone_surrogate_is_refused_and_nothing_is_written(self) -> None:
        state_before = self.state_bytes()
        journal = self.directory / state_tool.JOURNAL_FILE
        journal_before = journal.read_bytes()
        code, refused = run_tool(self.directory, "patch", "--writer", "lead", "--version", "1",
                                 "--patch", '{"meta":{"headline":"\\ud800"}}')
        self.assertEqual((code, refused["code"]), (1, "patch_invalid"))
        with self.assertRaises(state_tool.StateError) as entry:
            state_tool.append_journal(self.directory, kind="decision.recorded", summary="bad\udcff")
        self.assertEqual(entry.exception.code, "entry_invalid")
        with self.assertRaises(state_tool.StateError) as fresh:
            state_tool.init(self.directory.parent / "fresh", "bad\udcff")
        self.assertEqual(fresh.exception.code, "state_invalid")
        self.assertFalse((self.directory.parent / "fresh" / state_tool.STATE_FILE).exists())
        self.assertEqual(self.state_bytes(), state_before)
        self.assertEqual(journal.read_bytes(), journal_before)

    def test_files_are_the_same_bytes_on_every_platform(self) -> None:
        summary = "caf\u00e9\u2028line\r\nnext"
        entry = state_tool.append_journal(self.directory, kind="decision.recorded", summary=summary, lane="l\u00e9")
        line = json.dumps(entry, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
        self.assertEqual((self.directory / state_tool.JOURNAL_FILE).read_bytes(), line)
        state = self.lead(1, {"meta": {"headline": "caf\u00e9\r\nnext"}})
        expected = (json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        written = (self.directory / state_tool.STATE_FILE).read_bytes()
        self.assertEqual(written, expected)
        self.assertNotIn(b"\r\n", written)

    def test_an_unfinished_append_is_ignored_and_then_replaced(self) -> None:
        state_tool.append_journal(self.directory, kind="decision.recorded", summary="Whole")
        journal = self.directory / state_tool.JOURNAL_FILE
        with open(journal, "ab") as stream:
            stream.write(b'{"id":2,"at":"2026-10-20T10:00:00Z","kind":"decis')
        self.assertEqual([entry["id"] for entry in state_tool.show(self.directory)["journal"]], [1])
        entry = state_tool.append_journal(self.directory, kind="decision.recorded", summary="After the crash")
        self.assertEqual(entry["id"], 2)
        self.assertEqual(len(journal.read_text(encoding="utf-8").split("\n")), 3)
        self.assertEqual([item["summary"] for item in state_tool.show(self.directory)["journal"]],
                         ["Whole", "After the crash"])

    def test_set_writes_null_into_a_nullable_field(self) -> None:
        now = {"subject": "pr-1", "title": "Candidate", "startedAt": "2026-10-20T10:00:00Z", "steps": []}
        state_tool.patch(self.directory, writer="runner", version=1, changes={"meta": {"checking": {"now": now}}})
        code, cleared = run_tool(
            self.directory, "patch", "--writer", "runner", "--version", "2",
            "--set", json.dumps({"/meta/checking/now": None}),
        )
        self.assertEqual(code, 0, cleared)
        self.assertIsNone(cleared["state"]["meta"]["checking"]["now"])
        lane = {"id": "lane-1", "title": "Renderer", "focus": "views", "state": "blocked", "waitingOn": "lead",
                "since": "2026-10-20T10:00:00Z"}
        self.lead(3, {"lanes": [lane], "queue": [queue_item("pr-1", runId="run-1")]})
        code, patched = run_tool(
            self.directory, "patch", "--writer", "lead", "--version", "4", "--element", "lanes[id=lane-1]",
            "--patch", json.dumps({"state": "working"}), "--set", json.dumps({"/waitingOn": None}),
        )
        self.assertEqual(code, 0, patched)
        row = patched["state"]["lanes"][0]
        self.assertEqual(row, {**lane, "state": "working", "waitingOn": None, "focusAt": row["focusAt"]})
        patched = state_tool.patch(self.directory, writer="lead", version=5, selector="queue[subject=pr-1]",
                                   assignments={"/runId": None, "/lane": None, "/reportedRounds": None})
        self.assertEqual(patched["queue"][0], queue_item("pr-1", lane=None, reportedRounds=None))

    def test_set_is_held_to_the_same_rules_as_a_patch(self) -> None:
        self.lead(1, {"queue": [queue_item("pr-1")]})
        before = self.state_bytes()
        cases = {
            "runner on a lead field": ("runner", {"/meta/headline": "Runner says"}, None, "field_not_owned"),
            "lead on a runner field": ("lead", {"/stage": "merged"}, "queue[subject=pr-1]", "field_not_owned"),
            "the selected element's key": ("lead", {"/subject": "pr-2"}, "queue[subject=pr-1]", "patch_invalid"),
            "a required field set to null": ("lead", {"/meta/headline": None}, None, "patch_invalid"),
            "a missing parent": ("lead", {"/meta/nowhere/field": 1}, None, "patch_invalid"),
            "the whole document": ("lead", {"": {}}, None, "patch_invalid"),
            "not a pointer": ("lead", {"meta": {}}, None, "patch_invalid"),
            "not an object": ("lead", [["/meta/headline", "x"]], None, "patch_invalid"),
        }
        for name, (writer, assignments, selector, code) in cases.items():
            with self.subTest(name):
                with self.assertRaises(state_tool.StateError) as refused:
                    state_tool.patch(self.directory, writer=writer, version=2, selector=selector,
                                     assignments=assignments)
                self.assertEqual(refused.exception.code, code, str(refused.exception))
                self.assertEqual(self.state_bytes(), before)
        code, refused = run_tool(self.directory, "patch", "--writer", "lead", "--version", "2")
        self.assertEqual((code, refused["code"]), (1, "patch_invalid"))

    def test_show_reads_the_state_and_journal_from_one_write(self) -> None:
        state_tool.append_journal(self.directory, kind="decision.recorded", summary="First")
        landed = threading.Event()
        attempted = threading.Event()
        failures: list[BaseException] = []

        def writer() -> None:
            attempted.set()
            try:
                self.lead(1, {"meta": {"headline": "Landed between the reads"}})
                state_tool.append_journal(self.directory, kind="decision.recorded", summary="Second")
            except BaseException as error:  # noqa: BLE001
                failures.append(error)
            landed.set()

        thread = threading.Thread(target=writer)

        def between_reads() -> None:
            # A writer that arrives between the two reads must wait for both.
            thread.start()
            self.assertTrue(attempted.wait(5))
            self.assertFalse(landed.wait(0.5), "a write landed while show held the state lock")

        shown = state_tool.show(self.directory, between_reads=between_reads)
        thread.join(10)
        self.assertEqual(failures, [])
        self.assertTrue(landed.is_set())
        self.assertEqual(shown["state"]["version"], 1)
        self.assertEqual([entry["summary"] for entry in shown["journal"]], ["First"])
        after = state_tool.show(self.directory)
        self.assertEqual(after["state"]["version"], 2)
        self.assertEqual([entry["summary"] for entry in after["journal"]], ["First", "Second"])

    def test_show_of_a_missing_or_empty_directory_is_state_missing(self) -> None:
        missing = self.directory.parent / "never-created"
        empty = self.directory.parent / "empty"
        empty.mkdir()
        for directory in (missing, empty):
            with self.subTest(directory=directory.name):
                for command in ("show", "validate"):
                    code, refused = run_tool(directory, command)
                    self.assertEqual((code, refused["code"]), (1, "state_missing"))
        self.assertFalse(missing.exists())
        self.assertEqual(list(empty.iterdir()), [])

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "POSIX permissions; root ignores them")
    def test_a_read_only_state_directory_is_refused_as_json(self) -> None:
        (self.directory / state_tool.STATE_LOCK).unlink(missing_ok=True)
        os.chmod(self.directory, 0o555)
        self.addCleanup(os.chmod, self.directory, 0o755)
        code, refused = run_tool(self.directory, "show")
        self.assertEqual((code, refused["code"]), (1, "lock_unavailable"))

    def test_show_waits_for_a_write_in_progress(self) -> None:
        held = threading.Event()
        release = threading.Event()

        def hold() -> None:
            with state_tool.file_lock(self.directory / state_tool.STATE_LOCK):
                held.set()
                release.wait(10)

        holder = threading.Thread(target=hold)
        holder.start()
        self.addCleanup(holder.join)
        self.addCleanup(release.set)
        self.assertTrue(held.wait(5))
        original = state_tool.LOCK_TIMEOUT_SECONDS
        state_tool.LOCK_TIMEOUT_SECONDS = 0.2
        try:
            with self.assertRaises(state_tool.StateError) as refused:
                state_tool.show(self.directory)
        finally:
            state_tool.LOCK_TIMEOUT_SECONDS = original
            release.set()
        self.assertEqual(refused.exception.code, "lock_timeout")

    def test_a_merge_patch_deletes_on_null_and_set_stores_null(self) -> None:
        self.lead(1, {"extensions": {"a": 1, "b": 2}})
        patched = self.lead(2, {"extensions": {"a": None}})
        self.assertEqual(patched["extensions"], {"b": 2})
        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.patch(self.directory, writer="runner", version=3,
                             changes={"meta": {"checking": {"now": None}}})
        self.assertEqual(refused.exception.code, "patch_invalid")
        patched = state_tool.patch(self.directory, writer="lead", version=3, assignments={"/extensions/b": None})
        self.assertEqual(patched["extensions"], {"b": None})

    def test_set_unescapes_pointers_and_applies_after_the_merge_patch(self) -> None:
        patched = state_tool.patch(
            self.directory, writer="lead", version=1,
            changes={"meta": {"headline": "From the patch"}, "extensions": {"x~y": 1}},
            assignments={"/meta/headline": "From set", "/extensions/a~1b": 1, "/extensions/c~0d": 2},
        )
        self.assertEqual(patched["meta"]["headline"], "From set")
        self.assertEqual(patched["extensions"], {"x~y": 1, "a/b": 1, "c~d": 2})

    def test_a_torn_tail_that_splits_a_character_is_ignored_and_then_replaced(self) -> None:
        state_tool.append_journal(self.directory, kind="decision.recorded", summary="caf\u00e9")
        journal = self.directory / state_tool.JOURNAL_FILE
        with open(journal, "ab") as stream:
            stream.write(b'{"id":2,"summary":"\xc3')
        self.assertEqual([entry["summary"] for entry in state_tool.show(self.directory)["journal"]], ["caf\u00e9"])
        self.assertEqual(state_tool.append_journal(self.directory, kind="decision.recorded", summary="n")["id"], 2)
        self.assertTrue(journal.read_bytes().isascii())

    def test_a_journal_that_breaks_sequence_is_invalid(self) -> None:
        state_tool.append_journal(self.directory, kind="decision.recorded", summary="One")
        journal = self.directory / state_tool.JOURNAL_FILE
        entry = json.loads(journal.read_text(encoding="utf-8"))
        entry["id"] = 5
        journal.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        code, refused = run_tool(self.directory, "validate")
        self.assertEqual((code, refused["code"]), (1, "journal_invalid"))

    def test_a_journal_entry_dated_on_a_day_that_does_not_exist_is_invalid_not_a_crash(self) -> None:
        state_tool.append_journal(self.directory, kind="decision.recorded", summary="One")
        journal = self.directory / state_tool.JOURNAL_FILE
        entry = json.loads(journal.read_text(encoding="utf-8"))
        entry["at"] = "2026-02-30T08:00:00Z"
        journal.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        for command in ("validate", "show"):
            with self.subTest(command=command):
                code, refused = run_tool(self.directory, command)
                self.assertEqual((code, refused["code"]), (1, "journal_invalid"))
                self.assertIn("not a real time", refused["error"])

    def test_seam_refuses_a_stale_patch_while_a_concurrent_journal_append_succeeds(self) -> None:
        appended: list[dict] = []

        def meanwhile() -> None:
            # Another writer lands a patch and a journal entry between our read and write.
            self.lead(1, {"meta": {"headline": "Concurrent"}})
            appended.append(
                state_tool.append_journal(self.directory, kind="candidate.handed", summary="Handed", subject="pr-1")
            )

        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.patch(
                self.directory, writer="lead", version=1, changes={"meta": {"headline": "Mine"}},
                between_read_and_write=meanwhile,
            )
        self.assertEqual(refused.exception.code, "version_stale")
        shown = state_tool.show(self.directory)
        self.assertEqual(shown["state"]["version"], 2)
        self.assertEqual(shown["state"]["meta"]["headline"], "Concurrent")
        self.assertEqual(shown["journal"], appended)
        self.assertEqual(appended[0]["id"], 1)

    def test_runner_cannot_write_a_lead_owned_field(self) -> None:
        self.lead(1, {"queue": [queue_item("pr-1")]})
        before = self.state_bytes()
        cases = {
            "headline": ({"meta": {"headline": "Runner says"}}, None),
            "lanes": ({"lanes": [{"id": "lane-9", "title": "Runner's own", "focus": "", "state": "idle",
                                  "waitingOn": None, "since": "2026-10-20T10:00:00Z"}]}, None),
            "element title": ({"title": "Renamed"}, "queue[subject=pr-1]"),
            "reviewed head": ({"head": HEAD_B}, "queue[subject=pr-1]"),
            "new queue element": ({"queue": [queue_item("pr-1"), queue_item("pr-2")]}, None),
            "extensions": ({"extensions": {"adopter": True}}, None),
        }
        for name, (changes, selector) in cases.items():
            with self.subTest(name):
                code, refused = run_tool(
                    self.directory, "patch", "--writer", "runner", "--version", "2",
                    *(["--element", selector] if selector else []), "--patch", json.dumps(changes),
                )
                self.assertEqual((code, refused["code"]), (1, "field_not_owned"), refused)
                self.assertEqual(self.state_bytes(), before)

    def test_lead_cannot_write_a_runner_owned_field(self) -> None:
        self.lead(1, {"queue": [queue_item("pr-1")]})
        before = self.state_bytes()
        cases = {
            "stage": ({"stage": "merged"}, "queue[subject=pr-1]"),
            "checks": ({"checks": [{"id": "unit", "state": "passed"}]}, "queue[subject=pr-1]"),
            "synced head": ({"syncedHead": HEAD_B}, "queue[subject=pr-1]"),
            "base head": ({"meta": {"baseHead": HEAD_B}}, None),
            "checking": ({"meta": {"checking": {"now": None, "next": ["pr-1"]}}}, None),
            "released head": ({"meta": {"releasedHead": HEAD_B}}, None),
            "a new element that starts mid-queue": (
                {"queue": [queue_item("pr-1"), queue_item("pr-2", stage="checked")]}, None),
        }
        for name, (changes, selector) in cases.items():
            with self.subTest(name):
                with self.assertRaises(state_tool.StateError) as refused:
                    state_tool.patch(self.directory, writer="lead", version=2, changes=changes, selector=selector)
                self.assertEqual(refused.exception.code, "field_not_owned")
                self.assertEqual(self.state_bytes(), before)

    def test_element_update_changes_only_the_selected_element(self) -> None:
        self.lead(1, {"queue": [queue_item("pr-1"), queue_item("pr-2", order=2)]})
        before = state_tool.show(self.directory)["state"]
        checks = [{"id": "unit", "state": "running", "detail": "3 of 9"}]
        code, patched = run_tool(
            self.directory, "patch", "--writer", "runner", "--version", "2", "--element", "queue[subject=pr-2]",
            "--patch", json.dumps({"stage": "checking", "syncedHead": HEAD_B, "checks": checks}),
        )
        self.assertEqual(code, 0, patched)
        after = patched["state"]
        self.assertEqual(after["queue"][0], before["queue"][0])
        self.assertEqual(after["queue"][1], {**before["queue"][1], "stage": "checking", "syncedHead": HEAD_B,
                                             "checks": checks})
        expected = copy.deepcopy(before)
        expected["queue"] = after["queue"]
        expected["version"] = 3
        expected["meta"]["updatedAt"] = after["meta"]["updatedAt"]
        self.assertEqual(after, expected)

    def test_runner_writes_its_meta_fields(self) -> None:
        now = {"subject": "pr-1", "title": "Candidate", "startedAt": "2026-10-20T10:00:00Z",
               "steps": [{"label": "sync", "state": "passed"}, {"label": "check", "state": "running"}]}
        patched = state_tool.patch(
            self.directory, writer="runner", version=1,
            changes={"meta": {"baseHead": HEAD_A, "releasedHead": HEAD_B, "checking": {"now": now, "next": []}}},
        )
        self.assertEqual(patched["meta"]["checking"]["now"], now)
        self.assertEqual(patched["meta"]["baseHead"], HEAD_A)

    def test_lead_may_remove_and_reorder_queue_elements(self) -> None:
        self.lead(1, {"queue": [queue_item("pr-1"), queue_item("pr-2", order=2)]})
        state_tool.patch(self.directory, writer="runner", version=2, changes={"stage": "merged"},
                         selector="queue[subject=pr-1]")
        patched = self.lead(3, {"queue": [state_tool.show(self.directory)["state"]["queue"][1]]})
        self.assertEqual([item["subject"] for item in patched["queue"]], ["pr-2"])

    def test_a_long_held_queue_lock_does_not_block_a_lead_patch(self) -> None:
        held = threading.Event()
        release = threading.Event()

        def hold(name: str) -> None:
            with state_tool.file_lock(self.directory / name):
                held.set()
                release.wait(10)

        runner = threading.Thread(target=hold, args=(state_tool.QUEUE_LOCK,))
        runner.start()
        self.addCleanup(runner.join)
        self.addCleanup(release.set)
        self.assertTrue(held.wait(5))
        started = time.monotonic()
        code, patched = run_tool(
            self.directory, "patch", "--writer", "lead", "--version", "1",
            "--patch", json.dumps({"meta": {"headline": "Not blocked"}}),
        )
        self.assertEqual(code, 0, patched)
        self.assertLess(time.monotonic() - started, 5)
        release.set()
        runner.join()

        # The state lock itself is real: holding it does block a write.
        held.clear()
        release.clear()
        blocker = threading.Thread(target=hold, args=(state_tool.STATE_LOCK,))
        blocker.start()
        self.addCleanup(blocker.join)
        self.assertTrue(held.wait(5))
        original = state_tool.LOCK_TIMEOUT_SECONDS
        state_tool.LOCK_TIMEOUT_SECONDS = 0.2
        try:
            with self.assertRaises(state_tool.StateError) as refused:
                self.lead(2, {"meta": {"headline": "Blocked"}})
        finally:
            state_tool.LOCK_TIMEOUT_SECONDS = original
            release.set()
        self.assertEqual(refused.exception.code, "lock_timeout")

    def test_concurrent_journal_appends_get_every_id_once(self) -> None:
        count = 12
        processes = [
            subprocess.Popen(
                [sys.executable, "-B", str(TOOL), "--dir", str(self.directory), "journal",
                 "--kind", "decision.recorded", "--summary", f"Racer {number}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for number in range(count)
        ]
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            self.assertEqual(process.returncode, 0, stdout + stderr)
        journal = state_tool.show(self.directory)["journal"]
        self.assertEqual([entry["id"] for entry in journal], list(range(1, count + 1)))
        self.assertEqual(sorted(entry["summary"] for entry in journal),
                         sorted(f"Racer {number}" for number in range(count)))

    def test_a_long_held_queue_lock_does_not_block_a_journal_append(self) -> None:
        held = threading.Event()
        release = threading.Event()

        def hold() -> None:
            with state_tool.file_lock(self.directory / state_tool.QUEUE_LOCK):
                held.set()
                release.wait(10)

        runner = threading.Thread(target=hold)
        runner.start()
        self.addCleanup(runner.join)
        self.addCleanup(release.set)
        self.assertTrue(held.wait(5))
        original = state_tool.LOCK_TIMEOUT_SECONDS
        state_tool.LOCK_TIMEOUT_SECONDS = 0.5
        try:
            entry = state_tool.append_journal(self.directory, kind="decision.recorded", summary="Not blocked")
        finally:
            state_tool.LOCK_TIMEOUT_SECONDS = original
        self.assertEqual(entry["id"], 1)

    def test_every_write_advances_updated_at(self) -> None:
        seen = [state_tool.show(self.directory)["state"]["meta"]["updatedAt"]]
        for version in (1, 2, 3):
            seen.append(self.lead(version, {"meta": {"headline": f"Step {version}"}})["meta"]["updatedAt"])
        self.assertEqual(seen, sorted(set(seen)))
        self.assertEqual(state_tool.show(self.directory)["state"]["meta"]["updatedAt"], seen[-1])

    def test_a_failed_replace_leaves_the_state_and_no_temporary_file(self) -> None:
        before = self.state_bytes()
        with mock.patch.object(state_tool.os, "replace", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                self.lead(1, {"meta": {"headline": "Never lands"}})
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()),
                         sorted([state_tool.STATE_FILE, state_tool.JOURNAL_FILE, state_tool.STATE_LOCK,
                             state_tool.CHANGES_FILE]))

    def test_tools_write_no_bytecode_beside_the_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy_root = Path(temporary) / "orchestrate-work"
            shutil.copytree(SKILL, copy_root, ignore=shutil.ignore_patterns("__pycache__"))
            directory = Path(temporary) / "state"
            environment = {key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"}
            for args in (
                ["init", "--headline", "Bytecode"],
                ["patch", "--writer", "lead", "--version", "1", "--patch", "{}"],
                ["journal", "--kind", "decision.recorded", "--summary", "None written"],
                ["show"],
            ):
                # Deliberately without -B: the script itself must prevent bytecode.
                result = subprocess.run(
                    [sys.executable, str(copy_root / "scripts/delivery_state.py"), "--dir", str(directory), *args],
                    capture_output=True, text=True, env=environment, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(sorted(path.name for path in copy_root.rglob("*.pyc")), [])
            self.assertEqual(sorted(path.name for path in copy_root.rglob("__pycache__")), [])
        self.assertEqual(list(SCRIPTS.rglob("__pycache__")), [])


def taken(seconds_ago: float = 0) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class ReconcileTests(unittest.TestCase):
    """The record is reconciled from neutral snapshots the lead writes with its own tools."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "state"
        state_tool.init(self.directory, "Epic underway")
        self.count = 0

    def snapshot(self, source: str, items: list[dict], *, age: float = 0) -> str:
        self.count += 1
        path = self.root / f"snapshot {self.count}.json"
        path.write_text(json.dumps({"takenAt": taken(age), "source": source, "items": items}), encoding="utf-8")
        return str(path)

    def lead(self, changes: dict) -> dict:
        version = state_tool.show(self.directory)["state"]["version"]
        return state_tool.patch(self.directory, writer="lead", version=version, changes=changes)

    def bytes(self) -> tuple[bytes, bytes]:
        return (self.directory / state_tool.STATE_FILE).read_bytes(), (self.directory / state_tool.JOURNAL_FILE).read_bytes()

    def test_tracker_children_missing_from_the_record_are_added_and_a_second_run_finds_no_drift(self) -> None:
        tracker = self.snapshot("tracker", [
            {"subject": "work-later", "title": "Later work", "state": "backlog"},
            {"subject": "work-now", "title": "Work in progress", "state": "started"},
            {"subject": "work-dropped", "title": "Dropped", "state": "canceled"},
        ])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", tracker)
        self.assertEqual(code, 0, result)
        self.assertTrue(result["drift"])
        state = state_tool.show(self.directory)["state"]
        self.assertEqual(state["followups"], [{"id": "work-later", "title": "Later work", "reference": "work-later"}])
        self.assertEqual(state["planned"], [{"subject": "work-now", "title": "Work in progress", "lane": None,
                                              "stage": "building", "trackerStarted": True}])
        self.assertEqual(len(result["changes"]), 2)
        again = self.snapshot("tracker", json.loads(Path(tracker).read_text())["items"])
        version = state["version"]
        code, result = run_tool(self.directory, "reconcile", "--snapshot", again)
        self.assertEqual((code, result["drift"], result["changes"]), (0, False, []))
        self.assertEqual(result["summary"], "no drift")
        self.assertEqual(state_tool.show(self.directory)["state"]["version"], version)

    def test_a_tracker_item_may_name_its_status_and_offered_moves_and_reconcile_reads_them_for_nothing(self) -> None:
        plain = [{"subject": "work-now", "title": "Work in progress", "state": "started"}]
        named = [{**plain[0], "status": "In Progress", "offeredTransitions": ["Submit for review", "Stop work"]}]
        code, result = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot("tracker", named))
        self.assertEqual(code, 0, result)
        state = state_tool.show(self.directory)["state"]
        self.assertEqual(state["planned"], [{"subject": "work-now", "title": "Work in progress", "lane": None,
                                              "stage": "building", "trackerStarted": True}])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot("tracker", plain))
        self.assertEqual((code, result["drift"]), (0, False), "the fields' absence is drift")
        before = self.bytes()
        for name, source, item in (
                ("a forge item's status", "forge", {"subject": "work-now", "state": "open", "status": "Open"}),
                ("a forge item's offered moves", "forge", {"subject": "work-now", "state": "open", "offeredTransitions": []}),
                ("offered moves with no status", "tracker", {**plain[0], "offeredTransitions": ["Stop work"]}),
                ("an empty status", "tracker", {**plain[0], "status": ""}),
                ("a move offered twice", "tracker", {**plain[0], "status": "In Progress", "offeredTransitions": ["A", "A"]})):
            with self.subTest(name):
                code, refused = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot(source, [item]))
                self.assertEqual((code, refused["code"]), (1, "snapshot_invalid"), refused)
                self.assertEqual(self.bytes(), before)

    def test_a_forge_snapshot_s_blockers_change_nothing_the_tracker_s_are_authoritative(self) -> None:
        self.lead({"planned": [{"subject": "work-a", "title": "A", "lane": None, "stage": "planned"}]})
        plain = self.snapshot("forge", [{"subject": "work-a", "state": "open", "changeRequest": "cr-1"}])
        blocked = self.snapshot("forge", [{"subject": "work-a", "state": "open", "changeRequest": "cr-1",
                                           "blockedBy": ["work-b", "work-c"]}])
        twin = self.root / "twin"
        shutil.copytree(self.directory, twin)
        code, with_blockers = run_tool(self.directory, "reconcile", "--snapshot", blocked)
        self.assertEqual(code, 0, with_blockers)
        code, without = run_tool(twin, "reconcile", "--snapshot", plain)
        self.assertEqual(code, 0, without)
        self.assertEqual((with_blockers["changes"], with_blockers["reports"]), (without["changes"], without["reports"]))
        self.assertTrue(with_blockers["changes"], "the forge snapshot still moved the work into review")
        lead_view = ("queue", "planned", "followups")
        state, twin_state = state_tool.show(self.directory)["state"], state_tool.show(twin)["state"]
        self.assertEqual([state[key] for key in lead_view], [twin_state[key] for key in lead_view])
        self.assertNotIn("work-b", json.dumps(state), "a forge blocker never reaches the record")

    def test_reconcile_keeps_the_run_on_planned_work_and_carries_it_into_the_queue(self) -> None:
        self.lead({"planned": [
            {"subject": "work-now", "title": "Work", "lane": "lane-1", "stage": "planned", "runId": "run-now"},
            {"subject": "work-done", "title": "Done", "lane": "lane-1", "stage": "review", "runId": "run-done"},
        ]})
        tracker = self.snapshot("tracker", [{"subject": "work-now", "title": "Work", "state": "started"}])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", tracker)
        self.assertEqual(code, 0, result)
        state = state_tool.show(self.directory)["state"]
        building = next(entry for entry in state["planned"] if entry["subject"] == "work-now")
        self.assertEqual((building["stage"], building["runId"]), ("building", "run-now"))
        forge = self.snapshot("forge", [{"subject": "work-done", "state": "merged", "changeRequest": "cr-9",
                                         "head": HEAD_A}])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual(code, 0, result)
        state = state_tool.show(self.directory)["state"]
        merged = next(entry for entry in state["queue"] if entry["subject"] == "work-done")
        self.assertEqual(merged["runId"], "run-done", "work the lead missed joins the queue with its run")

    def test_planned_work_the_tracker_shows_started_moves_to_building_once(self) -> None:
        self.lead({"planned": [
            {"subject": "work-now", "title": "Work", "lane": "lane-1", "stage": "planned"},
            {"subject": "work-open", "title": "In review", "lane": "lane-2", "stage": "review", "trackerStarted": True},
        ]})
        started = [{"subject": "work-now", "title": "Work", "state": "started"},
                   {"subject": "work-open", "title": "In review", "state": "started"}]
        code, result = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot("tracker", started))
        self.assertEqual(code, 0, result)
        self.assertEqual([(change["subject"], change["change"]) for change in result["changes"]],
                         [("work-now", "started in the tracker: now building")])
        planned = {item["subject"]: item for item in state_tool.show(self.directory)["state"]["planned"]}
        self.assertEqual((planned["work-now"]["stage"], planned["work-now"]["lane"]), ("building", "lane-1"))
        self.assertEqual(planned["work-open"]["stage"], "review", "work in review stays in review")
        self.assertEqual(result["reports"], [])
        # The same snapshot again: nothing is written, and nothing is journaled.
        before = self.bytes()
        code, result = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot("tracker", started))
        self.assertEqual((code, result["drift"], result["changes"]), (0, False, []))
        self.assertEqual(self.bytes(), before)

    def test_started_work_the_record_lacks_is_added_building_and_settles_in_one_pass(self) -> None:
        tracker = [{"subject": "work-new", "title": "New", "state": "started"}]
        code, result = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot("tracker", tracker))
        self.assertEqual(code, 0, result)
        self.assertEqual([change["change"] for change in result["changes"]],
                         ["started in the tracker but not in the record: added as planned work, building"])
        self.assertEqual(state_tool.show(self.directory)["state"]["planned"],
                         [{"subject": "work-new", "title": "New", "lane": None, "stage": "building", "trackerStarted": True}])
        before = self.bytes()
        code, result = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot("tracker", tracker))
        self.assertEqual((code, result["drift"]), (0, False))
        self.assertEqual(self.bytes(), before)

    def test_work_under_way_the_tracker_shows_back_in_backlog_is_reported_not_reverted(self) -> None:
        self.lead({"planned": [
            {"subject": "work-now", "title": "Work", "lane": "lane-1", "stage": "building", "trackerStarted": True},
            {"subject": "work-open", "title": "Open", "lane": "lane-2", "stage": "review", "trackerStarted": True},
            {"subject": "work-later", "title": "Later", "lane": None, "stage": "planned"},
            # Under way by its draft change request, but its ticket was never started: not back in backlog.
            {"subject": "work-drafted", "title": "Drafted", "lane": "lane-3", "stage": "building"},
            {"subject": "work-reviewed", "title": "Reviewed", "lane": "lane-4", "stage": "review"},
            {"subject": "work-unset", "title": "Unset", "lane": "lane-5", "stage": "building", "trackerStarted": False},
        ]})
        before = self.bytes()
        code, result = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot("tracker", [
            {"subject": "work-now", "title": "Work", "state": "backlog"},
            {"subject": "work-open", "title": "Open", "state": "backlog"},
            {"subject": "work-later", "title": "Later", "state": "backlog"},
            {"subject": "work-drafted", "title": "Drafted", "state": "backlog"},
            {"subject": "work-reviewed", "title": "Reviewed", "state": "backlog"},
            {"subject": "work-unset", "title": "Unset", "state": "backlog"},
        ]))
        self.assertEqual((code, result["drift"], result["changes"]), (0, False, []))
        self.assertEqual(result["reports"], [
            {"subject": "work-now", "note": "building in the record but back in the tracker's backlog: check it"},
            {"subject": "work-open", "note": "in review in the record but back in the tracker's backlog: check it"},
        ])
        self.assertEqual(self.bytes(), before)

    def test_a_ticket_never_started_is_not_reported_back_in_backlog_until_it_was_started(self) -> None:
        """Never started: a draft moves work to building while its ticket is still in backlog, and no report follows;
        once the ticket was started, its return to backlog is reported."""
        self.lead({"planned": [{"subject": "work-x", "title": "X", "lane": "lane-1", "stage": "planned"}]})
        backlog = self.snapshot("tracker", [{"subject": "work-x", "title": "X", "state": "backlog"}])
        draft = self.snapshot("forge", [{"subject": "work-x", "state": "draft", "changeRequest": "cr-1"}])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", backlog, "--snapshot", draft)
        self.assertEqual(code, 0, result)
        self.assertIn("its change request is a draft: now building", [entry["change"] for entry in result["changes"]])
        self.assertEqual(result["reports"], [])
        entry = state_tool.show(self.directory)["state"]["planned"][0]
        self.assertEqual(entry["stage"], "building")
        self.assertNotIn("trackerStarted", entry)
        before = self.bytes()
        code, result = run_tool(self.directory, "reconcile", "--snapshot", backlog, "--snapshot", draft)
        self.assertEqual((code, result["drift"], result["changes"], result["reports"]), (0, False, [], []))
        self.assertEqual(self.bytes(), before)
        # The lane starts its ticket: the record notes it once.
        started = self.snapshot("tracker", [{"subject": "work-x", "title": "X", "state": "started"}])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", started, "--snapshot", draft)
        self.assertEqual([entry["change"] for entry in result["changes"]], ["started in the tracker: recorded as started"])
        self.assertTrue(state_tool.show(self.directory)["state"]["planned"][0]["trackerStarted"])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", started, "--snapshot", draft)
        self.assertEqual((result["drift"], result["changes"]), (False, []))
        # Back in backlog after it was started: reported, every pass, and the record is left as it is.
        for _ in range(2):
            before = self.bytes()
            code, result = run_tool(self.directory, "reconcile", "--snapshot", backlog, "--snapshot", draft)
            self.assertEqual((code, result["drift"], result["changes"]), (0, False, []))
            self.assertEqual(result["reports"], [
                {"subject": "work-x", "note": "building in the record but back in the tracker's backlog: check it"}])
            self.assertEqual(self.bytes(), before)

    def test_a_ticket_seen_started_keeps_its_flag_through_a_rejected_hand_off(self) -> None:
        """The flag rides the queue element and comes back with the rejection, so the return to backlog is still
        reported and a ticket still started writes no second "recorded as started"."""
        self.lead({"meta": {"handoffsNeedAcceptance": True}, "lanes": [lane("lane-1")],
                   "planned": [{"subject": "work-x", "title": "X", "lane": "lane-1", "stage": "planned"},
                               {"subject": "work-y", "title": "Y", "lane": "lane-1", "stage": "building"}]})
        started = self.snapshot("tracker", [{"subject": "work-x", "title": "X", "state": "started"}])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", started)
        self.assertEqual([entry["change"] for entry in result["changes"]], ["started in the tracker: now building"])
        state_tool.handoff(self.directory, lane="lane-1", subject="work-x", head=HEAD_A, change_request="cr-x")
        state_tool.handoff(self.directory, lane="lane-1", subject="work-y", head=HEAD_B, change_request="cr-y")
        queue = {entry["subject"]: entry for entry in state_tool.show(self.directory)["state"]["queue"]}
        self.assertIs(queue["work-x"]["trackerStarted"], True)
        self.assertNotIn("trackerStarted", queue["work-y"], "a ticket never seen started gains no flag")
        back = state_tool.reject_handoff(self.directory, "work-x", "the review missed a case")
        never = state_tool.reject_handoff(self.directory, "work-y", "the review missed a case")
        self.assertIs(back["trackerStarted"], True)
        self.assertNotIn("trackerStarted", never)
        planned_now = {entry["subject"]: entry for entry in state_tool.show(self.directory)["state"]["planned"]}
        self.assertIs(planned_now["work-x"]["trackerStarted"], True)
        self.assertNotIn("trackerStarted", planned_now["work-y"])
        # Still started: nothing to record, so the pass writes nothing.
        before = self.bytes()
        code, result = run_tool(self.directory, "reconcile", "--snapshot", started)
        self.assertEqual((code, result["drift"], result["changes"], result["reports"]), (0, False, [], []), result)
        self.assertEqual(self.bytes(), before)
        # Back in backlog before reconcile sees it started again: reported, and the record is left as it is.
        backlog = self.snapshot("tracker", [{"subject": "work-x", "title": "X", "state": "backlog"},
                                            {"subject": "work-y", "title": "Y", "state": "backlog"}])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", backlog)
        self.assertEqual((code, result["drift"], result["changes"]), (0, False, []), result)
        self.assertEqual(result["reports"], [
            {"subject": "work-x", "note": "in review in the record but back in the tracker's backlog: check it"}])
        self.assertEqual(self.bytes(), before)

    def test_planned_work_found_merged_takes_its_started_flag_into_the_queue(self) -> None:
        self.lead({"meta": {"handoffsNeedAcceptance": True}, "lanes": [lane("lane-1")],
                   "planned": [{"subject": "work-m", "title": "M", "lane": "lane-1", "stage": "review",
                                "trackerStarted": True, "changeRequest": "cr-m"},
                               {"subject": "work-n", "title": "N", "lane": "lane-1", "stage": "review",
                                "changeRequest": "cr-n"}]})
        forge = self.snapshot("forge", [{"subject": "work-m", "state": "merged", "changeRequest": "cr-m", "head": HEAD_A},
                                        {"subject": "work-n", "state": "merged", "changeRequest": "cr-n", "head": HEAD_B}])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual(code, 0, result)
        queue = {entry["subject"]: entry for entry in state_tool.show(self.directory)["state"]["queue"]}
        self.assertIs(queue["work-m"]["trackerStarted"], True)
        self.assertNotIn("trackerStarted", queue["work-n"])

    def test_each_stage_against_each_tracker_state_settles_in_one_pass(self) -> None:
        # (stage, ticket seen started before, tracker state) -> (stage after, change, report); a second pass writes
        # nothing. A ticket counts as started once reconcile has seen it started, and stays so.
        noted = "started in the tracker: recorded as started"
        back = "in the record but back in the tracker's backlog: check it"
        expected = {
            ("planned", False, "backlog"): ("planned", None, None),
            ("planned", False, "started"): ("building", "started in the tracker: now building", None),
            ("planned", False, "completed"): ("planned", None, "completed in the tracker but still planned in the record: check the forge"),
            ("planned", False, "canceled"): ("planned", None, "canceled in the tracker but still planned in the record"),
            ("planned", True, "backlog"): ("planned", None, None),
            ("planned", True, "started"): ("building", "started in the tracker: now building", None),
            ("building", False, "backlog"): ("building", None, None),
            ("building", False, "started"): ("building", noted, None),
            ("building", False, "completed"): ("building", None, "completed in the tracker but still building in the record: check the forge"),
            ("building", False, "canceled"): ("building", None, "canceled in the tracker but still building in the record"),
            ("building", True, "backlog"): ("building", None, "building " + back),
            ("building", True, "started"): ("building", None, None),
            ("review", False, "backlog"): ("review", None, None),
            ("review", False, "started"): ("review", noted, None),
            ("review", False, "completed"): ("review", None, "completed in the tracker but still in review in the record: check the forge"),
            ("review", False, "canceled"): ("review", None, "canceled in the tracker but still in review in the record"),
            ("review", True, "backlog"): ("review", None, "in review " + back),
            ("review", True, "started"): ("review", None, None),
        }
        for (stage, started, word), (after, change, note) in expected.items():
            with self.subTest(stage=stage, started=started, tracker=word):
                self.lead({"planned": [{"subject": "work-x", "title": "X", "lane": "lane-1", "stage": stage,
                                        **({"trackerStarted": True} if started else {})}]})
                tracker = [{"subject": "work-x", "title": "X", "state": word}]
                code, result = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot("tracker", tracker))
                self.assertEqual(code, 0, result)
                self.assertEqual([entry["change"] for entry in result["changes"]], [change] if change else [])
                self.assertEqual([entry["note"] for entry in result["reports"]], [note] if note else [])
                entry = state_tool.show(self.directory)["state"]["planned"][0]
                self.assertEqual(entry["stage"], after)
                self.assertEqual(entry.get("trackerStarted", False), started or word == "started")
                before = self.bytes()
                code, again = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot("tracker", tracker))
                self.assertEqual((code, again["drift"], again["changes"]), (0, False, []))
                self.assertEqual(self.bytes(), before)

    def test_work_already_moved_adds_no_change_beside_one_that_moves(self) -> None:
        # One item really moves, so the pass writes; the others must add nothing to its changes.
        self.lead({"planned": [
            {"subject": "work-a", "title": "A", "lane": "lane-1", "stage": "planned"},
            {"subject": "work-b", "title": "B", "lane": "lane-2", "stage": "building", "trackerStarted": True},
            {"subject": "work-c", "title": "C", "lane": "lane-3", "stage": "review", "changeRequest": "cr 3",
             "trackerStarted": True},
        ]})
        code, result = run_tool(self.directory, "reconcile",
                                "--snapshot", self.snapshot("tracker", [
                                    {"subject": "work-a", "title": "A", "state": "started"},
                                    {"subject": "work-b", "title": "B", "state": "started"},
                                    {"subject": "work-c", "title": "C", "state": "started"}]),
                                "--snapshot", self.snapshot("forge", [
                                    {"subject": "work-c", "state": "open", "changeRequest": "cr 3"}]))
        self.assertEqual(code, 0, result)
        self.assertEqual([(change["subject"], change["change"]) for change in result["changes"]],
                         [("work-a", "started in the tracker: now building")])

    def test_an_open_change_request_leaves_work_in_review_as_it_is(self) -> None:
        self.lead({"planned": [{"subject": "work-now", "title": "Work", "lane": "lane-1", "stage": "review",
                                "changeRequest": "cr 5"}]})
        before = self.bytes()
        code, result = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot(
            "forge", [{"subject": "work-now", "state": "open", "changeRequest": "cr 5"}]))
        self.assertEqual((code, result["drift"], result["changes"]), (0, False, []))
        self.assertEqual(self.bytes(), before)

    def test_a_follow_up_the_tracker_shows_started_is_reported_for_a_lane(self) -> None:
        self.lead({"followups": [{"id": "work-f", "title": "Later", "reference": "work-f"}]})
        before = self.bytes()
        code, result = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot(
            "tracker", [{"subject": "work-f", "title": "Later", "state": "started"}]))
        self.assertEqual((code, result["changes"]), (0, []))
        self.assertEqual(result["reports"], [{"subject": "work-f",
                                               "note": "a follow-up the tracker now shows started: assign it a lane"}])
        self.assertEqual(self.bytes(), before)

    def test_building_work_the_tracker_still_shows_started_is_left_as_it_is(self) -> None:
        self.lead({"planned": [{"subject": "work-now", "title": "Work", "lane": "lane-1", "stage": "building",
                                "trackerStarted": True}]})
        before = self.bytes()
        code, result = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot(
            "tracker", [{"subject": "work-now", "title": "Work", "state": "started"}]))
        self.assertEqual((code, result["drift"], result["changes"], result["reports"]), (0, False, [], []))
        self.assertEqual(self.bytes(), before)

    def test_started_with_an_open_change_request_settles_in_review_in_one_pass(self) -> None:
        self.lead({"planned": [{"subject": "work-now", "title": "Work", "lane": "lane-1", "stage": "planned"}]})
        snapshots = lambda: ["--snapshot", self.snapshot("forge", [{"subject": "work-now", "state": "open", "changeRequest": "cr 5"}]),
                             "--snapshot", self.snapshot("tracker", [{"subject": "work-now", "title": "Work", "state": "started"}])]
        code, result = run_tool(self.directory, "reconcile", *snapshots())
        self.assertEqual(code, 0, result)
        self.assertEqual(state_tool.show(self.directory)["state"]["planned"][0]["stage"], "review")
        before = self.bytes()
        code, result = run_tool(self.directory, "reconcile", *snapshots())
        self.assertEqual((code, result["drift"]), (0, False))
        self.assertEqual(self.bytes(), before)

    def test_an_open_change_request_moves_building_work_to_review(self) -> None:
        self.lead({"planned": [{"subject": "work-now", "title": "Work", "lane": "lane-1", "stage": "building"}]})
        code, result = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot(
            "forge", [{"subject": "work-now", "state": "open", "changeRequest": "cr 5"}]))
        self.assertEqual(code, 0, result)
        self.assertEqual(state_tool.show(self.directory)["state"]["planned"][0]["stage"], "review")

    def test_tracker_completion_the_record_has_not_seen_is_reported_not_written(self) -> None:
        self.lead({"planned": [{"subject": "work-now", "title": "Work", "lane": None, "stage": "planned"}]})
        before = self.bytes()
        tracker = self.snapshot("tracker", [
            {"subject": "work-now", "title": "Work", "state": "completed"},
            {"subject": "work-unknown", "title": "Never recorded", "state": "completed"},
        ])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", tracker)
        self.assertEqual((code, result["changes"]), (0, []))
        self.assertEqual(sorted(report["subject"] for report in result["reports"]), ["work-now", "work-unknown"])
        self.assertEqual(self.bytes(), before)

    def test_tracker_titles_and_summaries_are_kept_and_written_only_when_they_change(self) -> None:
        def tracker(*items: dict) -> str:
            return self.snapshot("tracker", list(items))

        later = {"subject": "work-later", "title": "Later work", "state": "backlog",
                 "summary": "Shows each ticket's summary on its card."}
        now = {"subject": "work-now", "title": "Work in progress", "state": "started"}
        code, result = run_tool(self.directory, "reconcile", "--snapshot", tracker(later, now))
        self.assertEqual(code, 0, result)
        self.assertEqual(sorted(change["subject"] for change in result["changes"]), ["work-later", "work-now"],
                         "work added with its ticket is one change")
        state = state_tool.show(self.directory)["state"]
        # Only what adds to the record: a title the record already holds, with no summary, is not repeated.
        self.assertEqual(state["tickets"], {
            "work-later": {"title": "Later work", "summary": "Shows each ticket's summary on its card."},
        })
        # The same snapshot again: nothing is written.
        version = state["version"]
        code, result = run_tool(self.directory, "reconcile", "--snapshot", tracker(later, now))
        self.assertEqual((code, result["drift"]), (0, False))
        self.assertEqual(state_tool.show(self.directory)["state"]["version"], version)
        # An edited summary, a new one and a retitled ticket are written, each reported once.
        edited = {**later, "summary": "Shows the tracker's summary, kept current."}
        renamed = {**now, "title": "Work, renamed", "summary": "Now with a summary."}
        code, result = run_tool(self.directory, "reconcile", "--snapshot", tracker(edited, renamed))
        self.assertEqual((code, result["drift"]), (0, True), result)
        self.assertEqual(sorted(change["subject"] for change in result["changes"]), ["work-later", "work-now"])
        self.assertEqual(state_tool.show(self.directory)["state"]["tickets"], {
            "work-later": {"title": "Later work", "summary": "Shows the tracker's summary, kept current."},
            "work-now": {"title": "Work, renamed", "summary": "Now with a summary."},
        })
        # A summary the tracker no longer gives is dropped; a ticket it no longer lists is kept.
        code, result = run_tool(self.directory, "reconcile", "--snapshot",
                                tracker({key: value for key, value in edited.items() if key != "summary"}))
        self.assertEqual((code, result["drift"]), (0, True), result)
        self.assertEqual(state_tool.show(self.directory)["state"]["tickets"], {
            "work-now": {"title": "Work, renamed", "summary": "Now with a summary."},
        })
        code, result = run_tool(self.directory, "reconcile", "--snapshot",
                                tracker({key: value for key, value in renamed.items() if key != "summary"}))
        self.assertEqual((code, result["drift"]), (0, True), result)
        self.assertEqual(state_tool.show(self.directory)["state"]["tickets"], {"work-now": {"title": "Work, renamed"}})
        # Work the record does not hold gets no ticket.
        code, result = run_tool(self.directory, "reconcile", "--snapshot", tracker(
            {"subject": "work-gone", "title": "Dropped", "state": "canceled", "summary": "Never shown."}))
        self.assertEqual((code, result["drift"]), (0, False), result)

    def test_a_ticket_listed_without_a_title_keeps_the_record_s_own(self) -> None:
        self.lead({"planned": [{"subject": "work-now", "title": "Work", "lane": None, "stage": "building",
                                "trackerStarted": True}]})
        before = self.bytes()
        code, result = run_tool(self.directory, "reconcile", "--snapshot",
                                self.snapshot("tracker", [{"subject": "work-now", "state": "started"}]))
        self.assertEqual((code, result["drift"]), (0, False), result)
        self.assertEqual(self.bytes(), before)
        code, result = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot(
            "tracker", [{"subject": "work-now", "state": "started", "summary": "What it does."}]))
        self.assertEqual(code, 0, result)
        self.assertEqual(state_tool.show(self.directory)["state"]["tickets"],
                         {"work-now": {"title": "Work", "summary": "What it does."}})

    def test_forge_titles_and_summaries_are_not_the_tracker_s(self) -> None:
        self.lead({"planned": [{"subject": "work-now", "title": "Work", "lane": None, "stage": "planned"}]})
        forge = self.snapshot("forge", [{"subject": "work-now", "title": "A change request title", "state": "draft",
                                         "summary": "The change request's own text."}])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual(code, 0, result)
        self.assertEqual([change["change"] for change in result["changes"]],
                         ["its change request is a draft: now building"])
        self.assertNotIn("tickets", state_tool.show(self.directory)["state"])

    def test_an_over_long_summary_is_refused_and_nothing_is_written(self) -> None:
        limit = state_tool.load_schema("work-snapshot/1")["properties"]["items"]["items"]["properties"]["summary"]["maxLength"]
        before = self.bytes()
        longest = self.snapshot("tracker", [{"subject": "x", "title": "X", "state": "backlog", "summary": "s" * limit}])
        over = self.snapshot("tracker", [{"subject": "x", "title": "X", "state": "backlog", "summary": "s" * (limit + 1)}])
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", over)
        self.assertEqual((code, refused["code"]), (1, "snapshot_invalid"))
        self.assertIn("summary", refused["error"])
        self.assertEqual(self.bytes(), before)
        code, result = run_tool(self.directory, "reconcile", "--snapshot", longest)
        self.assertEqual(code, 0, result)
        self.assertEqual(len(state_tool.show(self.directory)["state"]["tickets"]["x"]["summary"]), limit)
        # Counted in characters, not in UTF-16 units.
        wide = self.snapshot("tracker", [{"subject": "x", "title": "X", "state": "backlog",
                                          "summary": "\U0001F600" * limit}])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", wide)
        self.assertEqual(code, 0, result)
        wider = self.snapshot("tracker", [{"subject": "x", "title": "X", "state": "backlog",
                                           "summary": "\U0001F600" * (limit + 1)}])
        self.assertEqual(run_tool(self.directory, "reconcile", "--snapshot", wider)[1]["code"], "snapshot_invalid")

    def test_a_blank_summary_is_none_and_text_that_cannot_be_stored_is_refused_up_front(self) -> None:
        self.lead({"planned": [{"subject": "work-now", "title": "Work", "lane": None, "stage": "building",
                                "trackerStarted": True}]})
        before = self.bytes()
        code, result = run_tool(self.directory, "reconcile", "--snapshot", self.snapshot(
            "tracker", [{"subject": "work-now", "title": "Work", "state": "started", "summary": "  \n "}]))
        self.assertEqual((code, result["drift"]), (0, False), result)
        self.assertEqual(self.bytes(), before)
        path = self.root / "lone surrogate.json"
        path.write_text('{"takenAt": "%s", "source": "tracker", "items": [{"subject": "work-now", "title": "Work", '
                        '"state": "started", "summary": "\\ud800"}]}' % taken(), encoding="utf-8")
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", str(path))
        self.assertEqual((code, refused["code"]), (1, "snapshot_invalid"))
        self.assertEqual(self.bytes(), before)

    def test_a_follow_up_id_that_names_queued_work_keeps_the_queue_s_title(self) -> None:
        self.lead({"queue": [queue_item("work-a")],
                   "followups": [{"id": "work-a", "title": "A follow-up's title", "reference": "work-f"}]})
        before = self.bytes()
        own = state_tool.show(self.directory)["state"]["queue"][0]["title"]
        code, result = run_tool(self.directory, "reconcile", "--snapshot",
                                self.snapshot("tracker", [{"subject": "work-a", "title": own, "state": "started"}]))
        self.assertEqual((code, result["drift"]), (0, False), result)
        self.assertEqual(self.bytes(), before)

    def test_a_stale_or_malformed_snapshot_is_refused_and_nothing_is_written(self) -> None:
        before = self.bytes()
        stale = self.snapshot("tracker", [{"subject": "x", "title": "X", "state": "started"}], age=901)
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", stale)
        self.assertEqual((code, refused["code"]), (1, "snapshot_stale"))
        fresh = self.snapshot("tracker", [{"subject": "x", "title": "X", "state": "started"}])
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", fresh, "--snapshot", stale)
        self.assertEqual((code, refused["code"]), (1, "snapshot_stale"), "one stale snapshot refuses them all")
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", stale, "--max-age", "1000")
        self.assertEqual(code, 0, refused)
        self.assertEqual(state_tool.show(self.directory)["state"]["planned"][0]["subject"], "x")
        before = self.bytes()
        for name, document in {
            "wrong state word": {"takenAt": taken(), "source": "tracker", "items": [{"subject": "y", "state": "open"}]},
            "unknown source": {"takenAt": taken(), "source": "chat", "items": []},
            "no items": {"takenAt": taken(), "source": "forge"},
        }.items():
            with self.subTest(name):
                path = self.root / f"{name}.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                code, refused = run_tool(self.directory, "reconcile", "--snapshot", str(path))
                self.assertEqual((code, refused["code"]), (1, "snapshot_invalid"))
                self.assertEqual(self.bytes(), before)
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", str(self.root / "missing.json"))
        self.assertEqual((code, refused["code"]), (1, "snapshot_invalid"))
        twice = self.root / "twice.json"
        twice.write_text(json.dumps({"takenAt": taken(), "source": "forge", "items": [
            {"subject": "y", "state": "open"}, {"subject": "y", "state": "merged"}]}), encoding="utf-8")
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", str(twice))
        self.assertEqual((code, refused["code"]), (1, "snapshot_invalid"), "a subject appears once in a snapshot")
        self.assertIn("each subject appears once", refused["error"])
        first = self.snapshot("forge", [{"subject": "y", "state": "open", "changeRequest": "cr1"}])
        second = self.snapshot("forge", [{"subject": "y", "state": "merged", "changeRequest": "cr2"}])
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", first, "--snapshot", second)
        self.assertEqual((code, refused["code"]), (1, "snapshot_invalid"), "two forge views of one subject conflict")
        self.assertEqual(self.bytes(), before)
        ahead = self.snapshot("tracker", [{"subject": "z", "title": "Z", "state": "started"}], age=-120)
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", ahead)
        self.assertEqual((code, refused["code"]), (1, "snapshot_invalid"), "a snapshot from the future is refused")
        self.assertEqual(self.bytes(), before)
        skewed = self.snapshot("tracker", [{"subject": "z", "title": "Z", "state": "started"}], age=-30)
        code, result = run_tool(self.directory, "reconcile", "--snapshot", skewed)
        self.assertEqual(code, 0, "a clock a little ahead is tolerated")

    def test_an_open_change_request_links_planned_work_and_moves_it_to_review(self) -> None:
        self.lead({"planned": [
            {"subject": "work-a", "title": "A", "lane": "lane-1", "stage": "planned"},
            {"subject": "work-b", "title": "B", "lane": None, "stage": "planned"},
        ], "queue": [queue_item("work-c")]})
        forge = self.snapshot("forge", [
            {"subject": "work-a", "state": "open", "changeRequest": "change 11", "head": HEAD_A},
            {"subject": "work-b", "state": "draft", "changeRequest": "change 12"},
            {"subject": "work-c", "state": "open", "changeRequest": "change 13"},
            {"subject": "work-unrecorded", "state": "open", "changeRequest": "change 14"},
        ])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual(code, 0, result)
        state = state_tool.show(self.directory)["state"]
        self.assertEqual(state["planned"], [
            {"subject": "work-a", "title": "A", "lane": "lane-1", "stage": "review", "changeRequest": "change 11"},
            {"subject": "work-b", "title": "B", "lane": None, "stage": "building", "changeRequest": "change 12"},
        ])
        self.assertEqual(state["queue"][0]["changeRequest"], "change 13")
        self.assertEqual(state["queue"][0]["stage"], "queued", "the lead's reconcile never writes runner fields")
        self.assertEqual([report["subject"] for report in result["reports"]], ["work-unrecorded"])
        again = self.snapshot("forge", json.loads(Path(forge).read_text())["items"])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", again)
        self.assertEqual((code, result["drift"]), (0, False))

    def test_a_draft_change_request_moves_planned_work_to_building_and_nothing_further(self) -> None:
        self.lead({"planned": [
            {"subject": "work-a", "title": "A", "lane": "lane-1", "stage": "planned"},
            {"subject": "work-b", "title": "B", "lane": "lane-2", "stage": "building"},
            {"subject": "work-c", "title": "C", "lane": "lane-3", "stage": "review", "changeRequest": "change 23"},
        ], "queue": [queue_item("work-d")]})
        forge = self.snapshot("forge", [
            {"subject": "work-a", "state": "draft", "changeRequest": "change 21"},
            {"subject": "work-b", "state": "draft", "changeRequest": "change 22"},
            {"subject": "work-c", "state": "draft", "changeRequest": "change 23"},
            {"subject": "work-d", "state": "draft", "changeRequest": "change 24"},
        ])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual((code, result["reports"]), (0, []), result)
        self.assertEqual([(change["subject"], change["change"]) for change in result["changes"]], [
            ("work-a", "linked change request change 21"),
            ("work-a", "its change request is a draft: now building"),
            ("work-b", "linked change request change 22"),
            ("work-d", "linked change request change 24"),
        ])
        state = state_tool.show(self.directory)["state"]
        self.assertEqual(state["planned"], [
            {"subject": "work-a", "title": "A", "lane": "lane-1", "stage": "building", "changeRequest": "change 21"},
            {"subject": "work-b", "title": "B", "lane": "lane-2", "stage": "building", "changeRequest": "change 22"},
            {"subject": "work-c", "title": "C", "lane": "lane-3", "stage": "review", "changeRequest": "change 23"},
        ])
        self.assertEqual(state["queue"][0]["stage"], "queued", "queued work stays queued")
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual((code, result["drift"], result["changes"]), (0, False, []))

    def test_a_draft_then_open_change_request_moves_planned_work_through_building_to_review(self) -> None:
        self.lead({"planned": [{"subject": "work-a", "title": "A", "lane": "lane-1", "stage": "planned"}]})
        for word, stage in (("draft", "building"), ("open", "review")):
            with self.subTest(word=word):
                forge = self.snapshot("forge", [{"subject": "work-a", "state": word, "changeRequest": "change 21"}])
                code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
                self.assertEqual((code, result["drift"]), (0, True), result)
                self.assertEqual(state_tool.show(self.directory)["state"]["planned"][0]["stage"], stage)
                code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
                self.assertEqual((code, result["drift"]), (0, False))

    def test_a_snapshot_exactly_the_maximum_age_old_is_fresh_and_one_second_more_is_stale(self) -> None:
        moment = datetime.datetime(2026, 10, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
        path = self.root / "boundary.json"
        path.write_text(json.dumps({"takenAt": "2026-10-20T12:00:00Z", "source": "tracker", "items": []}),
                        encoding="utf-8")
        for elapsed, fresh in ((state_tool.SNAPSHOT_MAX_AGE_SECONDS, True), (state_tool.SNAPSHOT_MAX_AGE_SECONDS + 1, False)):
            later = moment + datetime.timedelta(seconds=elapsed)

            class Frozen(datetime.datetime):
                @classmethod
                def now(cls, tz=None):  # noqa: ANN001, ANN206 - the clock the tool reads
                    return later

            with self.subTest(elapsed=elapsed), mock.patch.object(state_tool.datetime, "datetime", Frozen):
                if fresh:
                    self.assertEqual(state_tool.read_snapshot(path, state_tool.SNAPSHOT_MAX_AGE_SECONDS)["items"], [])
                else:
                    with self.assertRaises(state_tool.StateError) as refused:
                        state_tool.read_snapshot(path, state_tool.SNAPSHOT_MAX_AGE_SECONDS)
                    self.assertEqual(refused.exception.code, "snapshot_stale")

    def test_one_pass_reaches_the_fixed_point_whatever_order_the_snapshots_come_in(self) -> None:
        tracker = [{"subject": "work-new", "title": "New", "state": "started"}]
        forge = [{"subject": "work-new", "state": "open", "changeRequest": "change 31"}]
        for order in (("forge", "tracker"), ("tracker", "forge")):
            with self.subTest(order=order):
                directory = self.root / f"state {'-'.join(order)}"
                state_tool.init(directory, "Epic underway")
                items = {"tracker": tracker, "forge": forge}
                paths = [self.snapshot(source, items[source]) for source in order]
                arguments = [argument for path in paths for argument in ("--snapshot", path)]
                code, result = run_tool(directory, "reconcile", *arguments)
                self.assertEqual((code, result["reports"]), (0, []), result)
                self.assertEqual(state_tool.show(directory)["state"]["planned"], [
                    {"subject": "work-new", "title": "New", "lane": None, "stage": "review",
                     "trackerStarted": True, "changeRequest": "change 31"}])
                code, result = run_tool(directory, "reconcile", *arguments)
                self.assertEqual((code, result["drift"]), (0, False))

    def test_no_merge_is_recovered_while_the_runner_holds_the_queue(self) -> None:
        self.lead({"queue": [queue_item("work-m", changeRequest="change 41")]})
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="runner", version=version, selector="queue[subject=work-m]",
                         changes={"stage": "merging"})
        forge = self.snapshot("forge", [{"subject": "work-m", "state": "merged", "changeRequest": "change 41",
                                         "head": HEAD_B}])
        before = self.bytes()
        # The runner has merged on the forge and not yet recorded it.
        with state_tool.file_lock(self.directory / state_tool.QUEUE_LOCK):
            code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual((code, result["drift"], result["changes"]), (0, False, []), result)
        self.assertEqual([(report["subject"], "runner is running" in report["note"]) for report in result["reports"]],
                         [("work-m", True)])
        self.assertEqual(self.bytes(), before)
        # Once the runner has gone without recording it, the next pass recovers it.
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual((code, [change["recovery"] for change in result["changes"]]), (0, [True]), result)
        self.assertEqual(state_tool.show(self.directory)["state"]["queue"][0]["stage"], "merged")

    def test_a_merge_recovered_from_the_forge_ends_the_wait_for_the_lead(self) -> None:
        self.lead({"queue": [queue_item("work-m", changeRequest="change 41", pending=True)]})
        forge = self.snapshot("forge", [{"subject": "work-m", "state": "merged", "changeRequest": "change 41",
                                         "head": HEAD_B}])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual((code, [change["recovery"] for change in result["changes"]]), (0, [True]), result)
        item = state_tool.show(self.directory)["state"]["queue"][0]
        self.assertEqual((item["stage"], item["syncedHead"]), ("merged", HEAD_B))
        self.assertNotIn("pending", item)
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual((code, result["drift"]), (0, False), result)

    def test_a_merge_recovered_for_an_item_already_at_merged_ends_the_wait_too(self) -> None:
        self.lead({"lanes": [lane("lane-1")], "queue": [queue_item("work-m", changeRequest="change 41", pending=True)]})
        version = state_tool.show(self.directory)["state"]["version"]
        # The runner wrote the stage and went before it journaled the merge.
        state_tool.patch(self.directory, writer="runner", version=version, selector="queue[subject=work-m]",
                         changes={"stage": "merged"})
        rows = state_tool.show(self.directory)["state"]["lanes"]
        forge = self.snapshot("forge", [{"subject": "work-m", "state": "merged", "changeRequest": "change 41",
                                         "head": HEAD_A}])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual((code, [change["recovery"] for change in result["changes"]]), (0, [True]), result)
        state = state_tool.show(self.directory)["state"]
        self.assertEqual(state["queue"][0]["stage"], "merged")
        self.assertNotIn("pending", state["queue"][0])
        self.assertEqual(state["lanes"], rows, "the stage did not move, so the row keeps its word")
        self.assertEqual([report["subject"] for report in result["reports"]], ["work-m"])
        self.assertEqual([entry["subject"] for entry in state_tool.read_journal(self.directory)
                          if entry["kind"] == "candidate.merged"], ["work-m"])

    def test_a_merge_that_skipped_the_lead_says_so_and_one_that_did_not_stays_silent(self) -> None:
        self.lead({"queue": [queue_item("work-m", changeRequest="change 41", pending=True),
                             queue_item("work-n", order=2, changeRequest="change 42")]})
        forge = self.snapshot("forge", [{"subject": subject, "state": "merged", "changeRequest": change, "head": HEAD_B}
                                        for subject, change in (("work-m", "change 41"), ("work-n", "change 42"))])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual(code, 0, result)
        self.assertEqual([report["subject"] for report in result["reports"]], ["work-m"])
        self.assertIn("before the lead accepted", result["reports"][0]["note"])
        summaries = {entry["subject"]: entry["summary"] for entry in state_tool.read_journal(self.directory)
                     if entry["kind"] == "candidate.merged"}
        self.assertIn("before the lead accepted", summaries["work-m"])
        self.assertNotIn("before the lead accepted", summaries["work-n"])

    def test_planned_work_merged_with_no_hand_off_skipped_the_lead_only_under_its_policy(self) -> None:
        for policy, reported in ((True, ["work-p"]), (False, [])):
            with self.subTest(policy=policy):
                directory = self.root / f"policy {policy}"
                state_tool.init(directory, "Epic underway")
                state_tool.patch(directory, writer="lead", version=state_tool.show(directory)["state"]["version"],
                                 changes={"meta": {"handoffsNeedAcceptance": policy},
                                          "planned": [{"subject": "work-p", "title": "P", "lane": "lane-1", "stage": "review"}]})
                forge = self.snapshot("forge", [{"subject": "work-p", "state": "merged", "head": HEAD_B}])
                code, result = run_tool(directory, "reconcile", "--snapshot", forge)
                self.assertEqual(code, 0, result)
                self.assertEqual([report["subject"] for report in result["reports"]], reported)
                summary = next(entry["summary"] for entry in state_tool.read_journal(directory)
                               if entry["kind"] == "candidate.merged")
                self.assertEqual("before the lead accepted" in summary, policy)

    def test_an_accepted_merge_under_the_policy_stays_silent(self) -> None:
        self.lead({"meta": {"handoffsNeedAcceptance": True}, "queue": [queue_item("work-a", changeRequest="change 43")]})
        forge = self.snapshot("forge", [{"subject": "work-a", "state": "merged", "changeRequest": "change 43", "head": HEAD_B}])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual((code, result["reports"]), (0, []), result)
        summary = next(entry["summary"] for entry in state_tool.read_journal(self.directory)
                       if entry["kind"] == "candidate.merged")
        self.assertNotIn("before the lead accepted", summary)

    def test_planned_work_keeps_its_skipped_acceptance_across_a_recovery_put_off_by_the_runner(self) -> None:
        self.lead({"meta": {"handoffsNeedAcceptance": True},
                   "planned": [{"subject": "work-p", "title": "P", "lane": "lane-1", "stage": "review"}]})
        forge = self.snapshot("forge", [{"subject": "work-p", "state": "merged", "head": HEAD_B}])
        with state_tool.file_lock(self.directory / state_tool.QUEUE_LOCK):
            code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual(code, 0, result)
        item = state_tool.show(self.directory)["state"]["queue"][0]
        self.assertEqual((item["stage"], item["pending"]), ("queued", True), "the queue must not take it meanwhile")
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual((code, [report["subject"] for report in result["reports"]]), (0, ["work-p"]), result)
        self.assertNotIn("pending", state_tool.show(self.directory)["state"]["queue"][0])
        self.assertIn("before the lead accepted", next(entry["summary"] for entry in state_tool.read_journal(self.directory)
                                                       if entry["kind"] == "candidate.merged"))

    def leftover(self) -> str:
        """A merged, journaled item carrying the flag, as an earlier version left it; returns its forge snapshot."""
        self.lead({"queue": [queue_item("work-m", changeRequest="change 41"), queue_item("work-w", order=2, pending=True),
                             queue_item("work-q", order=3)]})
        forge = self.snapshot("forge", [{"subject": "work-m", "state": "merged", "changeRequest": "change 41",
                                         "head": HEAD_B}])
        self.assertEqual(run_tool(self.directory, "reconcile", "--snapshot", forge)[0], 0)
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version, selector="queue[subject=work-m]",
                         changes={"pending": True})
        return forge

    def test_a_merged_item_that_already_carries_the_wait_is_cleared_by_the_next_forge_reconcile(self) -> None:
        forge = self.leftover()
        entries = len(state_tool.read_journal(self.directory))
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual((code, result["drift"], [change["recovery"] for change in result["changes"]]),
                         (0, True, [False]), result)
        state = state_tool.show(self.directory)["state"]
        self.assertEqual(state["queue"][0]["stage"], "merged")
        self.assertNotIn("pending", state["queue"][0])
        self.assertTrue(state["queue"][1]["pending"], "a wait on unmerged work stays")
        self.assertEqual([entry for entry in state_tool.read_journal(self.directory)[entries:]
                          if entry["kind"] == "candidate.merged"], [], "the merge was already recorded")
        before = self.bytes()
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual((code, result["drift"]), (0, False), result)
        self.assertEqual(self.bytes(), before)

    def test_until_then_the_leftover_validates_and_nothing_reads_it_as_waiting(self) -> None:
        self.leftover()
        before = self.bytes()
        state = state_tool.show(self.directory)["state"]
        self.assertEqual(state_tool.state_errors(state), [])
        # Its hand-off is over: rejecting it would send merged work back to the lane's plan.
        for call in (lambda: state_tool.accept_handoff(self.directory, "work-m"),
                     lambda: state_tool.reject_handoff(self.directory, "work-m", "no")):
            with self.assertRaises(state_tool.StateError) as caught:
                call()
            self.assertEqual(caught.exception.code, "handoff_invalid")
        self.assertEqual(self.bytes(), before)
        self.assertEqual(set(state_tool.queue_estimates(state)), {"work-q"})
        journal = state_tool.read_journal(self.directory)
        twin = {**state, "queue": [item for item in state["queue"] if item["subject"] != "work-m"]}
        self.assertEqual(state_tool.delivery_status(state, journal, "2026-11-03T00:00:00Z"),
                         state_tool.delivery_status({**twin, "queue": [
                             {key: value for key, value in state["queue"][0].items() if key != "pending"},
                             *twin["queue"]]}, journal, "2026-11-03T00:00:00Z"))

    def test_the_runner_clears_the_wait_only_by_recording_the_merge(self) -> None:
        self.lead({"queue": [queue_item("work-m", pending=True)]})

        def as_runner(item: dict) -> dict:
            version = state_tool.show(self.directory)["state"]["version"]
            return state_tool.patch(self.directory, writer="runner", version=version, changes={"queue": [item]})

        item = state_tool.show(self.directory)["state"]["queue"][0]
        without = {key: value for key, value in item.items() if key != "pending"}
        with self.assertRaises(state_tool.StateError) as caught:
            as_runner(without)
        self.assertIn("pending belongs to the lead", str(caught.exception))
        with self.assertRaises(state_tool.StateError) as caught:
            as_runner({**without, "stage": "merged", "title": "Renamed"})
        self.assertIn("title belongs to the lead", str(caught.exception))
        as_runner({**without, "stage": "merged"})
        self.assertNotIn("pending", state_tool.show(self.directory)["state"]["queue"][0])
        with self.assertRaises(state_tool.StateError) as caught:
            as_runner({**without, "stage": "merged", "pending": True})
        self.assertIn("pending belongs to the lead", str(caught.exception))

    def test_a_merge_the_runner_records_after_the_plan_is_left_as_the_runner_wrote_it(self) -> None:
        self.lead({"queue": [queue_item("work-r", changeRequest="change 51")]})
        forge = self.snapshot("forge", [{"subject": "work-r", "state": "merged", "changeRequest": "change 51",
                                         "head": HEAD_B}])
        planning = state_tool._plan

        def runner_records_its_merge(*args: object) -> tuple:
            planned = planning(*args)
            version = state_tool.show(self.directory)["state"]["version"]
            state_tool.patch(self.directory, writer="runner", version=version, selector="queue[subject=work-r]",
                             changes={"stage": "merged", "syncedHead": HEAD_A})
            state_tool.append_journal(self.directory, kind="candidate.merged", subject="work-r", summary="Merged")
            return planned

        with mock.patch.object(state_tool, "_plan", runner_records_its_merge):
            result = state_tool.reconcile(self.directory, [Path(forge)])
        self.assertEqual((result["drift"], result["changes"]), (False, []))
        shown = state_tool.show(self.directory)
        self.assertEqual(shown["state"]["queue"][0]["syncedHead"], HEAD_A)
        self.assertEqual([entry["summary"] for entry in shown["journal"] if entry["kind"] == "candidate.merged"],
                         ["Merged"])

    def test_a_merge_the_runner_did_not_record_is_recorded_through_the_recovery_path(self) -> None:
        self.lead({
            "planned": [{"subject": "work-p", "title": "P", "lane": "lane-2", "stage": "review",
                         "changeRequest": "change 21"}],
            "queue": [queue_item("work-q"), queue_item("work-done", order=2)],
        })
        state_tool.patch(self.directory, writer="runner", version=state_tool.show(self.directory)["state"]["version"],
                         selector="queue[subject=work-done]", changes={"stage": "merged"})
        state_tool.append_journal(self.directory, kind="candidate.merged", subject="work-done", summary="Runner merge")
        journal_before = state_tool.show(self.directory)["journal"]
        forge = self.snapshot("forge", [
            {"subject": "work-p", "state": "merged", "changeRequest": "change 21", "head": HEAD_B},
            {"subject": "work-q", "state": "merged", "changeRequest": "change 22", "head": HEAD_B},
            {"subject": "work-done", "state": "merged", "changeRequest": "change 23", "head": HEAD_A},
        ])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", forge)
        self.assertEqual(code, 0, result)
        shown = state_tool.show(self.directory)
        queue = {item["subject"]: item for item in shown["state"]["queue"]}
        # Planned work found merged joins the queue, as the lead, then is recorded merged, as the runner.
        self.assertEqual(shown["state"]["planned"], [])
        self.assertEqual((queue["work-p"]["stage"], queue["work-p"]["head"], queue["work-p"]["syncedHead"]),
                         ("merged", HEAD_B, HEAD_B))
        self.assertEqual((queue["work-p"]["lane"], queue["work-p"]["changeRequest"]), ("lane-2", "change 21"))
        self.assertEqual((queue["work-q"]["stage"], queue["work-q"]["syncedHead"]), ("merged", HEAD_B))
        # A merge the runner recorded stays exactly as the runner wrote it.
        self.assertEqual((queue["work-done"]["stage"], queue["work-done"]["syncedHead"]), ("merged", None))
        added = shown["journal"][len(journal_before):]
        self.assertEqual([(entry["kind"], entry["subject"]) for entry in added],
                         [("candidate.merged", "work-p"), ("candidate.merged", "work-q")])
        self.assertTrue(all("not recorded by the runner" in entry["summary"] for entry in added))
        self.assertEqual(sorted(change["subject"] for change in result["changes"] if change["recovery"]),
                         ["work-p", "work-q"])
        again = self.snapshot("forge", json.loads(Path(forge).read_text())["items"])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", again)
        self.assertEqual((code, result["drift"]), (0, False))
        self.assertEqual(len(state_tool.show(self.directory)["journal"]), len(shown["journal"]))

    def test_reconcile_retries_a_record_that_moved_and_refuses_one_that_keeps_moving(self) -> None:
        tracker = self.snapshot("tracker", [{"subject": "work-now", "title": "Work", "state": "started"}])
        landed = []

        def once() -> None:
            if not landed:
                landed.append(self.lead({"meta": {"headline": "Moved under reconcile"}}))

        result = state_tool.reconcile(self.directory, [Path(tracker)], between_read_and_write=once)
        self.assertTrue(result["drift"])
        state = state_tool.show(self.directory)["state"]
        self.assertEqual(state["meta"]["headline"], "Moved under reconcile")
        self.assertEqual(state["planned"][0]["subject"], "work-now")

        def always() -> None:
            self.lead({"meta": {"headline": f"Moved {state_tool.now()}"}})

        other = self.snapshot("tracker", [{"subject": "work-next", "title": "Next", "state": "started"}])
        attempts = []
        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.reconcile(self.directory, [Path(other)], between_read_and_write=lambda: (attempts.append(1), always()))
        self.assertEqual(refused.exception.code, "version_stale")
        self.assertEqual(len(attempts), state_tool.RECONCILE_ATTEMPTS, "a bounded number of attempts, then refusal")
        self.assertNotIn("work-next", [item["subject"] for item in state_tool.show(self.directory)["state"]["planned"]])

    def test_work_is_planned_or_queued_never_both(self) -> None:
        self.lead({"queue": [queue_item("work-a")]})
        version = state_tool.show(self.directory)["state"]["version"]
        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.patch(self.directory, writer="lead", version=version, changes={
                "planned": [{"subject": "work-a", "title": "A", "lane": None, "stage": "planned"}]})
        self.assertIn("already in the queue", str(refused.exception))

    def test_the_runner_cannot_write_planned_work_and_the_lead_cannot_merge_it(self) -> None:
        self.lead({"planned": [{"subject": "work-a", "title": "A", "lane": None, "stage": "planned"}]})
        version = state_tool.show(self.directory)["state"]["version"]
        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.patch(self.directory, writer="runner", version=version, selector="planned[subject=work-a]",
                             changes={"stage": "review"})
        self.assertEqual(refused.exception.code, "field_not_owned")
        patched = state_tool.patch(self.directory, writer="lead", version=version, selector="planned[subject=work-a]",
                                   changes={"stage": "review", "changeRequest": "change 1"})
        self.assertEqual(patched["planned"][0]["stage"], "review")


def review(round_number: int, started: str, finished: str, *, verdicts: dict | None = None,
           findings: tuple[int, int, int, int] = (0, 0, 0, 0)) -> dict:
    return {"round": round_number, "startedAt": started, "finishedAt": finished,
            "verdicts": verdicts if verdicts is not None else {"outcome": "approve", "adversarial": "approve"},
            "findings": dict(zip(("P0", "P1", "P2", "P3"), findings))}


class ReviewRoundTests(unittest.TestCase):
    """A review round is journaled by the lead or a lane, checked, and never by the runner."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "state"
        state_tool.init(self.directory, "Epic underway")

    def journal(self) -> list[dict]:
        return state_tool.show(self.directory)["journal"]

    def test_a_round_is_journaled_with_its_review_and_a_written_summary(self) -> None:
        payload = review(1, "2026-10-20T10:00:00Z", "2026-10-20T10:20:00.5Z",
                         verdicts={"outcome": "approve", "adversarial": "changes"}, findings=(0, 1, 2, 3))
        code, result = run_tool(self.directory, "journal", "--kind", "review.round", "--subject", "work-a",
                                "--lane", "lane-1", "--review", json.dumps(payload))
        self.assertEqual(code, 0, result)
        entry = self.journal()[-1]
        self.assertEqual((entry["kind"], entry["subject"], entry["review"]), ("review.round", "work-a", payload))
        self.assertEqual(entry["summary"], "Round 1: adversarial changes, outcome approve; findings 0/1/2/3")
        code, result = run_tool(self.directory, "journal", "--kind", "review.round", "--subject", "work-a",
                                "--review", "-", "--summary", "Round two, from the hand-off",
                                stdin=json.dumps(review(2, "2026-10-20T11:00:00Z", "2026-10-20T11:00:00Z")))
        self.assertEqual((code, self.journal()[-1]["summary"]), (0, "Round two, from the hand-off"))
        code, result = run_tool(self.directory, "journal", "--kind", "review.round", "--subject", "work-a", "--review",
                                json.dumps({**review(3, "2026-10-20T12:00:00Z", "2026-10-20T12:01:00Z"), "round": 3.0}))
        self.assertEqual((code, self.journal()[-1]["summary"][:8]), (0, "Round 3:"))

    def test_a_missing_review_is_named(self) -> None:
        code, refused = run_tool(self.directory, "journal", "--kind", "review.round", "--subject", "w", "--review", "null")
        self.assertEqual(refused["code"], "entry_invalid")
        self.assertIn("--review", refused["error"])

    def test_a_malformed_round_is_refused_and_nothing_is_written(self) -> None:
        good = review(1, "2026-10-20T10:00:00Z", "2026-10-20T10:20:00Z")
        before = (self.directory / state_tool.JOURNAL_FILE).read_bytes()
        cases = {
            "a verdict that is not approve or changes": ["--subject", "w", "--review",
                                                         json.dumps({**good, "verdicts": {"outcome": "maybe"}})],
            "finished before it started": ["--subject", "w", "--review", json.dumps(
                {**good, "startedAt": "2026-10-20T10:20:00.2Z", "finishedAt": "2026-10-20T10:20:00.1Z"})],
            "no candidate": ["--review", json.dumps(good)],
            "no verdict": ["--subject", "w", "--review", json.dumps({**good, "verdicts": {}})],
            "no review": ["--subject", "w", "--summary", "A round"],
            "a severity the schema does not know": ["--subject", "w", "--review", json.dumps(
                {**good, "findings": {**good["findings"], "P4": 1}})],
            "round zero": ["--subject", "w", "--review", json.dumps({**good, "round": 0})],
            "a negative count": ["--subject", "w", "--review", json.dumps(
                {**good, "findings": {**good["findings"], "P1": -1}})],
            "not JSON": ["--subject", "w", "--review", "{round"],
            "a date that does not exist": ["--subject", "w", "--review", json.dumps(
                {**good, "startedAt": "2026-13-45T00:10:00Z"})],
            "a null review": ["--subject", "w", "--review", "null"],
            "not an object": ["--subject", "w", "--review", "[1]"],
        }
        for name, args in cases.items():
            with self.subTest(name):
                code, refused = run_tool(self.directory, "journal", "--kind", "review.round", *args)
                self.assertEqual((code, refused["code"]), (1, "entry_invalid"), refused)
        code, refused = run_tool(self.directory, "journal", "--kind", "decision.recorded", "--summary", "x",
                                 "--review", json.dumps(good))
        self.assertEqual((code, refused["code"]), (1, "entry_invalid"), "only a review round carries a review")
        code, refused = run_tool(self.directory, "journal", "--kind", "decision.recorded")
        self.assertEqual((code, refused["code"]), (1, "entry_invalid"), "other kinds still need a summary")
        self.assertEqual((self.directory / state_tool.JOURNAL_FILE).read_bytes(), before)

    def test_the_runner_never_writes_a_review_round(self) -> None:
        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.append_journal(self.directory, kind="review.round", summary="Round 1", subject="w",
                                      review=review(1, "2026-10-20T10:00:00Z", "2026-10-20T10:01:00Z"), writer="runner")
        self.assertEqual(refused.exception.code, "kind_not_owned")
        self.assertEqual(self.journal(), [])
        # The runner's other entries are unaffected, and say they are the runner's.
        state_tool.append_journal(self.directory, kind="check.started", summary="Checking", subject="w", writer="runner")
        self.assertEqual(self.journal()[-1]["kind"], "check.started")
        source = (SKILL / "scripts" / "merge_queue.py").read_text(encoding="utf-8")
        self.assertIn('writer="runner"', source[source.index("def journal(self"):source.index("def checking(self")])


def entry(number: int, at: str, kind: str, subject: str | None = None, lane: str | None = None, **fields: object) -> dict:
    return {"id": number, "at": at, "kind": kind, "lane": lane, "subject": subject, "summary": kind, **fields}


class DeliveryStatsTests(unittest.TestCase):
    """Per-stop stats are a pure fold of the record, with unknowns left unknown."""

    def state(self, *items: dict, planned: list[dict] | None = None) -> dict:
        document = {"queue": list(items)}
        if planned is not None:
            document["planned"] = planned
        return document

    def test_a_full_lifecycle_folds_to_exact_durations(self) -> None:
        journal = [
            entry(1, "2026-10-20T09:00:00Z", "lane.assigned", "work-a", "lane-1"),
            entry(2, "2026-10-20T09:50:00Z", "review.round", "work-a", "lane-1", review=review(
                1, "2026-10-20T09:30:00Z", "2026-10-20T09:50:00Z",
                verdicts={"outcome": "changes", "adversarial": "changes"}, findings=(0, 1, 2, 3))),
            entry(3, "2026-10-20T10:10:00Z", "review.round", "work-a", "lane-1", review=review(
                2, "2026-10-20T10:00:00Z", "2026-10-20T10:10:00Z", findings=(0, 0, 1, 0))),
            entry(4, "2026-10-20T10:15:00Z", "candidate.handed", "work-a", "lane-1"),
            entry(5, "2026-10-20T10:20:00Z", "check.started", "work-a", "lane-1"),
            entry(6, "2026-10-20T10:30:00Z", "check.finished", "work-a", "lane-1"),
            entry(7, "2026-10-20T10:35:00Z", "candidate.merged", "work-a", "lane-1"),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-a", reportedRounds=5)), journal)
        self.assertEqual(stats, {"work-a": {
            "assignedAt": "2026-10-20T09:00:00Z", "handedAt": "2026-10-20T10:15:00Z",
            "checkStartedAt": "2026-10-20T10:20:00Z", "endedAt": "2026-10-20T10:35:00Z", "outcome": "merged",
            "buildSeconds": 1800, "reviewSeconds": 2400, "waitSeconds": 300, "checkToMergeSeconds": 900,
            "totalSeconds": 5700, "rounds": 2, "roundsSource": "journal",
            "findings": {"P0": 0, "P1": 1, "P2": 3, "P3": 3},
            "lastVerdicts": {"outcome": "approve", "adversarial": "approve"},
        }})

    def test_missing_events_are_unknown_never_zero_and_rounds_fall_back_to_the_report(self) -> None:
        journal = [
            entry(1, "2026-10-20T10:15:00Z", "candidate.handed", "work-old"),
            entry(2, "2026-10-20T10:20:00Z", "check.started", "work-old"),
            entry(3, "2026-10-20T10:21:30Z", "candidate.stopped", "work-old"),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-old", lane=None, reportedRounds=3),
                                                     queue_item("work-new", order=2, lane=None, reportedRounds=None)), journal)
        old = stats["work-old"]
        self.assertEqual((old["assignedAt"], old["buildSeconds"], old["reviewSeconds"], old["totalSeconds"]),
                         (None, None, None, None))
        self.assertEqual((old["waitSeconds"], old["checkToMergeSeconds"], old["outcome"]), (300, 90, "stopped"))
        self.assertEqual((old["rounds"], old["roundsSource"], old["findings"], old["lastVerdicts"]),
                         (3, "reported", None, None))
        new = stats["work-new"]
        self.assertTrue(all(new[key] is None for key in new), new)

    def test_a_lane_assignment_counts_for_the_work_it_led_to_and_a_re_hand_restarts_the_wait(self) -> None:
        journal = [
            entry(1, "2026-10-20T08:00:00Z", "lane.assigned", None, "lane-1"),
            entry(2, "2026-10-20T09:00:00Z", "lane.assigned", None, "lane-1"),
            entry(3, "2026-10-20T10:00:00Z", "candidate.handed", "work-a", "lane-1"),
            entry(4, "2026-10-20T10:05:00Z", "candidate.stopped", "work-a", "lane-1"),
            entry(5, "2026-10-20T10:30:00Z", "candidate.handed", "work-a", "lane-1"),
            entry(6, "2026-10-20T10:31:00Z", "check.started", "work-a", "lane-1"),
            entry(7, "2026-10-20T10:40:00Z", "candidate.stopped", "work-a", "lane-1"),
            entry(8, "2026-10-20T10:41:00Z", "check.started", "work-a", "lane-1"),
            entry(9, "2026-10-20T10:50:00Z", "candidate.merged", "work-a", "lane-1"),
            entry(10, "2026-10-20T11:00:00Z", "lane.assigned", None, "lane-1"),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-a")), journal)["work-a"]
        # The lane's last assignment before its first event, not a later one.
        self.assertEqual(stats["assignedAt"], "2026-10-20T09:00:00Z")
        self.assertEqual((stats["waitSeconds"], stats["checkToMergeSeconds"], stats["outcome"]), (60, 1140, "merged"))
        self.assertEqual(stats["totalSeconds"], 6600)

    def test_a_reused_lane_never_lends_one_candidate_s_assignment_to_the_next(self) -> None:
        journal = [
            entry(1, "2026-10-20T08:00:00Z", "lane.assigned", None, "lane-1"),
            entry(2, "2026-10-20T08:30:00Z", "lane.assigned", "work-b", "lane-1"),
            entry(3, "2026-10-20T09:00:00Z", "candidate.handed", "work-a", "lane-1"),
            entry(4, "2026-10-20T09:30:00Z", "candidate.merged", "work-a", "lane-1"),
            entry(5, "2026-10-20T10:00:00Z", "candidate.handed", "work-c", "lane-1"),
            entry(6, "2026-10-20T10:30:00Z", "lane.assigned", None, "lane-1"),
            entry(7, "2026-10-20T11:00:00Z", "candidate.handed", "work-d", "lane-1"),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-a"), queue_item("work-c", order=2),
                                                     queue_item("work-d", order=3)), journal)
        # Another candidate's own assignment, and the lane's work on it, both stand between.
        self.assertIsNone(stats["work-a"]["assignedAt"])
        self.assertIsNone(stats["work-c"]["assignedAt"], "the lane's earlier assignment led to work-a")
        self.assertEqual(stats["work-d"]["assignedAt"], "2026-10-20T10:30:00Z")
        # Handing an earlier candidate over ends what the lane's assignment led to.
        journal = [
            entry(1, "2026-10-20T08:00:00Z", "lane.assigned", None, "lane-1"),
            entry(2, "2026-10-20T09:00:00Z", "candidate.handed", "work-a", "lane-1"),
            entry(3, "2026-10-20T09:30:00Z", "candidate.merged", "work-a", "lane-1"),
            entry(4, "2026-10-20T10:00:00Z", "candidate.handed", "work-f", "lane-1"),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-a"), queue_item("work-f", order=2)), journal)
        self.assertEqual(stats["work-a"]["assignedAt"], "2026-10-20T08:00:00Z")
        self.assertIsNone(stats["work-f"]["assignedAt"])
        # The runner merging an earlier candidate says nothing about the lane's assignment.
        journal = [
            entry(1, "2026-10-20T08:00:00Z", "lane.assigned", "work-b", "lane-1"),
            entry(2, "2026-10-20T08:30:00Z", "lane.assigned", None, "lane-1"),
            entry(3, "2026-10-20T09:00:00Z", "candidate.merged", "work-b", "lane-1"),
            entry(4, "2026-10-20T09:30:00Z", "candidate.handed", "work-e", "lane-1"),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-e")), journal)
        self.assertEqual(stats["work-e"]["assignedAt"], "2026-10-20T08:30:00Z")

    def test_a_candidate_re_checked_after_a_stop_is_still_being_checked(self) -> None:
        journal = [
            entry(1, "2026-10-20T09:00:00Z", "lane.assigned", "work-a", "lane-1"),
            entry(2, "2026-10-20T09:10:00Z", "candidate.merged", "work-a"),
            entry(3, "2026-10-20T09:20:00Z", "check.started", "work-a"),
            entry(4, "2026-10-20T10:00:00Z", "candidate.handed", "work-a"),
            entry(5, "2026-10-20T10:01:00Z", "check.started", "work-a"),
            entry(6, "2026-10-20T10:06:00Z", "candidate.stopped", "work-a"),
            entry(7, "2026-10-20T10:07:00.5Z", "check.started", "work-a"),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-a")), journal)["work-a"]
        self.assertEqual((stats["checkStartedAt"], stats["waitSeconds"]), ("2026-10-20T10:01:00Z", 60),
                         "a check before the last hand-off does not count")
        self.assertEqual((stats["outcome"], stats["endedAt"], stats["checkToMergeSeconds"]), (None, None, None),
                         "neither the earlier merge nor the stop ends a check still running")
        journal.append(entry(8, "2026-10-20T10:09:00.25Z", "candidate.stopped", "work-a"))
        stats = state_tool.delivery_stats(self.state(queue_item("work-a")), journal)["work-a"]
        self.assertEqual((stats["outcome"], stats["checkToMergeSeconds"]), ("stopped", 480))
        self.assertIsNone(stats["totalSeconds"], "the total follows the latest attempt, which stopped")

    def test_times_compare_as_times_and_durations_count_whole_seconds(self) -> None:
        # As text, "10:00:00.9Z" sorts before "10:00:00Z" and "10:00:02Z" after "10:00:02.6Z";
        # as times both are the other way round.
        journal = [
            entry(1, "2026-10-20T10:00:00Z", "lane.assigned", "work-a", "lane-1"),
            entry(2, "2026-10-20T10:00:02Z", "review.round", "work-a", review=review(
                1, "2026-10-20T10:00:00.9Z", "2026-10-20T10:00:01.6Z")),
            entry(3, "2026-10-20T10:00:03Z", "review.round", "work-a", review=review(
                2, "2026-10-20T10:00:00Z", "2026-10-20T10:00:01Z")),
            entry(4, "2026-10-20T10:00:04Z", "review.round", "work-b", review=review(
                1, "2026-10-20T10:00:00.5Z", "2026-10-20T10:00:02Z")),
            entry(5, "2026-10-20T10:00:05Z", "review.round", "work-b", review=review(
                2, "2026-10-20T10:00:01Z", "2026-10-20T10:00:02.6Z")),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-a"), queue_item("work-b", order=2)), journal)
        self.assertEqual((stats["work-a"]["buildSeconds"], stats["work-a"]["reviewSeconds"]), (0, 1),
                         "1.6 seconds counts as one whole second, never rounded up")
        self.assertEqual(stats["work-b"]["reviewSeconds"], 2)

    def test_review_time_spans_the_earliest_start_to_the_latest_finish(self) -> None:
        journal = [
            entry(1, "2026-10-20T09:00:00Z", "lane.assigned", "work-a", "lane-1"),
            entry(2, "2026-10-20T10:00:00Z", "review.round", "work-a", review=review(
                1, "2026-10-20T09:40:00.5Z", "2026-10-20T10:00:00Z")),
            entry(3, "2026-10-20T10:05:00Z", "review.round", "work-a", review=review(
                2, "2026-10-20T09:30:00Z", "2026-10-20T09:35:00Z")),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-a")), journal)["work-a"]
        self.assertEqual((stats["buildSeconds"], stats["reviewSeconds"]), (1800, 1800))

    def test_a_round_recorded_again_replaces_itself_and_backwards_time_is_unknown(self) -> None:
        journal = [
            entry(1, "2026-10-20T09:00:00Z", "lane.assigned", "work-a", "lane-1"),
            entry(2, "2026-10-20T10:00:00Z", "review.round", "work-a", review=review(
                1, "2026-10-20T09:40:00Z", "2026-10-20T10:00:00Z", findings=(1, 1, 1, 1))),
            entry(3, "2026-10-20T10:05:00Z", "review.round", "work-a", review=review(
                1, "2026-10-20T08:30:00Z", "2026-10-20T08:45:00Z", findings=(0, 0, 0, 1))),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-a")), journal)["work-a"]
        self.assertEqual((stats["rounds"], stats["findings"]), (1, {"P0": 0, "P1": 0, "P2": 0, "P3": 1}))
        self.assertEqual((stats["buildSeconds"], stats["reviewSeconds"]), (None, 900),
                         "a review that started before the assignment has no build time")

    def test_stats_follow_the_latest_attempt_after_a_merge_and_a_re_hand(self) -> None:
        journal = [
            entry(1, "2026-10-20T09:00:00Z", "lane.assigned", "work-a", "lane-1"),
            entry(2, "2026-10-20T10:00:00Z", "candidate.handed", "work-a", "lane-1"),
            entry(3, "2026-10-20T10:05:00Z", "check.started", "work-a", "lane-1"),
            entry(4, "2026-10-20T10:10:00Z", "candidate.merged", "work-a", "lane-1"),
            entry(5, "2026-10-20T11:00:00Z", "candidate.handed", "work-a", "lane-1"),
            entry(6, "2026-10-20T11:05:00Z", "check.started", "work-a", "lane-1"),
            entry(7, "2026-10-20T11:20:00Z", "candidate.stopped", "work-a", "lane-1"),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-a")), journal)["work-a"]
        # Everything about the end follows the latest hand-off, which stopped:
        # the earlier merge's total is not this attempt's.
        self.assertEqual(
            (stats["handedAt"], stats["checkStartedAt"], stats["endedAt"], stats["outcome"]),
            ("2026-10-20T11:00:00Z", "2026-10-20T11:05:00Z", "2026-10-20T11:20:00Z", "stopped"),
        )
        self.assertEqual((stats["checkToMergeSeconds"], stats["totalSeconds"]), (900, None))
        # A later attempt that merges again has its own total, from the assignment.
        journal += [
            entry(8, "2026-10-20T12:00:00Z", "candidate.handed", "work-a", "lane-1"),
            entry(9, "2026-10-20T12:05:00Z", "check.started", "work-a", "lane-1"),
            entry(10, "2026-10-20T12:15:00Z", "candidate.merged", "work-a", "lane-1"),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-a")), journal)["work-a"]
        self.assertEqual((stats["outcome"], stats["totalSeconds"]), ("merged", 11700))

    def test_a_merge_recovered_from_the_forge_ends_the_latest_attempt(self) -> None:
        # Merged on the forge before any check: reconcile records the merge alone.
        journal = [
            entry(1, "2026-10-20T09:00:00Z", "lane.assigned", "work-a", "lane-1"),
            entry(2, "2026-10-20T10:00:00Z", "candidate.handed", "work-a", "lane-1"),
            entry(3, "2026-10-20T10:30:00Z", "candidate.merged", "work-a"),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-a")), journal)["work-a"]
        self.assertEqual((stats["outcome"], stats["endedAt"], stats["totalSeconds"], stats["checkToMergeSeconds"]),
                         ("merged", "2026-10-20T10:30:00Z", 5400, None))
        # After a stop and a re-hand, the recovered merge ends the new attempt.
        journal = [
            entry(1, "2026-10-20T09:00:00Z", "lane.assigned", "work-a", "lane-1"),
            entry(2, "2026-10-20T10:00:00Z", "candidate.handed", "work-a", "lane-1"),
            entry(3, "2026-10-20T10:05:00Z", "check.started", "work-a"),
            entry(4, "2026-10-20T10:10:00Z", "candidate.stopped", "work-a"),
            entry(5, "2026-10-20T11:00:00Z", "candidate.handed", "work-a", "lane-1"),
            entry(6, "2026-10-20T11:02:00Z", "candidate.merged", "work-a"),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-a")), journal)["work-a"]
        self.assertEqual((stats["outcome"], stats["totalSeconds"]), ("merged", 7320))
        # The total always ends where the attempt ended: a stop after a
        # recovered merge has no total, and a second merge after a check is
        # where both end.
        base = [entry(1, "2026-10-20T09:00:00Z", "lane.assigned", "work-a", "lane-1"),
                entry(2, "2026-10-20T10:00:00Z", "candidate.handed", "work-a", "lane-1"),
                entry(3, "2026-10-20T10:10:00Z", "candidate.merged", "work-a"),
                entry(4, "2026-10-20T10:20:00Z", "check.started", "work-a")]
        stopped = state_tool.delivery_stats(self.state(queue_item("work-a")), [
            *base, entry(5, "2026-10-20T10:30:00Z", "candidate.stopped", "work-a")])["work-a"]
        self.assertEqual((stopped["outcome"], stopped["totalSeconds"]), ("stopped", None))
        again = state_tool.delivery_stats(self.state(queue_item("work-a")), [
            *base, entry(5, "2026-10-20T10:40:00Z", "candidate.merged", "work-a")])["work-a"]
        self.assertEqual((again["outcome"], again["endedAt"], again["totalSeconds"]),
                         ("merged", "2026-10-20T10:40:00Z", 6000))
        # With no hand-off on record, the last merge stands, as before.
        journal = [
            entry(1, "2026-10-20T09:00:00Z", "lane.assigned", "work-a", "lane-1"),
            entry(2, "2026-10-20T10:30:00Z", "candidate.merged", "work-a"),
        ]
        self.assertEqual(state_tool.delivery_stats(self.state(queue_item("work-a")), journal)["work-a"]["totalSeconds"], 5400)

    def test_fractions_beyond_the_microsecond_are_truncated_never_rounded(self) -> None:
        # A schema timestamp may carry any number of fractional digits; the
        # stats keep the first six and drop the rest, so they never round up.
        moment = state_tool._parse_time("2026-10-20T09:00:00.1234569Z", exact=True)
        self.assertEqual(moment.microsecond, 123456)
        self.assertEqual(state_tool._seconds("2026-10-20T09:00:00Z", "2026-10-20T09:00:00.9999999Z"), 0)
        self.assertEqual(state_tool._seconds("2026-10-20T09:00:00.0000009Z", "2026-10-20T09:00:01.0000001Z"), 1)
        journal = [
            entry(1, "2026-10-20T09:00:00.000000001Z", "lane.assigned", "work-a", "lane-1"),
            entry(2, "2026-10-20T09:30:00.999999999Z", "review.round", "work-a", "lane-1", review=review(
                1, "2026-10-20T09:10:00.123456789Z", "2026-10-20T09:30:00.999999999Z")),
        ]
        stats = state_tool.delivery_stats(self.state(queue_item("work-a")), journal)["work-a"]
        self.assertEqual((stats["buildSeconds"], stats["reviewSeconds"]), (600, 1200))

    def test_planned_work_has_stats_and_the_fold_is_in_show(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name) / "state"
        state_tool.init(directory, "Epic underway")
        version = state_tool.show(directory)["state"]["version"]
        state_tool.patch(directory, writer="lead", version=version, changes={"planned": [
            {"subject": "work-p", "title": "P", "lane": "lane-2", "stage": "review"}]})
        state_tool.append_journal(directory, kind="lane.assigned", summary="Assigned", lane="lane-2", subject="work-p")
        state_tool.append_journal(directory, kind="review.round", summary="Round 1", subject="work-p",
                                  review=review(1, "2099-01-01T00:00:00Z", "2099-01-01T00:10:00Z"))
        shown = state_tool.show(directory)
        self.assertEqual(shown["stats"], state_tool.delivery_stats(shown["state"], shown["journal"]))
        self.assertEqual((shown["stats"]["work-p"]["rounds"], shown["stats"]["work-p"]["reviewSeconds"]), (1, 600))
        self.assertIsNone(shown["stats"]["work-p"]["handedAt"])


class RecoveryIntentTests(unittest.TestCase):
    """A recovery interrupted by a crash is finished by the next reconcile: no entry lost, none doubled."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "state"
        state_tool.init(self.directory, "Epic underway")
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version,
                         changes={"queue": [queue_item("work-q", changeRequest="change 7")]})
        self.forge = self.root / "forge.json"
        self.forge.write_text(json.dumps({"takenAt": taken(), "source": "forge", "items": [
            {"subject": "work-q", "state": "merged", "changeRequest": "change 7", "head": HEAD_B}]}), encoding="utf-8")

    def recovered(self) -> list[dict]:
        return [entry for entry in state_tool.show(self.directory)["journal"]
                if entry["kind"] == "candidate.merged" and entry["subject"] == "work-q"]

    def intent(self) -> Path:
        return self.directory / state_tool.RECOVERY_FILE

    def test_a_crash_between_the_state_write_and_its_entry_is_finished_next_time(self) -> None:
        with mock.patch.object(state_tool, "_journal_recovery", side_effect=RuntimeError("the machine stopped")):
            with self.assertRaises(RuntimeError):
                state_tool.reconcile(self.directory, [self.forge])
        self.assertTrue(self.intent().exists())
        self.assertEqual(state_tool.show(self.directory)["state"]["queue"][0]["stage"], "merged")
        self.assertEqual(self.recovered(), [], "the crash lost the entry")
        result = state_tool.reconcile(self.directory, [self.forge])
        self.assertTrue(result["drift"])
        self.assertEqual([(change["subject"], change["recovery"]) for change in result["changes"]], [("work-q", True)])
        self.assertEqual(result["reports"], [], "nothing was skipped")
        self.assertEqual(len(self.recovered()), 1)
        self.assertIn(HEAD_B, self.recovered()[0]["summary"])
        self.assertFalse(self.intent().exists())
        self.assertFalse(state_tool.reconcile(self.directory, [self.forge])["drift"])

    def test_a_crash_before_clearing_an_already_merged_items_wait_is_finished_next_time(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version, selector="queue[subject=work-q]",
                         changes={"pending": True})
        version += 1
        state_tool.patch(self.directory, writer="runner", version=version, selector="queue[subject=work-q]",
                         changes={"stage": "merged", "syncedHead": HEAD_B})
        with mock.patch.object(state_tool, "patch", side_effect=RuntimeError("the machine stopped")):
            with self.assertRaises(RuntimeError):
                state_tool.reconcile(self.directory, [self.forge])
        self.assertTrue(self.intent().exists())
        # A snapshot that does not name the item: only finishing the intent can clear the wait.
        elsewhere = self.root / "tracker.json"
        elsewhere.write_text(json.dumps({"takenAt": taken(), "source": "tracker", "items": []}), encoding="utf-8")
        result = state_tool.reconcile(self.directory, [elsewhere])
        self.assertNotIn("pending", state_tool.show(self.directory)["state"]["queue"][0])
        self.assertEqual(len(self.recovered()), 1)
        self.assertIn("before the lead accepted", self.recovered()[0]["summary"])
        self.assertEqual([report["subject"] for report in result["reports"]], ["work-q"])
        self.assertFalse(self.intent().exists())

    def test_a_crash_after_the_state_write_still_reports_the_skipped_acceptance(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version, selector="queue[subject=work-q]",
                         changes={"pending": True})
        with mock.patch.object(state_tool, "_journal_recovery", side_effect=RuntimeError("the machine stopped")):
            with self.assertRaises(RuntimeError):
                state_tool.reconcile(self.directory, [self.forge])
        self.assertNotIn("pending", state_tool.show(self.directory)["state"]["queue"][0])
        result = state_tool.reconcile(self.directory, [self.forge])
        self.assertEqual([report["subject"] for report in result["reports"]], ["work-q"])
        self.assertIn("lead-accept", result["reports"][0]["note"])

    def test_a_crash_after_the_entry_still_reports_the_skipped_acceptance_and_clears_the_wait(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version, selector="queue[subject=work-q]",
                         changes={"pending": True})
        version += 1
        state_tool.patch(self.directory, writer="runner", version=version, selector="queue[subject=work-q]",
                         changes={"stage": "merged", "syncedHead": HEAD_B})
        with mock.patch.object(state_tool, "patch", side_effect=RuntimeError("the machine stopped")):
            with self.assertRaises(RuntimeError):
                state_tool.reconcile(self.directory, [self.forge])
        with mock.patch.object(state_tool, "_tool_write", side_effect=RuntimeError("the machine stopped")):
            with self.assertRaises(RuntimeError):
                state_tool.reconcile(self.directory, [self.forge])
        self.assertEqual(len(self.recovered()), 1)
        self.assertTrue(self.intent().exists())
        # A snapshot that does not name the item: only finishing the intent can clear the wait.
        elsewhere = self.root / "tracker.json"
        elsewhere.write_text(json.dumps({"takenAt": taken(), "source": "tracker", "items": []}), encoding="utf-8")
        result = state_tool.reconcile(self.directory, [elsewhere])
        self.assertEqual([report["subject"] for report in result["reports"]], ["work-q"])
        self.assertNotIn("pending", state_tool.show(self.directory)["state"]["queue"][0])
        self.assertEqual(len(self.recovered()), 1)
        self.assertFalse(self.intent().exists())

    def put_off_recovery_of_a_waiting_item(self) -> Path:
        """The item waits, a recovery crashes before its state write, and a snapshot naming nothing is returned."""
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version, selector="queue[subject=work-q]",
                         changes={"pending": True})
        with mock.patch.object(state_tool, "patch", side_effect=RuntimeError("the machine stopped")):
            with self.assertRaises(RuntimeError):
                state_tool.reconcile(self.directory, [self.forge])
        self.assertTrue(self.intent().exists())
        elsewhere = self.root / "tracker.json"
        elsewhere.write_text(json.dumps({"takenAt": taken(), "source": "tracker", "items": []}), encoding="utf-8")
        return elsewhere

    def test_a_merge_the_runner_journaled_after_the_lead_accepted_reports_no_skipped_acceptance(self) -> None:
        elsewhere = self.put_off_recovery_of_a_waiting_item()
        state_tool.accept_handoff(self.directory, "work-q")
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="runner", version=version, selector="queue[subject=work-q]",
                         changes={"stage": "merged", "syncedHead": HEAD_B})
        state_tool.append_journal(self.directory, kind="candidate.merged", summary="Merged by the runner", lane="lane-1",
                                  subject="work-q", writer="runner")
        result = state_tool.reconcile(self.directory, [elsewhere])
        self.assertEqual(result["reports"], [])
        self.assertEqual([entry["summary"] for entry in self.recovered()], ["Merged by the runner"])
        self.assertFalse(self.intent().exists())

    def test_an_intent_for_another_head_records_clears_and_reports_nothing(self) -> None:
        elsewhere = self.put_off_recovery_of_a_waiting_item()
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="runner", version=version, selector="queue[subject=work-q]",
                         changes={"stage": "merged", "syncedHead": HEAD_A})
        result = state_tool.reconcile(self.directory, [elsewhere])
        self.assertEqual(result["reports"], [])
        self.assertEqual(self.recovered(), [])
        self.assertTrue(state_tool.show(self.directory)["state"]["queue"][0]["pending"])
        self.assertFalse(self.intent().exists())

    def test_a_crash_after_the_entry_and_before_the_intent_is_cleared_writes_no_second_entry(self) -> None:
        journal = state_tool._journal_recovery

        def journal_then_stop(*args: object) -> dict:
            journal(*args)
            raise RuntimeError("the machine stopped")

        with mock.patch.object(state_tool, "_journal_recovery", journal_then_stop):
            with self.assertRaises(RuntimeError):
                state_tool.reconcile(self.directory, [self.forge])
        self.assertTrue(self.intent().exists())
        result = state_tool.reconcile(self.directory, [self.forge])
        self.assertEqual((result["drift"], result["changes"]), (False, []))
        self.assertEqual(len(self.recovered()), 1)
        self.assertFalse(self.intent().exists())

    def test_a_crash_before_the_state_write_leaves_the_merge_to_be_recovered_again(self) -> None:
        with mock.patch.object(state_tool, "patch", side_effect=RuntimeError("the machine stopped")):
            with self.assertRaises(RuntimeError):
                state_tool.reconcile(self.directory, [self.forge])
        self.assertTrue(self.intent().exists())
        self.assertEqual(state_tool.show(self.directory)["state"]["queue"][0]["stage"], "queued")
        result = state_tool.reconcile(self.directory, [self.forge])
        self.assertEqual([change["recovery"] for change in result["changes"]], [True])
        self.assertEqual(len(self.recovered()), 1)
        self.assertFalse(self.intent().exists())

    def test_an_interrupted_recovery_waits_for_a_running_runner(self) -> None:
        with mock.patch.object(state_tool, "_journal_recovery", side_effect=RuntimeError("the machine stopped")):
            with self.assertRaises(RuntimeError):
                state_tool.reconcile(self.directory, [self.forge])
        with state_tool.file_lock(self.directory / state_tool.QUEUE_LOCK):
            code, result = run_tool(self.directory, "reconcile", "--snapshot", str(self.forge))
        self.assertEqual(code, 0, result)
        self.assertIn("waits for the queue lock", " ".join(report["note"] for report in result["reports"]))
        self.assertTrue(self.intent().exists())
        self.assertTrue(run_tool(self.directory, "show")[1]["publication"]["recoveryPending"])
        self.assertEqual(self.recovered(), [])

    def test_an_intent_left_for_the_runner_is_finished_before_another_recovery_overwrites_it(self) -> None:
        with mock.patch.object(state_tool, "_journal_recovery", side_effect=RuntimeError("the machine stopped")):
            with self.assertRaises(RuntimeError):
                state_tool.reconcile(self.directory, [self.forge])
        version = state_tool.show(self.directory)["state"]["version"]
        queue = state_tool.show(self.directory)["state"]["queue"]
        state_tool.patch(self.directory, writer="lead", version=version,
                         changes={"queue": [*queue, queue_item("work-r", order=2, changeRequest="change 8")]})
        both = self.root / "both.json"
        both.write_text(json.dumps({"takenAt": taken(), "source": "forge", "items": [
            {"subject": "work-q", "state": "merged", "changeRequest": "change 7", "head": HEAD_B},
            {"subject": "work-r", "state": "merged", "changeRequest": "change 8", "head": HEAD_A}]}), encoding="utf-8")

        def runner_held_the_queue(directory: Path, finished: list, held: list) -> None:
            # The runner held the lock as this reconcile began, and let it go just after.
            held.append({"subject": "work-q", "note": "an interrupted recovery waits for the queue lock: reconcile again once it is free"})

        with mock.patch.object(state_tool, "_finish_recovery", runner_held_the_queue):
            result = state_tool.reconcile(self.directory, [both])
        self.assertEqual(sorted(change["subject"] for change in result["changes"] if change["recovery"]),
                         ["work-q", "work-r"])
        merged = [entry["subject"] for entry in state_tool.show(self.directory)["journal"]
                  if entry["kind"] == "candidate.merged"]
        self.assertEqual(sorted(merged), ["work-q", "work-r"], "the waiting entry was not lost")
        self.assertFalse([report for report in result["reports"] if "waits for the queue lock" in report["note"]],
                         "an intent finished in the same pass is not reported as waiting")
        self.assertFalse(self.intent().exists())

    def test_a_malformed_intent_is_set_aside_reported_and_never_wedges_reconcile(self) -> None:
        quarantine = self.directory / state_tool.RECOVERY_QUARANTINE
        for contents in ("not json", json.dumps({"subject": "work-q"}),
                         json.dumps({"subject": "work-q", "head": HEAD_B, "lane": 3, "summary": "s", "journalAfter": 0})):
            with self.subTest(contents=contents):
                quarantine.unlink(missing_ok=True)
                self.intent().write_text(contents, encoding="utf-8")
                self.assertTrue(run_tool(self.directory, "show")[1]["publication"]["recoveryPending"])
                code, result = run_tool(self.directory, "reconcile", "--snapshot", str(self.forge))
                self.assertEqual(code, 0, result)
                self.assertIn("set aside", " ".join(report["note"] for report in result["reports"]))
                self.assertEqual(quarantine.read_text(encoding="utf-8"), contents, "kept for the lead, not deleted")
                self.assertFalse(self.intent().exists())
                self.assertTrue(run_tool(self.directory, "show")[1]["publication"]["recoveryPending"],
                                "a set-aside intent stays visible until the lead clears it")
        # A second one never overwrites the first set aside.
        self.intent().write_text("second", encoding="utf-8")
        run_tool(self.directory, "reconcile", "--snapshot", str(self.forge))
        self.assertTrue(quarantine.exists())
        self.assertEqual((self.directory / f"{state_tool.RECOVERY_QUARANTINE}.2").read_text(encoding="utf-8"), "second")
        # Clearing the first leaves the second still pending.
        quarantine.unlink()
        self.assertTrue(run_tool(self.directory, "show")[1]["publication"]["recoveryPending"])

    def test_a_malformed_intent_the_recovery_itself_meets_is_set_aside_too(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version, changes={"queue": [
            *state_tool.show(self.directory)["state"]["queue"], queue_item("work-r", order=2)]})
        both = self.root / "both.json"
        both.write_text(json.dumps({"takenAt": taken(), "source": "forge", "items": [
            {"subject": "work-r", "state": "merged", "head": HEAD_A}]}), encoding="utf-8")
        self.intent().write_text("{bad", encoding="utf-8")

        def queue_held(directory: Path, finished: list, held: list) -> None:
            held.append({"subject": None, "note": "an interrupted recovery waits for the queue lock"})

        with mock.patch.object(state_tool, "_finish_recovery", queue_held):
            result = state_tool.reconcile(self.directory, [both])
        self.assertEqual((self.directory / state_tool.RECOVERY_QUARANTINE).read_text(encoding="utf-8"), "{bad")
        self.assertIn("set aside", " ".join(report["note"] for report in result["reports"]))
        self.assertEqual([entry["subject"] for entry in state_tool.show(self.directory)["journal"]
                          if entry["kind"] == "candidate.merged"], ["work-r"], "the recovery still ran")

    def test_a_merge_another_writer_journaled_meanwhile_is_never_journaled_twice(self) -> None:
        with mock.patch.object(state_tool, "patch", side_effect=RuntimeError("the machine stopped")):
            with self.assertRaises(RuntimeError):
                state_tool.reconcile(self.directory, [self.forge])
        # Before the next reconcile, the runner records the merge itself.
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="runner", version=version, selector="queue[subject=work-q]",
                         changes={"stage": "merged", "syncedHead": HEAD_B})
        state_tool.append_journal(self.directory, kind="candidate.merged", summary=f"Merged at {HEAD_B}",
                                  subject="work-q", writer="runner")
        state_tool.reconcile(self.directory, [self.forge])
        self.assertEqual(len(self.recovered()), 1)
        self.assertFalse(self.intent().exists())

    def test_an_earlier_merge_entry_for_the_subject_does_not_count_for_a_later_recovery(self) -> None:
        state_tool.append_journal(self.directory, kind="candidate.merged", summary="An earlier merge, since reverted",
                                  subject="work-q", writer="runner")
        with mock.patch.object(state_tool, "_journal_recovery", side_effect=RuntimeError("the machine stopped")):
            with self.assertRaises(RuntimeError):
                state_tool.reconcile(self.directory, [self.forge])
        state_tool.reconcile(self.directory, [self.forge])
        self.assertEqual(len(self.recovered()), 2, "the recovery's own entry is still appended")

    def test_a_refused_state_write_clears_its_intent(self) -> None:
        refusal = state_tool.StateError("field_not_owned", "refused")
        with mock.patch.object(state_tool, "patch", side_effect=refusal):
            with self.assertRaises(state_tool.StateError):
                state_tool.reconcile(self.directory, [self.forge])
        self.assertFalse(self.intent().exists())
        self.assertFalse(run_tool(self.directory, "show")[1]["publication"]["recoveryPending"])


class BindingMaxAgeTests(unittest.TestCase):
    """The binding sets how old a snapshot may be; --max-age overrides it."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "state"
        state_tool.init(self.directory, "Epic underway")
        self.snapshot = self.root / "two minutes old.json"
        self.snapshot.write_text(json.dumps({"takenAt": taken(120), "source": "tracker", "items": [
            {"subject": "work-a", "title": "A", "state": "started"}]}), encoding="utf-8")

    def binding(self, **state: object) -> str:
        path = self.root / f"binding {len(list(self.root.iterdir()))}.json"
        path.write_text(json.dumps({"state": {"directory": "state", "renderers": [], "publishers": [], **state}}),
                        encoding="utf-8")
        return str(path)

    def test_the_binding_s_max_age_is_honoured_and_max_age_overrides_it(self) -> None:
        strict = self.binding(snapshotMaxAgeSeconds=60)
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", str(self.snapshot), "--binding", strict)
        self.assertEqual((code, refused["code"]), (1, "snapshot_stale"), refused)
        self.assertIn("60s", refused["error"])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", str(self.snapshot), "--binding", strict,
                                "--max-age", "300")
        self.assertEqual((code, result["drift"]), (0, True), result)
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", str(self.snapshot), "--max-age", "60")
        self.assertEqual((code, refused["code"]), (1, "snapshot_stale"))
        code, result = run_tool(self.directory, "reconcile", "--snapshot", str(self.snapshot), "--binding", self.binding())
        self.assertEqual(code, 0, "without the field, the default applies")

    def test_a_broken_binding_is_refused_even_when_max_age_overrides_it(self) -> None:
        bad = self.root / "broken.json"
        bad.write_text("{", encoding="utf-8")
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", str(self.snapshot), "--binding", str(bad),
                                 "--max-age", "300")
        self.assertEqual((code, refused["code"]), (1, "binding_invalid"))

    def test_a_malformed_binding_or_max_age_is_refused(self) -> None:
        bad = self.root / "bad.json"
        bad.write_text("{", encoding="utf-8")
        cases = [str(bad), self.binding(snapshotMaxAgeSeconds=0), self.binding(snapshotMaxAgeSeconds=True),
                 self.binding(snapshotMaxAgeSeconds=1.5), self.binding(snapshotMaxAgeSeconds="60")]
        for path in cases:
            with self.subTest(path=Path(path).name):
                code, refused = run_tool(self.directory, "reconcile", "--snapshot", str(self.snapshot), "--binding", path)
                self.assertEqual((code, refused["code"]), (1, "binding_invalid"), refused)


class StateDirectoryPlaceholderTests(unittest.TestCase):
    """``{stateDirectory}`` in a snapshot or hints command is the resolved state directory it runs for."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "served"
        state_tool.init(self.directory, "Epic underway")
        state_tool.patch(self.directory, writer="lead", version=state_tool.show(self.directory)["state"]["version"],
                         changes={"lanes": [{"id": "lane-1", "title": "Lane", "focus": "", "state": "idle",
                                             "waitingOn": None, "since": "2026-10-20T10:00:00Z"}]})
        self.seen = self.root / "seen.jsonl"
        self.tool = self.root / "recorder.py"
        self.tool.write_text(textwrap.dedent(f"""
            import datetime, json, sys
            with open({str(self.seen)!r}, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(sys.argv[2:]) + "\\n")
            if sys.argv[1] == "hints":
                print(json.dumps({{"items": {{}}}}))
            else:
                taken = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                print(json.dumps({{"takenAt": taken, "source": sys.argv[1], "items": []}}))
        """), encoding="utf-8")

    def argv(self, kind: str) -> list[str]:
        return [sys.executable, "-B", str(self.tool), kind, "--state-dir", "{stateDirectory}", "{stateDirectory}/x"]

    def test_snapshot_and_hints_commands_get_the_resolved_state_directory(self) -> None:
        # The binding names another directory: the placeholder follows the one the command runs for.
        binding = self.root / "binding.json"
        binding.write_text(json.dumps({"state": {"directory": "elsewhere", "renderers": [], "publishers": [],
                                                 "trackerSnapshot": {"argv": self.argv("tracker")},
                                                 "forgeSnapshot": {"argv": self.argv("forge")},
                                                 "workHints": {"argv": self.argv("hints")}}}), encoding="utf-8")
        for command, source in (("tracker-snapshot", "tracker"), ("forge-snapshot", "forge")):
            code, result = run_tool(self.directory, command, "--binding", str(binding),
                                    "--output", str(self.root / f"{source}.json"))
            self.assertEqual((code, result["taken"]), (0, True), result)
        code, result = run_tool(self.directory, "next", "--lane", "lane-1", "--snapshot",
                                str(self.root / "tracker.json"), "--binding", str(binding))
        self.assertEqual((code, result.get("hints")), (0, "command"), result)
        resolved = str(self.directory.resolve())
        seen = [json.loads(line) for line in self.seen.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(seen, [["--state-dir", resolved, f"{resolved}/x"]] * 3)


class TrackerSnapshotTests(unittest.TestCase):
    """The binding's own command takes the tracker snapshot; a failure keeps the last one."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "state"
        state_tool.init(self.directory, "Epic underway")
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version, changes={"meta": {"trackerParent": "epic-7"}})
        self.output = self.root / "tracker snapshot.json"
        self.tool = self.root / "tracker tool.py"
        self.tool.write_text(textwrap.dedent("""
            import json, sys, time, datetime
            parent, mode = sys.argv[1], sys.argv[2]
            now = datetime.datetime.now(datetime.timezone.utc)
            if mode == "fail":
                print("the tracker refused: Authorization: Bearer secret-token-123", file=sys.stderr); sys.exit(3)
            if mode == "huge":
                sys.stdout.write("x" * (17 * 1024 * 1024)); sys.exit(0)
            if mode == "sleep":
                time.sleep(5)
            if mode == "noisy":
                sys.stderr.write("warning\\n" * 30000); sys.stderr.flush()
            if mode == "holder":
                # A detached child that inherits stdout and stderr and outlives the command.
                import os, subprocess
                child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                         stdout=sys.stdout, stderr=sys.stderr, start_new_session=True)
                with open(os.environ["HOLDER_PID_FILE"], "w") as handle:
                    handle.write(str(child.pid))
            if mode == "garbage":
                print("not json"); sys.exit(0)
            if mode == "bigint":
                print('{"takenAt": ' + "9" * 5000 + '}'); sys.exit(0)
            if mode == "deep":
                print("[" * 200000 + "]" * 200000); sys.exit(0)
            taken = now - datetime.timedelta(seconds=7200 if mode == "old" else 0)
            source = "forge" if mode in ("forge", "forgeok") else "tracker"
            if mode == "forgeok":
                print(json.dumps({"takenAt": taken.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "forge", "items": [
                    {"subject": "a", "state": "open"}]})); sys.exit(0)
            if mode == "baddate":
                print(json.dumps({"takenAt": "2026-02-30T00:00:00Z", "source": "tracker", "items": []})); sys.exit(0)
            print(json.dumps({"takenAt": taken.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": source, "items": [
                {"subject": parent + "-child-1", "title": "Later work", "state": "backlog"},
                {"subject": parent + "-child-2", "title": "Work now", "state": "started",
                 "blockedBy": [parent + "-child-1"]}]}))
        """), encoding="utf-8")

    def binding(self, mode: str = "ok", **block: object) -> str:
        path = self.root / f"binding {mode}.json"
        member = {"directory": "state", "renderers": [], "publishers": [],
                  "trackerSnapshot": {"argv": [sys.executable, "-B", str(self.tool), "{parent}", mode], **block}}
        path.write_text(json.dumps({"state": member}), encoding="utf-8")
        return str(path)

    def snap(self, mode: str = "ok", *extra: str, **block: object) -> dict:
        code, result = run_tool(self.directory, "tracker-snapshot", "--binding", self.binding(mode, **block),
                                "--output", str(self.output), *extra)
        self.assertEqual(code, 0, result)
        return result

    def notes(self) -> list[str]:
        return [entry["summary"] for entry in state_tool.show(self.directory)["journal"]
                if entry["summary"].startswith("tracker-snapshot:")]

    def test_the_command_takes_a_snapshot_reconcile_reads_with_no_agent(self) -> None:
        result = self.snap()
        self.assertEqual((result["taken"], result["items"]), (True, 2))
        snapshot = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["items"][1]["blockedBy"], ["epic-7-child-1"], "the parent came from the record")
        code, reconciled = run_tool(self.directory, "reconcile", "--snapshot", str(self.output))
        self.assertEqual((code, reconciled["drift"]), (0, True), reconciled)
        state = state_tool.show(self.directory)["state"]
        self.assertEqual(state["followups"][0]["id"], "epic-7-child-1")
        self.assertEqual(state["planned"][0]["subject"], "epic-7-child-2")

    def test_a_failing_or_invalid_command_keeps_the_last_snapshot_and_notes_why_once(self) -> None:
        self.snap()
        kept = self.output.read_bytes()
        for mode, reason, detail in (("fail", "exited with 3", "the tracker refused"),
                                     ("garbage", "printed no JSON", None),
                                     ("forge", "not a valid tracker snapshot", "forge"),
                                     # The age is whole seconds and the run takes time: 7200s or a little more.
                                     ("old", "too old", re.compile(r"was taken 720\ds ago")),
                                     ("huge", "printed more than", None),
                                     ("bigint", "printed no JSON", None),
                                     ("deep", "printed no JSON", None),
                                     ("forgeok", "not a valid tracker snapshot", "not from the tracker"),
                                     ("baddate", "not a valid tracker snapshot", "not a real time")):
            with self.subTest(mode=mode):
                result = self.snap(mode)
                self.assertEqual((result["taken"], result["fallback"]), (False, True))
                self.assertIn(reason, result["reason"])
                if isinstance(detail, re.Pattern):
                    self.assertRegex(result["detail"], detail)
                elif detail:
                    self.assertIn(detail, result["detail"])
                self.assertEqual(self.output.read_bytes(), kept, "the last snapshot stands")
                self.assertIn(reason, self.notes()[-1])
        # What the command printed is never published: the journal names only the reason.
        self.assertFalse([note for note in self.notes() if "secret-token" in note or "Bearer" in note])
        before = len(self.notes())
        self.snap("baddate")
        self.assertEqual(len(self.notes()), before, "the same reason is noted once")

    def test_an_outage_that_ended_is_noted_so_the_next_one_is_noted_too(self) -> None:
        self.snap("fail")
        self.snap()
        self.snap("fail")
        self.assertEqual(self.notes(), [
            "tracker-snapshot: the command exited with 3; the last snapshot stands",
            "tracker-snapshot: taken again",
            "tracker-snapshot: the command exited with 3; the last snapshot stands",
        ])
        self.snap()
        self.snap()
        self.assertEqual(self.notes().count("tracker-snapshot: taken again"), 2, "a run of successes is noted once")

    def test_reconcile_refuses_a_snapshot_the_parser_cannot_read(self) -> None:
        for name, text in (("long number", '{"takenAt": ' + "9" * 5000 + "}"),
                           ("deep nesting", "[" * 200000 + "]" * 200000)):
            with self.subTest(name):
                path = self.root / f"{name}.json"
                path.write_text(text, encoding="utf-8")
                code, refused = run_tool(self.directory, "reconcile", "--snapshot", str(path))
                self.assertEqual((code, refused["code"]), (1, "snapshot_invalid"), refused)

    def test_a_reason_that_comes_back_after_another_is_noted_again(self) -> None:
        self.snap("fail")
        self.snap("garbage")
        self.snap("fail")
        self.assertEqual(len(self.notes()), 3)

    def test_a_snapshot_is_never_written_over_the_record_s_own_files(self) -> None:
        (self.directory / "outbox").mkdir()
        before = {name: (self.directory / name).read_bytes() for name in (state_tool.STATE_FILE, state_tool.JOURNAL_FILE)}
        for name in ("state.json", "journal.jsonl", "STATE.JSON", "outbox/outbox.json", "published.json",
                     "recovery.json", "recovery.json.invalid.2", "queue.lock", "changes.jsonl"):
            with self.subTest(name=name):
                code, refused = run_tool(self.directory, "tracker-snapshot", "--binding", self.binding(),
                                         "--output", str(self.directory / name))
                self.assertEqual((code, refused["code"]), (1, "output_invalid"), refused)
        self.assertEqual({name: (self.directory / name).read_bytes() for name in before}, before)
        # The directory named in another case is still the directory, where the filesystem ignores case.
        other_case = self.directory.with_name(self.directory.name.upper())
        if other_case.exists() and os.path.samefile(other_case, self.directory):
            code, refused = run_tool(self.directory, "tracker-snapshot", "--binding", self.binding(),
                                     "--output", str(other_case / "state.json"))
            self.assertEqual((code, refused["code"]), (1, "output_invalid"), refused)
            code, refused = run_tool(other_case, "tracker-snapshot", "--binding", self.binding(),
                                     "--output", str(self.directory / "state.json"))
            self.assertEqual((code, refused["code"]), (1, "output_invalid"), refused)
            self.assertEqual({name: (self.directory / name).read_bytes() for name in before}, before)
        code, result = run_tool(self.directory, "tracker-snapshot", "--binding", self.binding(),
                                "--output", str(self.directory / "tracker snapshot.json"))
        self.assertEqual((code, result["taken"]), (0, True), "any other name in the directory is fine")

    def test_the_binding_schema_takes_a_tracker_snapshot_block(self) -> None:
        schema = state_tool.load_schema("orchestration-binding/1")
        member = schema["properties"]["state"]
        block = {"argv": ["tool", "{parent}"], "timeoutSeconds": 30}
        errors = state_tool.validate({"directory": "s", "renderers": [], "publishers": [], "trackerSnapshot": block},
                                     {**member, "$defs": schema["$defs"]})
        self.assertEqual(errors, [])
        errors = state_tool.validate({"directory": "s", "renderers": [], "publishers": [],
                                      "trackerSnapshot": {"argv": ["tool"], "extra": 1}}, {**member, "$defs": schema["$defs"]})
        self.assertTrue(errors)

    def test_an_output_that_cannot_be_written_is_refused_not_a_crash(self) -> None:
        code, refused = run_tool(self.directory, "tracker-snapshot", "--binding", self.binding(),
                                 "--output", str(self.root / "missing" / "out.json"))
        self.assertEqual((code, refused["code"]), (1, "output_invalid"), refused)

    @unittest.skipIf(os.name == "nt", "what a command leaves running is looked for on POSIX only, as _run_bounded says")
    def test_a_child_that_holds_stdout_open_does_not_turn_valid_output_into_a_timeout(self) -> None:
        pid_file = self.root / "holder.pid"

        def stop_holder() -> None:
            # Only the one child this test started, by the pid it wrote.
            with contextlib.suppress(OSError, ValueError):
                os.kill(int(pid_file.read_text()), signal.SIGKILL)

        self.addCleanup(stop_holder)
        argv = [sys.executable, "-B", str(self.tool), "epic-7", "holder"]
        # The preview first: it names the child, and nothing outside the run, and stops nothing.
        seen: list[dict] = []
        with mock.patch.dict(os.environ, {"HOLDER_PID_FILE": str(pid_file)}):
            code, _, _ = state_tool._run_bounded(argv, 20, 4096, preview=seen.append)
        self.assertEqual(code, 0)
        child = int(pid_file.read_text())
        self.assertEqual(seen[0]["selected"], [child], seen)
        time.sleep(0.5)
        os.kill(child, 0)  # the preview stopped nothing
        stop_holder()
        started = time.monotonic()
        code, result = run_tool(self.directory, "tracker-snapshot", "--binding", self.binding("holder", timeoutSeconds=20),
                                "--output", str(self.output), environment={"HOLDER_PID_FILE": str(pid_file)})
        elapsed = time.monotonic() - started
        self.assertEqual(code, 0, result)
        self.assertEqual((result["taken"], result.get("items")), (True, 2), result)
        self.assertLess(elapsed, 15, "reading stopped at the command's exit, not at the child's")
        child = int(pid_file.read_text())
        self.assertIn(f"1 selected [{child}]", result.get("cleanup", ""), result)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("the child the command left holding stdout survived the run")

    def test_a_command_s_long_warnings_on_stderr_do_not_cost_its_snapshot(self) -> None:
        result = self.snap("noisy")
        self.assertEqual((result["taken"], result.get("items")), (True, 2), result)

    def test_output_still_unread_when_the_command_exits_is_read_in_the_drain(self) -> None:
        # Readers that lag behind the exit still collect what was written before it.
        read = state_tool._read_into

        def late(*args: object) -> None:
            time.sleep(0.5)
            read(*args)

        argv = [sys.executable, "-c", "print('x' * 1000)"]
        with mock.patch.object(state_tool, "_read_into", side_effect=late):
            code, out, _ = state_tool._run_bounded(argv, 10, 4096)
        self.assertEqual((code, out.strip()), (0, b"x" * 1000))

    def test_a_tracker_snapshot_may_carry_its_parent_s_state_and_only_a_tracker_one_does(self) -> None:
        items = [{"subject": "epic-7-1", "state": "started"}]
        tracker = {"takenAt": taken(), "source": "tracker", "items": items,
                   "parent": {"subject": "epic-7", "state": "completed"}}
        self.assertIs(state_tool.check_snapshot(tracker, "the snapshot", 900, parent="epic-7"), tracker)
        for snapshot, words in (
            ({**tracker, "source": "forge", "items": [{"subject": "epic-7-1", "state": "open"}]},
             "/parent: only a tracker snapshot carries it"),
            (tracker, "/parent: epic-7 is not the record's tracker parent epic-8"),
            ({**tracker, "parent": {"subject": "epic-7", "state": "merged"}}, "parent"),
            ({**tracker, "parent": {"subject": "epic-7"}}, "parent"),
        ):
            with self.subTest(words=words, snapshot=snapshot.get("parent")):
                with self.assertRaises(state_tool.StateError) as refused:
                    state_tool.check_snapshot(snapshot, "the snapshot", 900, parent="epic-8" if "epic-8" in words else "epic-7")
                self.assertEqual(refused.exception.code, "snapshot_invalid")
                self.assertIn(words, str(refused.exception))

    def test_every_dependency_shape_is_checked_in_a_tracker_snapshot(self) -> None:
        # Imported here: release qualification runs this module beside a candidate's own tests package.
        from tests.dependency_cases import CASES as DEPENDENCY_CASES
        from tests.dependency_cases import CYCLES as DEPENDENCY_CYCLES
        from tests.dependency_cases import ONCE as DEPENDENCY_ONCE

        for name, (parent, items, named) in DEPENDENCY_CASES.items():
            with self.subTest(name):
                snapshot = {"takenAt": taken(), "source": "tracker", "items": [
                    {"subject": reference, "state": "backlog", **({"blockedBy": blockers} if blockers else {})}
                    for reference, blockers in items]}
                if named is None:
                    state_tool.check_snapshot(snapshot, "the snapshot", 900, parent=parent)
                    continue
                with self.assertRaises(state_tool.StateError) as refused:
                    state_tool.check_snapshot(snapshot, "the snapshot", 900, parent=parent)
                self.assertEqual(refused.exception.code, "snapshot_invalid")
                for text in named:
                    self.assertIn(text, str(refused.exception))
                self.assertEqual(str(refused.exception).count("cycle"), DEPENDENCY_CYCLES.get(name, 0))
                if name in DEPENDENCY_ONCE:
                    self.assertEqual(str(refused.exception).count(DEPENDENCY_ONCE[name]), 1)

    def test_a_long_or_dense_graph_is_checked_quickly_in_little_text(self) -> None:
        # Each item is blocked by the next, so the search walks one path through all of them.
        size = 100_000
        chain = [(f"n{index}", [f"n{index + 1}"] if index + 1 < size else []) for index in range(size)]
        started = time.monotonic()
        self.assertEqual(state_tool.dependency_problems(chain, "epic-7"), [])
        # The last item blocked by every other: as many cycles as items, each through all of them.
        dense = chain[:-1] + [(f"n{size - 1}", [f"n{index}" for index in range(size - 1)])]
        problems = state_tool.dependency_problems(dense, "epic-7")
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(len(problems), state_tool.DEPENDENCY_PROBLEM_LIMIT + 1)
        self.assertEqual(problems[-1], f"and {size - 1 - state_tool.DEPENDENCY_PROBLEM_LIMIT} more")
        self.assertLess(len("; ".join(problems)), 2000, "a long cycle is shortened")
        self.assertIn("... ", problems[0])

    def test_the_command_s_snapshot_and_reconcile_check_dependencies_against_the_record_s_parent(self) -> None:
        # The command prints an item its parent blocks; the parent is the record's.
        tool = self.root / "blocked tool.py"
        tool.write_text(textwrap.dedent("""
            import json, sys, datetime
            now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            print(json.dumps({"takenAt": now, "source": "tracker", "items": [
                {"subject": "a", "state": "backlog", "blockedBy": [sys.argv[1]]}]}))
        """), encoding="utf-8")
        binding = self.root / "binding blocked.json"
        binding.write_text(json.dumps({"state": {"directory": "state", "renderers": [], "publishers": [],
                                                 "trackerSnapshot": {"argv": [sys.executable, "-B", str(tool), "{parent}"]}}}),
                           encoding="utf-8")
        code, result = run_tool(self.directory, "tracker-snapshot", "--binding", str(binding), "--output", str(self.output))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["taken"], False, result)
        self.assertIn("not a valid tracker snapshot", result["reason"])
        self.assertIn("epic-7", result["detail"])
        written = self.root / "written.json"
        written.write_text(json.dumps({"takenAt": taken(), "source": "tracker", "items": [
            {"subject": "a", "state": "backlog", "blockedBy": ["epic-7"]}]}), encoding="utf-8")
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", str(written))
        self.assertEqual((code, refused.get("code")), (1, "snapshot_invalid"), refused)
        self.assertIn("epic-7", refused["error"])

    def test_reconcile_refuses_a_cycle_that_runs_across_two_tracker_snapshots(self) -> None:
        first, second = self.root / "first.json", self.root / "second.json"
        first.write_text(json.dumps({"takenAt": taken(), "source": "tracker", "items": [
            {"subject": "a", "state": "backlog", "blockedBy": ["b"]}]}), encoding="utf-8")
        second.write_text(json.dumps({"takenAt": taken(), "source": "tracker", "items": [
            {"subject": "b", "state": "backlog", "blockedBy": ["a"]}]}), encoding="utf-8")
        before = (self.directory / state_tool.STATE_FILE).read_bytes()
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", str(first), "--snapshot", str(second))
        self.assertEqual((code, refused.get("code")), (1, "snapshot_invalid"), refused)
        self.assertIn("a -> b -> a", refused["error"])
        self.assertEqual((self.directory / state_tool.STATE_FILE).read_bytes(), before)

    def test_reconcile_leaves_forge_blockers_out_of_the_combined_check(self) -> None:
        tracker, forge = self.root / "tracker.json", self.root / "forge.json"
        tracker.write_text(json.dumps({"takenAt": taken(), "source": "tracker", "items": [
            {"subject": "a", "state": "backlog"}]}), encoding="utf-8")
        forge.write_text(json.dumps({"takenAt": taken(), "source": "forge", "items": [
            {"subject": "b", "state": "open", "blockedBy": ["b", "a"]}]}), encoding="utf-8")
        code, result = run_tool(self.directory, "reconcile", "--snapshot", str(tracker), "--snapshot", str(forge))
        self.assertEqual(code, 0, result)

    def test_a_forge_snapshot_s_blockers_are_not_checked_since_they_are_ignored(self) -> None:
        snapshot = {"takenAt": taken(), "source": "forge", "items": [
            {"subject": "a", "state": "open", "blockedBy": ["a", "a"]}]}
        state_tool.check_snapshot(snapshot, "the snapshot", 900, parent="epic-7")

    def test_a_command_that_runs_too_long_is_stopped(self) -> None:
        result = self.snap("sleep", timeoutSeconds=1)
        self.assertEqual(result["taken"], False)
        self.assertIn("timed out after 1s", result["reason"])
        self.assertFalse(result["fallback"], "there was no earlier snapshot to fall back to")

    def test_a_stale_fallback_is_refused_by_reconcile(self) -> None:
        self.snap()
        stale = json.loads(self.output.read_text(encoding="utf-8"))
        stale["takenAt"] = taken(3600)
        self.output.write_text(json.dumps(stale), encoding="utf-8")
        self.snap("fail")
        code, refused = run_tool(self.directory, "reconcile", "--snapshot", str(self.output))
        self.assertEqual((code, refused["code"]), (1, "snapshot_stale"))

    def test_the_command_needs_a_parent_when_it_names_one(self) -> None:
        fresh = self.root / "unparented"
        state_tool.init(fresh, "No parent")
        code, result = run_tool(fresh, "tracker-snapshot", "--binding", self.binding(), "--output", str(self.output))
        self.assertEqual((code, result["taken"]), (0, False))
        self.assertIn("meta.trackerParent", result["reason"])

    def test_without_a_command_nothing_runs_and_a_malformed_block_is_refused(self) -> None:
        plain = self.root / "plain.json"
        plain.write_text(json.dumps({"state": {"directory": "state", "renderers": [], "publishers": []}}),
                         encoding="utf-8")
        code, result = run_tool(self.directory, "tracker-snapshot", "--binding", str(plain), "--output", str(self.output))
        self.assertEqual((code, result["taken"]), (0, False))
        self.assertFalse(self.output.exists())
        for block in ({"argv": []}, {"argv": ["x"], "timeoutSeconds": 0}, {"argv": ["x"], "retries": 2},
                      {"argv": "x"}):
            with self.subTest(block=block):
                path = self.root / "bad.json"
                path.write_text(json.dumps({"state": {"directory": "state", "renderers": [], "publishers": [],
                                                      "trackerSnapshot": block}}), encoding="utf-8")
                code, refused = run_tool(self.directory, "tracker-snapshot", "--binding", str(path),
                                         "--output", str(self.output))
                self.assertEqual((code, refused["code"]), (1, "binding_invalid"))

    def test_the_binding_s_max_age_applies_to_the_command_s_snapshot(self) -> None:
        path = self.root / "strict.json"
        path.write_text(json.dumps({"state": {"directory": "state", "renderers": [], "publishers": [],
                                              "snapshotMaxAgeSeconds": 60, "trackerSnapshot": {
                                                  "argv": [sys.executable, "-B", str(self.tool), "{parent}", "old"]}}}),
                        encoding="utf-8")
        code, result = run_tool(self.directory, "tracker-snapshot", "--binding", str(path), "--output", str(self.output))
        self.assertEqual(result["taken"], False)
        self.assertIn("more than 60s", result["detail"])
        code, result = run_tool(self.directory, "tracker-snapshot", "--binding", str(path), "--output", str(self.output),
                                "--max-age", "86400")
        self.assertEqual(result["taken"], True)


SOURCE_TOOL = """
import datetime, json, sys, time
source, world = sys.argv[1], sys.argv[2]
with open(world + ".runs", "a") as runs:
    runs.write("run\\n")
seen = json.load(open(world))
if seen["mode"] == "fail":
    print("refused", file=sys.stderr); sys.exit(3)
if seen["mode"] == "sleep":
    time.sleep(5)
taken = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
print(json.dumps({"takenAt": taken, "source": seen.get("source", source), "items": seen["items"]}))
"""


class SourceRefreshTests(unittest.TestCase):
    """serve's refresh: each declared snapshot command runs, what it took is reconciled, and each source's status is kept."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "state"
        state_tool.init(self.directory, "Epic underway")
        self.tool = self.root / "source tool.py"
        self.tool.write_text(SOURCE_TOOL, encoding="utf-8")
        self.worlds = {source: self.root / f"{source} world.json" for source in ("tracker", "forge")}
        for source in self.worlds:
            self.world(source, [])

    def world(self, which: str, items: list[dict], mode: str = "ok", **extra: object) -> None:
        self.worlds[which].write_text(json.dumps({"mode": mode, "items": items, **extra}), encoding="utf-8")

    def runs(self, source: str) -> int:
        path = Path(str(self.worlds[source]) + ".runs")
        return len(path.read_text().splitlines()) if path.exists() else 0

    def member(self, *sources: str, **extra: object) -> dict:
        member = {"directory": "state", "renderers": [], "publishers": [], **extra}
        for source in sources:
            member[f"{source}Snapshot"] = {"argv": [sys.executable, "-B", str(self.tool), source, str(self.worlds[source])],
                                           "timeoutSeconds": 2}
        return member

    def refresh(self, *sources: str, **extra: object) -> dict:
        return state_tool.refresh_sources(self.directory, self.member(*sources, **extra))

    def sources(self) -> dict | None:
        return state_tool.read_state(self.directory)["meta"].get("sources")

    def footprint(self) -> tuple:
        return state_tool.read_state(self.directory)["version"], len(state_tool.read_journal(self.directory))

    def notes(self, source: str) -> list[str]:
        return [entry["summary"] for entry in state_tool.read_journal(self.directory)
                if entry["summary"].startswith(f"{source}-snapshot:")]

    def queue(self, *items: dict) -> None:
        version = state_tool.read_state(self.directory)["version"]
        state_tool.patch(self.directory, writer="lead", version=version, changes={"queue": list(items)})

    def test_both_sources_are_reconciled_and_an_unchanged_refresh_writes_nothing(self) -> None:
        self.queue(queue_item("work-a"))
        self.world("tracker", [{"subject": "work-t", "title": "Ticket", "state": "started"}])
        self.world("forge", [{"subject": "work-a", "state": "merged", "head": HEAD_A}])
        self.refresh("tracker", "forge")
        state = state_tool.read_state(self.directory)
        self.assertEqual([entry["subject"] for entry in state["planned"]], ["work-t"])
        self.assertEqual(next(item for item in state["queue"] if item["subject"] == "work-a")["stage"], "merged")
        self.assertEqual({source: entry["status"] for source, entry in self.sources().items()},
                         {"tracker": "observed", "forge": "observed"})
        before = self.footprint()
        self.refresh("tracker", "forge")
        self.assertEqual(self.footprint(), before, "nothing changed: no record write and no journal entry")
        self.assertEqual((self.runs("tracker"), self.runs("forge")), (2, 2))

    def test_a_failing_source_is_noted_once_keeps_its_last_snapshot_and_recovers(self) -> None:
        self.world("tracker", [{"subject": "work-t", "title": "Ticket", "state": "backlog"}])
        self.refresh("tracker", "forge")
        kept = json.loads((self.directory / "snapshots" / "tracker.json").read_text(encoding="utf-8"))
        self.world("tracker", [], mode="fail")
        self.world("forge", [{"subject": "work-t", "state": "open"}])
        self.refresh("tracker", "forge")
        tracker = self.sources()["tracker"]
        self.assertEqual((tracker["status"], tracker["lastReadAt"]), ("stale", kept["takenAt"]))
        self.assertIn("exited with 3", tracker["reason"])
        self.assertEqual(self.sources()["forge"]["status"], "observed", "the other source is still read")
        self.assertEqual(json.loads((self.directory / "snapshots" / "tracker.json").read_text(encoding="utf-8")), kept)
        self.assertEqual(self.notes("tracker"), ["tracker-snapshot: the command exited with 3; the last snapshot stands"])
        before = self.footprint()
        self.refresh("tracker", "forge")
        self.assertEqual(self.footprint(), before, "the same outage is recorded once")
        self.world("tracker", [{"subject": "work-t", "title": "Ticket", "state": "backlog"}])
        self.refresh("tracker", "forge")
        self.assertEqual(self.sources()["tracker"]["status"], "observed")
        self.assertNotIn("lastReadAt", self.sources()["tracker"])
        self.assertEqual(self.notes("tracker")[-1], "tracker-snapshot: taken again")

    def test_a_source_that_never_answered_was_never_read(self) -> None:
        self.world("forge", [], mode="sleep")
        self.refresh("forge")
        forge = self.sources()["forge"]
        self.assertEqual((forge["status"], forge["lastReadAt"]), ("stale", None))
        self.assertIn("timed out", forge["reason"])

    def test_only_declared_sources_are_read_and_the_rest_are_reported_as_not_observed(self) -> None:
        for declared, expected in ((("tracker",), {"tracker": "observed", "forge": "not_observed"}),
                                   (("forge",), {"tracker": "not_observed", "forge": "observed"}),
                                   ((), {"tracker": "not_observed", "forge": "not_observed"})):
            with self.subTest(declared=declared):
                self.refresh(*declared)
                self.assertEqual({source: entry["status"] for source, entry in self.sources().items()}, expected)
                before = self.footprint()
                self.refresh(*declared)
                self.assertEqual(self.footprint(), before)

    def test_a_snapshot_from_the_wrong_source_is_refused_as_that_source(self) -> None:
        self.world("forge", [{"subject": "work-t", "state": "started"}], source="tracker")
        self.refresh("forge")
        self.assertEqual(self.sources()["forge"]["status"], "stale")
        self.assertEqual(self.notes("forge"), ["forge-snapshot: the command's snapshot is not a valid forge snapshot; "
                                               "the last snapshot stands"])

    def test_a_snapshot_reconcile_refuses_marks_its_source_stale_rather_than_failing(self) -> None:
        self.world("tracker", [{"subject": "work-t", "state": "started"}])
        refused = state_tool.StateError("version_stale", "the record kept changing")
        with mock.patch.object(state_tool, "reconcile", side_effect=refused):
            result = self.refresh("tracker", "forge")
        self.assertTrue(result["ok"])
        for source in ("tracker", "forge"):
            self.assertEqual(self.sources()[source]["status"], "stale")
            self.assertIn("the record kept changing", self.sources()[source]["reason"])

    def test_the_forge_snapshot_command_line_mirrors_the_tracker_one(self) -> None:
        self.world("forge", [{"subject": "work-a", "state": "open"}])
        binding = self.root / "binding.json"
        binding.write_text(json.dumps({"state": self.member("forge")}), encoding="utf-8")
        output = self.root / "forge.json"
        code, result = run_tool(self.directory, "forge-snapshot", "--binding", str(binding), "--output", str(output))
        self.assertEqual((code, result["taken"]), (0, True), result)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["source"], "forge")
        for block in ({"argv": []}, {"argv": ["x"], "timeoutSeconds": 0}, {"argv": ["x"], "retries": 2}):
            with self.subTest(block=block):
                binding.write_text(json.dumps({"state": {"directory": "state", "renderers": [], "publishers": [],
                                                         "forgeSnapshot": block}}), encoding="utf-8")
                code, refused = run_tool(self.directory, "forge-snapshot", "--binding", str(binding),
                                         "--output", str(output))
                self.assertEqual((code, refused["code"]), (1, "binding_invalid"))

    def test_the_binding_declares_the_forge_command_and_the_interval(self) -> None:
        schema = state_tool.load_schema("orchestration-binding/1")
        member = {**schema["properties"]["state"], "$defs": schema["$defs"]}
        good = {"directory": "s", "renderers": [], "publishers": [], "refreshSeconds": 0,
                "forgeSnapshot": {"argv": ["tool"], "timeoutSeconds": 30}}
        self.assertEqual(state_tool.validate(good, member), [])
        self.assertTrue(state_tool.validate({**good, "refreshSeconds": -1}, member))
        self.assertEqual(state_tool.refresh_interval({}), 180)
        self.assertEqual(state_tool.refresh_interval({"refreshSeconds": 0}), 0)
        for bad in (-1, True, "60", 1.5):
            with self.subTest(bad=bad):
                path = self.root / "bad.json"
                path.write_text(json.dumps({"state": {"directory": "state", "renderers": [], "publishers": [],
                                                      "refreshSeconds": bad}}), encoding="utf-8")
                with self.assertRaises(state_tool.StateError) as caught:
                    state_tool.binding_state(path)
                self.assertEqual(caught.exception.code, "binding_invalid")

    def test_only_the_runner_writes_the_sources(self) -> None:
        version = state_tool.read_state(self.directory)["version"]
        with self.assertRaises(state_tool.StateError) as caught:
            state_tool.patch(self.directory, writer="lead", version=version,
                             changes={"meta": {"sources": {"tracker": {"status": "not_observed", "since": taken(0)}}}})
        self.assertEqual(caught.exception.code, "field_not_owned")

    def test_what_reconcile_leaves_for_the_lead_reaches_the_record_and_changes_only_when_it_does(self) -> None:
        self.queue(queue_item("work-a"))
        version = state_tool.read_state(self.directory)["version"]
        state_tool.patch(self.directory, writer="lead", version=version, changes={"planned": [planned("work-p")]})
        self.world("tracker", [{"subject": "work-p", "state": "canceled"}])
        self.world("forge", [{"subject": "work-a", "state": "closed"}])
        self.refresh("tracker", "forge")
        reports = [{"subject": "work-a", "note": "its change request closed without merging"},
                   {"subject": "work-p", "note": "canceled in the tracker but still in review in the record"}]
        self.assertEqual(state_tool.read_state(self.directory)["meta"]["sourceReports"], reports)
        self.assertEqual(state_tool.show(self.directory)["status"]["text"],
                         "next: work-a · for the lead: work-a: its change request closed without merging, then 1 more")
        before = self.footprint()
        self.refresh("tracker", "forge")
        self.assertEqual(self.footprint(), before, "the same reports are not written again")
        # The same reports in another order are the same reports.
        self.queue(queue_item("work-a"), queue_item("work-b", order=2))
        self.world("forge", [{"subject": "work-a", "state": "closed"}, {"subject": "work-b", "state": "closed"}])
        self.refresh("tracker", "forge")
        before = self.footprint()
        self.world("forge", [{"subject": "work-b", "state": "closed"}, {"subject": "work-a", "state": "closed"}])
        self.refresh("tracker", "forge")
        self.assertEqual(self.footprint(), before)
        self.world("tracker", [])
        self.world("forge", [])
        self.refresh("tracker", "forge")
        self.assertEqual(state_tool.read_state(self.directory)["meta"]["sourceReports"], [])

    def test_a_failed_source_s_last_fresh_read_still_counts_for_the_reports(self) -> None:
        self.queue(queue_item("work-a"))
        self.world("forge", [{"subject": "work-a", "state": "closed"}])
        self.refresh("tracker", "forge")
        self.world("forge", [], mode="fail")
        self.refresh("tracker", "forge")
        self.assertEqual([report["subject"] for report in state_tool.read_state(self.directory)["meta"]["sourceReports"]],
                         ["work-a"], "a forge outage does not clear what the forge last said")
        self.world("tracker", [], mode="fail")
        self.refresh("tracker", "forge")
        self.assertEqual([report["subject"] for report in state_tool.read_state(self.directory)["meta"]["sourceReports"]],
                         ["work-a"], "nothing was reconciled, so the reports stand")

    def test_a_block_the_tool_cannot_run_marks_only_its_own_source_stale(self) -> None:
        self.world("tracker", [{"subject": "work-t", "title": "Ticket", "state": "started"}])
        member = self.member("tracker")
        member["forgeSnapshot"] = {"argv": ["tool"], "timeoutSeconds": 900}
        result = state_tool.refresh_sources(self.directory, member)
        self.assertTrue(result["ok"])
        self.assertEqual(self.sources()["tracker"]["status"], "observed")
        self.assertEqual([entry["subject"] for entry in state_tool.read_state(self.directory)["planned"]], ["work-t"])
        self.assertEqual(self.sources()["forge"]["status"], "stale")
        self.assertIn("forgeSnapshot", self.sources()["forge"]["reason"])

    def test_a_block_that_turns_bad_leaves_its_source_s_last_read_counting(self) -> None:
        self.queue(queue_item("work-a"))
        self.world("forge", [{"subject": "work-a", "state": "closed"}])
        self.refresh("tracker", "forge")
        member = self.member("tracker", "forge")
        member["forgeSnapshot"]["timeoutSeconds"] = 900
        state_tool.refresh_sources(self.directory, member)
        self.assertEqual([report["subject"] for report in state_tool.read_state(self.directory)["meta"]["sourceReports"]],
                         ["work-a"])

    def test_a_command_s_error_output_is_kept_only_as_a_tail(self) -> None:
        import tracemalloc
        tracemalloc.start()
        self.addCleanup(tracemalloc.stop)
        code, _, detail = state_tool._run_bounded(
            [sys.executable, "-c", "import sys\nfor _ in range(800): sys.stderr.write('e' * 65536)\nsys.exit(4)"], 30, 1024)
        _, peak = tracemalloc.get_traced_memory()
        self.assertEqual(code, 4)
        self.assertLessEqual(len(detail), 300)
        self.assertLess(peak, 8 * 1024 * 1024, "50 MiB on stderr is never held whole")

    def test_a_reconcile_that_keeps_refusing_keeps_the_last_applied_read_and_writes_once(self) -> None:
        self.refresh("tracker")
        applied = json.loads((self.directory / "snapshots" / "tracker.json").read_text(encoding="utf-8"))["takenAt"]
        refused = state_tool.StateError("version_stale", "the record kept changing")
        with mock.patch.object(state_tool, "reconcile", side_effect=refused):
            time.sleep(1.1)
            self.refresh("tracker")
            self.assertEqual(self.sources()["tracker"]["lastReadAt"], applied, "a refused read was never applied")
            before = self.footprint()
            time.sleep(1.1)
            self.refresh("tracker")
            self.assertEqual(self.footprint(), before)

    def test_an_unchanged_read_after_an_outage_ended_writes_nothing(self) -> None:
        self.world("tracker", [], mode="fail")
        self.refresh("tracker")
        self.world("tracker", [])
        self.refresh("tracker")
        before = self.footprint()
        self.refresh("tracker")
        self.assertEqual(self.footprint(), before)

    def test_the_interval_has_a_ceiling(self) -> None:
        for bad in (86401, 10 ** 400):
            with self.subTest(bad=bad):
                with self.assertRaises(state_tool.StateError) as caught:
                    state_tool.refresh_interval({"refreshSeconds": bad})
                self.assertEqual(caught.exception.code, "binding_invalid")
        self.assertEqual(state_tool.refresh_interval({"refreshSeconds": 86400}), 86400)

    def test_a_command_that_never_stops_printing_is_stopped_at_the_limit(self) -> None:
        import tracemalloc
        tracemalloc.start()
        self.addCleanup(tracemalloc.stop)
        started = time.monotonic()
        code, out, reason = state_tool._run_bounded(
            # Far past the limit, yet finite: a reader that ignored the limit could not take the machine with it.
            [sys.executable, "-c", "import sys\nfor _ in range(4096): sys.stdout.write('x' * 65536)"], 30, 1_000_000)
        self.assertEqual((code, out), (None, b""))
        self.assertIn("printed more than 1000000 bytes", reason)
        self.assertLess(time.monotonic() - started, 15, "stopped at the limit, not at the timeout")
        self.assertLess(tracemalloc.get_traced_memory()[1], 8 * 1024 * 1024, "never held past the limit")

    def test_the_status_says_when_a_stale_source_was_last_read(self) -> None:
        record = {"meta": {"headline": "Epic", "checking": {"now": None, "next": []}, "sources": {
            "tracker": {"status": "stale", "since": "2026-10-21T11:00:00Z", "lastReadAt": "2026-10-21T10:57:00Z",
                        "reason": "the command exited with 3"},
            "forge": {"status": "stale", "since": "2026-10-21T11:00:00Z", "lastReadAt": None, "reason": "timed out"},
        }}, "queue": [], "lanes": [], "planned": []}
        self.assertEqual(state_tool.delivery_status(record, [], at="2026-10-21T12:00:00Z")["text"],
                         "tracker last read at 2026-10-21T10:57:00Z · forge never read")


class QueueEstimateTests(unittest.TestCase):
    """Waiting candidates get a position and an estimated start, folded from the record's check history."""

    NOW = "2026-10-21T10:00:00Z"

    def state(self, *items: dict, now: dict | None = None, runs: list[int] | None = None) -> dict:
        meta = {"checking": {"now": now, "next": []}}
        if runs is not None:
            meta["checkHistory"] = {"checks": {}, "runs": runs}
        return {"meta": meta, "queue": list(items)}

    def test_a_batch_ahead_counts_once_and_each_waiting_candidate_one_run_more(self) -> None:
        batch = {"subject": "work-a", "title": "Batch of 2: work-a, work-b", "startedAt": "2026-10-21T09:58:20Z", "steps": []}
        state = self.state(
            queue_item("work-a", stage="checking"), queue_item("work-b", order=2, stage="checking"),
            queue_item("work-c", order=3), queue_item("work-d", order=4), queue_item("work-e", order=5),
            # Only the last five runs count: the oldest, 50000s, is left out of the median.
            now=batch, runs=[50000, 500, 600, 600, 700, 9000],
        )
        estimates = state_tool.queue_estimates(state, at=self.NOW)
        # The median run is 600s; the batch has run 100s, so 500s remain, once for both of its members.
        self.assertEqual(estimates, {
            "work-c": {"position": 1, "expectedStart": "2026-10-21T10:08:20Z"},
            "work-d": {"position": 2, "expectedStart": "2026-10-21T10:18:20Z"},
            "work-e": {"position": 3, "expectedStart": "2026-10-21T10:28:20Z"},
        })

    def test_without_history_waiting_candidates_get_a_position_and_no_guess(self) -> None:
        state = self.state(queue_item("work-c"), queue_item("work-d", order=2))
        self.assertEqual(state_tool.queue_estimates(state, at=self.NOW), {
            "work-c": {"position": 1, "expectedStart": None},
            "work-d": {"position": 2, "expectedStart": None},
        })

    def test_an_overrunning_check_leaves_nothing_remaining_rather_than_negative(self) -> None:
        running = {"subject": "work-a", "title": "A", "startedAt": "2026-10-21T09:00:00Z", "steps": []}
        state = self.state(queue_item("work-a", stage="checking"), queue_item("work-c", order=2),
                           now=running, runs=[600] * 5)
        self.assertEqual(state_tool.queue_estimates(state, at=self.NOW),
                         {"work-c": {"position": 1, "expectedStart": self.NOW}})

    def test_only_queued_candidates_wait_and_the_order_decides_the_position(self) -> None:
        state = self.state(queue_item("work-z", order=3), queue_item("work-m", order=1, stage="merged"),
                           queue_item("work-y", order=2, stage="stopped"), queue_item("work-x", order=4), runs=[60] * 5)
        self.assertEqual(state_tool.queue_estimates(state, at=self.NOW), {
            "work-z": {"position": 1, "expectedStart": self.NOW},
            "work-x": {"position": 2, "expectedStart": "2026-10-21T10:01:00Z"},
        })

    def test_a_hand_off_waiting_for_the_lead_does_not_wait_in_the_queue(self) -> None:
        state = self.state(queue_item("work-c", pending=True), queue_item("work-d", order=2), runs=[60] * 5)
        self.assertEqual(state_tool.queue_estimates(state, at=self.NOW),
                         {"work-d": {"position": 1, "expectedStart": self.NOW}})

    def test_an_even_count_takes_the_mean_of_the_middle_two(self) -> None:
        state = self.state(queue_item("work-c"), queue_item("work-d", order=2), runs=[100, 200, 300, 400])
        self.assertEqual(state_tool.queue_estimates(state, at=self.NOW)["work-d"]["expectedStart"], "2026-10-21T10:04:10Z")

    def test_a_start_in_the_future_never_adds_more_than_one_run(self) -> None:
        running = {"subject": "work-a", "title": "A", "startedAt": "2026-10-21T11:00:00Z", "steps": []}
        state = self.state(queue_item("work-a", stage="checking"), queue_item("work-c", order=2),
                           now=running, runs=[600] * 5)
        self.assertEqual(state_tool.queue_estimates(state, at=self.NOW)["work-c"]["expectedStart"], "2026-10-21T10:10:00Z")

    def test_an_absurd_history_leaves_the_start_unknown_rather_than_failing(self) -> None:
        state = self.state(queue_item("work-c"), queue_item("work-d", order=2), runs=[10 ** 18] * 5)
        self.assertEqual(state_tool.queue_estimates(state, at=self.NOW)["work-d"],
                         {"position": 2, "expectedStart": None})

    def test_show_includes_the_estimates(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name) / "state"
        state_tool.init(directory, "Epic underway")
        version = state_tool.show(directory)["state"]["version"]
        state_tool.patch(directory, writer="lead", version=version, changes={"queue": [queue_item("work-c")]})
        shown = state_tool.show(directory)
        self.assertEqual(shown["estimates"], {"work-c": {"position": 1, "expectedStart": None}})


def lane(lane_id: str, **fields: object) -> dict:
    return {"id": lane_id, "title": f"Lane {lane_id}", "focus": "", "state": "working", "waitingOn": None,
            "since": "2026-10-21T09:00:00Z", **fields}


def planned(subject: str, lane_id: str | None = "lane-1") -> dict:
    return {"subject": subject, "title": f"Work {subject}", "lane": lane_id, "stage": "review"}


class HandoffNamedChecksTests(unittest.TestCase):
    """A hand-off may name the checks its lane ran, each once: kept on the row and in the entry, and dropped by a
    later hand-off that names none."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "state"
        state_tool.init(self.directory, "Epic underway")
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version, changes={
            "lanes": [lane("lane-1")],
            "planned": [{"subject": "work-a", "title": "Work A", "lane": "lane-1", "stage": "building"}]})

    def hand(self, *extra: str, head: str = "b" * 40) -> tuple[int, dict]:
        return run_tool(self.directory, "handoff", "--lane", "lane-1", "--subject", "work-a", "--head", head,
                        "--change-request", "cr 1", "--rounds", "1", *extra)

    def row(self) -> dict:
        return next(item for item in state_tool.show(self.directory)["state"]["queue"] if item["subject"] == "work-a")

    def test_named_checks_are_kept_each_once_and_dropped_by_a_hand_off_naming_none(self) -> None:
        before = state_tool.show(self.directory)
        for bad in ("a,a", "", " ", "a,,b"):
            with self.subTest(checks=bad):
                code, result = self.hand("--checks", bad)
                self.assertEqual((code, result["code"]), (1, "handoff_invalid"), result)
                self.assertIn("--checks names each check the lane ran once", result["error"])
        self.assertEqual(state_tool.show(self.directory), before, "a refused hand-off writes nothing")
        code, result = self.hand("--checks", "tests.test_two, tests.test_one")
        self.assertEqual(code, 0, result)
        self.assertEqual(self.row()["namedChecks"], ["tests.test_two", "tests.test_one"])
        handed = [e for e in state_tool.read_journal(self.directory) if e["kind"] == "candidate.handed"]
        self.assertIn("; the lane ran tests.test_two, tests.test_one", handed[-1]["summary"])
        # Stopped and handed off again with none named: the earlier names go, so no acceptance proposes stale checks.
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="runner", version=version, changes={},
                         selector="queue[subject=work-a]", assignments={"/stage": "stopped"})
        code, result = self.hand(head="c" * 40)
        self.assertEqual(code, 0, result)
        self.assertNotIn("namedChecks", self.row())
        handed = [e for e in state_tool.read_journal(self.directory) if e["kind"] == "candidate.handed"]
        self.assertNotIn("the lane ran", handed[-1]["summary"])


class NoChangeHandoffTests(unittest.TestCase):
    """A story whose criteria already hold hands off with no change: it never enters the queue, and once the lead
    accepts it the record holds it settled, never removed."""

    BASE = "a" * 40

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "state"
        state_tool.init(self.directory, "Epic underway")
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version, changes={
            "meta": {"handoffsNeedAcceptance": True},
            "lanes": [lane("lane-1"), lane("lane-2")],
            "planned": [{"subject": "done-already", "title": "Already done", "lane": "lane-1", "stage": "building"},
                        {"subject": "other", "title": "Other", "lane": "lane-2", "stage": "planned"}]})

    def hand(self, *extra: str, subject: str = "done-already", lane_id: str = "lane-1") -> tuple[int, dict]:
        return run_tool(self.directory, "handoff", "--no-change", "--lane", lane_id, "--subject", subject,
                        "--base-head", self.BASE, "--evidence-text", "test_x passes at the base: done-already-1", *extra)

    def entry(self, subject: str = "done-already") -> dict:
        return next(item for item in state_tool.show(self.directory)["state"]["planned"] if item["subject"] == subject)

    def test_a_no_change_hand_off_waits_for_the_lead_then_settles_and_never_queues(self) -> None:
        code, result = self.hand()
        self.assertEqual(code, 0, result)
        entry = self.entry()
        self.assertEqual(entry["stage"], "review")
        self.assertEqual({k: entry["noChange"][k] for k in ("evidence", "baseHead")},
                         {"evidence": "test_x passes at the base: done-already-1", "baseHead": self.BASE})
        self.assertNotIn("settledAt", entry["noChange"])
        document = state_tool.show(self.directory)
        self.assertEqual(document["state"]["queue"], [])
        self.assertEqual(next(item for item in document["state"]["lanes"] if item["id"] == "lane-1")["state"], "handed")
        handed = [e for e in document["journal"] if e["kind"] == "candidate.handed"]
        self.assertEqual(len(handed), 1)
        self.assertIn("with no change needed", handed[0]["summary"])
        self.assertIn("waiting for the lead", handed[0]["summary"])
        code, result = run_tool(self.directory, "handoff", "--accept", "done-already")
        self.assertEqual(code, 0, result)
        entry = self.entry()
        self.assertEqual(entry["stage"], "settled")
        self.assertIn("settledAt", entry["noChange"])
        document = state_tool.show(self.directory)
        self.assertEqual(document["state"]["queue"], [], "a settled story entered the queue")
        self.assertIn("done-already", [item["subject"] for item in document["state"]["planned"]], "settled, not removed")
        self.assertEqual(next(item for item in document["state"]["lanes"] if item["id"] == "lane-1")["state"], "idle",
                         "a lane with nothing else open rests once its story settles")
        self.assertIn("settled with no change needed", document["journal"][-1]["summary"])
        # Settled once: neither handed off nor accepted again.
        code, result = self.hand()
        self.assertEqual((code, result.get("code")), (1, "handoff_invalid"), result)
        code, result = run_tool(self.directory, "handoff", "--accept", "done-already")
        self.assertNotEqual(code, 0, result)

    def test_a_lane_still_holding_open_work_doesnt_rest_when_its_no_change_story_settles(self) -> None:
        for other, where in (({"subject": "next-one", "title": "Next", "lane": "lane-1", "stage": "planned"}, "planned"),
                             (queue_item("next-one", lane="lane-1"), "queue")):
            with self.subTest(where=where):
                self.setUp()
                document = state_tool.show(self.directory)["state"]
                state_tool.patch(self.directory, writer="lead", version=document["version"],
                                 changes={where: [*document.get(where, []), other]})
                self.assertEqual(self.hand()[0], 0)
                code, result = run_tool(self.directory, "handoff", "--accept", "done-already")
                self.assertEqual(code, 0, result)
                self.assertEqual(self.entry()["stage"], "settled")
                lanes = state_tool.show(self.directory)["state"]["lanes"]
                self.assertNotEqual(next(item for item in lanes if item["id"] == "lane-1")["state"], "idle",
                                    f"a lane still holding {where} work rested")

    def test_the_lead_rejecting_a_no_change_hand_off_sends_it_back_building_without_its_evidence(self) -> None:
        self.assertEqual(self.hand()[0], 0)
        code, result = run_tool(self.directory, "handoff", "--reject", "done-already", "--reason", "one criterion fails")
        self.assertEqual(code, 0, result)
        entry = self.entry()
        self.assertEqual(entry["stage"], "building")
        self.assertNotIn("noChange", entry)
        lanes = state_tool.show(self.directory)["state"]["lanes"]
        self.assertEqual(next(item for item in lanes if item["id"] == "lane-1")["state"], "working")

    def test_without_the_leads_acceptance_required_it_settles_at_once(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version, changes={"meta": {"handoffsNeedAcceptance": False}})
        code, result = self.hand()
        self.assertEqual(code, 0, result)
        self.assertEqual(self.entry()["stage"], "settled")
        self.assertIn("; settled", state_tool.show(self.directory)["journal"][-1]["summary"])

    def test_a_no_change_hand_off_it_cant_take_is_refused_and_writes_nothing(self) -> None:
        before = state_tool.show(self.directory)["state"]
        for argv, words in (
                (["handoff", "--no-change", "--lane", "lane-1", "--subject", "done-already", "--base-head", self.BASE],
                 "--evidence-text"),
                (["handoff", "--no-change", "--lane", "lane-1", "--subject", "done-already", "--base-head", "abc",
                  "--evidence-text", "x"], "--base-head is the full commit"),
                (["handoff", "--no-change", "--lane", "lane-1", "--subject", "done-already", "--base-head", self.BASE,
                  "--evidence-text", "   "], "1 to 4000 characters"),
                (["handoff", "--no-change", "--lane", "lane-1", "--subject", "done-already", "--base-head", self.BASE,
                  "--evidence-text", "x" * 4001], "1 to 4000 characters"),
                (["handoff", "--no-change", "--lane", "lane-1", "--subject", "done-already", "--base-head", self.BASE,
                  "--evidence-text", "x", "--head", self.BASE], "no --head, --change-request or --evidence"),
                (["handoff", "--no-change", "--lane", "lane-1", "--subject", "done-already", "--base-head", self.BASE,
                  "--evidence-text", "x", "--change-request", "https://forge.test/pr/1"], "no --head, --change-request or --evidence"),
                (["handoff", "--no-change", "--lane", "lane-1", "--subject", "done-already", "--base-head", self.BASE,
                  "--evidence-text", "x", "--evidence", "{}"], "no --head, --change-request or --evidence"),
                (["handoff", "--no-change", "--lane", "lane-1", "--subject", "other", "--base-head", self.BASE,
                  "--evidence-text", "x"], "planned in lane-2, not lane-1"),
                (["handoff", "--no-change", "--lane", "lane-1", "--subject", "nobody", "--base-head", self.BASE,
                  "--evidence-text", "x"], "is not planned")):
            with self.subTest(words=words):
                code, result = run_tool(self.directory, *argv)
                self.assertEqual(code, 1, result)
                self.assertIn(words, result["error"])
                self.assertEqual(state_tool.show(self.directory)["state"], before)

    def test_reconcile_leaves_a_settled_story_alone_whatever_the_tracker_says(self) -> None:
        self.hand()
        run_tool(self.directory, "handoff", "--accept", "done-already")
        before = self.entry()
        for word in ("completed", "canceled", "backlog", "started"):
            with self.subTest(word=word):
                snapshot = Path(self.temporary.name) / "tracker.json"
                snapshot.write_text(json.dumps({"takenAt": taken(), "source": "tracker", "items": [
                    {"subject": "done-already", "state": word}, {"subject": "other", "state": "backlog"}]}))
                code, result = run_tool(self.directory, "reconcile", "--snapshot", str(snapshot))
                self.assertEqual(code, 0, result)
                self.assertFalse([r for r in result.get("reports", []) if r.get("subject") == "done-already"], result)
                self.assertEqual(self.entry(), before)


class RunnerHoldTests(unittest.TestCase):
    """The runner may hold serving when its setup breaks; only the lead changes or clears a hold."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "state"
        state_tool.init(self.directory, "Epic underway")

    def write(self, writer: str, hold: object) -> None:
        version = state_tool.read_state(self.directory)["version"]
        state_tool.patch(self.directory, writer=writer, version=version, assignments={"/meta/hold": hold})

    def test_the_runner_sets_a_hold_only_where_none_is_set(self) -> None:
        held = {"reason": "serve cannot use its setup", "since": "2026-10-21T10:00:00Z"}
        self.write("runner", held)
        self.assertEqual(state_tool.read_state(self.directory)["meta"]["hold"], held)
        for name, hold in (("change it", {**held, "reason": "something else"}), ("clear it", None)):
            with self.subTest(name):
                with self.assertRaises(state_tool.StateError) as caught:
                    self.write("runner", hold)
                self.assertEqual(caught.exception.code, "field_not_owned")
        self.write("lead", None)
        self.assertIsNone(state_tool.read_state(self.directory)["meta"]["hold"])


class LaneWriterTests(unittest.TestCase):
    """A lane records its own hand-off, rounds and focus through the tool, and nothing that is not its own."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "state"
        state_tool.init(self.directory, "Epic underway")
        self.lead({"lanes": [lane("lane-1"), lane("lane-2")],
                   "planned": [planned("work-a"), planned("work-b", "lane-2")],
                   "queue": [queue_item("work-q", lane="lane-1", order=1)]})

    def version(self) -> int:
        return state_tool.read_state(self.directory)["version"]

    def lead(self, changes: dict, selector: str | None = None) -> dict:
        return state_tool.patch(self.directory, writer="lead", version=self.version(), changes=changes, selector=selector)

    def as_lane(self, lane_id: str, changes: object = None, selector: str | None = None,
                assignments: object = None) -> dict:
        return state_tool.patch(self.directory, writer="lane", lane=lane_id, version=self.version(), changes=changes,
                                selector=selector, assignments=assignments)

    def refused(self, call) -> str:
        before = (self.directory / state_tool.STATE_FILE).read_bytes()
        with self.assertRaises(state_tool.StateError) as caught:
            call()
        self.assertEqual((self.directory / state_tool.STATE_FILE).read_bytes(), before)
        return caught.exception.code

    def test_only_the_lead_can_write_lane_ownership(self) -> None:
        ownership = {"lane-1": ["src/*"], "lane-2": []}
        self.assertEqual(self.lead({"laneOwnership": ownership})["laneOwnership"], ownership)
        change = {"laneOwnership": {"lane-1": ["tools/*"]}}
        self.assertEqual(self.refused(lambda: self.as_lane("lane-1", change)), "writer_not_owned")
        self.assertEqual(self.refused(lambda: state_tool.patch(
            self.directory, writer="runner", version=self.version(), changes=change)), "field_not_owned")

    def test_acceptance_rechecks_the_claims_seen_by_the_lead(self) -> None:
        self.lead({"meta": {"handoffsNeedAcceptance": True}, "laneOwnership": {"lane-1": ["src/*"], "lane-2": []}})
        item = state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="a" * 40,
                                  change_request="https://forge.test/pr/1")
        authorization = {"ownership": {"lane-1": ["src/*"], "lane-2": []}, "lane": "lane-1", "head": item["head"]}
        self.lead({"laneOwnership": {"lane-1": [], "lane-2": ["src/*"]}})
        self.assertEqual(self.refused(lambda: state_tool.accept_handoff(self.directory, "work-a", authorization)),
                         "handoff_invalid")

    def test_a_lane_writes_its_own_focus_state_and_waiting_on(self) -> None:
        written = self.as_lane("lane-1", {"focus": "Wiring the hand-off", "state": "blocked", "waitingOn": "lead"},
                               selector="lanes[id=lane-1]")
        own = next(entry for entry in written["lanes"] if entry["id"] == "lane-1")
        self.assertEqual((own["focus"], own["state"], own["waitingOn"]), ("Wiring the hand-off", "blocked", "lead"))
        # The tool stamps since when the state changes.
        self.assertNotEqual(own["since"], "2026-10-21T09:00:00Z")
        unchanged = self.as_lane("lane-1", {"focus": "Still wiring"}, selector="lanes[id=lane-1]")
        self.assertEqual(next(entry for entry in unchanged["lanes"] if entry["id"] == "lane-1")["since"], own["since"])

    def queue_checks(self) -> None:
        """The merge queue's write while it checks a candidate, as the runner makes it."""
        state_tool.patch(self.directory, writer="runner", version=self.version(), selector="queue[subject=work-q]",
                         assignments={"/stage": "checked"})

    def test_a_lanes_focus_lands_when_the_queue_writes_inside_its_window(self) -> None:
        # The queue writes the record between the focus write's version read and its pinned write, and the lead writes
        # it more times than the focus has attempts: only a write that reads the record again can land.
        wrote = []

        def queue_once() -> None:
            if not wrote:
                wrote.append(self.version())
                self.queue_checks()
                for moved in range(state_tool.FOCUS_ATTEMPTS):
                    self.lead({"meta": {"headline": f"Moved {moved}"}})

        before = self.version()
        written = state_tool.set_focus(self.directory, "lane-1", "work-q merged; asking the lead", state="done",
                                       waiting_on="lead", between_read_and_write=queue_once)
        self.assertEqual(wrote, [before], "the queue wrote inside the window")
        own = self.row()
        self.assertEqual((own["focus"], own["state"], own["waitingOn"]), ("work-q merged; asking the lead", "done", "lead"),
                         "the retry carried every field the lane wrote")
        self.assertEqual(written["version"], before + 2 + state_tool.FOCUS_ATTEMPTS,
                         "the queue's write, the lead's, and then the lane's")
        state = state_tool.read_state(self.directory)
        self.assertEqual(next(item for item in state["queue"] if item["subject"] == "work-q")["stage"], "checked",
                         "the retry kept the queue's write")

    def test_a_lanes_focus_gives_up_on_a_record_that_keeps_moving(self) -> None:
        attempts = []

        def queue_always() -> None:
            attempts.append(self.version())
            self.lead({"meta": {"headline": f"Moved {len(attempts)}"}})

        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.set_focus(self.directory, "lane-1", "never lands", between_read_and_write=queue_always)
        self.assertEqual(refused.exception.code, "version_stale")
        # Each attempt is a new read; the row above proves the read, by moving the record past every guessed version.
        self.assertEqual(len(attempts), state_tool.FOCUS_ATTEMPTS, "a bounded number of attempts, then refusal")
        self.assertNotEqual(self.row()["focus"], "never lands")

    def test_a_lanes_focus_is_not_retried_past_a_refusal_other_than_a_moved_record(self) -> None:
        before = (self.directory / state_tool.STATE_FILE).read_bytes()
        with mock.patch.object(state_tool, "patch", wraps=state_tool.patch) as patched:
            with self.assertRaises(state_tool.StateError) as refused:
                state_tool.set_focus(self.directory, "lane-9", "not a lane")
        self.assertNotEqual(refused.exception.code, "version_stale")
        self.assertEqual(patched.call_count, 1, "only a moved record is read again")
        self.assertEqual((self.directory / state_tool.STATE_FILE).read_bytes(), before)

    def queue_stops(self, lane_id: str = "lane-1", waiting_on: str = "lead") -> None:
        """The queue's words on a row, as the runner writes them when it stops a candidate."""
        state_tool.patch(self.directory, writer="runner", version=self.version(), selector=f"lanes[id={lane_id}]",
                         changes={"focus": "work-q stopped: check_failed", "state": "blocked", "waitingOn": waiting_on})

    def row(self, lane_id: str = "lane-1") -> dict:
        return next(entry for entry in state_tool.read_state(self.directory)["lanes"] if entry["id"] == lane_id)

    def test_the_queues_wait_ends_when_its_lane_speaks_by_set_too(self) -> None:
        self.queue_stops()
        self.assertEqual((self.row()["waitingOn"], self.row()["waitingBy"]), ("lead", "runner"))
        self.as_lane("lane-1", selector="lanes[id=lane-1]", assignments={"/focus": "fixing the check"})
        row = self.row()
        self.assertEqual((row["focus"], row["waitingOn"]), ("fixing the check", None))
        self.assertNotIn("waitingBy", row)
        self.assertNotIn("focusBy", row)
        # On the command line as well.
        self.queue_stops()
        code, result = run_tool(self.directory, "patch", "--writer", "lane", "--lane", "lane-1", "--version",
                                str(self.version()), "--element", "lanes[id=lane-1]", "--set", '{"/focus": "again"}')
        self.assertEqual(code, 0, result)
        self.assertEqual((self.row()["focus"], self.row()["waitingOn"]), ("again", None))

    def test_a_wait_the_lane_sets_under_the_queues_words_stays_through_its_next_focus(self) -> None:
        for name, waiting, kwargs in (
                ("a new wait by --patch", "the lead's answer", {"changes": {"waitingOn": "the lead's answer"}}),
                ("the same wait by --patch", "lead", {"changes": {"waitingOn": "lead"}}),
                ("the same wait by --set", "lead", {"assignments": {"/waitingOn": "lead"}})):
            with self.subTest(name):
                self.queue_stops()
                self.as_lane("lane-1", selector="lanes[id=lane-1]", **kwargs)
                row = self.row()
                self.assertEqual((row["focus"], row["waitingOn"], row["waitingBy"], row["focusBy"]),
                                 ("work-q stopped: check_failed", waiting, "lane", "runner"))
                state_tool.set_focus(self.directory, "lane-1", "still asking")
                self.assertEqual((self.row()["waitingOn"], self.row()["waitingBy"]), (waiting, "lane"))
                state_tool.set_focus(self.directory, "lane-1", "answered", waiting_on="")
                self.assertNotIn("waitingBy", self.row())

    def test_a_wait_the_lead_sets_under_the_queues_words_stays_through_the_lanes_next_focus(self) -> None:
        for name, kwargs in (("by --patch", {"changes": {"waitingOn": "lead"}}),
                             ("by --set", {"assignments": {"/waitingOn": "lead"}})):
            with self.subTest(name):
                self.queue_stops()
                state_tool.patch(self.directory, writer="lead", version=self.version(), selector="lanes[id=lane-1]",
                                 **kwargs)
                self.assertEqual((self.row()["waitingBy"], self.row()["focusBy"]), ("lead", "runner"))
                state_tool.set_focus(self.directory, "lane-1", "working on it")
                self.assertEqual((self.row()["waitingOn"], self.row()["waitingBy"]), ("lead", "lead"))
                state_tool.patch(self.directory, writer="lead", version=self.version(), selector="lanes[id=lane-1]",
                                 assignments={"/waitingOn": None})
                self.assertNotIn("waitingBy", self.row())

    def test_the_lead_rewriting_only_the_focus_leaves_the_queues_wait_for_the_lane_to_end(self) -> None:
        self.queue_stops()
        self.lead({"focus": "the lead says: see the failing check"}, selector="lanes[id=lane-1]")
        row = self.row()
        self.assertNotIn("focusBy", row)
        self.assertEqual((row["waitingOn"], row["waitingBy"]), ("lead", "runner"))
        state_tool.set_focus(self.directory, "lane-1", "fixing the check")
        self.assertEqual(self.row()["waitingOn"], None)

    def runner_stops_as_the_queue_does(self, subject: str = "work-q") -> None:
        """The queue's stop as the runner writes it: the whole lanes list from lane_rows_after, with no selector."""
        state = state_tool.read_state(self.directory)
        state_tool.patch(self.directory, writer="runner", version=state["version"],
                         changes={"lanes": state_tool.lane_rows_after(self.directory, state, subject,
                                                                      stopped="check_failed")})

    def test_the_queue_rewriting_a_rows_words_owns_its_wait_even_at_the_same_value(self) -> None:
        state_tool.set_focus(self.directory, "lane-1", "work-q: asking the lead", waiting_on="lead")
        self.assertEqual(self.row()["waitingBy"], "lane")
        self.runner_stops_as_the_queue_does()
        row = self.row()
        self.assertEqual((row["focus"], row["waitingOn"], row["waitingBy"], row["focusBy"]),
                         ("work-q stopped: check_failed", "lead", "runner", "runner"))
        state_tool.set_focus(self.directory, "lane-1", "fixing the check")
        self.assertEqual(self.row()["waitingOn"], None)

    def test_a_wait_the_lead_changes_in_a_whole_lanes_list_is_the_leads(self) -> None:
        self.runner_stops_as_the_queue_does()
        self.assertEqual(self.row()["waitingBy"], "runner")
        lanes = [{**entry, "waitingOn": "the lead's ruling"} if entry["id"] == "lane-1" else entry
                 for entry in state_tool.read_state(self.directory)["lanes"]]
        self.lead({"lanes": lanes})
        self.assertEqual((self.row()["waitingOn"], self.row()["waitingBy"]), ("the lead's ruling", "lead"))
        state_tool.set_focus(self.directory, "lane-1", "working on it")
        self.assertEqual((self.row()["waitingOn"], self.row()["waitingBy"]), ("the lead's ruling", "lead"))

    def test_a_hand_off_ends_the_queues_wait_after_the_lead_rewrote_its_words(self) -> None:
        state_tool.patch(self.directory, writer="runner", version=self.version(), selector="queue[subject=work-q]",
                         assignments={"/stage": "stopped"})
        self.runner_stops_as_the_queue_does()
        self.lead({"focus": "the lead says: see the failing check"}, selector="lanes[id=lane-1]")
        self.assertNotIn("focusBy", self.row())
        # Another lane the queue stopped, whose words and wait a hand-off in lane-1 must leave alone.
        self.queue_stops("lane-2")
        other = self.row("lane-2")
        self.assertEqual((other["focusBy"], other["waitingBy"]), ("runner", "runner"))
        state_tool.handoff(self.directory, lane="lane-1", subject="work-q", head="b" * 40, change_request="cr")
        row = self.row()
        self.assertEqual((row["focus"], row["state"], row["waitingOn"]),
                         ("the lead says: see the failing check", "handed", None))
        self.assertNotIn("waitingBy", row)
        self.assertEqual(self.row("lane-2"), other)

    def test_a_put_back_leaves_the_queues_wait_once_the_lead_rewrote_its_words(self) -> None:
        self.runner_stops_as_the_queue_does()
        self.lead({"focus": "the lead says: retrying it"}, selector="lanes[id=lane-1]")
        state = state_tool.read_state(self.directory)
        state_tool.patch(self.directory, writer="runner", version=state["version"],
                         changes={"lanes": state_tool.lane_rows_put_back(state, "work-q")})
        row = self.row()
        self.assertEqual((row["focus"], row["waitingOn"], row["waitingBy"]), ("the lead says: retrying it", "lead", "runner"))
        state_tool.set_focus(self.directory, "lane-1", "back on it")
        self.assertEqual(self.row()["waitingOn"], None)

    def test_a_hand_off_and_a_put_back_end_only_the_queues_wait(self) -> None:
        state_tool.patch(self.directory, writer="runner", version=self.version(), selector="queue[subject=work-q]",
                         assignments={"/stage": "stopped"})
        for name, own in (("the queue's wait", None), ("the lane's own wait", "the lead's answer")):
            with self.subTest(name):
                self.queue_stops()
                if own:
                    self.as_lane("lane-1", {"waitingOn": own}, selector="lanes[id=lane-1]")
                state_tool.handoff(self.directory, lane="lane-1", subject="work-q", head="b" * 40, change_request="cr")
                row = self.row()
                self.assertEqual((row["focus"], row["waitingOn"], row.get("waitingBy")),
                                 ("", own, "lane" if own else None))
                state_tool.patch(self.directory, writer="runner", version=self.version(),
                                 selector="queue[subject=work-q]", assignments={"/stage": "stopped"})
                self.as_lane("lane-1", selector="lanes[id=lane-1]", assignments={"/waitingOn": None})
        for name, own in (("the queue's wait", None), ("the lane's own wait", "the lead's answer")):
            with self.subTest("put back: " + name):
                self.queue_stops()
                if own:
                    self.as_lane("lane-1", {"waitingOn": own}, selector="lanes[id=lane-1]")
                state = state_tool.read_state(self.directory)
                rows = state_tool.lane_rows_put_back(state, "work-q")
                state_tool.patch(self.directory, writer="runner", version=state["version"], changes={"lanes": rows})
                row = self.row()
                self.assertEqual((row["focus"], row["state"], row["waitingOn"], row.get("waitingBy")),
                                 ("", "handed", own, "lane" if own else None))
                self.as_lane("lane-1", selector="lanes[id=lane-1]", assignments={"/waitingOn": None})

    def test_a_row_written_before_waiting_by_reads_as_the_queues_while_its_words_stand(self) -> None:
        state = state_tool.read_state(self.directory)
        for entry in state["lanes"]:
            if entry["id"] == "lane-1":
                entry.update(focus="work-q stopped: check_failed", state="blocked", waitingOn="lead",
                             focusBy="runner", focusAt="2026-10-21T09:00:00Z")
            if entry["id"] == "lane-2":
                entry.update(focus="asking", state="blocked", waitingOn="lead")
        (self.directory / state_tool.STATE_FILE).write_text(state_tool.dumps(state), encoding="utf-8")
        # A lead write that leaves the wait alone labels it from the words that stood.
        self.lead({"focus": "the lead says: see the failing check"}, selector="lanes[id=lane-1]")
        self.assertEqual(self.row()["waitingBy"], "runner")
        self.assertNotIn("waitingBy", self.row("lane-2"))
        state_tool.set_focus(self.directory, "lane-1", "fixing the check")
        state_tool.set_focus(self.directory, "lane-2", "still asking")
        self.assertEqual((self.row()["waitingOn"], self.row("lane-2")["waitingOn"]), (None, "lead"))

    def test_who_set_the_wait_is_the_tools_alone(self) -> None:
        self.queue_stops()
        self.assertEqual(self.refused(lambda: self.as_lane("lane-1", {"waitingBy": "lead"}, selector="lanes[id=lane-1]")),
                         "writer_not_owned")
        self.assertEqual(self.refused(lambda: state_tool.patch(
            self.directory, writer="runner", version=self.version(), selector="lanes[id=lane-1]",
            changes={"waitingBy": "lead"})), "field_not_owned")
        self.lead({"waitingBy": "lane", "title": "Renamed"}, selector="lanes[id=lane-1]")
        self.assertEqual(self.row()["waitingBy"], "runner")
        self.lead({"waitingBy": "runner"}, selector="lanes[id=lane-2]")
        self.assertNotIn("waitingBy", self.row("lane-2"))

    def planned_entry(self, subject: str) -> dict:
        return next(entry for entry in state_tool.read_state(self.directory)["planned"] if entry["subject"] == subject)

    def test_a_lane_binds_a_run_to_its_own_planned_work_before_its_hand_off(self) -> None:
        journal = (self.directory / state_tool.JOURNAL_FILE).read_bytes()
        bound = state_tool.bind_run(self.directory, lane="lane-1", subject="work-a", run="run-a")
        self.assertEqual(bound, {**planned("work-a"), "runId": "run-a"})
        self.assertEqual(self.planned_entry("work-a")["runId"], "run-a")
        self.assertNotIn("runId", self.planned_entry("work-b"))
        self.assertEqual((self.directory / state_tool.JOURNAL_FILE).read_bytes(), journal, "a binding journals nothing")
        # The same run again changes nothing at all.
        before = (self.directory / state_tool.STATE_FILE).read_bytes()
        self.assertEqual(state_tool.bind_run(self.directory, lane="lane-1", subject="work-a", run="run-a"), bound)
        self.assertEqual((self.directory / state_tool.STATE_FILE).read_bytes(), before)

    def test_a_lane_cannot_bind_a_run_to_work_that_is_not_its_own_planned_work(self) -> None:
        for name, lane_id, subject, code in (
                ("another lane's planned work", "lane-1", "work-b", "writer_not_owned"),
                ("its own queued work", "lane-1", "work-q", "writer_not_owned"),
                ("work the record lacks", "lane-1", "work-z", "subject_unknown"),
                ("a lane the record lacks", "lane-9", "work-z", "writer_not_owned")):
            with self.subTest(name):
                self.assertEqual(self.refused(lambda: state_tool.bind_run(
                    self.directory, lane=lane_id, subject=subject, run="run-x")), code)
        self.assertEqual(self.refused(lambda: state_tool.bind_run(self.directory, lane="lane-1", subject="work-a", run="")),
                         "run_bound")

    def test_a_bound_run_is_never_replaced_nor_bound_to_a_second_subject(self) -> None:
        state_tool.bind_run(self.directory, lane="lane-1", subject="work-a", run="run-a")
        self.assertEqual(self.refused(lambda: state_tool.bind_run(
            self.directory, lane="lane-1", subject="work-a", run="run-x")), "run_bound")
        self.assertEqual(self.refused(lambda: state_tool.bind_run(
            self.directory, lane="lane-2", subject="work-b", run="run-a")), "run_bound")
        # A run a queued candidate carries is taken too.
        state_tool.handoff(self.directory, lane="lane-2", subject="work-b", head="c" * 40, change_request="cr", run="run-b")
        self.lead({"planned": [*state_tool.read_state(self.directory)["planned"], planned("work-c")]})
        self.assertEqual(self.refused(lambda: state_tool.bind_run(
            self.directory, lane="lane-1", subject="work-c", run="run-b")), "run_bound")
        self.assertEqual(self.planned_entry("work-a")["runId"], "run-a")

    def test_the_write_checks_again_under_the_lock_when_the_record_moved_after_the_first_look(self) -> None:
        # The first look reads the record from before another write; the write under the lock must still refuse.
        for name, settle, lane_id, subject, run in (
                ("another subject took the run", ("lane-2", "work-b", "run-a"), "lane-1", "work-a", "run-a"),
                ("the element was bound to another run", ("lane-1", "work-a", "run-x"), "lane-1", "work-a", "run-y")):
            with self.subTest(name):
                stale = state_tool.read_state(self.directory)
                state_tool.bind_run(self.directory, lane=settle[0], subject=settle[1], run=settle[2])
                before = (self.directory / state_tool.STATE_FILE).read_bytes()
                real = state_tool.read_state
                looks = iter([stale])
                with mock.patch.object(state_tool, "read_state", side_effect=lambda directory: next(looks, None) or real(directory)):
                    self.assertEqual(self.refused(lambda: state_tool.bind_run(
                        self.directory, lane=lane_id, subject=subject, run=run)), "run_bound")
                self.assertEqual((self.directory / state_tool.STATE_FILE).read_bytes(), before)
                # Start the next case from a record with no run bound.
                for entry in ("work-a", "work-b"):
                    version = self.version()
                    state_tool.patch(self.directory, writer="lead", version=version, selector=f"planned[subject={entry}]",
                                     changes={"runId": None})

    def test_the_hand_off_keeps_the_bound_run_unless_it_names_another(self) -> None:
        state_tool.bind_run(self.directory, lane="lane-1", subject="work-a", run="run-a")
        state_tool.bind_run(self.directory, lane="lane-2", subject="work-b", run="run-b")
        kept = state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="a" * 40, change_request="cr")
        named = state_tool.handoff(self.directory, lane="lane-2", subject="work-b", head="c" * 40, change_request="cr",
                                   run="run-other")
        self.assertEqual((kept["runId"], named["runId"]), ("run-a", "run-other"))

    def test_a_hand_off_naming_a_run_another_subject_holds_is_refused_and_writes_nothing(self) -> None:
        state_tool.bind_run(self.directory, lane="lane-1", subject="work-a", run="run-r")
        self.assertEqual(self.refused(lambda: state_tool.handoff(
            self.directory, lane="lane-2", subject="work-b", head="c" * 40, change_request="cr", run="run-r")), "run_bound")
        self.assertEqual(self.planned_entry("work-b")["subject"], "work-b", "work-b is still planned")
        # A queued subject holds its run too, and so does one the queue has merged, as for bind-run.
        state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="a" * 40, change_request="cr")
        self.assertEqual(self.refused(lambda: state_tool.handoff(
            self.directory, lane="lane-2", subject="work-b", head="c" * 40, change_request="cr", run="run-r")), "run_bound")
        state_tool.patch(self.directory, writer="runner", version=self.version(), selector="queue[subject=work-a]",
                         assignments={"/stage": "merged"})
        self.assertEqual(self.refused(lambda: state_tool.handoff(
            self.directory, lane="lane-2", subject="work-b", head="c" * 40, change_request="cr", run="run-r")), "run_bound")
        # A queued subject handed off again at a new head, naming another subject's run, is refused the same way.
        state_tool.patch(self.directory, writer="runner", version=self.version(), selector="queue[subject=work-q]",
                         assignments={"/stage": "stopped"})
        self.assertEqual(self.refused(lambda: state_tool.handoff(
            self.directory, lane="lane-1", subject="work-q", head="b" * 40, change_request="cr", run="run-r")), "run_bound")
        code, refused = run_tool(self.directory, "handoff", "--lane", "lane-2", "--subject", "work-b", "--head", "c" * 40,
                                 "--change-request", "cr", "--run", "run-r")
        self.assertEqual((code, refused["code"]), (1, "run_bound"))
        self.assertIn("run-r", refused["error"])
        self.assertIn("work-a", refused["error"])

    def test_the_bound_subject_hands_off_with_its_own_run_and_a_hand_off_without_a_run_still_works(self) -> None:
        state_tool.bind_run(self.directory, lane="lane-1", subject="work-a", run="run-r")
        handed = state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="a" * 40, change_request="cr",
                                    run="run-r")
        self.assertEqual(handed["runId"], "run-r")
        # Handed off again at a new head with the same run, once it stopped.
        state_tool.patch(self.directory, writer="runner", version=self.version(), selector="queue[subject=work-a]",
                         assignments={"/stage": "stopped"})
        again = state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="d" * 40, change_request="cr",
                                   run="run-r")
        self.assertEqual((again["head"], again["runId"]), ("d" * 40, "run-r"))
        plain = state_tool.handoff(self.directory, lane="lane-2", subject="work-b", head="c" * 40, change_request="cr")
        self.assertIsNone(plain["runId"])

    def test_the_command_line_binds_a_run_and_says_why_it_refuses(self) -> None:
        code, bound = run_tool(self.directory, "bind-run", "--lane", "lane-1", "--subject", "work-a", "--run", "run-a")
        self.assertEqual(code, 0, bound)
        self.assertEqual(bound["item"]["runId"], "run-a")
        code, refused = run_tool(self.directory, "bind-run", "--lane", "lane-1", "--subject", "work-a", "--run", "run-x")
        self.assertEqual((code, refused["code"]), (1, "run_bound"))
        code, refused = run_tool(self.directory, "bind-run", "--lane", "lane-1", "--subject", "work-b", "--run", "run-y")
        self.assertEqual((code, refused["code"]), (1, "writer_not_owned"))

    def test_every_write_a_lane_does_not_own_is_refused(self) -> None:
        refusals = {
            "another lane's focus": lambda: self.as_lane("lane-1", {"focus": "x"}, selector="lanes[id=lane-2]"),
            "its own title": lambda: self.as_lane("lane-1", {"title": "Renamed"}, selector="lanes[id=lane-1]"),
            "a queue stage": lambda: self.as_lane("lane-1", {"stage": "merged"}, selector="queue[subject=work-q]"),
            "a queue head": lambda: self.as_lane("lane-1", {"head": "f" * 40}, selector="queue[subject=work-q]"),
            "the checking panel": lambda: self.as_lane("lane-1", assignments={"/meta/checking/next": ["work-q"]}),
            "the headline": lambda: self.as_lane("lane-1", {"meta": {"headline": "Mine now"}}),
            "the planned list": lambda: self.as_lane("lane-1", {"planned": []}),
            "a new queue element": lambda: self.as_lane("lane-1", {"queue": [queue_item("work-q"), queue_item("x", order=2)]}),
        }
        for name, call in refusals.items():
            with self.subTest(name):
                self.assertEqual(self.refused(call), "writer_not_owned")

    def at_stage(self, subject: str, stage: str) -> None:
        self.lead({"stage": stage}, selector=f"planned[subject={subject}]")

    def stage_of(self, subject: str) -> str:
        return next(entry for entry in state_tool.read_state(self.directory)["planned"] if entry["subject"] == subject)["stage"]

    def test_a_lane_moves_its_own_planned_story_to_building_and_nothing_else(self) -> None:
        self.at_stage("work-a", "planned")
        before = state_tool.read_state(self.directory)
        written = self.as_lane("lane-1", {"stage": "building"}, selector="planned[subject=work-a]")
        self.assertEqual(self.stage_of("work-a"), "building")
        # Only that stage moved: the other planned story, the queue and the lanes are as they were.
        self.assertEqual([entry for entry in written["planned"] if entry["subject"] != "work-a"],
                         [entry for entry in before["planned"] if entry["subject"] != "work-a"])
        self.assertEqual((written["queue"], written["lanes"]), (before["queue"], before["lanes"]))
        # Moved again, it is already building: the story is left as it is.
        again = self.as_lane("lane-1", {"stage": "building"}, selector="planned[subject=work-a]")
        self.assertEqual([entry for entry in again["planned"]], [entry for entry in written["planned"]])

    def test_every_other_stage_write_by_a_lane_is_refused(self) -> None:
        self.at_stage("work-a", "planned")
        self.at_stage("work-b", "planned")
        refusals = {
            "another lane's planned story": lambda: self.as_lane("lane-1", {"stage": "building"},
                                                                 selector="planned[subject=work-b]"),
            "a stage other than building": lambda: self.as_lane("lane-1", {"stage": "review"},
                                                                selector="planned[subject=work-a]"),
            "the stage and its title at once": lambda: self.as_lane("lane-1", {"stage": "building", "title": "Mine"},
                                                                    selector="planned[subject=work-a]"),
        }
        for name, call in refusals.items():
            with self.subTest(name):
                self.assertEqual(self.refused(call), "writer_not_owned")
        for stage in ("building", "review"):
            with self.subTest(f"a story already in {stage}"):
                self.at_stage("work-a", stage)
                moves = ["planned", "review"] if stage == "building" else ["building", "planned"]
                for to in moves:
                    self.assertEqual(self.refused(lambda: self.as_lane("lane-1", {"stage": to},
                                                                       selector="planned[subject=work-a]")),
                                     "writer_not_owned")

    def test_the_skill_names_the_one_stage_a_lane_moves(self) -> None:
        skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("the one stage move it makes, a story planned in its own lane from `planned` to `building` when it "
                      "starts it", skill)
        self.assertIn("refuses anything else with `writer_not_owned` for a lane", skill)

    def test_a_lane_write_names_its_lane(self) -> None:
        code = self.refused(lambda: state_tool.patch(self.directory, writer="lane", version=self.version(),
                                                     changes={"lanes": []}))
        self.assertEqual(code, "writer_invalid")

    def test_a_hand_off_moves_planned_work_into_the_queue_and_journals_it(self) -> None:
        item = state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="a" * 40,
                                  change_request="change request 4", rounds=3)
        state = state_tool.read_state(self.directory)
        self.assertEqual([entry["subject"] for entry in state.get("planned", [])], ["work-b"])
        queued = next(entry for entry in state["queue"] if entry["subject"] == "work-a")
        self.assertEqual(queued, item)
        self.assertEqual((queued["stage"], queued["order"], queued["head"], queued["lane"], queued["title"]),
                         ("queued", 2, "a" * 40, "lane-1", "Work work-a"))
        self.assertEqual((queued["changeRequest"], queued["reportedRounds"], queued["syncedHead"]),
                         ("change request 4", 3, None))
        self.assertNotIn("pending", queued)
        self.assertEqual(next(entry for entry in state["lanes"] if entry["id"] == "lane-1")["state"], "handed")
        [handed] = [entry for entry in state_tool.read_journal(self.directory) if entry["kind"] == "candidate.handed"]
        self.assertEqual((handed["lane"], handed["subject"]), ("lane-1", "work-a"))

    def test_a_hand_off_names_its_run_and_a_later_hand_off_keeps_or_replaces_it(self) -> None:
        first = state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="a" * 40,
                                   change_request="change request 4", run="run-abc")
        self.assertEqual(first["runId"], "run-abc")
        kept = state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="b" * 40,
                                  change_request="change request 4")
        self.assertEqual(kept["runId"], "run-abc", "a hand-off that names no run keeps the stored one")
        code, result = run_tool(self.directory, "handoff", "--lane", "lane-1", "--subject", "work-a",
                                "--head", "c" * 40, "--change-request", "change request 4", "--run", "run-def")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["item"]["runId"], "run-def")
        queued = next(entry for entry in state_tool.read_state(self.directory)["queue"] if entry["subject"] == "work-a")
        self.assertEqual(queued["runId"], "run-def")

    def test_a_hand_off_that_names_no_run_writes_null(self) -> None:
        code, result = run_tool(self.directory, "handoff", "--lane", "lane-1", "--subject", "work-a",
                                "--head", "a" * 40, "--change-request", "change request 4")
        self.assertEqual(code, 0, result)
        self.assertIsNone(result["item"]["runId"])

    def test_a_hand_off_of_planned_work_keeps_the_run_the_lead_planned_it_with(self) -> None:
        self.lead({"runId": "run-planned"}, selector="planned[subject=work-a]")
        item = state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="a" * 40,
                                  change_request="change request 4")
        self.assertEqual(item["runId"], "run-planned")

    def test_a_malformed_run_id_is_refused_and_nothing_is_written(self) -> None:
        before = (self.directory / state_tool.STATE_FILE).read_bytes()
        journal = (self.directory / state_tool.JOURNAL_FILE).read_bytes()
        code = self.refused(lambda: state_tool.handoff(self.directory, lane="lane-1", subject="work-a",
                                                       head="a" * 40, change_request="change request 4", run=""))
        self.assertEqual(code, "patch_invalid")
        code, result = run_tool(self.directory, "handoff", "--lane", "lane-1", "--subject", "work-a",
                                "--head", "a" * 40, "--change-request", "change request 4", "--run", "")
        self.assertNotEqual(code, 0, result)
        self.assertEqual(result["code"], "patch_invalid")
        self.assertEqual((self.directory / state_tool.STATE_FILE).read_bytes(), before)
        self.assertEqual((self.directory / state_tool.JOURNAL_FILE).read_bytes(), journal)

    @staticmethod
    def evidence(**changes: object) -> dict:
        found = {"check": "full", "definition": "sha256:" + "d" * 64, "head": "a" * 40, "tree": "e" * 40,
                 "result": "passed"}
        found.update(changes)
        return found

    def test_a_hand_off_records_the_lanes_suite_evidence_for_that_candidate(self) -> None:
        item = state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="a" * 40,
                                  change_request="cr", evidence=[self.evidence()])
        [recorded] = item["evidence"]
        self.assertEqual({key: recorded[key] for key in ("check", "definition", "head", "tree", "result")},
                         self.evidence())
        self.assertIn("recordedAt", recorded)
        [handed] = [entry for entry in state_tool.read_journal(self.directory) if entry["kind"] == "candidate.handed"]
        self.assertIn("suite evidence for full (passed)", handed["summary"])
        # A hand-off at a new head replaces it: none given, none kept.
        state_tool.patch(self.directory, writer="runner", version=self.version(), selector="queue[subject=work-a]",
                         assignments={"/stage": "stopped"})
        again = state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="b" * 40, change_request="cr")
        self.assertNotIn("evidence", again)
        state_tool.patch(self.directory, writer="runner", version=self.version(), selector="queue[subject=work-a]",
                         assignments={"/stage": "stopped"})
        third = state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="b" * 40, change_request="cr",
                                   evidence=[self.evidence(head="b" * 40, result="failed")])
        self.assertEqual([(entry["head"], entry["result"]) for entry in third["evidence"]], [("b" * 40, "failed")])

    def test_malformed_suite_evidence_is_refused(self) -> None:
        cases = {
            "a missing tree": {key: value for key, value in self.evidence().items() if key != "tree"},
            "a short head": self.evidence(head="abc"),
            "an unknown result": self.evidence(result="flaky"),
            "an unknown field": self.evidence(note="trust me"),
            "a bare definition": self.evidence(definition="d" * 64),
            "a short tree": self.evidence(tree="abc"),
            "an empty check": self.evidence(check=""),
            "the same check twice": None,
        }
        for name, entry in cases.items():
            with self.subTest(name):
                evidence = [self.evidence(), self.evidence()] if entry is None else [entry]
                self.assertEqual(self.refused(lambda: state_tool.handoff(
                    self.directory, lane="lane-1", subject="work-a", head="a" * 40, change_request="cr",
                    evidence=evidence)), "handoff_invalid")
        with self.subTest("not a list"):
            self.assertEqual(self.refused(lambda: state_tool.handoff(
                self.directory, lane="lane-1", subject="work-a", head="a" * 40, change_request="cr",
                evidence=self.evidence())), "handoff_invalid")

    def test_suite_evidence_is_written_only_by_a_hand_off(self) -> None:
        state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="a" * 40, change_request="cr",
                           evidence=[self.evidence()])
        recorded = next(entry for entry in state_tool.read_state(self.directory)["queue"]
                        if entry["subject"] == "work-a")["evidence"]
        forged = [{**recorded[0], "tree": "f" * 40}]
        refusals = {
            "the lead changing it": (lambda: self.lead({"evidence": forged}, selector="queue[subject=work-a]"),
                                     "field_not_owned"),
            "the lead removing it": (lambda: self.lead({"evidence": []}, selector="queue[subject=work-a]"),
                                     "field_not_owned"),
            "the lead adding it to another item": (
                lambda: self.lead({"evidence": forged}, selector="queue[subject=work-q]"), "field_not_owned"),
            "the runner changing it": (lambda: state_tool.patch(
                self.directory, writer="runner", version=self.version(), selector="queue[subject=work-a]",
                changes={"evidence": forged}), "field_not_owned"),
            "a lane changing it": (lambda: self.as_lane("lane-1", {"evidence": forged}, selector="queue[subject=work-a]"),
                                   "writer_not_owned"),
        }
        for name, (call, code) in refusals.items():
            with self.subTest(name):
                self.assertEqual(self.refused(call), code)
        state = state_tool.read_state(self.directory)
        new_item = {**queue_item("work-n", lane="lane-1", order=3), "evidence": forged}
        self.assertEqual(self.refused(lambda: self.lead({"queue": [*state["queue"], new_item]})), "field_not_owned")
        without = copy.deepcopy(state["queue"])
        for item in without:
            item.pop("evidence", None)
        self.assertEqual(self.refused(lambda: self.lead({"queue": without})), "field_not_owned")
        # The lead's own writes to a queue item that carries evidence still go through.
        self.lead({"leadInputs": ["api"]}, selector="queue[subject=work-a]")

    def test_a_lane_hands_off_only_its_own_work(self) -> None:
        for name, subject, code in (("another lane's planned work", "work-b", "writer_not_owned"),
                                    ("unknown work", "work-z", "subject_unknown")):
            with self.subTest(name):
                self.assertEqual(self.refused(lambda: state_tool.handoff(
                    self.directory, lane="lane-1", subject=subject, head="a" * 40, change_request="cr")), code)

    def test_a_stopped_candidate_is_handed_off_again_at_its_new_head(self) -> None:
        state_tool.patch(self.directory, writer="runner", version=self.version(), selector="queue[subject=work-q]",
                         assignments={"/stage": "stopped"})
        state_tool.handoff(self.directory, lane="lane-1", subject="work-q", head="b" * 40, change_request="cr", rounds=4)
        item = next(entry for entry in state_tool.read_state(self.directory)["queue"] if entry["subject"] == "work-q")
        self.assertEqual((item["stage"], item["head"], item["reportedRounds"], item["order"]), ("queued", "b" * 40, 4, 1))

    def test_a_candidate_in_flight_cannot_be_handed_off_again(self) -> None:
        state_tool.patch(self.directory, writer="runner", version=self.version(), selector="queue[subject=work-q]",
                         assignments={"/stage": "checking"})
        self.assertEqual(self.refused(lambda: state_tool.handoff(
            self.directory, lane="lane-1", subject="work-q", head="b" * 40, change_request="cr")), "handoff_invalid")

    def test_when_the_lead_must_accept_a_hand_off_waits_until_accepted_or_returns_on_rejection(self) -> None:
        # The policy is the record's, so a lane cannot skip it by leaving anything out of its command.
        self.lead({"meta": {"handoffsNeedAcceptance": True}})
        state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="a" * 40, change_request="cr")
        item = next(entry for entry in state_tool.read_state(self.directory)["queue"] if entry["subject"] == "work-a")
        self.assertTrue(item["pending"])
        # Handing it off again does not clear the wait either.
        state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="b" * 40, change_request="cr")
        item = next(entry for entry in state_tool.read_state(self.directory)["queue"] if entry["subject"] == "work-a")
        self.assertTrue(item["pending"])
        state_tool.accept_handoff(self.directory, "work-a")
        item = next(entry for entry in state_tool.read_state(self.directory)["queue"] if entry["subject"] == "work-a")
        self.assertNotIn("pending", item)
        state_tool.handoff(self.directory, lane="lane-2", subject="work-b", head="c" * 40, change_request="cr")
        state_tool.reject_handoff(self.directory, "work-b", "the review missed the migration")
        state = state_tool.read_state(self.directory)
        self.assertNotIn("work-b", [entry["subject"] for entry in state["queue"]])
        self.assertEqual([entry["subject"] for entry in state["planned"]], ["work-b"])
        decisions = {entry["subject"]: entry["summary"] for entry in state_tool.read_journal(self.directory)
                     if entry["kind"] == "decision.recorded"}
        self.assertIn("Accepted", decisions["work-a"])
        self.assertIn("the review missed the migration", decisions["work-b"])

    def test_a_lane_that_drops_its_own_state_is_refused_as_invalid_not_crashed(self) -> None:
        version = self.version()
        state = state_tool.read_state(self.directory)
        lanes = [{key: value for key, value in entry.items() if not (entry["id"] == "lane-1" and key == "state")}
                 for entry in state["lanes"]]
        code = self.refused(lambda: state_tool.patch(self.directory, writer="lane", lane="lane-1", version=version,
                                                     assignments={"/lanes": lanes}))
        self.assertIn(code, ("patch_invalid", "writer_not_owned"))

    def test_accept_and_reject_need_a_pending_hand_off(self) -> None:
        self.assertEqual(self.refused(lambda: state_tool.accept_handoff(self.directory, "work-q")), "handoff_invalid")
        self.assertEqual(self.refused(lambda: state_tool.reject_handoff(self.directory, "work-q", "no")), "handoff_invalid")

    def test_a_lane_journals_review_rounds_for_its_own_work_only(self) -> None:
        own = review(1, "2026-10-21T09:00:00Z", "2026-10-21T09:10:00Z")
        entry = state_tool.append_journal(self.directory, kind="review.round", summary="Round 1", lane="lane-1",
                                          subject="work-a", review=own, writer="lane")
        self.assertEqual((entry["lane"], entry["subject"]), ("lane-1", "work-a"))
        for name, kind, subject in (("another lane's work", "review.round", "work-b"),
                                    ("a kind the lane does not write", "candidate.merged", "work-a")):
            with self.subTest(name):
                with self.assertRaises(state_tool.StateError) as caught:
                    state_tool.append_journal(self.directory, kind=kind, summary="x", lane="lane-1", subject=subject,
                                              review=own if kind == "review.round" else None, writer="lane")
                self.assertEqual(caught.exception.code, "writer_not_owned")

    def test_the_command_line_hands_off_and_sets_focus(self) -> None:
        code, handed = run_tool(self.directory, "handoff", "--lane", "lane-1", "--subject", "work-a",
                                "--head", "a" * 40, "--change-request", "cr 9", "--rounds", "2")
        self.assertEqual(code, 0, handed)
        self.assertEqual(handed["item"]["stage"], "queued")
        code, evidenced = run_tool(self.directory, "handoff", "--lane", "lane-2", "--subject", "work-b",
                                   "--head", "c" * 40, "--change-request", "cr 10",
                                   "--evidence", json.dumps(self.evidence(head="c" * 40)),
                                   "--evidence", json.dumps(self.evidence(check="lint", head="c" * 40)))
        self.assertEqual(code, 0, evidenced)
        self.assertEqual([(entry["check"], entry["head"]) for entry in evidenced["item"]["evidence"]],
                         [("full", "c" * 40), ("lint", "c" * 40)])
        state_tool.patch(self.directory, writer="runner", version=self.version(), selector="queue[subject=work-b]",
                         assignments={"/stage": "stopped"})
        code, refused = run_tool(self.directory, "handoff", "--lane", "lane-2", "--subject", "work-b",
                                 "--head", "c" * 40, "--change-request", "cr 10", "--evidence", "not json")
        self.assertEqual((code, refused["code"]), (1, "handoff_invalid"))
        code, focused = run_tool(self.directory, "focus", "--lane", "lane-2", "--text", "Reviewing round 2",
                                 "--state", "reviewing")
        self.assertEqual(code, 0, focused)
        own = next(entry for entry in focused["state"]["lanes"] if entry["id"] == "lane-2")
        self.assertEqual((own["focus"], own["state"]), ("Reviewing round 2", "reviewing"))
        code, refused = run_tool(self.directory, "patch", "--writer", "lane", "--lane", "lane-2", "--version",
                                 str(self.version()), "--element", "lanes[id=lane-1]", "--patch", '{"focus": "x"}')
        self.assertEqual((code, refused["code"]), (1, "writer_not_owned"))
        self.lead({"meta": {"handoffsNeedAcceptance": True}})
        code, pending = run_tool(self.directory, "handoff", "--lane", "lane-2", "--subject", "work-b",
                                 "--head", "c" * 40, "--change-request", "cr")
        self.assertTrue(pending["item"]["pending"])
        code, accepted = run_tool(self.directory, "handoff", "--accept", "work-b")
        self.assertEqual(code, 0, accepted)
        self.assertNotIn("pending", accepted["item"])

    def test_a_lane_clears_its_own_waiting_on(self) -> None:
        code, blocked = run_tool(self.directory, "focus", "--lane", "lane-1", "--text", "Blocked on the lead",
                                 "--state", "blocked", "--waiting-on", "lead")
        self.assertEqual(code, 0, blocked)
        own = next(entry for entry in blocked["state"]["lanes"] if entry["id"] == "lane-1")
        self.assertEqual((own["state"], own["waitingOn"]), ("blocked", "lead"))
        code, cleared = run_tool(self.directory, "focus", "--lane", "lane-1", "--text", "Unblocked",
                                 "--state", "working", "--waiting-on", "")
        self.assertEqual(code, 0, cleared)
        own = next(entry for entry in cleared["state"]["lanes"] if entry["id"] == "lane-1")
        self.assertEqual((own["focus"], own["state"], own["waitingOn"]), ("Unblocked", "working", None))

    def test_a_candidate_handed_off_again_starts_afresh(self) -> None:
        state_tool.patch(self.directory, writer="runner", version=self.version(), selector="queue[subject=work-q]",
                         assignments={"/stage": "stopped", "/syncedHead": "c" * 40,
                                      "/checks": [{"id": "unit", "state": "failed"}]})
        state_tool.handoff(self.directory, lane="lane-1", subject="work-q", head="b" * 40, change_request="cr 2")
        item = next(entry for entry in state_tool.read_state(self.directory)["queue"] if entry["subject"] == "work-q")
        self.assertEqual((item["syncedHead"], item["checks"], item["changeRequest"]), (None, [], "cr 2"))

    def test_a_rejected_hand_off_returns_its_lane_to_work_with_its_change_request(self) -> None:
        self.lead({"meta": {"handoffsNeedAcceptance": True}})
        state_tool.handoff(self.directory, lane="lane-2", subject="work-b", head="c" * 40, change_request="cr 7",
                           run="run-b")
        back = state_tool.reject_handoff(self.directory, "work-b", "no")
        state = state_tool.read_state(self.directory)
        self.assertEqual(state["planned"][-1], back)
        self.assertEqual((back["changeRequest"], back["runId"]), ("cr 7", "run-b"))
        again = state_tool.handoff(self.directory, lane="lane-2", subject="work-b", head="d" * 40,
                                   change_request="cr 7")
        self.assertEqual(again["runId"], "run-b", "the run survives a rejected hand-off")
        self.assertEqual(next(entry for entry in state["lanes"] if entry["id"] == "lane-2")["state"], "working")


class StatusTests(unittest.TestCase):
    """The status line derives from the record unless the lead's override is fresh; an empty lane focus derives too."""

    NOW = "2026-10-21T12:00:00Z"

    def record(self, *, now: dict | None = None, queue: list[dict] | None = None, lanes: list[dict] | None = None,
               override: dict | None = None) -> dict:
        meta = {"headline": "Epic", "checking": {"now": now, "next": []}}
        if override is not None:
            meta["statusOverride"] = override
        return {"meta": meta, "queue": queue or [], "lanes": lanes or [], "planned": []}

    def test_an_idle_record_says_nothing_is_in_flight(self) -> None:
        self.assertEqual(state_tool.delivery_status(self.record(), [], at=self.NOW),
                         {"text": "Nothing in flight", "source": "derived"})

    def test_a_busy_record_names_the_check_the_last_landing_the_blocked_lanes_and_what_is_next(self) -> None:
        now = {"subject": "work-a", "title": "Batch of 2: work-a, work-b", "startedAt": "2026-10-21T11:50:00Z",
               "steps": [{"label": "unit", "state": "passed"}, {"label": "lint", "state": "running"}]}
        record = self.record(
            now=now,
            queue=[queue_item("work-a", stage="checking"), queue_item("work-b", order=2, stage="checking"),
                   queue_item("work-c", order=3), queue_item("work-d", order=4, pending=True),
                   queue_item("work-e", order=5)],
            lanes=[lane("lane-1"), lane("lane-2", state="blocked", waitingOn="lead"), lane("lane-3", state="blocked")],
        )
        journal = [entry(1, "2026-10-21T11:00:00Z", "candidate.merged", "work-0"),
                   entry(2, "2026-10-21T11:40:00Z", "candidate.merged", "work-z")]
        self.assertEqual(state_tool.delivery_status(record, journal, at=self.NOW)["text"],
                         "Checking Batch of 2: work-a, work-b, 1 of 2 checks done · last landed work-z at "
                         "2026-10-21T11:40:00Z · blocked: lane-2 (waiting on lead), lane-3 · next: work-c, then 1 more")

    def test_a_reused_check_counts_as_done(self) -> None:
        now = {"subject": "work-a", "title": "Work A", "startedAt": "2026-10-21T11:50:00Z",
               "steps": [{"label": "full", "state": "reused", "reusedFrom": "a" * 40},
                         {"label": "lint", "state": "running"}]}
        record = self.record(now=now, queue=[queue_item("work-a", stage="checking")])
        self.assertIn("Checking Work A, 1 of 2 checks done", state_tool.delivery_status(record, [], at=self.NOW)["text"])

    def test_the_status_moves_with_the_queue(self) -> None:
        record = self.record(queue=[queue_item("work-a", stage="merged"), queue_item("work-c", order=2)])
        journal = [entry(1, "2026-10-21T11:40:00Z", "candidate.merged", "work-a")]
        self.assertEqual(state_tool.delivery_status(record, journal, at=self.NOW)["text"],
                         "last landed work-a at 2026-10-21T11:40:00Z · next: work-c")

    def test_a_fresh_override_replaces_the_derived_status_and_an_expired_one_is_ignored(self) -> None:
        override = {"text": "Release candidate cut; holding merges", "setAt": "2026-10-21T11:00:00Z",
                    "expiresAt": "2026-10-21T13:00:00Z"}
        self.assertEqual(state_tool.delivery_status(self.record(override=override), [], at=self.NOW),
                         {"text": "Release candidate cut; holding merges", "source": "override",
                          "expiresAt": "2026-10-21T13:00:00Z"})
        expired = {**override, "expiresAt": "2026-10-21T11:59:59Z"}
        self.assertEqual(state_tool.delivery_status(self.record(override=expired), [], at=self.NOW),
                         {"text": "Nothing in flight", "source": "derived"})

    def test_the_status_says_when_serving_is_paused_and_why(self) -> None:
        held = self.record(queue=[queue_item("work-c")])
        held["meta"]["hold"] = {"reason": "release in progress", "since": "2026-10-21T11:00:00Z"}
        self.assertEqual(state_tool.delivery_status(held, [], at=self.NOW)["text"],
                         "Serving is paused: release in progress · next: work-c")
        broken = [entry(1, "2026-10-21T11:00:00Z", "base.broken")]
        self.assertEqual(state_tool.delivery_status(self.record(queue=[queue_item("work-c")]), broken, at=self.NOW)["text"],
                         "Serving is paused until the base is confirmed · next: work-c")
        repaired = [*broken, entry(2, "2026-10-21T11:30:00Z", "base.repaired")]
        self.assertEqual(state_tool.delivery_status(self.record(queue=[queue_item("work-c")]), repaired, at=self.NOW)["text"],
                         "next: work-c")

    def test_an_empty_lane_focus_derives_from_its_work(self) -> None:
        record = self.record(
            # lane-2 has queued work too, but wrote its own focus: that stays its own.
            queue=[queue_item("work-a", lane="lane-1", stage="merged"),
                   queue_item("work-c", order=2, lane="lane-1", stage="checking", reportedRounds=3),
                   queue_item("work-g", order=3, lane="lane-2")],
            lanes=[lane("lane-1"), lane("lane-2", focus="Writing the guide"), lane("lane-3")],
        )
        record["planned"] = [planned("work-p", "lane-3")]
        self.assertEqual(state_tool.lane_focus(record), {
            "lane-1": "work-c: checking, 3 review rounds",
            "lane-3": "work-p: review",
        })

    def test_a_derived_focus_follows_the_lanes_latest_unmerged_work(self) -> None:
        record = self.record(
            queue=[queue_item("work-a", lane="lane-1", order=3, stage="merged"),
                   queue_item("work-b", lane="lane-1", order=1, stage="stopped", reportedRounds=1),
                   queue_item("work-c", lane="lane-1", order=2, reportedRounds=2)],
            lanes=[lane("lane-1"), lane("lane-3")],
        )
        record["planned"] = [planned("work-p", "lane-3"), planned("work-r", "lane-3")]
        self.assertEqual(state_tool.lane_focus(record), {
            "lane-1": "work-c: queued, 2 review rounds",
            "lane-3": "work-r: review",
        })

    def test_stopped_work_is_named_rather_than_nothing_in_flight(self) -> None:
        record = self.record(queue=[queue_item("work-b", order=2, stage="stopped"),
                                    queue_item("work-a", stage="stopped"), queue_item("work-c", order=3)])
        self.assertEqual(state_tool.delivery_status(record, [], at=self.NOW)["text"],
                         "stopped: work-a, work-b · next: work-c")

    def test_an_override_has_expired_at_its_expiry(self) -> None:
        override = {"text": "Holding", "setAt": "2026-10-21T11:00:00Z", "expiresAt": self.NOW}
        self.assertEqual(state_tool.delivery_status(self.record(override=override), [], at=self.NOW)["source"],
                         "derived")

    def test_the_check_count_leaves_out_selection_and_counts_every_finished_step(self) -> None:
        steps = [{"label": "selecting checks", "state": "passed"}, {"label": "unit", "state": "passed"},
                 {"label": "lint", "state": "failed"}, {"label": "docs", "state": "skipped"},
                 {"label": "e2e", "state": "timed_out"}, {"label": "build", "state": "running"}]
        now = {"subject": "work-a", "title": "work-a", "startedAt": "2026-10-21T11:50:00Z", "steps": steps}
        record = self.record(now=now, queue=[queue_item("work-a", stage="checking")])
        self.assertEqual(state_tool.delivery_status(record, [], at=self.NOW)["text"],
                         "Checking work-a, 4 of 5 checks done")

    def test_the_headline_refuses_an_expiry_it_cannot_keep(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name) / "state"
        state_tool.init(directory, "Epic")
        for age in ("0", "-5", "100000000000000"):
            with self.subTest(age):
                code, refused = run_tool(directory, "headline", "--text", "hi", "--max-age", age)
                self.assertEqual((code, refused["code"]), (1, "patch_invalid"))
        binding = Path(temporary.name) / "binding.json"
        for bound in ("60", 0, True):
            with self.subTest(bound=bound):
                binding.write_text(json.dumps({"state": {"publishers": [], "statusOverrideMaxAgeSeconds": bound}}))
                code, refused = run_tool(directory, "headline", "--text", "hi", "--binding", str(binding))
                self.assertEqual((code, refused["code"]), (1, "binding_invalid"))
        self.assertIsNone(state_tool.read_state(directory)["meta"].get("statusOverride"))

    def test_the_lead_sets_an_override_that_expires(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name) / "state"
        state_tool.init(directory, "Epic")
        code, set_override = run_tool(directory, "headline", "--text", "Holding for the release", "--max-age", "600")
        self.assertEqual(code, 0, set_override)
        override = set_override["state"]["meta"]["statusOverride"]
        self.assertEqual(state_tool._parse_time(override["expiresAt"]) - state_tool._parse_time(override["setAt"]),
                         datetime.timedelta(seconds=600))
        self.assertEqual(state_tool.show(directory)["status"], {"text": "Holding for the release", "source": "override",
                                                                "expiresAt": override["expiresAt"]})
        binding = Path(temporary.name) / "binding.json"
        binding.write_text(json.dumps({"state": {"publishers": [], "statusOverrideMaxAgeSeconds": 60}}))
        code, bound = run_tool(directory, "headline", "--text", "Shorter", "--binding", str(binding))
        bound_override = bound["state"]["meta"]["statusOverride"]
        self.assertEqual(state_tool._parse_time(bound_override["expiresAt"]) - state_tool._parse_time(bound_override["setAt"]),
                         datetime.timedelta(seconds=60))
        code, cleared = run_tool(directory, "headline", "--clear")
        self.assertIsNone(cleared["state"]["meta"]["statusOverride"])
        self.assertEqual(state_tool.show(directory)["status"]["source"], "derived")
        # A runner never sets the override; it is the lead's.
        version = state_tool.read_state(directory)["version"]
        with self.assertRaises(state_tool.StateError):
            state_tool.patch(directory, writer="runner", version=version,
                             assignments={"/meta/statusOverride": {"text": "x", "setAt": self.NOW, "expiresAt": self.NOW}})


@unittest.skipIf(os.name == "nt", "process groups and the process table are POSIX here")
class SnapshotCleanupTests(unittest.TestCase):
    """What a snapshot command leaves running is stopped, and nothing outside the run ever is.

    Every scenario runs with a preview first and checks its list before any
    real cleanup. The only processes a test signals itself are the ones it
    started and recorded by pid.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def command(self, *, detached: bool, then: str = "") -> tuple[list[str], Path]:
        pid_file = self.root / f"child {len(list(self.root.iterdir()))}.pid"
        script = textwrap.dedent(f"""
            import subprocess, sys, time
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session={detached!r})
            open({str(pid_file)!r}, "w").write(str(child.pid))
            print("done", flush=True)
            {then}
        """)

        def stop() -> None:
            # Only the one child this test started, by the pid it wrote.
            with contextlib.suppress(OSError, ValueError):
                os.kill(int(pid_file.read_text()), signal.SIGKILL)

        self.addCleanup(stop)
        return [sys.executable, "-c", script], pid_file

    def gone(self, pid: int) -> bool:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.05)
        return False

    def previewed(self, argv: list[str], pid_file: Path) -> dict:
        seen: list[dict] = []
        code, out, _ = state_tool._run_bounded(argv, 20, 4096, preview=seen.append)
        self.assertEqual((code, out.strip()), (0, b"done"))
        self.assertEqual(len(seen), 1, seen)
        selection = seen[0]
        child = int(pid_file.read_text())
        # Exactly the probe: a wider list would be signalled for real by the run that follows.
        self.assertEqual((selection["selected"], selection["refused"]), ([child], []), selection)
        self.assertFalse(self.gone_now(child), "a preview stops nothing")
        os.kill(child, signal.SIGKILL)
        self.assertTrue(self.gone(child))
        return selection

    def state(self, pid: int) -> str:
        return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()

    def gone_now(self, pid: int) -> bool:
        # A process killed a moment ago answers until its new parent reaps it: give that time first.
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    def test_a_detached_child_found_by_the_run_s_marker_is_stopped_after_a_successful_run(self) -> None:
        argv, pid_file = self.command(detached=True)
        selection = self.previewed(argv, pid_file)
        self.assertIn("1 selected", state_tool._describe_selection(selection))
        argv, pid_file = self.command(detached=True)
        done: list[dict] = []
        code, out, _ = state_tool._run_bounded(argv, 20, 4096, cleanup=done)
        self.assertEqual((code, out.strip()), (0, b"done"))
        self.assertTrue(self.gone(int(pid_file.read_text())), "the detached child was stopped")
        self.assertEqual(done[0]["selected"], [int(pid_file.read_text())])

    def test_a_child_left_in_the_command_s_group_is_stopped(self) -> None:
        argv, pid_file = self.command(detached=False)
        self.previewed(argv, pid_file)
        argv, pid_file = self.command(detached=False)
        code, _, _ = state_tool._run_bounded(argv, 20, 4096)
        self.assertEqual(code, 0)
        self.assertTrue(self.gone(int(pid_file.read_text())))

    def test_an_interrupt_during_the_drain_still_stops_what_the_command_left(self) -> None:
        argv, pid_file = self.command(detached=True)
        self.previewed(argv, pid_file)
        argv, pid_file = self.command(detached=True)
        with mock.patch.object(state_tool, "_drain", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                state_tool._run_bounded(argv, 20, 4096)
        self.assertTrue(self.gone(int(pid_file.read_text())))

    def previewed_overrun(self, argv: list[str], pid_file: Path, run) -> None:
        seen: list[dict] = []
        run(argv, preview=seen.append)
        child = int(pid_file.read_text())
        self.assertEqual((seen[0]["selected"], seen[0]["refused"]), ([child], []), seen)
        self.assertFalse(self.gone_now(child), "a preview stops nothing it selected")
        os.kill(child, signal.SIGKILL)

    def test_a_timed_out_command_s_detached_child_is_stopped_too(self) -> None:
        def run(argv: list[str], **options: object) -> None:
            code, _, why = state_tool._run_bounded(argv, 2, 4096, **options)
            self.assertEqual((code, why), (None, "timed out after 2s"))

        argv, pid_file = self.command(detached=True, then="time.sleep(30)")
        self.previewed_overrun(argv, pid_file, run)
        argv, pid_file = self.command(detached=True, then="time.sleep(30)")
        run(argv)
        self.assertTrue(self.gone(int(pid_file.read_text())))

    def test_an_interrupt_while_the_command_runs_stops_it_and_what_it_left(self) -> None:
        real = state_tool._exited

        def run(argv: list[str], **options: object) -> None:
            calls = []

            def interrupted(process, timeout):
                calls.append(timeout)
                if len(calls) == 8:  # the command has been running for a while
                    raise KeyboardInterrupt
                return real(process, timeout)

            with mock.patch.object(state_tool, "_exited", side_effect=interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    state_tool._run_bounded(argv, 20, 4096, **options)

        argv, pid_file = self.command(detached=True, then="time.sleep(30)")
        self.previewed_overrun(argv, pid_file, run)
        argv, pid_file = self.command(detached=True, then="time.sleep(30)")
        run(argv)
        self.assertTrue(self.gone(int(pid_file.read_text())))

    def test_a_process_still_spawning_leaves_no_children_behind(self) -> None:
        pids = self.root / "spawned.pids"

        def stop_recorded() -> None:
            # Only the processes this test's command recorded.
            for line in pids.read_text().split() if pids.exists() else ():
                with contextlib.suppress(OSError, ValueError):
                    os.kill(int(line), signal.SIGKILL)

        self.addCleanup(stop_recorded)
        spawner = textwrap.dedent(f"""
            import os, subprocess, sys, time
            with open({str(pids)!r}, "a") as out:
                out.write(f"{{os.getpid()}}\\n"); out.flush()
                while True:
                    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"],
                                             start_new_session=True)
                    out.write(f"{{child.pid}}\\n"); out.flush()
                    time.sleep(0.01)
        """)
        script = textwrap.dedent(f"""
            import subprocess, sys, time
            subprocess.Popen([sys.executable, "-c", {spawner!r}], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.5)
            print("done")
        """)
        seen: list[dict] = []
        state_tool._run_bounded([sys.executable, "-c", script], 20, 4096, preview=seen.append)
        recorded = {int(line) for line in pids.read_text().split()}
        self.assertTrue(seen[0]["selected"], seen)
        # A child forked but not yet logged is still the spawner's: its parent is logged.
        table = state_tool._process_table() or {}
        unlogged = [pid for pid in seen[0]["selected"] if pid not in recorded]
        self.assertTrue(all(pid in table and table[pid][0] in recorded for pid in unlogged), (unlogged, recorded))
        self.assertEqual(seen[0]["refused"], [])
        stop_recorded()
        time.sleep(0.5)
        stop_recorded()
        pids.unlink()
        code, _, _ = state_tool._run_bounded([sys.executable, "-c", script], 20, 4096)
        self.assertEqual(code, 0)
        time.sleep(0.5)
        survivors = [pid for pid in {int(line) for line in pids.read_text().split()} if not self.gone(pid)]
        self.assertEqual(survivors, [], "nothing the spawner started survives")

    def test_a_pid_that_left_the_group_and_carries_no_marker_is_not_signalled(self) -> None:
        probe = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        self.addCleanup(probe.wait)
        try:
            state_tool._signal_selected([probe.pid], os.getpid(), b"ORCHESTRATE_SNAPSHOT_RUN=nobody", signal.SIGKILL)
            time.sleep(0.3)
            self.assertIsNone(probe.poll(), "checked again before the signal, and spared")
        finally:
            probe.kill()

    def test_a_command_reaped_elsewhere_gives_up_its_group(self) -> None:
        process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        self.assertTrue(state_tool._exited(process, 10))
        self.assertTrue(state_tool._unreaped(process.pid))
        process.wait()
        self.assertFalse(state_tool._unreaped(process.pid))
        marker, me, uid = b"RUN=abc", os.getpid(), os.geteuid()
        table = {me: (1, me, uid, 1.0), 501: (500, 500, uid, 2_000_000.0), 502: (1, 502, uid, 2_000_000.0)}
        selection = state_tool._cleanup_selection(None, marker, 1_000_000.0, table,
                                                  lambda pid: [marker] if pid == 502 else [], caller=me)
        self.assertEqual(selection["selected"], [502], "a group member counts only while the group id is held")
        self.assertIn("marked processes only", state_tool._describe_selection(selection))

    @contextlib.contextmanager
    def without_waitid(self, absent: bool):
        """As Python below 3.13 on macOS runs: finding the command unreaped takes a process-table read of its own."""
        waitid = getattr(os, "waitid", None)
        if absent and waitid is not None:
            del os.waitid
        try:
            yield
        finally:
            if absent and waitid is not None:
                os.waitid = waitid

    def after_the_first_freeze(self, *, interrupt: bool = False, blind: bool = False, without: Path | None = None,
                               always: bool = False, restarted: Path | None = None, later: float = 60,
                               last_read: str | None = None):
        """What happens at the first table read after a real freeze, however many reads came before it."""
        real_table, real_environment, real_signal = state_tool._process_table, state_tool._process_environment, state_tool._signal_selected
        froze, fired = [], []

        def signalled(pids: list[int], group: int | None, marker: bytes, signum: signal.Signals) -> list[int]:
            done = real_signal(pids, group, marker, signum)
            if signum == signal.SIGSTOP and done:
                froze.append(True)
            return done

        def table() -> dict | None:
            read = real_table()
            if froze and not fired:
                fired.append(True)
                if interrupt:
                    raise KeyboardInterrupt
            elif last_read is not None and froze and len(fired) == 2:
                # With nothing new to freeze, the second read after the freeze is the last.
                if last_read == "interrupted":
                    raise KeyboardInterrupt
                return None
            if froze and read is not None:
                if without is not None and (always or len(fired) == 1):
                    read = {pid: row for pid, row in read.items() if pid != int(without.read_text())}
                if restarted is not None and int(restarted.read_text()) in read:
                    pid = int(restarted.read_text())
                    read = {**read, pid: (*read[pid][:3], read[pid][3] + later)}
                fired.append(True)
            return read

        def environment(pid: int) -> list[bytes] | None:
            if restarted is not None and froze and pid == int(restarted.read_text()):
                return []  # the process that took the pid carries no marker of this run
            return None if blind and fired else real_environment(pid)

        return mock.patch.multiple(state_tool, _process_table=table, _process_environment=environment, _signal_selected=signalled)

    # Without waitid an exit is watched with kqueue, so that path exists only where kqueue does.
    WAITID_PATHS = (False, True) if hasattr(select, "kqueue") else (False,)

    def test_an_interrupt_between_the_passes_never_leaves_a_process_frozen(self) -> None:
        for absent in self.WAITID_PATHS:
            with self.subTest(without_waitid=absent), self.without_waitid(absent):
                argv, pid_file = self.command(detached=True)
                self.previewed(argv, pid_file)
                argv, pid_file = self.command(detached=True)
                with self.after_the_first_freeze(interrupt=True), self.assertRaises(KeyboardInterrupt):
                    state_tool._run_bounded(argv, 20, 4096)
                child = int(pid_file.read_text())
                self.assertTrue(self.gone(child), f"killed, not left stopped: the process's state is {self.state(child)!r}")

    def test_a_frozen_process_is_killed_though_its_environment_can_no_longer_be_read(self) -> None:
        for absent in self.WAITID_PATHS:
            with self.subTest(without_waitid=absent), self.without_waitid(absent):
                argv, pid_file = self.command(detached=True)
                self.previewed(argv, pid_file)
                argv, pid_file = self.command(detached=True)
                with self.after_the_first_freeze(interrupt=True, blind=True), self.assertRaises(KeyboardInterrupt):
                    state_tool._run_bounded(argv, 20, 4096)
                child = int(pid_file.read_text())
                self.assertTrue(self.gone(child), f"killed, not left stopped: the process's state is {self.state(child)!r}")

    def test_a_frozen_process_a_later_read_refuses_is_let_go_though_its_environment_can_no_longer_be_read(self) -> None:
        argv, pid_file = self.command(detached=True)
        self.previewed(argv, pid_file)
        argv, pid_file = self.command(detached=True)
        real_selection, blind, selections = state_tool._cleanup_selection, [], []

        def selection(*args: object, **kwargs: object) -> dict:
            result = real_selection(*args, **kwargs)
            selections.append(result)
            if len(selections) == 2:
                # The later read refuses what the first selected, and nothing can be read of it from here on.
                blind.append(True)
                return {**result, "selected": [], "refused": [(pid, "a later read refused it") for pid in selections[0]["selected"]]}
            return result

        real_environment = state_tool._process_environment
        with mock.patch.multiple(state_tool, _cleanup_selection=selection,
                                 _process_environment=lambda pid: None if blind else real_environment(pid)):
            state_tool._run_bounded(argv, 20, 4096)
        child = int(pid_file.read_text())
        self.assertTrue(blind, "the scenario reached the later read")
        status = self.state(child)
        self.assertTrue(status, "let go, not killed: a refused process is not this run's to stop")
        self.assertNotIn("T", status, "let go, not left stopped")

    def test_an_interrupt_within_a_pass_still_kills_what_that_pass_had_frozen(self) -> None:
        second = self.root / "second.pid"
        then = (f"second = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdout=subprocess.DEVNULL, "
                f"stderr=subprocess.DEVNULL, start_new_session=True); open({str(second)!r}, 'w').write(str(second.pid))")
        argv, pid_file = self.command(detached=True, then=then)
        real_signal, stopped = state_tool._signal_selected, []

        def signalled(pids: list[int], group: int | None, marker: bytes, signum: signal.Signals) -> list[int]:
            if signum == signal.SIGSTOP and stopped:
                raise KeyboardInterrupt
            done = real_signal(pids, group, marker, signum)
            if signum == signal.SIGSTOP:
                stopped.extend(done)
            return done

        try:
            with mock.patch.object(state_tool, "_signal_selected", signalled), self.assertRaises(KeyboardInterrupt):
                state_tool._run_bounded(argv, 20, 4096)
            children = [int(pid_file.read_text()), int(second.read_text())]
            self.assertEqual(len(stopped), 1, stopped)
            self.assertTrue(self.gone(stopped[0]), f"killed, not left stopped: the state is {self.state(stopped[0])!r}")
            self.assertTrue(all("T" not in self.state(child) for child in children))
        finally:
            with contextlib.suppress(OSError, ValueError):
                os.kill(int(second.read_text()), signal.SIGKILL)

    def signalled_after_the_freeze(self, argv: list[str], **freeze: Path | bool) -> list[int]:
        real_frozen, signalled = state_tool._signal_frozen, []

        def frozen(pids: list[int], signum: signal.Signals) -> None:
            signalled.extend(pids)
            real_frozen(pids, signum)

        with self.after_the_first_freeze(**freeze), mock.patch.object(state_tool, "_signal_frozen", frozen):
            state_tool._run_bounded(argv, 20, 4096)
        return signalled

    def test_a_frozen_process_every_later_read_misses_is_still_killed(self) -> None:
        argv, pid_file = self.command(detached=True)
        self.previewed(argv, pid_file)
        argv, pid_file = self.command(detached=True)
        # The last read misses it too. A pid no process holds takes no harm from the signal, and a live one needs it.
        self.signalled_after_the_freeze(argv, without=pid_file, always=True)
        self.assertTrue(self.gone(int(pid_file.read_text())), "killed, not left stopped")

    def test_a_frozen_pid_now_held_by_a_later_process_is_not_signalled(self) -> None:
        argv, pid_file = self.command(detached=True)
        self.previewed(argv, pid_file)
        argv, pid_file = self.command(detached=True)
        # The table holds the pid with a later start: another actor killed it and the pid was reused.
        signalled = self.signalled_after_the_freeze(argv, restarted=pid_file)
        child = int(pid_file.read_text())
        self.assertNotIn(child, signalled, "that pid is another process's by now")
        with contextlib.suppress(OSError):
            os.kill(child, signal.SIGKILL)

    def test_a_start_within_the_slack_is_the_same_process(self) -> None:
        argv, pid_file = self.command(detached=True)
        self.previewed(argv, pid_file)
        argv, pid_file = self.command(detached=True)
        # Linux computes a start from the boot time, which a stepped clock moves between reads.
        self.signalled_after_the_freeze(argv, restarted=pid_file, later=state_tool.START_SLACK_SECONDS)
        self.assertTrue(self.gone(int(pid_file.read_text())), "killed, not left stopped")

    def test_a_start_within_the_slack_the_other_way_is_the_same_process(self) -> None:
        argv, pid_file = self.command(detached=True)
        self.previewed(argv, pid_file)
        argv, pid_file = self.command(detached=True)
        self.signalled_after_the_freeze(argv, restarted=pid_file, later=-state_tool.START_SLACK_SECONDS)
        self.assertTrue(self.gone(int(pid_file.read_text())), "killed, not left stopped")

    def test_a_start_past_the_slack_the_other_way_is_another_process(self) -> None:
        argv, pid_file = self.command(detached=True)
        self.previewed(argv, pid_file)
        argv, pid_file = self.command(detached=True)
        signalled = self.signalled_after_the_freeze(argv, restarted=pid_file, later=-60)
        child = int(pid_file.read_text())
        self.addCleanup(self.kill_quietly, child)
        self.assertNotIn(child, signalled, "that pid is another process's by now")

    def test_a_last_read_that_fails_still_kills_what_was_frozen(self) -> None:
        argv, pid_file = self.command(detached=True)
        self.previewed(argv, pid_file)
        argv, pid_file = self.command(detached=True)
        self.signalled_after_the_freeze(argv, last_read="unreadable")
        self.assertTrue(self.gone(int(pid_file.read_text())), "killed, not left stopped")

    def test_an_interrupted_last_read_still_kills_what_was_frozen_and_the_interrupt_arrives(self) -> None:
        argv, pid_file = self.command(detached=True)
        self.previewed(argv, pid_file)
        argv, pid_file = self.command(detached=True)
        with self.assertRaises(KeyboardInterrupt):
            self.signalled_after_the_freeze(argv, last_read="interrupted")
        self.assertTrue(self.gone(int(pid_file.read_text())), "killed, not left stopped")

    def test_a_group_member_the_kill_removed_is_still_reported(self) -> None:
        argv, pid_file = self.command(detached=False)
        self.previewed(argv, pid_file)
        argv, pid_file = self.command(detached=False)
        cleanup: list[dict] = []
        state_tool._run_bounded(argv, 20, 4096, cleanup=cleanup)
        child = int(pid_file.read_text())
        self.assertTrue(self.gone(child))
        self.assertIn(child, cleanup[0]["selected"], "whoever reads the result is told what was stopped")
        self.assertIn(f"[{child}]", state_tool._cleanup_note(cleanup).get("cleanup", ""))

    def test_a_frozen_process_missing_from_one_read_is_still_killed(self) -> None:
        argv, pid_file = self.command(detached=True)
        self.previewed(argv, pid_file)
        argv, pid_file = self.command(detached=True)
        # One read missed it; it is alive and stopped, and only this run would ever kill it.
        self.signalled_after_the_freeze(argv, without=pid_file)
        child = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self.gone_now(child):
            time.sleep(0.05)
        self.assertTrue(self.gone_now(child), "killed, not left stopped")

    def test_an_interrupt_before_the_first_read_leaves_what_it_never_selected_running(self) -> None:
        argv, pid_file = self.command(detached=True)
        real_table, calls = state_tool._process_table, []

        def table() -> dict | None:
            calls.append(True)
            if len(calls) == 1:
                raise KeyboardInterrupt
            return real_table()

        with mock.patch.object(state_tool, "_process_table", table), self.assertRaises(KeyboardInterrupt):
            state_tool._run_bounded(argv, 20, 4096)
        child = int(pid_file.read_text())
        self.addCleanup(lambda: self.kill_quietly(child))
        self.assertFalse(self.gone_now(child))
        self.assertNotIn("T", self.state(child), "never frozen")
        docstring = " ".join(state_tool._run_bounded.__doc__.split())
        self.assertIn("An interrupt during its first read of the process table leaves them running", docstring)

    @staticmethod
    def kill_quietly(pid: int) -> None:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)

    def test_a_command_reaped_elsewhere_is_previewed_without_its_group(self) -> None:
        argv, pid_file = self.command(detached=False)
        seen: list[dict] = []
        with mock.patch.object(state_tool, "_unreaped", return_value=False):
            code, _, _ = state_tool._run_bounded(argv, 20, 4096, preview=seen.append)
        self.assertEqual(code, 0)
        child = int(pid_file.read_text())
        # No group members and no group signal; the child still carries the run's marker.
        self.assertEqual((seen[0]["group"], seen[0]["selected"], seen[0]["whole_group"]), (None, [child], False), seen)
        self.assertFalse(self.gone_now(child))
        os.kill(child, signal.SIGKILL)

    def test_an_unreadable_process_table_stops_nothing_and_says_so(self) -> None:
        argv, pid_file = self.command(detached=False)
        seen: list[dict] = []
        with mock.patch.object(state_tool, "_process_table", return_value=None):
            code, _, _ = state_tool._run_bounded(argv, 20, 4096, preview=seen.append)
        self.assertEqual(code, 0)
        self.assertEqual((seen[0]["unreadable"], seen[0]["selected"]), (True, []))
        self.assertIn("unreadable", state_tool._describe_selection(seen[0]))
        os.kill(int(pid_file.read_text()), signal.SIGKILL)
        argv, pid_file = self.command(detached=False)
        done: list[dict] = []
        with mock.patch.object(state_tool, "_process_table", return_value=None):
            state_tool._run_bounded(argv, 20, 4096, cleanup=done)
        child = int(pid_file.read_text())
        self.assertFalse(self.gone_now(child), "fails closed: nothing is stopped")
        self.assertTrue(done[0]["unreadable"])

    def test_the_command_s_exit_is_seen_without_reaping_it_so_its_group_id_stays_reserved(self) -> None:
        process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        self.addCleanup(process.wait)
        self.assertTrue(state_tool._exited(process, 10))
        self.assertIsNone(process.returncode, "not reaped")
        os.kill(process.pid, 0)  # still there, as a zombie, holding its pid
        process.wait()
        self.assertEqual(process.returncode, 0)

    def test_where_waitid_is_missing_the_exit_is_seen_through_kqueue_without_reaping(self) -> None:
        import select
        if not hasattr(select, "kqueue"):
            self.skipTest("kqueue is the fallback only where the system has it")
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"], start_new_session=True)
        self.addCleanup(process.wait)
        waitid = getattr(os, "waitid", None)
        if waitid is not None:
            del os.waitid
        try:
            self.assertFalse(state_tool._exited(process, 0.05), "still running")
            self.assertTrue(state_tool._exited(process, 10))
            self.assertTrue(state_tool._exited(process, 0), "an exited, unreaped command still reads as exited")
        finally:
            if waitid is not None:
                os.waitid = waitid
        self.assertIsNone(process.returncode, "not reaped")
        os.kill(process.pid, 0)

    def test_the_selection_refuses_everything_outside_the_run(self) -> None:
        marker = b"RUN=abc"
        me, parent = os.getpid(), os.getppid()
        uid = os.geteuid()
        started = 1_000_000.0
        table = {
            # pid: (parent, group, uid, start)
            1: (0, 1, 0, 0.0),
            parent: (1, parent, uid, 10.0),
            me: (parent, me, uid, 20.0),
            500: (me, 500, uid, started + 1),        # the command: its own group
            501: (500, 500, uid, started + 2),       # left in the group
            502: (1, 502, uid, started + 3),         # detached, carries the marker
            503: (1, 503, uid + 1, started + 3),     # another user's, carries the marker
            504: (1, 504, uid, started - 60),        # started before the run, carries the marker
            505: (1, 505, uid, started + 3),         # unrelated
        }
        carriers = {1, parent, me, 502, 503, 504}
        environment = {pid: [b"PATH=/bin", marker] if pid in carriers else [b"PATH=/bin"] for pid in table}
        table[506] = (1, 506, uid, started + 3)
        environment[506] = [b"PATH=/bin", b"RUN=abcd", b"RUN=ab"]  # another run's marker, and prefixes of this one
        selection = state_tool._cleanup_selection(500, marker, started, table, environment.get, caller=me)
        self.assertEqual(selection["selected"], [501, 502], "the command itself has exited and is reaped, not signalled")
        refused = dict(selection["refused"])
        self.assertEqual(set(refused), {1, parent, me, 503, 504})
        self.assertIn("pid 1", refused[1])
        self.assertIn("caller", refused[me])
        self.assertIn("caller", refused[parent])
        self.assertIn("another user", refused[503])
        self.assertIn("before the run", refused[504])
        self.assertFalse(selection["unreadable"])
        line = state_tool._describe_selection(selection)
        self.assertNotIn("\n", line)
        self.assertIn("2 selected", line)
        self.assertIn("5 refused", line)
        unreadable = state_tool._cleanup_selection(500, marker, started, None, environment.get, caller=me)
        self.assertEqual((unreadable["selected"], unreadable["unreadable"]), ([], True))

    def test_a_group_member_another_user_owns_is_refused_and_the_group_signalled_one_by_one(self) -> None:
        marker = b"RUN=abc"
        me, uid, started = os.getpid(), os.geteuid(), 1_000_000.0
        table = {me: (1, me, uid, 1.0), 500: (me, 500, uid, started + 1), 501: (500, 500, uid + 1, started + 2),
                 502: (500, 500, uid, started + 2)}
        selection = state_tool._cleanup_selection(500, marker, started, table, lambda pid: [], caller=me)
        self.assertEqual(selection["selected"], [502])
        self.assertEqual([pid for pid, _ in selection["refused"]], [501])
        self.assertFalse(selection["whole_group"], "a refused member keeps the group from being signalled whole")


class NextTests(unittest.TestCase):
    """next --lane ranks the work a lane could take next, with a reason each, and assigns nothing."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "state"
        state_tool.init(self.directory, "Epic underway")
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version, changes={
            "meta": {"trackerParent": "epic-7"},
            "lanes": [lane("lane-1", state="idle"), lane("lane-2"), lane("lane-3", state="done")],
            # lane-2 is working on src/shared.py; lane-1 once landed src/mine.py; lane-3 is done.
            "planned": [{"subject": "busy", "title": "Busy", "lane": "lane-2", "stage": "planned"},
                        {"subject": "free", "title": "Free", "lane": None, "stage": "planned"},
                        {"subject": "taken", "title": "Taken", "lane": "lane-2", "stage": "planned"},
                        # lane-3 is done: what it still holds collides with nothing.
                        {"subject": "parked", "title": "Parked", "lane": "lane-3", "stage": "planned"}],
            "queue": [queue_item("landed", lane="lane-1"), queue_item("old", lane="lane-3", order=2),
                      queue_item("waiting", lane="lane-2", order=3)],
            "followups": [{"id": "waiting", "title": "Already queued", "reference": "waiting"},
                          {"id": "fix-a", "title": "Fix A", "reference": "fix-a"},
                          {"id": "fix-b", "title": "Fix B", "reference": "fix-b"},
                          {"id": "fix-c", "title": "Fix C", "reference": "fix-c"},
                          {"id": "fix-d", "title": "Fix D", "reference": "fix-d"},
                          {"id": "gone", "title": "Gone", "reference": "gone"}],
        })
        for subject in ("landed", "old"):
            version = state_tool.show(self.directory)["state"]["version"]
            state_tool.patch(self.directory, writer="runner", version=version, selector=f"queue[subject={subject}]",
                             changes={"stage": "merged"})

    def contents(self) -> dict:
        return {str(path.relative_to(self.directory)): path.read_bytes() if path.is_file() else None
                for path in sorted(self.directory.rglob("*"))}

    def write(self, name: str, document: dict) -> str:
        path = self.root / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return str(path)

    def snapshot(self, items: list[dict]) -> str:
        return self.write("tracker.json", {"takenAt": taken(), "source": "tracker", "items": items})

    def hints(self, items: dict) -> str:
        return self.write("hints.json", {"items": items})

    def ranked(self, snapshot: str, hints: str | None = None, *extra: str) -> list[dict]:
        before = self.contents()
        code, result = run_tool(self.directory, "next", "--lane", "lane-1", "--snapshot", snapshot,
                                *(("--hints", hints) if hints else ()), *(extra or ("--limit", "20")))
        self.assertEqual(code, 0, result)
        self.assertEqual(self.contents(), before, "next assigns nothing and writes nothing")
        self.assertEqual(result["lane"], "lane-1")
        return result["next"]

    def test_unblocked_non_colliding_higher_priority_work_ranks_first_and_each_says_why(self) -> None:
        snapshot = self.snapshot([
            {"subject": "free", "state": "backlog"},
            {"subject": "fix-a", "state": "backlog", "blockedBy": ["busy"]},        # blocked by unfinished work
            {"subject": "fix-b", "state": "backlog"},
            {"subject": "fix-c", "state": "backlog", "blockedBy": ["landed-ticket"]},
            {"subject": "landed-ticket", "state": "completed"},
            # Blocked only by work the record shows merged, which the snapshot does not list.
            {"subject": "fix-d", "state": "backlog", "blockedBy": ["landed"]},
            {"subject": "busy", "state": "started"},
            {"subject": "gone", "state": "canceled"},
        ])
        hints = self.hints({
            "fix-a": {"files": ["docs/a.md"], "priority": 1},                       # urgent, but blocked
            "fix-b": {"files": ["src/shared.py"], "priority": 2},                   # collides with lane-2
            "fix-c": {"files": ["docs/d.md"], "priority": 3},                       # lane-3's, but lane-3 is done
            "fix-d": {"files": ["src/mine.py"], "priority": 3},                     # lane-1's own file
            "parked": {"files": ["docs/d.md"]},
            "free": {"files": ["docs/free.md"], "priority": 2},
            "busy": {"files": ["src/shared.py"], "priority": 2},
            "landed": {"files": ["src/mine.py"]},
            "old": {"files": ["docs/d.md"]},
        })
        ranked = self.ranked(snapshot, hints)
        order = [item["subject"] for item in ranked]
        # Never: work another lane holds, completed or canceled work, or work already queued.
        self.assertEqual(sorted(order), ["fix-a", "fix-b", "fix-c", "fix-d", "free"])
        self.assertEqual(order[0], "free", ranked)
        self.assertLess(order.index("fix-d"), order.index("fix-c"), "the lane's own files first, whatever the order")
        self.assertLess(order.index("fix-c"), order.index("fix-b"), "a collision ranks below a lower priority")
        self.assertEqual(order[-1], "fix-a", "blocked work is never ahead of unblocked work")
        reasons = {item["subject"]: item["reason"] for item in ranked}
        self.assertIn("blocked by busy", reasons["fix-a"])
        self.assertIn("lane-2", reasons["fix-b"])
        self.assertIn("src/shared.py", reasons["fix-b"])
        self.assertIn("src/mine.py", reasons["fix-d"])
        self.assertIn("unblocked", reasons["fix-d"], "a blocker the record shows merged is done")
        self.assertEqual(next(item for item in ranked if item["subject"] == "fix-c")["collidesWith"], [],
                         "a done lane holds nothing")
        self.assertIn("priority 2", reasons["free"])
        for item in ranked:
            self.assertNotIn("\n", item["reason"])
            self.assertTrue(item["reason"].startswith(item["subject"] + ":"), item)
        self.assertEqual([item["rank"] for item in ranked], list(range(1, len(ranked) + 1)))

    def test_the_hints_blockers_rank_work_after_its_open_dependency_when_the_tracker_has_no_blocked_by(self) -> None:
        # The tracker knows no order: nothing carries blockedBy. The plan's order reaches next only through the hints.
        snapshot = self.snapshot([{"subject": "fix-a", "state": "backlog"}, {"subject": "fix-b", "state": "backlog"},
                                  {"subject": "busy", "state": "started"}])
        unordered = {"fix-a": {"files": ["docs/a.md"], "priority": 1}, "fix-b": {"files": ["docs/b.md"], "priority": 2}}
        order = [item["subject"] for item in self.ranked(snapshot, self.hints(unordered))]
        self.assertLess(order.index("fix-a"), order.index("fix-b"), "without the plan's order, priority decides")
        ordered = {**unordered, "fix-a": {**unordered["fix-a"], "blockers": ["busy"]}}
        ranked = self.ranked(snapshot, self.hints(ordered))
        order = [item["subject"] for item in ranked]
        self.assertLess(order.index("fix-b"), order.index("fix-a"), "work after an open dependency ranks after the rest")
        fix_a = next(item for item in ranked if item["subject"] == "fix-a")
        self.assertTrue(fix_a["blocked"])
        self.assertIn("blocked by busy", fix_a["reason"])
        # A blocker both the tracker and the hints name is named once.
        both = self.snapshot([{"subject": "fix-a", "state": "backlog", "blockedBy": ["busy"]},
                              {"subject": "busy", "state": "started"}])
        reason = next(item for item in self.ranked(both, self.hints(ordered)) if item["subject"] == "fix-a")["reason"]
        self.assertEqual(reason.count("busy"), 1, reason)

    def test_a_hints_dependency_merged_in_the_record_or_completed_in_the_tracker_blocks_no_more(self) -> None:
        for label, dependency, extra in (("merged in the record", "landed", []),
                                         ("completed in the tracker", "done-ticket",
                                          [{"subject": "done-ticket", "state": "completed"}])):
            with self.subTest(label):
                snapshot = self.snapshot([{"subject": "fix-a", "state": "backlog"},
                                          {"subject": "fix-b", "state": "backlog"},
                                          {"subject": "busy", "state": "started"}, *extra])
                # fix-a's dependency is done; fix-b's is still open, and fix-b has the higher priority.
                ranked = self.ranked(snapshot, self.hints({
                    "fix-a": {"files": ["docs/a.md"], "priority": 3, "blockers": [dependency]},
                    "fix-b": {"files": ["docs/b.md"], "priority": 1, "blockers": ["busy"]}}))
                order = [item["subject"] for item in ranked]
                self.assertLess(order.index("fix-a"), order.index("fix-b"), ranked)
                fix_a = next(item for item in ranked if item["subject"] == "fix-a")
                self.assertFalse(fix_a["blocked"])
                self.assertIn("unblocked", fix_a["reason"])

    def test_a_dependency_settled_with_no_change_blocks_no_more(self) -> None:
        # The record holds the dependency settled with no change needed: never queued, never merged, done all the same.
        version = state_tool.show(self.directory)["state"]["version"]
        planned = state_tool.show(self.directory)["state"]["planned"]
        state_tool.patch(self.directory, writer="lead", version=version, changes={"planned": [*planned, {
            "subject": "settled-dep", "title": "Already held", "lane": "lane-2", "stage": "settled",
            "noChange": {"evidence": "settled-dep-1 holds", "baseHead": "a" * 40, "handedAt": taken(),
                         "settledAt": taken()}}]})
        # The tracker still shows it in the backlog, as it may: only the record says it is done.
        snapshot = self.snapshot([{"subject": "fix-a", "state": "backlog"}, {"subject": "fix-b", "state": "backlog"},
                                  {"subject": "busy", "state": "started"}, {"subject": "settled-dep", "state": "backlog"}])
        ranked = self.ranked(snapshot, self.hints({
            "fix-a": {"files": ["docs/a.md"], "priority": 3, "blockers": ["settled-dep"]},
            "fix-b": {"files": ["docs/b.md"], "priority": 1, "blockers": ["busy"]}}))
        order = [item["subject"] for item in ranked]
        self.assertLess(order.index("fix-a"), order.index("fix-b"), ranked)
        fix_a = next(item for item in ranked if item["subject"] == "fix-a")
        self.assertFalse(fix_a["blocked"], fix_a)
        self.assertIn("unblocked", fix_a["reason"])
        self.assertNotIn("settled-dep", order, "settled work is never next")

    def test_hints_blockers_are_a_list_of_subjects(self) -> None:
        snapshot = self.snapshot([{"subject": "fix-a", "state": "backlog"}])
        for blockers in ("busy", [""], [7]):
            with self.subTest(blockers=blockers):
                code, result = run_tool(self.directory, "next", "--lane", "lane-1", "--snapshot", snapshot,
                                        "--hints", self.hints({"fix-a": {"blockers": blockers}}))
                self.assertNotEqual(code, 0, result)
                self.assertIn("blockers", json.dumps(result))

    def test_a_blocked_item_is_never_first_even_alone_with_the_highest_priority(self) -> None:
        snapshot = self.snapshot([{"subject": "free", "state": "backlog", "blockedBy": ["outside"]},
                                  {"subject": "fix-a", "state": "backlog"}])
        ranked = self.ranked(snapshot, self.hints({"free": {"files": [], "priority": 1},
                                                   "fix-a": {"files": [], "priority": None}}))
        order = [item["subject"] for item in ranked]
        self.assertNotEqual(order[0], "free")
        self.assertLess(order.index("fix-a"), order.index("free"))
        free = next(item for item in ranked if item["subject"] == "free")
        self.assertTrue(free["blocked"])
        self.assertTrue(all(item["blocked"] for item in ranked[order.index("free"):]), "only blocked work after it")
        self.assertIn("outside", free["reason"])
        self.assertIn("not in the snapshot", free["reason"])

    def test_a_collision_ranks_below_a_non_colliding_item_of_the_same_priority(self) -> None:
        snapshot = self.snapshot([{"subject": "fix-a", "state": "backlog"}, {"subject": "fix-b", "state": "backlog"}])
        for colliding in (["src/shared.py"], ["src/"], ["src/*.py"]):
            with self.subTest(colliding=colliding):
                ranked = self.ranked(snapshot, self.hints({
                    "fix-a": {"files": colliding, "priority": 2}, "fix-b": {"files": ["docs/b.md"], "priority": 2},
                    "busy": {"files": ["src/shared.py"]}}))
                order = [item["subject"] for item in ranked]
                self.assertLess(order.index("fix-b"), order.index("fix-a"), ranked)
                self.assertEqual(next(item for item in ranked if item["subject"] == "fix-a")["collidesWith"], ["lane-2"])

    def test_without_hints_the_ranking_still_works_and_says_files_are_unknown(self) -> None:
        snapshot = self.snapshot([{"subject": "fix-a", "state": "backlog", "blockedBy": ["fix-b"]},
                                  {"subject": "fix-b", "state": "backlog"}])
        ranked = self.ranked(snapshot)
        order = [item["subject"] for item in ranked]
        self.assertLess(order.index("fix-b"), order.index("fix-a"))
        self.assertIn("no files declared", ranked[0]["reason"])

    def test_the_limit_shortens_the_list(self) -> None:
        snapshot = self.snapshot([{"subject": name, "state": "backlog"} for name in ("fix-a", "fix-b", "fix-c")])
        self.assertEqual(len(self.ranked(snapshot, None, "--limit", "2")), 2)

    def test_an_unknown_lane_a_forge_snapshot_or_malformed_hints_are_refused(self) -> None:
        tracker = self.snapshot([{"subject": "fix-a", "state": "backlog"}])
        code, refused = run_tool(self.directory, "next", "--lane", "lane-9", "--snapshot", tracker)
        self.assertEqual((code, refused["code"]), (1, "lane_unknown"), refused)
        forge = self.write("forge.json", {"takenAt": taken(), "source": "forge", "items": []})
        code, refused = run_tool(self.directory, "next", "--lane", "lane-1", "--snapshot", forge)
        self.assertEqual((code, refused["code"]), (1, "snapshot_invalid"), refused)
        for bad in ({"items": {"fix-a": {"priority": 9}}}, {"items": {"fix-a": {"files": "src"}}}, {"items": []},
                    {"items": {"fix-a": {"owner": "x"}}}):
            with self.subTest(bad=bad):
                code, refused = run_tool(self.directory, "next", "--lane", "lane-1", "--snapshot", tracker,
                                         "--hints", self.write("bad.json", bad))
                self.assertEqual((code, refused["code"]), (1, "hints_invalid"), refused)

    def hints_binding(self, document: object, *, exit_code: int = 0) -> str:
        tool = self.root / "hints tool.py"
        tool.write_text(textwrap.dedent(f"""
            import json, sys
            print(json.dumps({document!r}))
            sys.exit({exit_code})
        """), encoding="utf-8")
        return self.write("binding.json", {"state": {"directory": "state", "renderers": [], "publishers": [],
                                                     "workHints": {"argv": [sys.executable, "-B", str(tool), "{parent}"],
                                                                   "timeoutSeconds": 20}}})

    def test_the_binding_s_command_produces_the_hints(self) -> None:
        snapshot = self.snapshot([{"subject": "fix-a", "state": "backlog"}, {"subject": "fix-b", "state": "backlog"}])
        binding = self.hints_binding({"items": {"fix-a": {"files": ["src/shared.py"], "priority": 2},
                                                "fix-b": {"files": ["docs/b.md"], "priority": 2},
                                                "busy": {"files": ["src/shared.py"]}}})
        code, result = run_tool(self.directory, "next", "--lane", "lane-1", "--snapshot", snapshot,
                                "--binding", binding, "--limit", "20")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["hints"], "command")
        order = [item["subject"] for item in result["next"]]
        self.assertLess(order.index("fix-b"), order.index("fix-a"), "the command's files were used")

    def test_a_hints_file_is_the_fallback_when_the_command_fails(self) -> None:
        snapshot = self.snapshot([{"subject": "fix-a", "state": "backlog"}])
        binding = self.hints_binding({"items": {}}, exit_code=3)
        hints = self.hints({"fix-a": {"files": ["docs/a.md"], "priority": 1}})
        code, result = run_tool(self.directory, "next", "--lane", "lane-1", "--snapshot", snapshot,
                                "--binding", binding, "--hints", hints, "--limit", "20")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["hints"], "file")
        self.assertIn("exited with 3", result["hintsNote"])
        self.assertIn("priority 1", next(item for item in result["next"] if item["subject"] == "fix-a")["reason"])
        for document in ("not json", {"items": {"fix-a": {"priority": 7}}}):
            with self.subTest(document=document):
                tool = self.root / "hints tool.py"
                tool.write_text(f"print({json.dumps(document) if isinstance(document, dict) else document!r})\n",
                                encoding="utf-8")
                code, result = run_tool(self.directory, "next", "--lane", "lane-1", "--snapshot", snapshot,
                                        "--binding", binding, "--hints", hints)
                self.assertEqual((code, result["hints"]), (0, "file"), result)
                self.assertTrue(result["hintsNote"])
        code, result = run_tool(self.directory, "next", "--lane", "lane-1", "--snapshot", snapshot,
                                "--binding", binding)
        self.assertEqual((code, result["hints"]), (0, "none"), result)

    def test_queued_work_collides_on_its_changed_files_and_the_reason_says_so(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version,
                         changes={"queue": [*state_tool.show(self.directory)["state"]["queue"],
                                            queue_item("in-flight", lane="lane-2", order=4)]})
        snapshot = self.snapshot([{"subject": "fix-a", "state": "backlog"}, {"subject": "fix-b", "state": "backlog"}])
        hints = {"in-flight": {"files": ["src/"], "changedFiles": ["src/engine.py"]},
                 "fix-a": {"files": ["src/shared.py"]}, "fix-b": {"files": ["src/engine.py"]}}
        ranked = {item["subject"]: item for item in self.ranked(snapshot, self.hints(hints))}
        # The declared folder would catch both; the change touches only src/engine.py.
        self.assertEqual(ranked["fix-a"]["collidesWith"], [], "src/shared.py is outside what in-flight changed")
        self.assertEqual(ranked["fix-b"]["collidesWith"], ["lane-2"])
        self.assertIn("src/engine.py", ranked["fix-b"]["reason"])
        self.assertIn("changed files", ranked["fix-b"]["reason"])
        # Planned work with no change yet collides on what it declares, and says so.
        hints["busy"] = {"files": ["src/shared.py"]}
        ranked = {item["subject"]: item for item in self.ranked(snapshot, self.hints(hints))}
        self.assertEqual(ranked["fix-a"]["collidesWith"], ["lane-2"])
        self.assertIn("declared files", ranked["fix-a"]["reason"])

    def test_an_empty_or_partial_change_never_hides_declared_files(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version,
                         changes={"queue": [*state_tool.show(self.directory)["state"]["queue"],
                                            queue_item("fresh", lane="lane-2", order=4)]})
        snapshot = self.snapshot([{"subject": name, "state": "backlog"} for name in ("fix-a", "fix-b", "fix-c")])
        ranked = {item["subject"]: item for item in self.ranked(snapshot, self.hints({
            # Queued with no commits yet: what it declares still counts.
            "fresh": {"files": ["core/"], "changedFiles": []},
            # Planned, part done: both what it declares and what it changed count.
            "busy": {"files": ["lib/a.py", "lib/b.py"], "changedFiles": ["lib/a.py"]},
            "fix-a": {"files": ["core/x.py"], "priority": 1},
            "fix-b": {"files": ["lib/b.py"], "priority": 1},
            "fix-c": {"files": ["docs/c.md"], "changedFiles": []},
        }))}
        self.assertEqual(ranked["fix-a"]["collidesWith"], ["lane-2"])
        self.assertIn("declared files", ranked["fix-a"]["reason"])
        self.assertEqual(ranked["fix-b"]["collidesWith"], ["lane-2"])
        self.assertIn("declared and changed files", ranked["fix-b"]["reason"])
        self.assertNotIn("no files declared", ranked["fix-c"]["reason"])

    def test_a_follow_up_another_lane_holds_under_its_id_is_not_offered(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version, changes={"followups": [
            {"id": "busy", "title": "Held by lane-2 under its id", "reference": "TRK-5"},
            {"id": "fu-6", "title": "Held by lane-2 under its reference", "reference": "taken"}]})
        ranked = self.ranked(self.snapshot([{"subject": "TRK-5", "state": "backlog"},
                                            {"subject": "taken", "state": "backlog"}]))
        self.assertNotIn("TRK-5", [item["subject"] for item in ranked])
        self.assertNotIn("taken", [item["subject"] for item in ranked])

    def test_the_result_says_why_hints_came_from_where_they_did(self) -> None:
        snapshot = self.snapshot([{"subject": "fix-a", "state": "backlog"}])
        tool = self.root / "hints tool.py"
        tool.write_text("import sys\nprint('the tracker refused the request', file=sys.stderr)\nsys.exit(4)\n",
                        encoding="utf-8")
        binding = self.write("binding.json", {"state": {"directory": "state", "renderers": [], "publishers": [],
                                                        "workHints": {"argv": [sys.executable, "-B", str(tool)]}}})
        code, result = run_tool(self.directory, "next", "--lane", "lane-1", "--snapshot", snapshot, "--binding", binding)
        self.assertEqual((code, result["hints"]), (0, "none"), result)
        self.assertIn("exited with 4: the tracker refused the request", result["hintsNote"])
        self.assertIn("no hints", result["hintsNote"])
        working = self.hints_binding({"items": {}})
        code, result = run_tool(self.directory, "next", "--lane", "lane-1", "--snapshot", snapshot,
                                "--binding", working, "--hints", self.hints({}))
        self.assertEqual((code, result["hints"]), (0, "command"), result)
        self.assertIn("was not read", result["hintsNote"])

    def test_the_binding_and_the_render_wrapper_take_a_work_hints_block(self) -> None:
        schema = state_tool.load_schema("orchestration-binding/1")
        member = {**schema["properties"]["state"], "$defs": schema["$defs"]}
        block = {"argv": ["tool", "{parent}"], "timeoutSeconds": 30}
        base = {"directory": "s", "renderers": [], "publishers": []}
        self.assertEqual(state_tool.validate({**base, "workHints": block}, member), [])
        self.assertTrue(state_tool.validate({**base, "workHints": {"argv": ["tool"], "extra": 1}}, member))
        _load("render_state").parse_config({**base, "workHints": block})
        for bad in ({"argv": []}, {"argv": ["tool"], "timeoutSeconds": 0}, {"argv": ["tool"], "extra": 1}):
            with self.subTest(bad=bad):
                code, refused = run_tool(self.directory, "next", "--lane", "lane-1",
                                         "--snapshot", self.snapshot([]),
                                         "--binding", self.write("bad binding.json",
                                                                 {"state": {**base, "workHints": bad}}))
                self.assertEqual((code, refused["code"]), (1, "binding_invalid"), refused)

    def test_a_follow_up_is_the_tracker_s_item_named_by_its_reference(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="lead", version=version, changes={"followups": [
            {"id": "fu-1", "title": "Blocked in the tracker", "reference": "TRK-9"},
            {"id": "fu-2", "title": "Done in the tracker", "reference": "TRK-8"},
            {"id": "fu-3", "title": "Free", "reference": None},
        ]})
        snapshot = self.snapshot([{"subject": "TRK-9", "state": "backlog", "blockedBy": ["TRK-1"]},
                                  {"subject": "TRK-1", "state": "started"},
                                  {"subject": "TRK-8", "state": "completed"},
                                  {"subject": "fu-3", "state": "backlog"}])
        ranked = self.ranked(snapshot, self.hints({"TRK-9": {"priority": 1}, "fu-3": {"priority": 4}}))
        order = [item["subject"] for item in ranked]
        self.assertNotIn("TRK-8", order, "completed in the tracker")
        self.assertLess(order.index("fu-3"), order.index("TRK-9"), "a blocked follow-up is never ahead")
        blocked = next(item for item in ranked if item["subject"] == "TRK-9")
        self.assertTrue(blocked["blocked"])
        self.assertIn("priority 1", blocked["reason"], "hints are keyed by the reference")

    def test_the_lane_s_own_work_in_review_is_not_next_and_never_owns_itself(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        state = state_tool.show(self.directory)["state"]
        state_tool.patch(self.directory, writer="lead", version=version, changes={"planned": [
            *state["planned"],
            {"subject": "mine-review", "title": "In review", "lane": "lane-1", "stage": "review"},
            {"subject": "nobody-review", "title": "In review, no lane", "lane": None, "stage": "review"},
            {"subject": "mine-next", "title": "Planned for me", "lane": "lane-1", "stage": "planned"},
        ]})
        snapshot = self.snapshot([{"subject": "mine-review", "state": "started"},
                                  {"subject": "mine-next", "state": "backlog"}])
        ranked = self.ranked(snapshot, self.hints({"mine-next": {"files": ["lib/next.py"]},
                                                   "mine-review": {"files": ["lib/review.py"]}}))
        order = [item["subject"] for item in ranked]
        self.assertNotIn("mine-review", order)
        self.assertNotIn("nobody-review", order, "work in review is nobody's next work")
        mine = next(item for item in ranked if item["subject"] == "mine-next")
        self.assertFalse(mine["owned"], "its own files are not a sign the lane worked on them")

    def test_a_glob_collides_with_a_folder_or_a_glob_that_can_name_the_same_file(self) -> None:
        overlap = state_tool._overlap
        for first, second in (("src/**/x.py", "src/"), ("src/*.md", "src/docs/"), ("src/*.py", "src/a*.py"),
                              ("**/x.py", "src/"), ("src/a.py", "src/*.py"), ("src/", "src/a/b.py")):
            with self.subTest(first=first, second=second):
                self.assertTrue(overlap(first, second))
                self.assertTrue(overlap(second, first))
        for first, second in (("docs/*.md", "src/"), ("src/a.py", "src/b.py"), ("lib/*.py", "src/*.py"),
                              ("src/a.py", "docs/*.md")):
            with self.subTest(first=first, second=second):
                self.assertFalse(overlap(first, second))
                self.assertFalse(overlap(second, first))

    def test_every_rule_holds_where_only_it_decides(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        state = state_tool.show(self.directory)["state"]
        state_tool.patch(self.directory, writer="lead", version=version, changes={
            "lanes": [*state["lanes"], lane("lane-4", state="idle")],
            "planned": [*state["planned"],
                        {"subject": "idle-work", "title": "Idle lane's", "lane": "lane-4", "stage": "planned"},
                        {"subject": "finished", "title": "Done in the tracker", "lane": "lane-2", "stage": "planned"}],
            "followups": [*state["followups"],
                          {"id": "taken", "title": "Held by lane-2 as planned work", "reference": "taken"},
                          {"id": "free", "title": "Also planned, unassigned", "reference": "free"},
                          {"id": "ghost", "title": "Not in the snapshot", "reference": "ghost"},
                          {"id": "tie-1", "title": "Tie 1", "reference": "tie-1"},
                          {"id": "tie-2", "title": "Tie 2", "reference": "tie-2"},
                          {"id": "none-p", "title": "No priority", "reference": "none-p"},
                          {"id": "low-p", "title": "Priority 4", "reference": "low-p"},
                          {"id": "on-idle", "title": "Touches the idle lane's files", "reference": "on-idle"},
                          {"id": "on-done", "title": "Touches finished work's files", "reference": "on-done"},
                          {"id": "on-queue", "title": "Touches queued work's files", "reference": "on-queue"},
                          {"id": "after-cancel", "title": "Blocked by canceled work", "reference": "after-cancel"},
                          {"id": "dotted", "title": "Dotted path", "reference": "dotted"},
                          {"id": "float-p", "title": "Float priority", "reference": "float-p"},
                          {"id": "on-merged", "title": "Touches merged work's files", "reference": "on-merged"}],
            "queue": [*state["queue"], queue_item("merged-2", lane="lane-2", order=5)],
        })
        version = state_tool.show(self.directory)["state"]["version"]
        state_tool.patch(self.directory, writer="runner", version=version, selector="queue[subject=merged-2]",
                         changes={"stage": "merged"})
        items = ["free", "taken", "tie-1", "tie-2", "none-p", "low-p", "on-idle", "on-done", "on-queue", "dotted",
                 "float-p", "on-merged", "fix-a", "fix-b", "fix-c", "fix-d"]
        snapshot = self.snapshot([
            *({"subject": name, "state": "backlog"} for name in items),
            {"subject": "after-cancel", "state": "backlog", "blockedBy": ["dropped"]},
            {"subject": "dropped", "state": "canceled"},
            {"subject": "finished", "state": "completed"},
        ])
        hints = self.hints({
            "idle-work": {"files": ["idle/"]}, "on-idle": {"files": ["idle/x.py"], "priority": 3},
            "finished": {"files": ["done/"]}, "on-done": {"files": ["done/x.py"], "priority": 3},
            "waiting": {"files": ["queue/"]}, "on-queue": {"files": ["queue/x.py"], "priority": 3},
            "busy": {"files": ["src/shared.py"]}, "dotted": {"files": ["./src//shared.py"], "priority": 3},
            "tie-1": {"priority": 3}, "tie-2": {"priority": 3}, "none-p": {"priority": None},
            "low-p": {"priority": 4}, "float-p": {"priority": 2.0}, "ghost": {"priority": 1},
            "after-cancel": {"priority": 3},
            "merged-2": {"files": ["merged/"]}, "on-merged": {"files": ["merged/x.py"], "priority": 3},
        })
        ranked = self.ranked(snapshot, hints)
        order = [item["subject"] for item in ranked]
        by = {item["subject"]: item for item in ranked}
        self.assertEqual(order.count("free"), 1, "planned and follow-up are one item")
        self.assertNotIn("taken", order, "held by another lane")
        self.assertLess(order.index("tie-1"), order.index("tie-2"), "the record's order breaks ties")
        self.assertLess(order.index("low-p"), order.index("none-p"), "no priority after priority 4")
        self.assertEqual(by["on-idle"]["collidesWith"], [], "an idle lane holds nothing")
        self.assertEqual(by["on-done"]["collidesWith"], [], "finished in the tracker")
        self.assertEqual(by["on-queue"]["collidesWith"], ["lane-2"], "queued, not merged")
        self.assertEqual(by["dotted"]["collidesWith"], ["lane-2"], "a path is compared normalised")
        self.assertFalse(by["after-cancel"]["blocked"], "a canceled blocker is done")
        self.assertIs(type(by["float-p"]["priority"]), int)
        self.assertTrue(by["float-p"]["reason"].endswith("priority 2"), by["float-p"]["reason"])
        self.assertEqual(by["on-merged"]["collidesWith"], [], "merged work holds nothing")
        self.assertIn("not in the snapshot", by["ghost"]["reason"])
        self.assertFalse(by["ghost"]["listed"])
        listed_unblocked = [name for name in order if by[name]["listed"] and not by[name]["blocked"]]
        self.assertLess(max(order.index(name) for name in listed_unblocked), order.index("ghost"),
                        "work the snapshot does not list comes after work it shows unblocked")

    def test_a_path_outside_the_repository_is_refused(self) -> None:
        snapshot = self.snapshot([{"subject": "fix-a", "state": "backlog"}])
        for path in ("/etc/passwd", "../outside.py", "src/../../x", "."):
            with self.subTest(path=path):
                code, refused = run_tool(self.directory, "next", "--lane", "lane-1", "--snapshot", snapshot,
                                         "--hints", self.hints({"fix-a": {"files": [path]}}))
                self.assertEqual((code, refused["code"]), (1, "hints_invalid"), refused)

    def test_the_hints_schema_is_one_of_the_skill_s_schemas(self) -> None:
        schema = state_tool.load_schema("work-hints/1")
        self.assertEqual(state_tool.validate({"items": {"x": {"files": ["a/b.py", "c/"], "priority": 1,
                                                               "changedFiles": ["a/b.py"]}}}, schema), [])
        self.assertTrue(state_tool.validate({"items": {"x": {"priority": 0}}}, schema))


class ShowTests(unittest.TestCase):
    """What `show` adds beside the record: only a recovery still pending. Nothing records what was published, and an
    outbox or a `published.json` an earlier generation left is neither read nor reported."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "state"
        state_tool.init(self.directory, "Epic underway")
        state_tool.append_journal(self.directory, kind="decision.recorded", summary="Decision 1")

    def test_show_reports_only_a_pending_recovery_whatever_an_earlier_generation_left(self) -> None:
        code, shown = run_tool(self.directory, "show")
        self.assertEqual((code, shown["publication"]), (0, {"recoveryPending": False}))
        (self.directory / "outbox").mkdir()
        (self.directory / "outbox" / "outbox.json").write_text("{not json")
        (self.directory / "published.json").write_text(json.dumps({"version": 1, "journalThrough": 1, "at": "x"}))
        code, shown = run_tool(self.directory, "show")
        self.assertEqual((code, shown["publication"]), (0, {"recoveryPending": False}), "neither is read")
        (self.directory / "recovery.json").write_text("{}")
        code, shown = run_tool(self.directory, "show")
        self.assertEqual(shown["publication"], {"recoveryPending": True})
        # What renderers read: the state, the journal, and the stats, estimates, status and focus folded from them.
        self.assertEqual(sorted(state_tool.show(self.directory)), ["estimates", "focus", "journal", "state", "stats", "status"])

    def test_ack_and_outbox_are_unknown_commands(self) -> None:
        for args in (["ack"], ["outbox", "--retire", "live"]):
            with self.subTest(args=args):
                done = subprocess.run([sys.executable, "-B", str(TOOL), "--dir", str(self.directory), *args],
                                      capture_output=True, text=True)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn(f"invalid choice: '{args[0]}'", done.stderr)
        self.assertFalse((self.directory / "published.json").exists())
        self.assertFalse(hasattr(state_tool, "ack") or hasattr(state_tool, "read_outbox"))


def valid_state() -> dict:
    state = state_tool.initial_state("Fixture")
    state["meta"]["updatedAt"] = "2026-10-20T10:00:00.5Z"
    state["meta"]["baseHead"] = HEAD_A
    state["meta"]["checking"] = {
        "now": {"subject": "pr-1", "title": "Candidate", "startedAt": "2026-10-20T10:00:00Z",
                "steps": [{"label": "sync", "state": "passed"}]},
        "next": ["pr-2"],
    }
    state["lanes"] = [{"id": "lane-1", "title": "Renderer", "focus": "views", "state": "handed",
                       "waitingOn": "lead", "since": "2026-10-20T09:00:00Z"}]
    state["queue"] = [queue_item("pr-1", syncedHead=HEAD_B, runId="run-1",
                                 checks=[{"id": "unit", "state": "timed_out", "detail": "killed"}])]
    state["finish"] = [{"id": "release", "text": "A release ships", "met": False}]
    state["followups"] = [{"id": "f-1", "title": "Later", "reference": None}]
    state["leases"] = [{"id": "dev-backend", "holder": "lead", "since": "2026-10-20T08:00:00Z"}]
    state["extensions"] = {"adopter": {"anything": [1, "two"]}}
    return state


def with_change(document: dict, path: tuple, value: object) -> dict:
    changed = copy.deepcopy(document)
    target = changed
    for part in path[:-1]:
        target = target[part]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return changed


VALID_ENTRY = {"id": 1, "at": "2026-10-20T10:00:00Z", "kind": "candidate.merged", "lane": None,
               "subject": "pr-1", "summary": "Merged"}


SNAPSHOT = {"takenAt": "2026-10-20T10:00:00Z", "source": "tracker",
            "items": [{"subject": "pr-1", "title": "Candidate", "state": "started"}]}
VALID_FIXTURES = {
    "valid", "fresh", "version as whole float", "long head", "null rounds", "checking now null", "with lane",
    "with tickets", "snapshot with a summary", "planned with a run", "planned with its ticket started",
    "queued with its ticket started", "lane wait set by the queue", "a decision carrying a review",
    "queue check with a title", "queue check with its node", "step with a title", "a close started", "a close finished", "queue check with its base re-run",
    "checking now awaiting the base rerun",
}
JOURNAL_KINDS = [
    "lane.assigned", "review.round", "candidate.handed", "check.started", "check.finished",
    "check.failed", "check.retried", "check.reused", "candidate.merged", "candidate.stopped", "base.verified", "base.broken",
    "base.repaired", "base.rerun.started", "base.rerun.finished",
    "lease.granted", "lease.released", "release.started", "release.finished",
    "finish.met", "decision.recorded", "blocker.recorded", "delivery.closed", "review.evidence", "check.selected",
    "selection.refused", "hook.held",
]


class SchemaAgreementTests(unittest.TestCase):
    def fixtures(self) -> dict[str, list[tuple[str, object]]]:
        state = valid_state()
        return {
            "delivery-state/1": [
                ("valid", state),
                ("fresh", state_tool.initial_state("Fresh")),
                ("version as whole float", with_change(state, ("version",), 2.0)),
                ("version zero", with_change(state, ("version",), 0)),
                ("version as boolean", with_change(state, ("version",), True)),
                ("version as text", with_change(state, ("version",), "2")),
                ("wrong schema version", with_change(state, ("schemaVersion",), "delivery-state/2")),
                ("missing extensions", with_change(state, ("extensions",), KeyError)),
                ("extensions not an object", with_change(state, ("extensions",), [])),
                ("unknown top-level field", with_change(state, ("ledger",), [])),
                ("bad timestamp", with_change(state, ("meta", "updatedAt"), "yesterday")),
                ("short head", with_change(state, ("queue", 0, "head"), "abc123")),
                ("long head", with_change(state, ("queue", 0, "head"), "c" * 64)),
                ("null head", with_change(state, ("queue", 0, "head"), None)),
                ("unknown stage", with_change(state, ("queue", 0, "stage"), "lost")),
                ("negative rounds", with_change(state, ("queue", 0, "reportedRounds"), -1)),
                ("null rounds", with_change(state, ("queue", 0, "reportedRounds"), None)),
                ("empty subject", with_change(state, ("queue", 0, "subject"), "")),
                ("check without state", with_change(state, ("queue", 0, "checks"), [{"id": "unit"}])),
                ("unknown lane state", with_change(state, ("lanes", 0, "state"), "napping")),
                ("waitingOn as number", with_change(state, ("lanes", 0, "waitingOn"), 3)),
                ("lane wait set by the queue", with_change(state, ("lanes", 0, "waitingBy"), "runner")),
                ("lane wait set by someone else", with_change(state, ("lanes", 0, "waitingBy"), "someone")),
                ("checking now absent", with_change(state, ("meta", "checking", "now"), KeyError)),
                ("checking now null", with_change(state, ("meta", "checking", "now"), None)),
                ("queue check with a title", with_change(state, ("queue", 0, "checks"),
                                                         [{"id": "unit", "state": "passed", "title": "Unit tests"},
                                                          {"id": "lint", "state": "passed"}])),
                ("queue check with its base re-run", with_change(state, ("queue", 0, "checks"), [
                    {"id": "unit", "state": "failed", "failures": ["test_a"], "log": "logs/a.log",
                     "baseRerun": {"failures": ["test_a"], "log": "logs/b.log"}},
                    {"id": "lint", "state": "failed", "baseRerun": {"log": "logs/c.log"}}])),
                ("base re-run without its log", with_change(state, ("queue", 0, "checks"), [
                    {"id": "unit", "state": "failed", "baseRerun": {"failures": ["test_a"]}}])),
                ("base re-run with an unknown field", with_change(state, ("queue", 0, "checks"), [
                    {"id": "unit", "state": "failed", "baseRerun": {"log": "logs/b.log", "verdict": "broken"}}])),
                ("queue check with its node", with_change(state, ("queue", 0, "checks"), [
                    {"id": "unit", "state": "failed", "node": "20.0.0", "nodeBelowMinimum": True},
                    {"id": "lint", "state": "passed", "node": None}, {"id": "docs", "state": "passed"}])),
                ("queue check with its node as a number", with_change(state, ("queue", 0, "checks"),
                                                                      [{"id": "unit", "state": "passed", "node": 22}])),
                ("queue check with its node as an object", with_change(state, ("queue", 0, "checks"),
                                                                       [{"id": "unit", "state": "passed", "node": {}}])),
                ("queue check with its node mark as text", with_change(state, ("queue", 0, "checks"), [
                    {"id": "unit", "state": "passed", "node": "20.0.0", "nodeBelowMinimum": "yes"}])),
                ("queue check with an empty title", with_change(state, ("queue", 0, "checks"),
                                                                [{"id": "unit", "state": "passed", "title": ""}])),
                ("queue check title over long", with_change(state, ("queue", 0, "checks"),
                                                            [{"id": "unit", "state": "passed", "title": "t" * 201}])),
                ("step with a title", with_change(state, ("meta", "checking", "now", "steps"),
                                                  [{"label": "unit", "state": "running", "title": "Unit tests"}])),
                ("step title over long", with_change(state, ("meta", "checking", "now", "steps"),
                                                     [{"label": "unit", "state": "running", "title": "t" * 201}])),
                ("a close started", with_change(state, ("meta", "closing"), {"started": {"entry": 4, "at": "2026-10-20T10:00:00Z"}})),
                ("a close finished", with_change(state, ("meta", "closing"), {"started": {"entry": 4, "at": "2026-10-20T10:00:00Z"},
                                                                              "finished": {"entry": 4, "at": "2026-10-20T10:05:00Z"}})),
                ("a close finished but never started", with_change(state, ("meta", "closing"),
                                                                   {"finished": {"entry": 4, "at": "2026-10-20T10:05:00Z"}})),
                ("a close mark for entry zero", with_change(state, ("meta", "closing"), {"started": {"entry": 0, "at": "2026-10-20T10:00:00Z"}})),
                ("step state unknown",
                 with_change(state, ("meta", "checking", "now", "steps"), [{"label": "x", "state": "?"}])),
                ("checking now awaiting the base rerun", with_change(state, ("meta", "checking", "now", "baseRerun"),
                                                                     {"startedAt": "2026-10-20T10:00:00Z", "checks": ["unit"]})),
                ("base rerun naming a check by number", with_change(state, ("meta", "checking", "now", "baseRerun"),
                                                                    {"startedAt": "2026-10-20T10:00:00Z", "checks": [3]})),
                ("base rerun without its start", with_change(state, ("meta", "checking", "now", "baseRerun"),
                                                             {"checks": ["unit"]})),
                ("base rerun with an unknown field", with_change(state, ("meta", "checking", "now", "baseRerun"),
                                                                 {"startedAt": "2026-10-20T10:00:00Z", "checks": ["unit"],
                                                                  "verdict": "passed"})),
                ("finish met as text", with_change(state, ("finish", 0, "met"), "no")),
                ("lease without holder", with_change(state, ("leases", 0), {"id": "x", "since": "2026-10-20T08:00:00Z"})),
                ("with tickets", with_change(state, ("tickets",), {"pr-1": {"title": "Candidate", "summary": "What it does."},
                                                                  "pr-2": {"title": "Title only"}})),
                ("ticket without title", with_change(state, ("tickets",), {"pr-1": {"summary": "What it does."}})),
                ("ticket with unknown field", with_change(state, ("tickets",), {"pr-1": {"title": "T", "body": "B"}})),
                ("ticket summary over long", with_change(state, ("tickets",), {"pr-1": {"title": "T", "summary": "s" * 1001}})),
                ("planned with a run", with_change(state, ("planned",), [
                    {"subject": "pr-3", "title": "Planned", "lane": "lane-1", "stage": "planned", "runId": "run-3"}])),
                ("planned with its ticket started", with_change(state, ("planned",), [
                    {"subject": "pr-3", "title": "Planned", "lane": "lane-1", "stage": "building", "trackerStarted": True}])),
                ("planned with trackerStarted as text", with_change(state, ("planned",), [
                    {"subject": "pr-3", "title": "Planned", "lane": "lane-1", "stage": "building", "trackerStarted": "yes"}])),
                ("queued with its ticket started", with_change(state, ("queue", 0, "trackerStarted"), True)),
                ("queued with trackerStarted false", with_change(state, ("queue", 0, "trackerStarted"), False)),
                ("queued with trackerStarted as text", with_change(state, ("queue", 0, "trackerStarted"), "yes")),
                ("planned with an empty run", with_change(state, ("planned",), [
                    {"subject": "pr-3", "title": "Planned", "lane": "lane-1", "stage": "planned", "runId": ""}])),
                ("empty run", with_change(state, ("queue", 0, "runId"), "")),
                ("not an object", []),
            ],
            "delivery-journal/1": [
                ("valid", VALID_ENTRY),
                ("with lane", {**VALID_ENTRY, "lane": "lane-1"}),
                ("a decision carrying a review", {**VALID_ENTRY, "kind": "decision.recorded",
                                                 "reviewCarry": {"from": HEAD_A, "to": HEAD_B, "digest": "0" * 64}}),
                ("a review carry with a short digest", {**VALID_ENTRY, "kind": "decision.recorded",
                                                       "reviewCarry": {"from": HEAD_A, "to": HEAD_B, "digest": "0" * 8}}),
                ("a review carry without its target", {**VALID_ENTRY, "kind": "decision.recorded",
                                                      "reviewCarry": {"from": HEAD_A, "digest": "0" * 64}}),
                ("unknown kind", {**VALID_ENTRY, "kind": "candidate.vanished"}),
                ("id zero", {**VALID_ENTRY, "id": 0}),
                ("id as boolean", {**VALID_ENTRY, "id": True}),
                ("empty summary", {**VALID_ENTRY, "summary": ""}),
                ("empty lane", {**VALID_ENTRY, "lane": ""}),
                ("missing subject", {key: value for key, value in VALID_ENTRY.items() if key != "subject"}),
                ("extra field", {**VALID_ENTRY, "extra": 1}),
                ("bad time", {**VALID_ENTRY, "at": "2026-10-20 10:00:00"}),
            ],
            "work-snapshot/1": [
                ("valid", SNAPSHOT),
                ("snapshot with a summary", with_change(SNAPSHOT, ("items", 0, "summary"), "s" * 1000)),
                ("snapshot summary over long", with_change(SNAPSHOT, ("items", 0, "summary"), "s" * 1001)),
                ("snapshot summary not text", with_change(SNAPSHOT, ("items", 0, "summary"), ["s"])),
            ],
        }

    def test_a_trailing_newline_never_satisfies_an_anchored_pattern(self) -> None:
        # The schema dialect uses ECMA-262 patterns, where "$" matches only at
        # the very end. The reference validator uses Python's "$", which also
        # matches before a final newline, so these cases are asserted directly.
        state = valid_state()
        cases = {
            "delivery-state/1": [
                with_change(state, ("meta", "baseHead"), HEAD_A + "\n"),
                with_change(state, ("queue", 0, "head"), HEAD_A + "\n"),
                with_change(state, ("meta", "updatedAt"), "2026-10-20T10:00:00Z\n"),
            ],
            "delivery-journal/1": [{**VALID_ENTRY, "at": "2026-10-20T10:00:00Z\n"}],
        }
        for name, documents in cases.items():
            schema = schema_module.load_schema(name)
            for document in documents:
                with self.subTest(schema=name):
                    self.assertNotEqual(schema_module.validate(document, schema), [])

    def test_bundled_validator_agrees_with_the_reference_validator(self) -> None:
        for name, cases in self.fixtures().items():
            schema = schema_module.load_schema(name)
            reference = Draft202012Validator(schema)
            Draft202012Validator.check_schema(schema)
            outcomes = set()
            for label, document in cases:
                expected = label in VALID_FIXTURES
                with self.subTest(schema=name, fixture=label):
                    self.assertEqual(not schema_module.validate(document, schema), expected)
                    self.assertEqual(reference.is_valid(document), expected)
                    outcomes.add(expected)
            self.assertEqual(outcomes, {True, False}, f"{name} fixtures must include valid and invalid documents")

    def test_closed_value_lists_are_exact(self) -> None:
        state = schema_module.load_schema("delivery-state/1")
        journal = schema_module.load_schema("delivery-journal/1")
        self.assertEqual(journal["properties"]["kind"]["enum"], JOURNAL_KINDS)
        queue = state["properties"]["queue"]["items"]["properties"]
        self.assertEqual(queue["stage"]["enum"],
                         ["queued", "syncing", "synced", "checking", "checked", "merging", "merged", "stopped"])
        self.assertEqual(state["properties"]["lanes"]["items"]["properties"]["state"]["enum"],
                         ["idle", "assigned", "working", "reviewing", "handed", "blocked", "done"])
        self.assertEqual(state["$defs"]["stepState"]["enum"],
                         ["pending", "running", "passed", "failed", "skipped", "timed_out", "reused"])
        self.assertEqual(journal["properties"]["id"]["minimum"], 1)
        self.assertEqual(state["properties"]["version"]["minimum"], 1)
        for kind in JOURNAL_KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(schema_module.validate({**VALID_ENTRY, "kind": kind}, journal), [])

    def test_schemas_are_keyed_by_name_use_urn_ids_and_declare_no_dialect(self) -> None:
        text = SCHEMA_FILE.read_text(encoding="utf-8")
        schemas = json.loads(text)
        self.assertEqual(text, json.dumps(schemas, indent=2, ensure_ascii=False) + "\n")
        self.assertEqual(sorted(schemas), [
            "affected-checks/1", "delivery-journal/1", "delivery-state/1", "merge-queue-result/1", "orchestration-binding/1",
            "work-hints/1", "work-snapshot/1",
        ])
        for name, schema in schemas.items():
            with self.subTest(name=name):
                self.assertEqual(schema["title"], name)
                self.assertTrue(schema["$id"].startswith("urn:"))
                self.assertNotIn("$schema", schema)
                self.assertEqual(schema_module.load_schema(name), schema)
        with self.assertRaises(schema_module.SchemaError):
            schema_module.load_schema("delivery-state")

    def test_validator_refuses_a_keyword_it_does_not_implement(self) -> None:
        for schema in ({"type": "object", "oneOf": [{}]}, {"type": "date"}, {"$ref": "other.json#/x"},
                       {"properties": {"x": {"format": "date-time"}}}, {"$ref": "#/$defs/missing"},
                       {"$ref": "#/$defs/a~1b", "$defs": {"a/b": {}}}, {"$ref": "#/$defs/a/b", "$defs": {"a": {}}},
                       {"$ref": "#/$defs/a%20b", "$defs": {"a b": {}}}, {"pattern": "(a$|b$)"},
                       {"pattern": "^a\\$"}, {"pattern": "^a$b"}):
            with self.subTest(schema=schema):
                with self.assertRaises(schema_module.SchemaError):
                    schema_module._check_schema(schema, "fixture")

    def test_schemas_and_tools_pass_the_leakage_scan(self) -> None:
        findings = [finding for finding in validate_corpus(ROOT).findings
                    if finding.rule_id in {"core.leakage", "core.boundary"}]
        self.assertEqual(findings, [])
        paths = [SCHEMA_FILE, *sorted(SCRIPTS.glob("*.py")), SKILL / "SKILL.md"]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for class_id, pattern in LEAKAGE_PATTERNS:
                with self.subTest(path=path.name, pattern=class_id):
                    self.assertIsNone(pattern.search(text))


GUIDE = SKILL / "references/orchestration-guide.md"
ESCALATION_LEAD_IN = "The operator is brought in only for:"
# The closed list of cases that bring the operator into the loop. A change
# here changes when every lead stops to ask; make it deliberately.
OPERATOR_ESCALATIONS = [
    "a release or production approval the binding requires;",
    "a change to the approved scope or finish line;",
    "an irreversible or outward-facing action not already authorized;",
    "a permission or access grant the lead does not hold;",
    "a host that cannot start or message sessions, so no lane can start;",
    "owner intent that is genuinely ambiguous, where neither the plan nor standing direction settles it.",
]


def section(text: str, heading: str) -> str:
    """The body of one Markdown section, up to the next heading of any level."""
    lines = text.splitlines()
    start = lines.index(heading) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("#")), len(lines))
    return "\n".join(lines[start:end])


LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")


def bullets_after(text: str, lead_in: str) -> list[str]:
    """The whole list that follows a lead-in line, as Markdown renders it.

    Any list marker starts an item, an indented line continues it, and a blank
    line does not end the list when another item follows, so an item added
    after a gap or with another marker is still counted.
    """
    lines = text.splitlines()
    index = lines.index(lead_in) + 1
    items: list[str] = []
    while index < len(lines):
        line = lines[index]
        item = LIST_ITEM.match(line)
        if item:
            items.append(item.group(1).strip())
        elif line.startswith((" ", "\t")) and line.strip() and items:
            items[-1] += " " + line.strip()
        elif line.strip():
            break
        index += 1
    return items


# Files the skill text names that live in an adopter's repository or are
# written into its state directory, as views, the outbox, the
# acknowledgement, or beside it, as the close's closed.json; none
# is shipped with the skill.
ADOPTER_FILES = frozenset({".agents/orchestration.json", ".agents/delivery-posture.json", "delivery.md", "delivery.html",
                           "published.json", "recovery.json", "changes.jsonl", "plan.json", "closed.json",
                           # run-delivery's lead-wait: its lease in the record and its default output beside the flow logs
                           "<state>/lead-wait.json", "<delivery>.wait.json"})


def review_round(number: int, candidate: str, *, finding: bool = False, resolved: bool = False) -> dict:
    """One native review outcome file for work-a, as review-reduce writes it, with two lenses."""
    entries = []
    for lens in ("lens.outcome-correctness", "lens.adversarial-testing"):
        raised = ([{"id": "F1", "severity": "P1", "summary": "A real problem.", "state": "open"}]
                  if finding and lens.endswith("correctness") else [])
        closures = ([{"id": "F1", "state": "fixed", "resolution": "Fixed and confirmed."}]
                    if resolved and lens.endswith("correctness") else [])
        binding = {"subjectRef": "work-a", "candidateRef": candidate, "round": number}
        entries.append({"lens": lens, "state": "obtained", "binding": binding, "result": {
            "schemaVersion": "review-lens-result/1", "lensId": lens, **binding,
            "outcome": "changes-requested" if raised else "aligned", "findings": raised,
            "resolvedFindings": closures, "evidence": ["observed"]}})
    return {"round": number, "outcome": "unresolved" if finding else "aligned", "entries": entries}


class ReviewRefusalTests(unittest.TestCase):
    """A refused capture says which round, lens and field were wrong, so a lane can mend its files before hand-off."""

    def rounds(self) -> list[dict]:
        return [review_round(1, HEAD_A, finding=True), review_round(2, HEAD_B, resolved=True)]

    def refusal(self, outcomes: list[dict], candidate: str = HEAD_B) -> str:
        with self.assertRaises(state_tool.StateError) as caught:
            state_tool.validate_review_outcomes(outcomes, "work-a", candidate)
        self.assertEqual(caught.exception.code, "review_artifact_invalid")
        return str(caught.exception)

    def test_the_valid_rounds_pass(self) -> None:
        state_tool.validate_review_outcomes(self.rounds(), "work-a", HEAD_B)

    def test_a_verification_round_may_defer_a_new_p1_it_lists_as_late_and_nothing_else(self) -> None:
        def with_late(number=2, severity="P1", listed=True):
            outcomes = self.rounds()
            result = outcomes[number - 1]["entries"][0]["result"]
            result["findings"] = [*result["findings"], {"id": "F9", "severity": severity, "summary": "late",
                                                         "state": "deferred", "deferredIssue": "file its follow-up"}]
            if listed:
                result["lateFindings"] = ["F9"]
            return outcomes

        state_tool.validate_review_outcomes(with_late(), "work-a", HEAD_B)
        refused = "only a P2 or P3 finding may be deferred, or a P1 a verification round lists in lateFindings"
        for name, outcomes, where in (("not listed in lateFindings", with_late(listed=False), "round 2"),
                                      ("in round 1, though listed late", with_late(number=1), "round 1"),
                                      ("a P0, though listed late", with_late(severity="P0"), "round 2")):
            with self.subTest(name):
                self.assertIn(f"{where}, lens lens.outcome-correctness, finding F9: {refused}", self.refusal(outcomes))

    def test_each_refusal_names_its_round_lens_and_field(self) -> None:
        def change(edit):
            outcomes = self.rounds()
            edit(outcomes)
            return outcomes

        def result(outcomes, number=2, lens=0):
            return outcomes[number - 1]["entries"][lens]["result"]

        cases = {
            "a round out of order": (lambda o: o[1].update(round=3), "round 2: round is 3, not 2"),
            "a binding for another subject": (lambda o: o[0]["entries"][1]["binding"].update(subjectRef="work-b"),
                                              "round 1, lens lens.adversarial-testing: binding.subjectRef is 'work-b'"),
            "a binding round that disagrees": (lambda o: o[1]["entries"][0]["binding"].update(round=1),
                                               "round 2, lens lens.outcome-correctness: binding.round is 1, not 2"),
            "lenses that reviewed different heads": (lambda o: o[1]["entries"][1]["binding"].update(candidateRef=HEAD_A),
                                                     "round 2, lens lens.adversarial-testing: binding.candidateRef"),
            "a result for another head": (lambda o: result(o).update(candidateRef=HEAD_A),
                                          "round 2, lens lens.outcome-correctness: result.candidateRef is"),
            "a result for another lens": (lambda o: result(o).update(lensId="lens.other"),
                                          "round 2, lens lens.outcome-correctness: result.lensId is 'lens.other'"),
            "an unknown outcome": (lambda o: result(o, 1).update(outcome="maybe"),
                                   "round 1, lens lens.outcome-correctness: result.outcome is 'maybe'"),
            "no evidence": (lambda o: result(o, 1, 1).update(evidence=[]),
                            "round 1, lens lens.adversarial-testing: result.evidence is empty"),
            "a finding with an unknown severity": (lambda o: result(o, 1)["findings"][0].update(severity="P9"),
                                                   "round 1, lens lens.outcome-correctness, finding F1: severity is 'P9'"),
            "a P1 deferred": (lambda o: result(o, 1)["findings"][0].update(state="deferred", deferredIssue="later"),
                              "round 1, lens lens.outcome-correctness, finding F1: only a P2 or P3 finding may be deferred"),
            "a closure by another lens": (lambda o: o[1]["entries"][1]["result"].update(
                                              resolvedFindings=[{"id": "F1", "state": "fixed", "resolution": "x"}]),
                                          "round 2, lens lens.adversarial-testing, resolved finding F1: no finding with this id"),
            "a closure with no resolution": (lambda o: result(o)["resolvedFindings"][0].update(resolution=" "),
                                             "round 2, lens lens.outcome-correctness, resolved finding F1: resolution is empty"),
            "an aligned round with an open finding": (lambda o: result(o).update(resolvedFindings=[]),
                                                      "round 2: the round is aligned with findings still open: F1"),
            "a lens dropped in a later round": (lambda o: o[1]["entries"].pop(),
                                                "round 2: the lenses are ['lens.outcome-correctness']"),
        }
        for name, (edit, message) in cases.items():
            with self.subTest(name):
                self.assertIn(message, self.refusal(change(edit)))
        self.assertIn("round 2: the last round reviewed", self.refusal(self.rounds(), candidate="c" * 40))

    def test_capture_review_says_what_it_refused(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        directory = root / "state"
        state_tool.init(directory, "Epic underway")
        state_tool.patch(directory, writer="lead", version=1, changes={"queue": [queue_item("work-a", head=HEAD_B)]})
        rounds = self.rounds()
        rounds[1]["entries"][0]["result"]["round"] = 1
        paths = []
        for number, data in enumerate(rounds, 1):
            paths.append(root / f"round-{number}.json")
            paths[-1].write_text(json.dumps(data), encoding="utf-8")
        code, refused = run_tool(directory, "capture-review", "--subject", "work-a", "--candidate", HEAD_B,
                                 *[part for path in paths for part in ("--outcome", str(path))])
        self.assertEqual((code, refused["code"]), (1, "review_artifact_invalid"))
        self.assertIn("round 2, lens lens.outcome-correctness: result.round is 1, not 2", refused["error"])
        paths[0].write_text("{not json", encoding="utf-8")
        code, refused = run_tool(directory, "capture-review", "--subject", "work-a", "--candidate", HEAD_B,
                                 *[part for path in paths for part in ("--outcome", str(path))])
        self.assertIn("round 1: the outcome file is not bounded JSON", refused["error"])
        self.assertEqual(state_tool.read_journal(directory), [])


HEAD_C = "c" * 40
HEAD_D = "d" * 40


class CarryReviewTests(unittest.TestCase):
    """The lead carries captured review evidence across a commit it judged needs no new round, and nothing more."""

    def setUp(self, rounds: list[dict] | None = None) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "state"
        state_tool.init(self.directory, "Epic underway")
        state_tool.patch(self.directory, writer="lead", version=1, changes={
            "lanes": [lane("lane-1")], "planned": [{"subject": "work-a", "title": "A", "lane": "lane-1", "stage": "review"}]})
        # Reviewed at HEAD_B; a fixture commit after the last round hands off HEAD_C.
        self.capture(rounds or [review_round(1, HEAD_A, finding=True), review_round(2, HEAD_B, resolved=True)])
        state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head=HEAD_C, change_request="cr", rounds=2)

    def capture(self, rounds: list[dict], candidate: str = HEAD_B) -> dict:
        paths = []
        for number, data in enumerate(rounds, 1):
            paths.append(self.root / f"round-{len(list(self.root.glob('round-*')))}-{number}.json")
            paths[-1].write_text(json.dumps(data), encoding="utf-8")
        return state_tool.capture_review(self.directory, subject="work-a", candidate=candidate, outcomes=paths)

    def carry(self, from_head: str = HEAD_B, to_head: str = HEAD_C, reason: str = "only a fixture changed") -> dict:
        return state_tool.carry_review(self.directory, subject="work-a", from_head=from_head, to_head=to_head, reason=reason)

    def refused(self, call) -> str:
        before = (self.directory / state_tool.JOURNAL_FILE).read_bytes()
        with self.assertRaises(state_tool.StateError) as caught:
            call()
        self.assertEqual((self.directory / state_tool.JOURNAL_FILE).read_bytes(), before)
        return f"{caught.exception.code}: {caught.exception}"

    def evidence(self) -> dict:
        return state_tool.review_evidence(self.directory, "work-a")

    def test_a_carry_makes_the_evidence_hold_at_the_queued_head_and_says_so(self) -> None:
        self.assertEqual(self.evidence()["evidence"], "unavailable", "a capture binds to the head it reviewed")
        before = state_tool.show(self.directory)
        entry = self.carry()
        self.assertEqual((entry["kind"], entry["subject"]), ("decision.recorded", "work-a"))
        self.assertEqual(entry["reviewCarry"]["from"], HEAD_B)
        self.assertEqual(entry["reviewCarry"]["to"], HEAD_C)
        self.assertIn("only a fixture changed", entry["summary"])
        found = self.evidence()
        self.assertEqual((found["evidence"], found["carriedFrom"], found["carriedTo"]), ("captured", HEAD_B, HEAD_C))
        after = state_tool.show(self.directory)
        observed = after["reviewEvidence"]["work-a"]
        # The evidence itself is unchanged: its rounds, its own reviewed head, its digest.
        self.assertEqual(observed["candidateRef"], HEAD_B)
        self.assertEqual(len(observed["outcomes"]), 2)
        self.assertEqual(observed["carried"], {"from": HEAD_B, "to": HEAD_C, "note": entry["summary"]})

    def test_a_carry_is_no_run_no_round_and_no_admission_evidence(self) -> None:
        before = state_tool.show(self.directory)
        self.carry()
        after = state_tool.show(self.directory)
        self.assertEqual(after["state"]["queue"], before["state"]["queue"], "the queue element, its run and evidence, unchanged")
        self.assertIsNone(after["state"]["queue"][0]["runId"])
        self.assertNotIn("evidence", after["state"]["queue"][0])
        self.assertEqual(after["stats"]["work-a"].get("rounds"), before["stats"]["work-a"].get("rounds"))
        self.assertEqual([entry["kind"] for entry in after["journal"]][len(before["journal"]):], ["decision.recorded"])

    def test_a_later_round_or_capture_ends_a_carry(self) -> None:
        self.carry()
        state_tool.append_journal(self.directory, kind="review.round", summary="Round 3", lane="lane-1", subject="work-a",
                                  review=review(3, "2026-10-21T09:00:00Z", "2026-10-21T09:10:00Z"), writer="lane")
        self.assertEqual(self.evidence()["evidence"], "unavailable")
        self.capture([review_round(1, HEAD_A, finding=True), review_round(2, HEAD_B, resolved=True),
                      review_round(3, HEAD_B)])
        found = self.evidence()
        self.assertEqual(found["evidence"], "unavailable", "the new capture is of HEAD_B and needs a carry of its own")
        self.assertNotIn("carriedTo", found)
        self.carry()
        self.assertEqual(self.evidence()["evidence"], "captured")

    def test_each_hop_is_carried_by_name_from_the_head_the_last_carry_reached(self) -> None:
        self.carry()
        state_tool.patch(self.directory, writer="runner", version=state_tool.read_state(self.directory)["version"],
                         selector="queue[subject=work-a]", assignments={"/stage": "stopped"})
        state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head=HEAD_D, change_request="cr", rounds=2)
        self.assertEqual(self.evidence()["evidence"], "unavailable", "the carry was to HEAD_C, not the new head")
        self.assertIn(f"covers {HEAD_C}, not {HEAD_B}", self.refused(lambda: self.carry(HEAD_B, HEAD_D)))
        self.carry(HEAD_C, HEAD_D, "another fixture")
        found = self.evidence()
        self.assertEqual((found["evidence"], found["carriedFrom"], found["carriedTo"]), ("captured", HEAD_C, HEAD_D))

    def test_a_carry_never_changes_what_the_evidence_says(self) -> None:
        cases = (
            ("an open P1", [review_round(1, HEAD_A), review_round(2, HEAD_B, finding=True)], "leaves F1 (P1) open"),
            ("an open P0", [review_round(1, HEAD_A), {**review_round(2, HEAD_B, finding=True),
                                                     "entries": self.open_at(HEAD_B, "P0")}], "leaves F1 (P0) open"),
            ("an unaligned last round", [review_round(1, HEAD_A), {**review_round(2, HEAD_B, finding=True),
                                                                   "entries": self.minor_open(HEAD_B)}],
             "round 2, is not aligned"),
        )
        for name, rounds, message in cases:
            with self.subTest(name):
                self.setUp(rounds)
                self.assertEqual(self.evidence()["evidence"], "unavailable")
                self.assertIn(message, self.refused(lambda: self.carry()))
                self.assertEqual(self.evidence()["evidence"], "unavailable")

    def minor_open(self, candidate: str) -> list[dict]:
        return self.open_at(candidate, "P2")

    def open_at(self, candidate: str, severity: str) -> list[dict]:
        entries = review_round(2, candidate, finding=True)["entries"]
        entries[0]["result"]["findings"][0]["severity"] = severity
        return entries

    def test_run_built_merged_or_damaged_evidence_is_never_carried(self) -> None:
        artifact = next((self.directory / "review-artifacts").glob("*.json"))
        original = artifact.read_bytes()
        artifact.write_bytes(original + b" ")
        self.assertIn("carry_invalid: the captured review artifact is damaged", self.refused(lambda: self.carry()))
        artifact.write_bytes(original)
        version = state_tool.read_state(self.directory)["version"]
        state_tool.patch(self.directory, writer="runner", version=version, selector="queue[subject=work-a]",
                         assignments={"/stage": "merged"})
        self.assertIn("carry_invalid: work-a is merged", self.refused(lambda: self.carry()))
        state = state_tool.read_state(self.directory)
        state["queue"][0].update(stage="queued", runId="run-a")
        (self.directory / state_tool.STATE_FILE).write_text(state_tool.dumps(state), encoding="utf-8")
        self.assertIn("carry_invalid: work-a is built by run run-a", self.refused(lambda: self.carry()))

    def test_each_refusal_says_why_and_writes_nothing(self) -> None:
        self.assertIn("carry_invalid: say why", self.refused(lambda: self.carry(reason=" ")))
        self.assertIn(f"covers {HEAD_B}, not {HEAD_A}", self.refused(lambda: self.carry(from_head=HEAD_A)))
        self.assertIn("to a different head", self.refused(lambda: self.carry(to_head=HEAD_B)))
        self.assertIn(f"queued at {HEAD_C}, not {HEAD_D}", self.refused(lambda: self.carry(to_head=HEAD_D)))
        state_tool.append_journal(self.directory, kind="review.round", summary="Round 3", lane="lane-1", subject="work-a",
                                  review=review(3, "2026-10-21T09:00:00Z", "2026-10-21T09:10:00Z"), writer="lane")
        self.assertIn("latest review round has not been captured", self.refused(lambda: self.carry()))
        self.assertIn("no queued work named work-z", self.refused(lambda: state_tool.carry_review(
            self.directory, subject="work-z", from_head=HEAD_B, to_head=HEAD_C, reason="r")))

    def test_planned_work_and_work_without_a_capture_have_nothing_to_carry(self) -> None:
        state_tool.patch(self.directory, writer="lead", version=state_tool.read_state(self.directory)["version"],
                         changes={"planned": [{"subject": "work-b", "title": "B", "lane": "lane-1", "stage": "review"},
                                              {"subject": "work-c", "title": "C", "lane": "lane-1", "stage": "review"}]})
        self.assertIn("planned, not handed off", self.refused(lambda: state_tool.carry_review(
            self.directory, subject="work-b", from_head=HEAD_B, to_head=HEAD_C, reason="r")))
        state_tool.handoff(self.directory, lane="lane-1", subject="work-c", head=HEAD_C, change_request="cr")
        self.assertIn("no captured review", self.refused(lambda: state_tool.carry_review(
            self.directory, subject="work-c", from_head=HEAD_B, to_head=HEAD_C, reason="r")))

    def test_base_merge_handoff_requires_authorization_for_its_review_and_each_parent(self) -> None:
        # The earlier queued capture is not eligible until its rounds are
        # journaled and the exact reviewed head is captured after them.
        review = {"round": 2, "startedAt": state_tool.now(), "finishedAt": state_tool.now(),
                  "verdicts": {e["lens"]: "approve" for e in review_round(2, HEAD_B, resolved=True)["entries"]},
                  "findings": {level: 0 for level in ("P0", "P1", "P2", "P3")}}
        before = (self.directory / state_tool.STATE_FILE).read_bytes()
        carry = {"reviewed": HEAD_B, "baseCommits": [HEAD_A, HEAD_C]}
        def handoff():
            return state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head=HEAD_C,
                                      change_request="cr", rounds=2, base_merge=carry)
        self.assertIn("exact reviewed head", self.refused(handoff))
        self.assertEqual((self.directory / state_tool.STATE_FILE).read_bytes(), before)
        # A lane cannot write the authorization through the journal CLI.
        code, result = run_tool(self.directory, "journal", "--writer", "lane", "--kind", "decision.recorded",
                                "--subject", "work-a", "--lane", "lane-1", "--summary", "fixture",
                                "--base-merge-authorization", json.dumps(carry))
        self.assertEqual((code, result["code"]), (1, "writer_not_owned"))
        self.assertEqual((self.directory / state_tool.STATE_FILE).read_bytes(), before)
        state_tool.append_journal(self.directory, kind="review.round", subject="work-a", lane="lane-1",
                                  summary="Latest round", review=review)
        self.assertIn("exact reviewed head", self.refused(handoff))

    def test_only_the_lead_carries_and_only_a_decision_holds_a_carry(self) -> None:
        arguments = ("carry-review", "--subject", "work-a", "--from", HEAD_B, "--to", HEAD_C, "--reason", "fixtures only")
        code, refused = run_tool(self.directory, *arguments, "--writer", "lane")
        self.assertEqual((code, refused["code"]), (1, "writer_not_owned"))
        code, carried = run_tool(self.directory, *arguments, "--writer", "lead")
        self.assertEqual(code, 0, carried)
        self.assertEqual(carried["entry"]["reviewCarry"]["to"], HEAD_C)
        carry = {"from": HEAD_B, "to": HEAD_C, "digest": "0" * 64}
        for stray in ({**VALID_ENTRY, "kind": "candidate.merged", "reviewCarry": carry},
                      {**VALID_ENTRY, "kind": "decision.recorded", "subject": None, "reviewCarry": carry}):
            with self.subTest(kind=stray["kind"], subject=stray["subject"]):
                self.assertTrue(any("only a decision about a subject carries its review" in error
                                    for error in state_tool.journal_errors([stray])))
        self.assertEqual(state_tool.journal_errors([{**VALID_ENTRY, "kind": "decision.recorded", "reviewCarry": carry}]), [])


class SkillTextTests(unittest.TestCase):
    def test_every_file_the_skill_text_names_exists(self) -> None:
        shipped = {path.relative_to(SKILL).as_posix() for path in SKILL.rglob("*") if path.is_file()}
        names = {Path(path).name for path in shipped}
        for path in (SKILL / "SKILL.md", GUIDE):
            text = path.read_text(encoding="utf-8")
            # A skill-relative path, inline or in a command, must resolve.
            for relative in re.findall(r"(?<![\w/.-])(?:<skill>/)?((?:scripts|references)/[\w.-]+\.\w+)", text):
                with self.subTest(path=path.name, relative=relative):
                    self.assertIn(relative, shipped)
            # A file named on its own is a shipped file or a declared adopter file.
            for name in re.findall(r"`([^`\s/]*\.(?:json|jsonl|py|md|mjs|yaml|html))`", text):
                with self.subTest(path=path.name, name=name):
                    self.assertTrue(name in names or name in ADOPTER_FILES, name)
            for name in re.findall(r"`([^`\s]*/[^`\s]*\.(?:json|jsonl|py|md|mjs|yaml|html))`", text):
                with self.subTest(path=path.name, name=name):
                    self.assertTrue(name.removeprefix("<skill>/") in shipped or name in ADOPTER_FILES, name)
            # A versioned format name is one of the skill's own schemas.
            schemas = set(json.loads(SCHEMA_FILE.read_text(encoding="utf-8")))
            for name in re.findall(r"`([a-z][a-z-]*/\d+)`", text):
                with self.subTest(path=path.name, schema=name):
                    self.assertIn(name, schemas)
            # Links to the skill's own references resolve.
            for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
                if "://" in target:
                    continue
                with self.subTest(path=path.name, link=target):
                    self.assertTrue((path.parent / target).is_file(), target)

    def test_the_run_is_bound_at_start_in_the_skill_and_the_lane_brief(self) -> None:
        def normal(path: Path) -> str:
            return " ".join(path.read_text(encoding="utf-8").split())
        skill = normal(SKILL / "SKILL.md")
        for phrase in ("A lane whose work a product run builds binds that run at start, with"
                       " `delivery_state.py bind-run --lane <lane> --subject <subject> --run <id>`",
                       "is refused with `run_bound` for a run another subject holds",
                       "The hand-off keeps the bound run unless it names another.",
                       "A hand-off naming a run another subject holds is refused with `run_bound` too."):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, skill)
        brief = normal(PRODUCT_ROOT / "skills/run-delivery/templates/lane-brief.md")
        for phrase in ("If a product run builds your story, bind its run id at start, before your first round:",
                       "bind-run --lane <lane> --subject <key> --run <run id>"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, brief)

    def test_the_work_root_row_lists_every_git_folder_the_runner_refuses(self) -> None:
        runner = _load("merge_queue")
        row = next(line for line in GUIDE.read_text(encoding="utf-8").splitlines() if line.startswith("| `workRoot` |"))
        # The parenthesised list after "git's own folders there".
        folders = re.search(r"git's own folders there \(([^)]*)\)", row)
        self.assertIsNotNone(folders, row)
        listed = re.findall(r"`([\w-]+)`", folders.group(1))
        self.assertEqual(sorted(listed), sorted(runner.GIT_OWN_DIRECTORIES))
        self.assertEqual(len(listed), len(set(listed)))

    def test_every_journal_kind_has_exactly_one_tracker_update_instruction(self) -> None:
        kinds = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))["delivery-journal/1"]["properties"]["kind"]["enum"]
        table = section(GUIDE.read_text(encoding="utf-8"), "### Tracker updates")
        rows = [line for line in table.splitlines() if line.startswith("| `")]
        mapped = [row.split("`")[1] for row in rows]
        # A journal kind added to the schema without an update instruction,
        # or an instruction for a kind that does not exist, fails here.
        self.assertEqual(sorted(mapped), sorted(kinds))
        self.assertEqual(len(mapped), len(set(mapped)))
        for row in rows:
            with self.subTest(row=row):
                instruction = row.split("|")[2].strip()
                self.assertTrue(instruction.startswith(("On the ", "None: ")), instruction)
                # An update is never a gate: no instruction waits on, blocks or
                # orders anything after itself.
                self.assertIsNone(re.search(r"\b(?:wait(?:s|ing|ed)?|block(?:s|ing)?|until|till|before|after|gate[sd]?|hold(?:s|ing)?)\b", instruction), instruction)

    def test_the_tracker_publisher_posts_nothing_for_exactly_the_kinds_the_guide_says_post_nothing(self) -> None:
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location(
                "tracker_publisher_for_the_guide", PRODUCT_ROOT / "skills/run-delivery/scripts/tracker_publisher.py")
            publisher = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(publisher)
        finally:
            sys.dont_write_bytecode = previous
        table = section(GUIDE.read_text(encoding="utf-8"), "### Tracker updates")
        rows = {row.split("`")[1]: row.split("|")[2].strip() for row in table.splitlines() if row.startswith("| `")}
        self.assertEqual(sorted(publisher.UPDATES), sorted(rows))
        for kind, instruction in rows.items():
            with self.subTest(kind=kind):
                self.assertEqual(publisher.UPDATES[kind] is None, instruction.startswith("None: "), instruction)

    def test_the_operator_escalation_list_is_closed_and_pinned(self) -> None:
        for path in (SKILL / "SKILL.md", GUIDE):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertEqual(text.count(ESCALATION_LEAD_IN), 1)
                self.assertEqual(bullets_after(text, ESCALATION_LEAD_IN), OPERATOR_ESCALATIONS)
                normalized = " ".join(text.split())
                # No second list of operator cases under other wording.
                self.assertEqual(len(re.findall(r"operator is (?:\w+ )?brought in", normalized)), 1)
                # The only place that asks the operator anything is the
                # release approval, the list's first case.
                asks = re.findall(r"\bask(?:s|ing)? the operator\b[^.]*", normalized, re.IGNORECASE)
                self.assertTrue(all("approve" in ask for ask in asks), asks)

    def test_the_lead_authority_rules_are_stated(self) -> None:
        for path in (SKILL / "SKILL.md", GUIDE):
            text = " ".join(path.read_text(encoding="utf-8").split())
            for rule in ("does not ask", "Never hold approved work.", "Never leave a lane idle.",
                         "Reconcile at every event.", "Lanes escalate to the lead, never to the operator",
                         "the epic's child items", "before the views are published", "disagrees with the record",
                         "never delays acting or publishing", "except a change the operator made", "never reverting" if path == GUIDE else "never reverted"):
                with self.subTest(path=path.name, rule=rule):
                    self.assertIn(rule, text)
        brief = " ".join(section(GUIDE.read_text(encoding="utf-8"), "## Lane brief template").split())
        # The brief's own sections are bold lines, so the whole template is one section.
        for point in ("at start;", "at each decision you make", "at each review round", "pass to the lead to defer",
                      "at hand-off", "Escalate only to the lead, never to the operator"):
            with self.subTest(point=point):
                self.assertIn(point, brief)



def selected_check(check_id: str, **fields: object) -> dict:
    check = {"id": check_id, "argv": ["python3", "-m", "pytest", f"tests/{check_id}"], "cwd": ".",
             "reasons": ["the parser changed"], "prerequisites": []}
    check.update(fields)
    return check


class LeadSelectionTests(unittest.TestCase):
    """The lead records a candidate's checks through the tool; only the tool writes them, and a new head voids them."""

    BINDING = {
        "selection": {"lead": True},
        "alwaysRun": [selected_check("always")],
        "fallback": [selected_check("fallback")],
        "profiles": {"default": {"environment": {}}},
        "checkTimeoutCeiling": 600,
    }

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "state"
        state_tool.init(self.directory, "Epic underway")
        state = state_tool.read_state(self.directory)
        state_tool.patch(self.directory, writer="lead", version=state["version"],
                         changes={"lanes": [lane("lane-1")], "queue": [queue_item("work-q", changeRequest="cr-1")]})

    def item(self) -> dict:
        return state_tool.read_state(self.directory)["queue"][0]

    def select(self, checks: object = None, *, head: str = HEAD_A, reason: str = "only the parser changed",
               binding: dict | None = None) -> dict:
        return state_tool.select_checks(self.directory, "work-q", binding=binding or self.BINDING, head=head,
                                        checks=[selected_check("parser")] if checks is None else checks, reason=reason)

    def refused(self, call) -> str:
        before = (self.directory / state_tool.STATE_FILE).read_bytes()
        with self.assertRaises(state_tool.StateError) as caught:
            call()
        self.assertEqual((self.directory / state_tool.STATE_FILE).read_bytes(), before)
        return caught.exception.code

    def test_a_selection_is_recorded_for_the_reviewed_head_and_journaled(self) -> None:
        item = self.select()
        self.assertEqual(item["leadSelection"]["head"], HEAD_A)
        self.assertEqual(item["leadSelection"]["checks"], [selected_check("parser")])
        self.assertEqual(item["leadSelection"]["reason"], "only the parser changed")
        self.assertEqual(self.item(), item)
        entry = state_tool.read_journal(self.directory)[-1]
        self.assertEqual((entry["kind"], entry["subject"], entry["lane"]), ("check.selected", "work-q", "lane-1"))
        self.assertIn("parser", entry["summary"])
        self.assertIn("only the parser changed", entry["summary"])

    def test_a_selected_check_keeps_its_title_and_an_empty_one_is_refused(self) -> None:
        item = self.select([{**selected_check("parser"), "title": "Parser unit tests"}])
        self.assertEqual(item["leadSelection"]["checks"][0]["title"], "Parser unit tests")
        self.assertEqual(self.refused(lambda: self.select([{**selected_check("lexer"), "title": ""}])), "select_invalid")

    def test_the_command_line_records_and_clears_a_selection(self) -> None:
        binding = Path(self.directory.parent) / "orchestration.json"
        binding.write_text(json.dumps(self.BINDING), encoding="utf-8")
        code, result = run_tool(self.directory, "select", "--subject", "work-q", "--binding", str(binding),
                                "--head", HEAD_A, "--checks", "-", "--reason", "parser only",
                                stdin=json.dumps([selected_check("parser")]))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["item"]["leadSelection"]["reason"], "parser only")
        code, result = run_tool(self.directory, "select", "--subject", "work-q", "--clear")
        self.assertEqual(code, 0, result)
        self.assertNotIn("leadSelection", result["item"])
        self.assertEqual(state_tool.read_journal(self.directory)[-1]["kind"], "check.selected")
        code, result = run_tool(self.directory, "select", "--subject", "work-q", "--head", HEAD_A)
        self.assertEqual((code, result["code"]), (1, "select_invalid"))

    def test_a_binding_that_does_not_enable_it_is_refused(self) -> None:
        for binding in ({**self.BINDING, "selection": {"lead": False}},
                        {key: value for key, value in self.BINDING.items() if key != "selection"}):
            with self.subTest(binding=binding.get("selection")):
                self.assertEqual(self.refused(lambda: self.select(binding=binding)), "select_invalid")

    def test_a_selection_for_another_head_or_without_a_reason_is_refused(self) -> None:
        self.assertEqual(self.refused(lambda: self.select(head=HEAD_B)), "select_invalid")
        self.assertEqual(self.refused(lambda: self.select(reason=" ")), "select_invalid")
        self.assertEqual(self.refused(lambda: state_tool.select_checks(
            self.directory, "missing", binding=self.BINDING, head=HEAD_A, checks=[selected_check("x")], reason="r")),
            "subject_unknown")

    def test_checks_the_runner_would_refuse_are_refused_when_recorded(self) -> None:
        cases = {
            "empty": [],
            "shape": [{"id": "parser"}],
            "repeated": [selected_check("parser"), selected_check("parser")],
            "binding id": [selected_check("always")],
            "unselected prerequisite": [selected_check("parser", prerequisites=["build"])],
            "cycle": [selected_check("a", prerequisites=["b"]), selected_check("b", prerequisites=["a"])],
            "unknown profile": [selected_check("parser", environmentProfile="missing")],
            "over the ceiling": [selected_check("parser", timeoutSeconds=601)],
            "escaping cwd": [selected_check("parser", cwd="../elsewhere")],
            "absolute cwd": [selected_check("parser", cwd="/tmp")],
            "empty argv": [selected_check("parser", argv=[])],
        }
        for name, checks in cases.items():
            with self.subTest(name):
                self.assertEqual(self.refused(lambda: self.select(checks)), "select_invalid")

    def test_only_the_select_command_writes_a_selection(self) -> None:
        self.select()
        state = state_tool.read_state(self.directory)
        for writer in ("lead", "runner"):
            with self.subTest(writer=writer):
                self.assertEqual(self.refused(lambda: state_tool.patch(
                    self.directory, writer=writer, version=state["version"], selector="queue[subject=work-q]",
                    changes={"leadSelection": {"head": HEAD_A, "checks": [], "reason": "r",
                                               "recordedAt": "2026-10-30T00:00:00Z"}})), "field_not_owned")
        self.assertEqual(self.refused(lambda: state_tool.patch(
            self.directory, writer="lead", version=state["version"], selector="queue[subject=work-q]",
            changes={"checkSelection": {"by": "lead"}})), "field_not_owned")
        written = state_tool.patch(self.directory, writer="runner", version=state["version"],
                                   selector="queue[subject=work-q]", changes={"checkSelection": {"by": "provider"}})
        self.assertEqual(written["queue"][0]["checkSelection"], {"by": "provider"})

    def test_a_hand_off_at_a_new_head_drops_the_selection_and_one_at_the_same_head_keeps_it(self) -> None:
        self.select()
        state = state_tool.read_state(self.directory)
        state_tool.patch(self.directory, writer="runner", version=state["version"], selector="queue[subject=work-q]",
                         changes={"stage": "stopped", "checkSelection": {"by": "lead", "head": HEAD_A, "reason": "r"}})
        state_tool.handoff(self.directory, lane="lane-1", subject="work-q", head=HEAD_A, change_request="cr-1")
        self.assertIn("leadSelection", self.item())
        self.assertNotIn("checkSelection", self.item())
        state = state_tool.read_state(self.directory)
        state_tool.patch(self.directory, writer="runner", version=state["version"], selector="queue[subject=work-q]",
                         changes={"stage": "stopped"})
        state_tool.handoff(self.directory, lane="lane-1", subject="work-q", head=HEAD_B, change_request="cr-1")
        self.assertNotIn("leadSelection", self.item())

    def test_a_merged_candidate_takes_no_selection(self) -> None:
        state = state_tool.read_state(self.directory)
        state_tool.patch(self.directory, writer="runner", version=state["version"], selector="queue[subject=work-q]",
                         changes={"stage": "merged"})
        self.assertEqual(self.refused(self.select), "select_invalid")

    def test_the_stats_say_whose_checks_ran(self) -> None:
        state = state_tool.read_state(self.directory)
        self.assertNotIn("checksSelectedBy", state_tool.delivery_stats(state, [])["work-q"])
        state = state_tool.patch(self.directory, writer="runner", version=state["version"],
                                 selector="queue[subject=work-q]",
                                 changes={"checkSelection": {"by": "lead", "head": HEAD_A, "reason": "parser only"}})
        self.assertEqual(state_tool.delivery_stats(state, [])["work-q"]["checksSelectedBy"], "lead")


def git_in(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *arguments], cwd=repo, capture_output=True,
                               check=True, text=True)
    return completed.stdout.strip()


class CheckCatalogueTests(unittest.TestCase):
    """The lead selects by catalogue id; guards join every selection; a change selects what the binding says reads it."""

    SCHEMA = "skills/orchestrate-work/references/schemas.json"
    CATALOGUE = [
        selected_check("run-delivery", argv=["python3", "-B", "tests/test_run_delivery.py"]),
        selected_check("corpus-guards", argv=["python3", "-B", "tests/test_registry.py"]),
        selected_check("schema-readers", argv=["python3", "-B", "tests/test_orchestration_state.py"]),
        selected_check("plan-fixtures", argv=["python3", "-B", "tests/test_plan_fixtures.py"],
                       prerequisites=["fixture-build"]),
        selected_check("fixture-build", argv=["python3", "-B", "scripts/build-fixtures.py"]),
        selected_check("mentions-schema", argv=["python3", "-B", "tests/test_mentions.py"]),
    ]

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.directory = root / "state"
        self.repo = root / "repo"
        self.repo.mkdir()
        git_in(self.repo, "init", "--quiet", "--initial-branch=main")
        quieten(self.repo)
        git_in(self.repo, "config", "user.email", "lead@example.test")
        git_in(self.repo, "config", "user.name", "Lead")
        for relative, text in {self.SCHEMA: "{}\n", "tests/test_mentions.py": f"# reads {self.SCHEMA}\n",
                               "tests/fixtures/plans/one.json": "{}\n", "src/app.py": "one\n"}.items():
            (self.repo / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / relative).write_text(text, encoding="utf-8")
        git_in(self.repo, "add", "--all")
        git_in(self.repo, "commit", "--quiet", "-m", "base")
        self.base = git_in(self.repo, "rev-parse", "HEAD")
        git_in(self.repo, "update-ref", "refs/remotes/origin/main", self.base)
        self.binding = {
            "base": {"remote": "origin", "ref": "main"},
            "selection": {
                "lead": True, "catalogue": copy.deepcopy(self.CATALOGUE), "guards": ["corpus-guards"],
                "readers": [{"paths": [self.SCHEMA, "tests/fixtures/plans/**"], "checks": ["schema-readers", "plan-fixtures"]}],
            },
            "alwaysRun": [selected_check("always")],
            "fallback": [selected_check("fallback")],
            "profiles": {"default": {"environment": {}}},
            "checkTimeoutCeiling": 600,
        }
        state_tool.init(self.directory, "Epic underway")

    def candidate(self, files: dict[str, str], subject: str = "work-q") -> str:
        git_in(self.repo, "checkout", "--quiet", "-B", subject, self.base)
        for relative, text in files.items():
            (self.repo / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / relative).write_text(text, encoding="utf-8")
        git_in(self.repo, "add", "--all")
        git_in(self.repo, "commit", "--quiet", "-m", subject)
        head = git_in(self.repo, "rev-parse", "HEAD")
        git_in(self.repo, "checkout", "--quiet", "main")
        state = state_tool.read_state(self.directory)
        queue = [item for item in state["queue"] if item["subject"] != subject]
        lanes = state["lanes"] or [lane("lane-1")]
        state_tool.patch(self.directory, writer="lead", version=state["version"],
                         changes={"lanes": lanes, "queue": [*queue, queue_item(subject, head=head, changeRequest="cr-1")]})
        return head

    def select(self, checks: object, head: str, *, binding: dict | None = None, subject: str = "work-q") -> list[str]:
        item = state_tool.select_checks(self.directory, subject, binding=binding or self.binding, head=head,
                                        checks=checks, reason="the lead's reason", repo=self.repo)
        return [check["id"] for check in item["leadSelection"]["checks"]]

    def refused(self, call) -> Exception:
        before = (self.directory / state_tool.STATE_FILE).read_bytes()
        with self.assertRaises(state_tool.StateError) as caught:
            call()
        self.assertEqual((self.directory / state_tool.STATE_FILE).read_bytes(), before)
        return caught.exception

    def test_catalogue_ids_resolve_to_their_entries_and_an_unknown_id_is_refused_by_name(self) -> None:
        head = self.candidate({"src/app.py": "two\n"})
        item = state_tool.select_checks(self.directory, "work-q", binding=self.binding, head=head,
                                        checks=["run-delivery", "corpus-guards"], reason="r", repo=self.repo)
        catalogue = {entry["id"]: entry for entry in self.CATALOGUE}
        self.assertEqual(item["leadSelection"]["checks"], [catalogue["run-delivery"], catalogue["corpus-guards"]])
        error = self.refused(lambda: self.select(["run-delivery", "no-such-check"], head))
        self.assertEqual(error.code, "select_invalid")
        self.assertIn("no-such-check", str(error))
        self.assertNotIn("run-delivery,", str(error).split(":")[0])

    def test_the_command_line_takes_comma_separated_ids(self) -> None:
        head = self.candidate({"src/app.py": "two\n"})
        binding = self.directory.parent / "orchestration.json"
        binding.write_text(json.dumps(self.binding), encoding="utf-8")
        code, result = run_tool(self.directory, "select", "--subject", "work-q", "--binding", str(binding), "--head", head,
                                "--checks", "run-delivery,corpus-guards", "--reason", "run-delivery only",
                                "--repo", str(self.repo))
        self.assertEqual(code, 0, result)
        self.assertEqual([check["id"] for check in result["item"]["leadSelection"]["checks"]],
                         ["run-delivery", "corpus-guards"])
        self.assertEqual(result["baseHead"], self.base)
        for given in ("run-delivery,,corpus-guards", " , "):
            with self.subTest(given=given):
                code, result = run_tool(self.directory, "select", "--subject", "work-q", "--binding", str(binding),
                                        "--head", head, "--checks", given, "--reason", "r", "--repo", str(self.repo))
                self.assertEqual((code, result["code"]), (1, "select_invalid"))
        code, result = run_tool(self.directory, "select", "--subject", "work-q", "--binding", str(binding), "--head", head,
                                "--checks", "-", "--reason", "r", "--repo", str(self.repo), stdin="run-delivery\n")
        self.assertEqual(code, 0, result)
        self.assertEqual([check["id"] for check in result["item"]["leadSelection"]["checks"]],
                         ["run-delivery", "corpus-guards"])

    def test_a_guard_is_in_every_selection_including_one_that_names_none(self) -> None:
        head = self.candidate({"src/app.py": "two\n"})
        self.assertEqual(self.select(["run-delivery"], head), ["run-delivery", "corpus-guards"])
        self.assertEqual(self.select([selected_check("parser")], head), ["parser", "corpus-guards"])
        self.assertEqual(self.select([], head), ["corpus-guards"])
        entry = state_tool.read_journal(self.directory)[-1]
        self.assertIn("corpus-guards (a guard)", entry["summary"])
        # A guard the lead names itself is selected once, and one defined differently is refused.
        self.assertEqual(self.select(["corpus-guards", "run-delivery"], head), ["corpus-guards", "run-delivery"])
        differing = {**self.CATALOGUE[1], "argv": ["python3", "other.py"]}
        self.assertEqual(self.refused(lambda: self.select([differing], head)).code, "select_invalid")
        # A catalogue check named both by id and by its entry, in either order, is selected once.
        self.assertEqual(self.select(["run-delivery", self.CATALOGUE[0]], head), ["run-delivery", "corpus-guards"])
        self.assertEqual(self.select([self.CATALOGUE[0], "run-delivery"], head), ["run-delivery", "corpus-guards"])
        self.assertEqual(self.refused(lambda: self.select(
            ["run-delivery", {**self.CATALOGUE[0], "argv": ["python3", "other.py"]}], head)).code, "select_invalid")

    def test_a_json_list_as_today_selects_exactly_what_it_lists_plus_the_guards(self) -> None:
        head = self.candidate({"src/app.py": "two\n"})
        listed = [selected_check("parser"), selected_check("lexer", prerequisites=["parser"])]
        item = state_tool.select_checks(self.directory, "work-q", binding=self.binding, head=head, checks=listed,
                                        reason="r", repo=self.repo)
        self.assertEqual(item["leadSelection"]["checks"], [*listed, self.CATALOGUE[1]])
        # A binding with no catalogue keeps today's selection exactly.
        plain = {key: value for key, value in self.binding.items() if key != "selection"} | {"selection": {"lead": True}}
        item = state_tool.select_checks(self.directory, "work-q", binding=plain, head=head, checks=listed, reason="r")
        self.assertEqual(item["leadSelection"]["checks"], listed)

    def test_an_id_brings_its_catalogue_prerequisites(self) -> None:
        head = self.candidate({"src/app.py": "two\n"})
        self.assertEqual(self.select(["plan-fixtures"], head), ["fixture-build", "plan-fixtures", "corpus-guards"])

    def test_a_schema_change_selects_its_mapped_readers_and_fixtures_but_not_a_test_that_only_mentions_it(self) -> None:
        head = self.candidate({self.SCHEMA: '{"changed": true}\n'})
        chosen = self.select(["run-delivery"], head)
        self.assertEqual(chosen, ["run-delivery", "corpus-guards", "schema-readers", "fixture-build", "plan-fixtures"])
        self.assertNotIn("mentions-schema", chosen)
        entry = state_tool.read_journal(self.directory)[-1]
        self.assertIn(f"schema-readers (reads {self.SCHEMA})", entry["summary"])
        # A change elsewhere selects no reader, and a captured fixture's own change selects them.
        other = self.candidate({"src/app.py": "two\n"}, subject="work-r")
        self.assertEqual(self.select(["run-delivery"], other, subject="work-r"), ["run-delivery", "corpus-guards"])
        fixture = self.candidate({"tests/fixtures/plans/one.json": '{"x": 1}\n'}, subject="work-s")
        self.assertIn("plan-fixtures", self.select(["run-delivery"], fixture, subject="work-s"))

    def test_readers_read_only_the_candidates_own_changes_since_it_forked(self) -> None:
        head = self.candidate({"src/app.py": "two\n"})
        # The base moves on after the fork, changing the schema itself.
        (self.repo / self.SCHEMA).write_text('{"base": true}\n', encoding="utf-8")
        git_in(self.repo, "commit", "--quiet", "-am", "base moves")
        git_in(self.repo, "update-ref", "refs/remotes/origin/main", git_in(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(self.select(["run-delivery"], head), ["run-delivery", "corpus-guards"])

    def test_a_reader_star_matches_within_one_segment(self) -> None:
        binding = {**self.binding, "selection": {**self.binding["selection"],
                                                 "readers": [{"paths": ["src/*"], "checks": ["schema-readers"]}]}}
        shallow = self.candidate({"src/x.py": "x\n"})
        self.assertIn("schema-readers", self.select(["run-delivery"], shallow, binding=binding))
        deep = self.candidate({"src/deep/x.py": "x\n"}, subject="work-r")
        self.assertNotIn("schema-readers", self.select(["run-delivery"], deep, binding=binding, subject="work-r"))
        # The runner reads binding patterns the same way.
        runner = _load("merge_queue")
        for pattern, path in (("src/*", "src/x.py"), ("src/*", "src/deep/x.py"), ("src/**", "src/deep/x.py"),
                              ("**/x.py", "x.py"), ("**/x.py", "a/b/x.py"), ("src/?.py", "src/x.py"),
                              ("src/?.py", "src/xy.py"), ("a.b", "axb")):
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(bool(state_tool._binding_pattern(pattern).match(path)),
                                 bool(runner.pattern_regex(pattern).match(path)))

    def test_a_rename_selects_the_readers_of_the_path_it_leaves(self) -> None:
        git_in(self.repo, "checkout", "--quiet", "-B", "work-q", self.base)
        git_in(self.repo, "mv", self.SCHEMA, "skills/orchestrate-work/references/moved.json")
        git_in(self.repo, "commit", "--quiet", "-m", "move")
        head = git_in(self.repo, "rev-parse", "HEAD")
        git_in(self.repo, "checkout", "--quiet", "main")
        state = state_tool.read_state(self.directory)
        state_tool.patch(self.directory, writer="lead", version=state["version"],
                         changes={"lanes": [lane("lane-1")], "queue": [queue_item("work-q", head=head, changeRequest="cr-1")]})
        self.assertIn("schema-readers", self.select(["run-delivery"], head))

    def test_readers_that_cannot_read_the_changes_are_refused_rather_than_skipped(self) -> None:
        head = self.candidate({self.SCHEMA: "{}\n{}\n"})
        elsewhere = self.directory.parent / "not-a-repo"
        elsewhere.mkdir()
        error = self.refused(lambda: state_tool.select_checks(
            self.directory, "work-q", binding=self.binding, head=head, checks=["run-delivery"], reason="r", repo=elsewhere))
        self.assertEqual(error.code, "select_invalid")
        self.assertIn("readers", str(error))

    def test_a_catalogue_the_runner_would_refuse_is_refused(self) -> None:
        head = self.candidate({"src/app.py": "two\n"})
        cases = {
            "repeated id": {"catalogue": [*self.CATALOGUE, self.CATALOGUE[0]]},
            "binding id": {"catalogue": [*self.CATALOGUE, selected_check("always")]},
            "outside prerequisite": {"catalogue": [*self.CATALOGUE, selected_check("x", prerequisites=["elsewhere"])]},
            "prerequisite cycle": {"catalogue": [*self.CATALOGUE, selected_check("x", prerequisites=["y"]),
                                                 selected_check("y", prerequisites=["x"])]},
            "unknown profile": {"catalogue": [*self.CATALOGUE, selected_check("x", environmentProfile="missing")]},
            "over the ceiling": {"catalogue": [*self.CATALOGUE, selected_check("x", timeoutSeconds=601)]},
            "unknown guard": {"guards": ["missing"]},
            "unknown reader": {"readers": [{"paths": ["src/**"], "checks": ["missing"]}]},
            "reader with no path": {"readers": [{"paths": [], "checks": ["run-delivery"]}]},
            "reader with no check": {"readers": [{"paths": ["src/**"], "checks": []}]},
        }
        for name, changes in cases.items():
            with self.subTest(name):
                binding = {**self.binding, "selection": {**self.binding["selection"], **changes}}
                self.assertEqual(self.refused(lambda: self.select(["run-delivery"], head, binding=binding)).code,
                                 "binding_invalid")
        # At the ceiling exactly is within it.
        at_ceiling = {**self.binding, "selection": {**self.binding["selection"], "catalogue": [
            *self.CATALOGUE, selected_check("x", timeoutSeconds=600)]}}
        self.assertEqual(state_tool.catalogue_problems(at_ceiling), [])
        self.assertEqual(self.select(["x"], head, binding=at_ceiling), ["x", "corpus-guards"])
        # Named by id, a cycle is refused as the binding's, never followed.
        cyclic = {**self.binding, "selection": {**self.binding["selection"], "catalogue": [
            *self.CATALOGUE, selected_check("x", prerequisites=["y"]), selected_check("y", prerequisites=["x"])]}}
        error = self.refused(lambda: self.select(["x"], head, binding=cyclic))
        self.assertEqual(error.code, "binding_invalid")
        self.assertIn("cycle", str(error))

    def test_the_binding_schema_takes_the_catalogue_guards_and_readers(self) -> None:
        schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))["orchestration-binding/1"]
        selection = {**schema, "$ref": "#/properties/selection"}
        selection.pop("required", None)
        selection.pop("properties", None)
        selection.pop("additionalProperties", None)
        selection.pop("type", None)
        validator = Draft202012Validator({**selection, "properties": schema["properties"], "$defs": schema["$defs"]})
        self.assertEqual([error.message for error in validator.iter_errors(self.binding["selection"])], [])
        self.assertNotEqual(list(validator.iter_errors({**self.binding["selection"], "aliases": []})), [])
        self.assertNotEqual(list(validator.iter_errors({**self.binding["selection"], "readers": [{"paths": ["a"]}]})), [])

    # -- the base head warning -------------------------------------------------------------------------------------

    def record_rerun(self, check: str, base_head: str, result: str, subject: str = "earlier") -> None:
        state = state_tool.read_state(self.directory)
        entry = {"check": check, "baseHead": base_head, "result": result, "subject": subject,
                 "recordedAt": "2026-11-08T00:00:00Z"}
        state_tool.patch(self.directory, writer="runner", version=state["version"],
                         assignments={"/meta/baseReruns": [*(state["meta"].get("baseReruns") or []), entry]})

    def notices(self, checks: str) -> dict:
        binding = self.directory.parent / "orchestration.json"
        binding.write_text(json.dumps(self.binding), encoding="utf-8")
        head = state_tool.read_state(self.directory)["queue"][0]["head"]
        code, result = run_tool(self.directory, "select", "--subject", "work-q", "--binding", str(binding), "--head", head,
                                "--checks", checks, "--reason", "r", "--repo", str(self.repo))
        self.assertEqual(code, 0, result)
        return result

    def test_a_selected_check_failed_at_the_current_base_head_is_warned_about_by_id_and_head(self) -> None:
        self.candidate({"src/app.py": "two\n"})
        self.record_rerun("run-delivery", self.base, "failed")
        result = self.notices("run-delivery")
        [warning] = result["warnings"]
        self.assertIn("run-delivery", warning)
        self.assertIn(self.base, warning)
        self.assertEqual(result["notes"], [])

    def test_the_same_check_passing_its_rerun_at_this_head_says_nothing(self) -> None:
        self.candidate({"src/app.py": "two\n"})
        self.record_rerun("run-delivery", self.base, "passed")
        result = self.notices("run-delivery")
        self.assertEqual((result["warnings"], result["notes"]), ([], []))
        # The latest re-run at the head is the one that counts.
        self.record_rerun("run-delivery", self.base, "failed")
        self.record_rerun("run-delivery", self.base, "passed")
        self.assertEqual(self.notices("run-delivery")["warnings"], [])

    def test_a_check_that_passed_and_then_failed_at_this_head_is_warned_about(self) -> None:
        self.candidate({"src/app.py": "two\n"})
        self.record_rerun("run-delivery", self.base, "passed")
        self.record_rerun("run-delivery", self.base, "failed", subject="later")
        [warning] = self.notices("run-delivery")["warnings"]
        self.assertIn("run-delivery", warning)
        self.assertIn(self.base, warning)
        self.assertIn("later", warning)

    def test_base_head_overrides_the_remote_tracking_ref(self) -> None:
        self.candidate({"src/app.py": "two\n"})
        elsewhere = "d" * 40
        self.record_rerun("run-delivery", elsewhere, "failed")
        binding = self.directory.parent / "orchestration.json"
        binding.write_text(json.dumps(self.binding), encoding="utf-8")
        head = state_tool.read_state(self.directory)["queue"][0]["head"]
        # No readers here: the given head is only compared, never diffed from.
        unread = {**self.binding, "selection": {**self.binding["selection"], "readers": []}}
        binding.write_text(json.dumps(unread), encoding="utf-8")
        code, result = run_tool(self.directory, "select", "--subject", "work-q", "--binding", str(binding), "--head", head,
                                "--checks", "run-delivery", "--reason", "r", "--repo", str(self.repo),
                                "--base-head", elsewhere)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["baseHead"], elsewhere)
        [warning] = result["warnings"]
        self.assertIn(elsewhere, warning)

    def test_an_unreadable_base_head_says_nothing_is_known_of_reruns(self) -> None:
        self.candidate({"src/app.py": "two\n"})
        self.record_rerun("run-delivery", self.base, "failed")
        baseless = {key: value for key, value in self.binding.items() if key != "base"}
        baseless["selection"] = {**baseless["selection"], "readers": []}
        binding = self.directory.parent / "orchestration.json"
        binding.write_text(json.dumps(baseless), encoding="utf-8")
        head = state_tool.read_state(self.directory)["queue"][0]["head"]
        code, result = run_tool(self.directory, "select", "--subject", "work-q", "--binding", str(binding), "--head", head,
                                "--checks", "run-delivery", "--reason", "r", "--repo", str(self.repo))
        self.assertEqual(code, 0, result)
        self.assertIsNone(result["baseHead"])
        self.assertEqual(result["warnings"], [])
        [note] = result["notes"]
        self.assertIn("can't be read", note)

    def test_the_same_check_failed_at_an_older_base_head_gives_no_warning(self) -> None:
        self.candidate({"src/app.py": "two\n"})
        self.record_rerun("run-delivery", "c" * 40, "failed")
        self.record_rerun("corpus-guards", self.base, "passed")
        result = self.notices("run-delivery")
        self.assertEqual((result["warnings"], result["notes"]), ([], []))

    def test_a_check_the_story_did_not_select_failing_at_this_head_gives_no_warning(self) -> None:
        self.candidate({"src/app.py": "two\n"})
        self.record_rerun("mentions-schema", self.base, "failed")
        result = self.notices("run-delivery")
        self.assertEqual((result["warnings"], result["notes"]), ([], []))

    def test_a_guard_failed_at_this_head_is_warned_about_though_the_lead_named_only_another(self) -> None:
        self.candidate({"src/app.py": "two\n"})
        self.record_rerun("corpus-guards", self.base, "failed")
        [warning] = self.notices("run-delivery")["warnings"]
        self.assertIn("corpus-guards", warning)

    def test_a_head_with_no_rerun_says_nothing_is_known(self) -> None:
        self.candidate({"src/app.py": "two\n"})
        self.record_rerun("run-delivery", "c" * 40, "failed")
        result = self.notices("run-delivery")
        self.assertEqual(result["warnings"], [])
        [note] = result["notes"]
        self.assertIn("nothing is known", note)
        self.assertIn(self.base, note)

    def test_only_the_runner_writes_the_base_reruns(self) -> None:
        state = state_tool.read_state(self.directory)
        entry = {"check": "x", "baseHead": self.base, "result": "failed", "subject": "s", "recordedAt": "2026-11-08T00:00:00Z"}
        self.assertEqual(self.refused(lambda: state_tool.patch(
            self.directory, writer="lead", version=state["version"], assignments={"/meta/baseReruns": [entry]})).code,
            "field_not_owned")


if __name__ == "__main__":
    # Run as a script, the repository root is not on the path, and a row imports the tests package from it.
    sys.path.insert(0, str(ROOT))
    unittest.main()
