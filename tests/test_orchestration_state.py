from __future__ import annotations

import copy
import importlib.util
import json
import os
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


def bullets_after(text: str, lead_in: str) -> list[str]:
    """The bullet list that directly follows a lead-in line, one item per bullet."""
    lines = text.splitlines()
    index = lines.index(lead_in) + 1
    while not lines[index].strip():
        index += 1
    items: list[str] = []
    while index < len(lines) and (lines[index].startswith("- ") or lines[index].startswith("  ")):
        if lines[index].startswith("- "):
            items.append(lines[index][2:].strip())
        else:
            items[-1] += " " + lines[index].strip()
        index += 1
    return items


class SkillTextTests(unittest.TestCase):
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

    def test_the_operator_escalation_list_is_closed_and_pinned(self) -> None:
        for path in (SKILL / "SKILL.md", GUIDE):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertEqual(text.count(ESCALATION_LEAD_IN), 1)
                self.assertEqual(bullets_after(text, ESCALATION_LEAD_IN), OPERATOR_ESCALATIONS)

    def test_the_lead_authority_rules_are_stated(self) -> None:
        for path in (SKILL / "SKILL.md", GUIDE):
            text = " ".join(path.read_text(encoding="utf-8").split())
            for rule in ("does not ask", "Never hold approved work.", "Never leave a lane idle.",
                         "Reconcile at every event.", "Lanes escalate to the lead, never to the operator",
                         "the epic's child items", "before the views are published", "disagrees with the record"):
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
