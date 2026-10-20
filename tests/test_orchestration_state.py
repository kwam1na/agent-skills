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
    "tests/test_linear_qualification.py": [("pending", ["profile"]), ("skip_if_pending", ["core", "linear"])],
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
        for contents in ("garbage", json.dumps({"version": "one", "journalThrough": 1})):
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
        self.assertIn("outbox", refused["error"])
        code, acked = run_tool(self.directory, "ack", "--version", str(version), "--through", "2")
        self.assertEqual((code, acked["published"]["journalThrough"]), (0, 2))

    def test_show_flags_a_publication_that_lags_the_record(self) -> None:
        code, shown = run_tool(self.directory, "show")
        self.assertEqual(shown["publication"], {"publishedVersion": None, "journalThrough": 0, "unpublishedEntries": 3,
                                                "outboxVersion": None, "outboxLags": False, "lags": True})
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
        # The function renderers read is unchanged: exactly the state and the journal.
        self.assertEqual(sorted(state_tool.show(self.directory)), ["journal", "state"])


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
    "lane.assigned", "candidate.handed", "check.started", "check.finished",
    "candidate.merged", "candidate.stopped", "base.broken", "base.repaired",
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
# written as views, not shipped with the skill.
ADOPTER_FILES = frozenset({".agents/orchestration.json", "delivery.md", "delivery.html"})


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
