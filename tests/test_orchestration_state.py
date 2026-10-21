from __future__ import annotations

import copy
import datetime
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import ast
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from agent_skills.validate import LEAKAGE_PATTERNS, validate_corpus


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/orchestrate-work"
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
        self.assertEqual(shown["state"]["lanes"], [lane])
        self.assertEqual(shown["state"]["queue"], [queue_item("pr-1")])
        self.assertEqual(shown["journal"], [])
        self.assertEqual(run_tool(fresh, "validate"), (0, {"ok": True}))

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
        self.assertEqual(patched["state"]["lanes"][0], {**lane, "state": "working", "waitingOn": None})
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
                         sorted([state_tool.STATE_FILE, state_tool.JOURNAL_FILE, state_tool.STATE_LOCK]))

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


# Every guard call site, in source order, with the arguments after the test case.
GUARD_CALLS = {
    "tests/test_qualification.py": [
        ("skip_if_pending", ["core"]), ("pending", ["core"]), ("skip_if_pending", ["core"]), ("skip_if_pending", ["core"]),
    ],
    "tests/test_provider_qualification.py": [("skip_if_pending", ["provider"])],
    "tests/test_linear_qualification.py": [
        ("skip_if_pending", ["core", "linear"]), ("pending", ["profile"]), ("skip_if_pending", ["core", "linear"]),
    ],
    "tests/test_bootstrap_qualification.py": [
        ("pending", ["bootstrap"]), ("skip_if_pending", ["bootstrap"]), ("skip_if_pending", ["bootstrap"]),
    ],
}


def _guard_calls(tree: ast.AST) -> list[tuple[str, list[str]]]:
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        if name not in {"pending", "skip_if_pending"}:
            continue
        arguments = node.args[1:] if name == "skip_if_pending" else node.args
        calls.append((node.lineno, name, [
            argument.value if isinstance(argument, ast.Constant) else ast.unparse(argument)
            for argument in arguments
        ]))
    return [(name, arguments) for _, name, arguments in sorted(calls)]


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
                                              "stage": "planned"}])
        self.assertEqual(len(result["changes"]), 2)
        again = self.snapshot("tracker", json.loads(Path(tracker).read_text())["items"])
        version = state["version"]
        code, result = run_tool(self.directory, "reconcile", "--snapshot", again)
        self.assertEqual((code, result["drift"], result["changes"]), (0, False, []))
        self.assertEqual(result["summary"], "no drift")
        self.assertEqual(state_tool.show(self.directory)["state"]["version"], version)

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
            {"subject": "work-b", "title": "B", "lane": None, "stage": "planned", "changeRequest": "change 12"},
        ])
        self.assertEqual(state["queue"][0]["changeRequest"], "change 13")
        self.assertEqual(state["queue"][0]["stage"], "queued", "the lead's reconcile never writes runner fields")
        self.assertEqual([report["subject"] for report in result["reports"]], ["work-unrecorded"])
        again = self.snapshot("forge", json.loads(Path(forge).read_text())["items"])
        code, result = run_tool(self.directory, "reconcile", "--snapshot", again)
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
                     "changeRequest": "change 31"}])
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
        self.assertEqual(len(self.recovered()), 1)
        self.assertIn(HEAD_B, self.recovered()[0]["summary"])
        self.assertFalse(self.intent().exists())
        self.assertFalse(state_tool.reconcile(self.directory, [self.forge])["drift"])

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
                                     ("old", "too old", "7200s ago"),
                                     ("huge", "printed more than", None),
                                     ("bigint", "printed no JSON", None),
                                     ("deep", "printed no JSON", None),
                                     ("forgeok", "not a valid tracker snapshot", "not from the tracker"),
                                     ("baddate", "not a valid tracker snapshot", "not a real time")):
            with self.subTest(mode=mode):
                result = self.snap(mode)
                self.assertEqual((result["taken"], result["fallback"]), (False, True))
                self.assertIn(reason, result["reason"])
                if detail:
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
                     "recovery.json", "recovery.json.invalid.2", "queue.lock"):
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

    def test_a_lane_writes_its_own_focus_state_and_waiting_on(self) -> None:
        written = self.as_lane("lane-1", {"focus": "Wiring the hand-off", "state": "blocked", "waitingOn": "lead"},
                               selector="lanes[id=lane-1]")
        own = next(entry for entry in written["lanes"] if entry["id"] == "lane-1")
        self.assertEqual((own["focus"], own["state"], own["waitingOn"]), ("Wiring the hand-off", "blocked", "lead"))
        # The tool stamps since when the state changes.
        self.assertNotEqual(own["since"], "2026-10-21T09:00:00Z")
        unchanged = self.as_lane("lane-1", {"focus": "Still wiring"}, selector="lanes[id=lane-1]")
        self.assertEqual(next(entry for entry in unchanged["lanes"] if entry["id"] == "lane-1")["since"], own["since"])

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
        state_tool.handoff(self.directory, lane="lane-1", subject="work-a", head="a" * 40, change_request="cr",
                           needs_acceptance=True)
        item = next(entry for entry in state_tool.read_state(self.directory)["queue"] if entry["subject"] == "work-a")
        self.assertTrue(item["pending"])
        state_tool.accept_handoff(self.directory, "work-a")
        item = next(entry for entry in state_tool.read_state(self.directory)["queue"] if entry["subject"] == "work-a")
        self.assertNotIn("pending", item)
        state_tool.handoff(self.directory, lane="lane-2", subject="work-b", head="c" * 40, change_request="cr",
                           needs_acceptance=True)
        state_tool.reject_handoff(self.directory, "work-b", "the review missed the migration")
        state = state_tool.read_state(self.directory)
        self.assertNotIn("work-b", [entry["subject"] for entry in state["queue"]])
        self.assertEqual([entry["subject"] for entry in state["planned"]], ["work-b"])
        decisions = {entry["subject"]: entry["summary"] for entry in state_tool.read_journal(self.directory)
                     if entry["kind"] == "decision.recorded"}
        self.assertIn("Accepted", decisions["work-a"])
        self.assertIn("the review missed the migration", decisions["work-b"])

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
        code, focused = run_tool(self.directory, "focus", "--lane", "lane-2", "--text", "Reviewing round 2",
                                 "--state", "reviewing")
        self.assertEqual(code, 0, focused)
        own = next(entry for entry in focused["state"]["lanes"] if entry["id"] == "lane-2")
        self.assertEqual((own["focus"], own["state"]), ("Reviewing round 2", "reviewing"))
        code, refused = run_tool(self.directory, "patch", "--writer", "lane", "--lane", "lane-2", "--version",
                                 str(self.version()), "--element", "lanes[id=lane-1]", "--patch", '{"focus": "x"}')
        self.assertEqual((code, refused["code"]), (1, "writer_not_owned"))
        binding = self.directory.parent / "binding.json"
        binding.write_text(json.dumps({"state": {"publishers": [], "handoffsNeedAcceptance": True}}))
        code, pending = run_tool(self.directory, "handoff", "--lane", "lane-2", "--subject", "work-b",
                                 "--head", "c" * 40, "--change-request", "cr", "--binding", str(binding))
        self.assertTrue(pending["item"]["pending"])
        code, accepted = run_tool(self.directory, "handoff", "--accept", "work-b")
        self.assertEqual(code, 0, accepted)
        self.assertNotIn("pending", accepted["item"])


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

    def test_the_status_moves_with_the_queue(self) -> None:
        record = self.record(queue=[queue_item("work-a", stage="merged"), queue_item("work-c", order=2)])
        journal = [entry(1, "2026-10-21T11:40:00Z", "candidate.merged", "work-a")]
        self.assertEqual(state_tool.delivery_status(record, journal, at=self.NOW)["text"],
                         "last landed work-a at 2026-10-21T11:40:00Z · next: work-c")

    def test_a_fresh_override_replaces_the_derived_status_and_an_expired_one_is_ignored(self) -> None:
        override = {"text": "Release candidate cut; holding merges", "setAt": "2026-10-21T11:00:00Z",
                    "expiresAt": "2026-10-21T13:00:00Z"}
        self.assertEqual(state_tool.delivery_status(self.record(override=override), [], at=self.NOW),
                         {"text": "Release candidate cut; holding merges", "source": "override"})
        expired = {**override, "expiresAt": "2026-10-21T11:59:59Z"}
        self.assertEqual(state_tool.delivery_status(self.record(override=expired), [], at=self.NOW),
                         {"text": "Nothing in flight", "source": "derived"})

    def test_an_empty_lane_focus_derives_from_its_work(self) -> None:
        record = self.record(
            queue=[queue_item("work-a", lane="lane-1", stage="merged"),
                   queue_item("work-c", order=2, lane="lane-1", stage="checking", reportedRounds=3)],
            lanes=[lane("lane-1"), lane("lane-2", focus="Writing the guide"), lane("lane-3")],
        )
        record["planned"] = [planned("work-p", "lane-3")]
        self.assertEqual(state_tool.lane_focus(record), {
            "lane-1": "work-c: checking, 3 review rounds",
            "lane-3": "work-p: review",
        })

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
        self.assertEqual(state_tool.show(directory)["status"], {"text": "Holding for the release", "source": "override"})
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


class PublicationTests(unittest.TestCase):
    """What was published, so an unpublished view is visible."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "state"
        state_tool.init(self.directory, "Epic underway")
        for number in range(1, 4):
            state_tool.append_journal(self.directory, kind="decision.recorded", summary=f"Decision {number}")

    def test_ack_records_what_went_out_and_never_moves_backwards(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        code, acked = run_tool(self.directory, "ack", "--version", str(version), "--through", "2")
        self.assertEqual(code, 0, acked)
        self.assertEqual(acked["published"], {"version": version, "journalThrough": 2, "at": acked["published"]["at"]})
        self.assertEqual(state_tool.show(self.directory)["state"]["version"], version, "an ack is not a state write")
        for args, reason in (
            (["--version", str(version + 1), "--through", "2"], "a version the record has not reached"),
            (["--version", str(version), "--through", "9"], "a journal entry that does not exist"),
            (["--version", str(version), "--through", "1"], "an earlier journal entry"),
            (["--version", "0", "--through", "2"], "an earlier version"),
        ):
            with self.subTest(reason):
                code, refused = run_tool(self.directory, "ack", *args)
                self.assertEqual((code, refused["code"]), (1, "ack_invalid"), refused)
        code, acked = run_tool(self.directory, "ack", "--version", str(version), "--through", "3")
        self.assertEqual((code, acked["published"]["journalThrough"]), (0, 3))

    def test_a_journal_entry_alone_leaves_the_publication_behind(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        run_tool(self.directory, "ack", "--version", str(version), "--through", "3")
        state_tool.append_journal(self.directory, kind="decision.recorded", summary="Decision 4")
        code, shown = run_tool(self.directory, "show")
        self.assertEqual((shown["publication"]["unpublishedEntries"], shown["publication"]["lags"]), (1, True))

    def test_a_malformed_acknowledgement_is_reported_and_repaired_by_the_next_ack(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        published = self.directory / state_tool.PUBLISHED_FILE
        for contents in ("not json", json.dumps({"version": version, "journalThrough": -5, "at": "x"}),
                         json.dumps({"version": version, "journalThrough": 1, "at": "x", "extra": True}),
                         json.dumps({"version": version, "journalThrough": 1, "at": 3})):
            with self.subTest(contents=contents):
                published.write_text(contents, encoding="utf-8")
                code, shown = run_tool(self.directory, "show")
                self.assertEqual(code, 0, "the record is still shown")
                self.assertTrue(shown["publication"]["lags"])
                self.assertIn("published.json", shown["publication"]["error"])
        outbox = self.directory / "outbox" / "outbox.json"
        outbox.parent.mkdir()
        outbox.write_text(json.dumps({"version": version - 1, "journalThrough": 3, "state": {}, "journal": []}))
        code, shown = run_tool(self.directory, "show")
        self.assertEqual((shown["publication"]["outboxVersion"], shown["publication"]["outboxLags"]),
                         (version - 1, True), "a bad acknowledgement hides nothing about the outbox")
        outbox.write_text(json.dumps({"version": version, "journalThrough": 3, "state": {}, "journal": []}))
        code, acked = run_tool(self.directory, "ack", "--version", str(version), "--through", "3")
        self.assertEqual(code, 0, acked)
        code, shown = run_tool(self.directory, "show")
        self.assertEqual((shown["publication"]["lags"], "error" in shown["publication"]), (False, False))

    def test_an_unreadable_outbox_is_reported_as_a_lag(self) -> None:
        outbox = self.directory / "outbox" / "outbox.json"
        outbox.parent.mkdir()
        for contents in ("garbage", json.dumps({"version": "one", "journalThrough": 1}),
                         json.dumps({"version": -3, "journalThrough": -1}),
                         json.dumps({"version": 1, "journalThrough": 1, "publisher": 3})):
            with self.subTest(contents=contents):
                outbox.write_text(contents, encoding="utf-8")
                code, shown = run_tool(self.directory, "show")
                self.assertEqual(code, 0)
                self.assertEqual((shown["publication"]["outboxVersion"], shown["publication"]["outboxLags"]),
                                 (None, True))
                self.assertIn("outbox", shown["publication"]["outboxError"])

    def test_ack_never_claims_more_than_the_outbox_held(self) -> None:
        version = state_tool.show(self.directory)["state"]["version"]
        outbox = self.directory / "outbox" / "outbox.json"
        outbox.parent.mkdir()
        outbox.write_text(json.dumps({"version": version, "journalThrough": 2, "state": {}, "journal": []}))
        code, refused = run_tool(self.directory, "ack", "--version", str(version), "--through", "3")
        self.assertEqual((code, refused["code"]), (1, "ack_invalid"), "entry 3 was never in the outbox")
        state_tool.patch(self.directory, writer="lead", version=version, changes={"meta": {"headline": "Later"}})
        code, refused = run_tool(self.directory, "ack", "--version", str(version + 1), "--through", "2")
        self.assertEqual((code, refused["code"]), (1, "ack_invalid"), "the record's version, not the outbox's")
        self.assertIn("outbox", refused["error"])
        code, acked = run_tool(self.directory, "ack", "--version", str(version), "--through", "2")
        self.assertEqual((code, acked["published"]["journalThrough"]), (0, 2))

    def test_show_flags_a_publication_that_lags_the_record(self) -> None:
        code, shown = run_tool(self.directory, "show")
        self.assertEqual(shown["publication"], {"publishedVersion": None, "journalThrough": 0, "unpublishedEntries": 3,
                                                "outboxVersion": None, "outboxPublisher": None, "recoveryPending": False,
                                                "outboxOrphaned": None,
                                                "outboxLags": False, "lags": True})
        version = shown["state"]["version"]
        run_tool(self.directory, "ack", "--version", str(version), "--through", "3")
        code, shown = run_tool(self.directory, "show")
        self.assertFalse(shown["publication"]["lags"])
        state_tool.patch(self.directory, writer="lead", version=version, changes={"meta": {"headline": "Later"}})
        code, shown = run_tool(self.directory, "show")
        self.assertTrue(shown["publication"]["lags"])
        self.assertEqual(shown["publication"]["publishedVersion"], version)
        # An outbox is behind when the record or the journal has moved past it.
        version = shown["state"]["version"]
        outbox = self.directory / "outbox" / "outbox.json"
        outbox.parent.mkdir()
        outbox.write_text(json.dumps({"version": version, "journalThrough": 3, "state": {}, "journal": []}))
        code, shown = run_tool(self.directory, "show")
        self.assertEqual((shown["publication"]["outboxVersion"], shown["publication"]["outboxLags"]), (version, False))
        state_tool.append_journal(self.directory, kind="decision.recorded", summary="Decision 4")
        code, shown = run_tool(self.directory, "show")
        self.assertTrue(shown["publication"]["outboxLags"], "a journal entry the outbox lacks is a lag")
        # What renderers read: the state, the journal, and the stats, estimates, status and focus folded from them.
        self.assertEqual(sorted(state_tool.show(self.directory)), ["estimates", "focus", "journal", "state", "stats", "status"])


class SkipGuardTests(unittest.TestCase):
    def test_each_skip_guard_names_its_own_qualification_set(self) -> None:
        for relative, expected in GUARD_CALLS.items():
            with self.subTest(path=relative):
                tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
                self.assertEqual(_guard_calls(tree), expected)
        # The linear guard's variable ranges over exactly the two profiles it compares.
        linear = (ROOT / "tests/test_linear_qualification.py").read_text(encoding="utf-8")
        self.assertIn('for profile in ("core", "linear"):', linear)


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


VALID_FIXTURES = {
    "valid", "fresh", "version as whole float", "long head", "null rounds", "checking now null", "with lane",
}
JOURNAL_KINDS = [
    "lane.assigned", "review.round", "candidate.handed", "check.started", "check.finished",
    "check.retried", "candidate.merged", "candidate.stopped", "base.broken", "base.repaired",
    "lease.granted", "lease.released", "release.started", "release.finished",
    "finish.met", "decision.recorded", "blocker.recorded", "delivery.closed",
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
                ("checking now absent", with_change(state, ("meta", "checking", "now"), KeyError)),
                ("checking now null", with_change(state, ("meta", "checking", "now"), None)),
                ("step state unknown",
                 with_change(state, ("meta", "checking", "now", "steps"), [{"label": "x", "state": "?"}])),
                ("finish met as text", with_change(state, ("finish", 0, "met"), "no")),
                ("lease without holder", with_change(state, ("leases", 0), {"id": "x", "since": "2026-10-20T08:00:00Z"})),
                ("not an object", []),
            ],
            "delivery-journal/1": [
                ("valid", VALID_ENTRY),
                ("with lane", {**VALID_ENTRY, "lane": "lane-1"}),
                ("unknown kind", {**VALID_ENTRY, "kind": "candidate.vanished"}),
                ("id zero", {**VALID_ENTRY, "id": 0}),
                ("id as boolean", {**VALID_ENTRY, "id": True}),
                ("empty summary", {**VALID_ENTRY, "summary": ""}),
                ("empty lane", {**VALID_ENTRY, "lane": ""}),
                ("missing subject", {key: value for key, value in VALID_ENTRY.items() if key != "subject"}),
                ("extra field", {**VALID_ENTRY, "extra": 1}),
                ("bad time", {**VALID_ENTRY, "at": "2026-10-20 10:00:00"}),
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
                         ["pending", "running", "passed", "failed", "skipped", "timed_out"])
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
            "work-snapshot/1",
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
# written into its state directory, as views, the outbox or the
# acknowledgement, not shipped with the skill.
ADOPTER_FILES = frozenset({".agents/orchestration.json", "delivery.md", "delivery.html", "outbox/outbox.json",
                           "published.json", "recovery.json"})


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


if __name__ == "__main__":
    unittest.main()
