"""The lead's own wake: `wait` reads the record and the machine, and returns on what needs the lead."""

from __future__ import annotations

import contextlib
import hashlib
import io
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/orchestrate-work/SKILL.md"
GUIDE = ROOT / "skills/orchestrate-work/references/orchestration-guide.md"
RUN_DELIVERY_BRIEF = ROOT / "skills/run-delivery/templates/lane-brief.md"
TOOL = ROOT / "skills/orchestrate-work/scripts/delivery_state.py"
EVENT_KINDS = ("candidate.handed", "check.failed", "candidate.merged", "candidate.stopped", "base.broken", "blocker.recorded")
GIB = 2**30


def _load_tool():
    spec = importlib.util.spec_from_file_location("delivery_state_for_wait", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


state_tool = _load_tool()


def _tree(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*")) if path.is_file()
    }


class Record:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.tool("init", "--headline", "Fixture")

    def tool(self, *arguments: str) -> dict:
        done = subprocess.run([sys.executable, "-B", str(TOOL), "--dir", str(self.directory), *arguments],
                              capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise AssertionError(done.stdout + done.stderr)
        return json.loads(done.stdout)

    def journal(self, kind: str, summary: str = "what happened") -> int:
        return self.tool("journal", "--kind", kind, "--summary", summary)["entry"]["id"]

    def add_lanes(self, *lanes: str) -> None:
        version = self.tool("show")["state"]["version"]
        patch = {"lanes": [{"id": lane, "title": lane, "focus": "work", "state": "working", "waitingOn": None,
                            "since": "2026-11-03T00:00:00Z"} for lane in lanes]}
        self.tool("patch", "--writer", "lead", "--version", str(version), "--patch", json.dumps(patch))

    def lane_state(self, lane: str, state: str, *, text: str = "", waiting_on: str = "lead") -> None:
        self.tool("focus", f"--lane={lane}", "--text", text or state, "--state", state,
                  f"--waiting-on={waiting_on if state == 'blocked' else ''}")

    def start_wait(self, *arguments: str, timeout: str = "20") -> subprocess.Popen:
        # Thresholds nothing here reaches, so only the record can end these waits.
        return subprocess.Popen(
            [sys.executable, "-B", str(TOOL), "--dir", str(self.directory), "wait", "--load-multiple", "1000000",
             "--min-free-bytes", "0", "--swap-growth-bytes", str(2**60), *arguments, "--poll", "0.05",
             "--timeout", timeout],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def wait(self, *arguments: str, timeout: str = "0.3") -> tuple[int, dict]:
        process = self.start_wait(*arguments, timeout=timeout)
        stdout, _ = process.communicate(timeout=30)
        return process.returncode, json.loads(stdout)


def _finish(process: subprocess.Popen) -> tuple[int, dict]:
    stdout, _ = process.communicate(timeout=30)
    return process.returncode, json.loads(stdout)


def _stdout() -> io.TextIOWrapper:
    """A stdout the tool's emit writes to in-process: it writes bytes to sys.stdout.buffer."""
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8")


class RecordCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.record = Record(Path(temporary.name) / "state")

    def assert_running(self, process: subprocess.Popen) -> None:
        time.sleep(0.25)
        self.assertIsNone(process.poll(), "the wait ended before anything needed the lead")
        self.addCleanup(process.kill)


class EventTests(RecordCase):
    def test_each_event_ends_a_running_wait_and_is_named(self) -> None:
        for kind in EVENT_KINDS:
            with self.subTest(kind=kind):
                process = self.record.start_wait()
                self.assert_running(process)
                identifier = self.record.journal(kind, f"the {kind} summary")
                code, result = _finish(process)
                self.assertEqual(code, 0)
                self.assertEqual(result["woke"], "event")
                self.assertEqual([(event["id"], event["kind"]) for event in result["events"]], [(identifier, kind)])
                self.assertEqual(result["events"][0]["summary"], f"the {kind} summary")
                self.assertEqual(result["through"], identifier)

    def test_a_hand_off_through_the_tool_ends_a_running_wait(self) -> None:
        self.record.add_lanes("lane-1")
        version = self.record.tool("show")["state"]["version"]
        planned = {"planned": [{"subject": "T-1", "title": "Ticket", "lane": "lane-1", "stage": "building"}]}
        self.record.tool("patch", "--writer", "lead", "--version", str(version), "--patch", json.dumps(planned))
        process = self.record.start_wait()
        self.assert_running(process)
        self.record.tool("handoff", "--lane", "lane-1", "--subject", "T-1", "--head", "a" * 40,
                         "--change-request", "https://example.invalid/1", "--rounds", "1")
        code, result = _finish(process)
        self.assertEqual(code, 0)
        self.assertEqual(result["events"][0]["kind"], "candidate.handed")
        self.assertEqual(result["events"][0]["subject"], "T-1")

    def test_an_entry_of_another_kind_does_not_end_it(self) -> None:
        process = self.record.start_wait(timeout="1")
        self.assert_running(process)
        self.record.journal("decision.recorded")
        code, result = _finish(process)
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        self.assertEqual(result["events"], [])

    def test_an_entry_before_the_start_id_does_not_end_it(self) -> None:
        first = self.record.journal("candidate.merged")
        code, result = self.record.wait("--after", str(first))
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        self.assertEqual(result["events"], [])

    def test_without_a_start_id_it_starts_after_the_last_entry(self) -> None:
        self.record.journal("candidate.merged")
        code, _ = self.record.wait()
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)

    def test_an_entry_written_between_two_waits_is_not_missed(self) -> None:
        self.record.journal("decision.recorded")
        code, first = self.record.wait()
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        between = self.record.journal("candidate.stopped")
        code, second = self.record.wait(*first["restart"])
        self.assertEqual(code, 0)
        self.assertEqual([event["id"] for event in second["events"]], [between])

    def test_every_event_since_the_start_id_is_returned_together(self) -> None:
        start = self.record.journal("decision.recorded")
        merged = self.record.journal("candidate.merged")
        self.record.journal("decision.recorded")
        blocker = self.record.journal("blocker.recorded")
        code, result = self.record.wait("--after", str(start))
        self.assertEqual(code, 0)
        self.assertEqual([event["id"] for event in result["events"]], [merged, blocker])

    def test_an_expiry_has_its_own_exit_code(self) -> None:
        code, result = self.record.wait()
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        self.assertNotIn(code, (0, 1, 2))
        self.assertEqual(result["woke"], "expired")
        self.assertTrue(result["ok"])

    def test_the_state_directory_is_byte_identical_after_a_wait(self) -> None:
        self.record.add_lanes("lane-1")
        first = self.record.journal("candidate.merged")
        before = _tree(self.record.directory)
        self.assertEqual(self.record.wait()[0], state_tool.WAIT_EXPIRED_EXIT)
        self.assertEqual(_tree(self.record.directory), before)
        self.assertEqual(self.record.wait("--after", str(first - 1))[0], 0)
        self.assertEqual(_tree(self.record.directory), before)


class FailureTests(RecordCase):
    def test_a_malformed_journal_is_an_error_exit(self) -> None:
        with (self.record.directory / "journal.jsonl").open("ab") as journal:
            journal.write(b"not json\n")
        code, result = self.record.wait()
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "journal_invalid")

    def test_a_journal_that_turns_malformed_during_the_wait_is_an_error_exit(self) -> None:
        process = self.record.start_wait()
        self.assert_running(process)
        with (self.record.directory / "journal.jsonl").open("ab") as journal:
            journal.write(b"not json\n")
        code, result = _finish(process)
        self.assertEqual(code, 1)
        self.assertEqual(result["code"], "journal_invalid")

    def test_a_missing_state_directory_is_an_error_exit(self) -> None:
        missing = Record.__new__(Record)
        missing.directory = self.record.directory.parent / "missing"
        code, result = missing.wait()
        self.assertEqual(code, 1)
        self.assertEqual(result["code"], "state_missing")
        self.assertFalse(missing.directory.exists())

    def test_a_partly_written_last_line_is_not_a_crash(self) -> None:
        with (self.record.directory / "journal.jsonl").open("ab") as journal:
            journal.write(b'{"id": 99, "kind": "candidate.mer')
        code, result = self.record.wait()
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        self.assertEqual(result["events"], [])

    def test_a_start_id_past_the_journal_is_refused_rather_than_waited_on(self) -> None:
        code, result = self.record.wait("--after", "99")
        self.assertEqual(code, 1)
        self.assertEqual(result["code"], "wait_invalid")

    def test_an_unbounded_or_empty_argument_is_refused(self) -> None:
        for arguments in (("--timeout", "inf"), ("--timeout", "nan"), ("--timeout", "-1"), ("--poll", "0"),
                          ("--poll", "nan"), ("--load-multiple", "-1"), ("--after", "-1")):
            with self.subTest(arguments=arguments):
                done = subprocess.run(
                    [sys.executable, "-B", str(TOOL), "--dir", str(self.record.directory), "wait", *arguments],
                    capture_output=True, text=True, check=False, timeout=30,
                )
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertEqual(json.loads(done.stdout)["code"], "wait_invalid")


class OutputTests(RecordCase):
    """One wait run is one JSON document. A host that runs it in the background captures stdout and adds lines of its
    own, such as an exit trailer, so the lead reads --output, which holds that one document alone."""

    def output(self) -> Path:
        return self.record.directory.parent / "wait.json"

    def test_stdout_is_one_document_and_a_host_s_trailer_is_what_breaks_a_reader(self) -> None:
        self.record.add_lanes("lane-1")
        first = self.record.journal("candidate.merged")
        process = self.record.start_wait("--after", str(first - 1), timeout="0.3")
        stdout, _ = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 0)
        document, end = json.JSONDecoder().raw_decode(stdout)
        self.assertEqual(stdout[end:], "\n", "the tool printed more than one document")
        self.assertEqual(document["woke"], "event")
        # What a background host keeps: the tool's document, then its own trailer, which no JSON reader takes.
        captured = stdout + "[exited with code 0]\n"
        with self.assertRaisesRegex(json.JSONDecodeError, "Extra data"):
            json.loads(captured)

    def test_output_holds_the_one_document_stdout_has_and_restart_keeps_it(self) -> None:
        self.record.add_lanes("lane-1")
        first = self.record.journal("candidate.merged")
        before = _tree(self.record.directory)
        process = self.record.start_wait("--after", str(first - 1), f"--output={self.output()}", timeout="0.3")
        stdout, _ = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(self.output().read_text(encoding="utf-8"), stdout)
        with self.output().open(encoding="utf-8") as stream:
            written = json.load(stream)
        self.assertEqual([event["id"] for event in written["events"]], [first])
        self.assertEqual(written["restart"][-1], f"--output={self.output().resolve()}")
        self.assertEqual(_tree(self.record.directory), before, "a wait wrote to the record")
        # The next wait, from the printed arguments, writes there again: an expiry this time, the file replaced whole.
        code, again = self.record.wait(*written["restart"])
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        self.assertEqual(json.loads(self.output().read_text(encoding="utf-8")), again)
        self.assertEqual(again["woke"], "expired")
        self.assertEqual(state_tool.wait_arguments(again["restart"]).output, self.output().resolve())

    def test_a_relative_output_is_carried_into_restart_resolved(self) -> None:
        self.record.add_lanes("lane-1")
        first = self.record.journal("candidate.merged")
        relative = os.path.relpath(self.output())
        code, result = self.record.wait("--after", str(first - 1), f"--output={relative}")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["restart"][-1], f"--output={self.output().resolve()}")
        self.assertTrue(Path(result["restart"][-1].split("=", 1)[1]).is_absolute())

    def test_output_is_written_whole_through_a_temporary_file_moved_onto_it_in_one_call(self) -> None:
        # A reader of --output never sees half a document: the tool writes a temporary file beside FILE, then moves
        # it onto FILE in one call, and never opens FILE for writing itself.
        self.record.add_lanes("lane-1")
        first = self.record.journal("candidate.merged")
        previous = "the previous wait's document\n"
        self.output().write_text(previous, encoding="utf-8")
        argv = ["--dir", str(self.record.directory), "wait", "--load-multiple", "1000000", "--min-free-bytes", "0",
                "--swap-growth-bytes", str(2**60), "--after", str(first - 1), f"--output={self.output()}",
                "--poll", "0.05", "--timeout", "0.3"]
        moves: list[tuple[str, str]] = []
        real_replace = os.replace

        def failed_move(source, destination):
            moves.append((os.fspath(source), os.fspath(destination)))
            self.assertEqual(self.output().read_text(encoding="utf-8"), previous, "FILE was written before the move")
            raise OSError(28, "No space left on device")

        with mock.patch.object(state_tool.os, "replace", failed_move), contextlib.redirect_stdout(_stdout()):
            with self.assertRaises(OSError):
                state_tool.main(argv)
        self.assertEqual(len(moves), 1, moves)
        source, destination = map(Path, moves[0])
        self.assertEqual(destination, self.output().resolve())
        self.assertEqual(source.parent, self.output().resolve().parent, "the temporary file is not in FILE's folder")
        self.assertTrue(source.name.startswith(f".{self.output().name}."), source.name)
        self.assertEqual(self.output().read_text(encoding="utf-8"), previous, "a failed move left FILE changed")
        self.assertEqual(sorted(path.name for path in self.output().parent.glob(f".{self.output().name}.*")), [],
                         "a failed move left its temporary file behind")

        moves.clear()

        def move(source, destination):
            moves.append((os.fspath(source), os.fspath(destination)))
            self.assertEqual(self.output().read_text(encoding="utf-8"), previous, "FILE was written before the move")
            real_replace(source, destination)

        stdout = _stdout()
        with mock.patch.object(state_tool.os, "replace", move), contextlib.redirect_stdout(stdout):
            code = state_tool.main(argv)
        stdout.flush()
        printed = stdout.buffer.getvalue().decode("utf-8")
        self.assertEqual(code, 0, printed)
        self.assertEqual(len(moves), 1, moves)
        self.assertEqual(Path(moves[0][1]), self.output().resolve())
        self.assertEqual(self.output().read_text(encoding="utf-8"), printed)

    def test_a_failed_wait_writes_its_one_error_document_there_too(self) -> None:
        with (self.record.directory / "journal.jsonl").open("ab") as journal:
            journal.write(b"not json\n")
        code, result = self.record.wait(f"--output={self.output()}")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(self.output().read_text(encoding="utf-8")), result)
        self.assertEqual(result["code"], "journal_invalid")

    def test_an_output_in_the_record_or_in_no_folder_is_refused_and_nothing_is_written(self) -> None:
        before = _tree(self.record.directory)
        for output, words in ((self.record.directory / "wait.json", "outside the state directory"),
                              (self.record.directory, "outside the state directory"),
                              (self.record.directory.parent / "no-such-folder/wait.json", "in a folder that exists"),
                              (self.record.directory.parent, "must be a file in a folder that exists")):
            with self.subTest(output=output):
                code, result = self.record.wait(f"--output={output}")
                self.assertEqual((code, result["code"]), (1, "wait_invalid"), result)
                self.assertIn(words, result["error"])
                self.assertFalse((self.record.directory.parent / "no-such-folder").exists())
        self.assertEqual(_tree(self.record.directory), before)

    def test_the_guide_tells_the_lead_to_read_the_output_file(self) -> None:
        text = " ".join(GUIDE.read_text(encoding="utf-8").split())
        self.assertIn("A lead that runs the wait in the background passes `--output <file>` and reads that file", text)
        self.assertIn("not the host's capture of the command's output, which may add lines of its own", text)


class BlockedLaneTests(RecordCase):
    def test_a_blocked_lane_ends_a_running_wait_and_is_named(self) -> None:
        self.record.add_lanes("lane-1")
        process = self.record.start_wait()
        self.assert_running(process)
        self.record.lane_state("lane-1", "blocked")
        code, result = _finish(process)
        self.assertEqual(code, 0)
        self.assertEqual(result["woke"], "event")
        self.assertEqual([lane["id"] for lane in result["blockedLanes"]], ["lane-1"])
        self.assertEqual(result["blockedLanes"][0]["waitingOn"], "lead")

    def test_a_lane_already_blocked_at_the_start_wakes_it(self) -> None:
        self.record.add_lanes("lane-1")
        self.record.lane_state("lane-1", "blocked")
        self.assertEqual(self.record.wait()[0], 0)

    def test_restarting_with_the_printed_arguments_does_not_wake_on_the_same_lane(self) -> None:
        self.record.add_lanes("lane-1")
        self.record.lane_state("lane-1", "blocked")
        _, woken = self.record.wait()
        code, result = self.record.wait(*woken["restart"])
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        self.assertEqual(result["blockedLanes"], [])

    def test_a_seen_lane_that_clears_and_blocks_again_wakes_it(self) -> None:
        self.record.add_lanes("lane-1")
        self.record.lane_state("lane-1", "blocked")
        _, woken = self.record.wait()
        process = self.record.start_wait(*woken["restart"])
        self.assert_running(process)
        self.record.lane_state("lane-1", "working")
        time.sleep(0.25)
        self.assertIsNone(process.poll())
        self.record.lane_state("lane-1", "blocked")
        code, result = _finish(process)
        self.assertEqual(code, 0)
        self.assertEqual([lane["id"] for lane in result["blockedLanes"]], ["lane-1"])

    def test_a_lane_that_cleared_between_two_waits_is_no_longer_seen(self) -> None:
        self.record.add_lanes("lane-1")
        self.record.lane_state("lane-1", "blocked")
        _, woken = self.record.wait()
        self.record.lane_state("lane-1", "working")
        code, expired = self.record.wait(*woken["restart"])
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        self.assertEqual(_seen_blocked(expired), [])

    def test_a_second_lane_blocking_wakes_it_while_the_first_is_seen(self) -> None:
        self.record.add_lanes("lane-1", "lane-2")
        self.record.lane_state("lane-1", "blocked")
        _, woken = self.record.wait()
        process = self.record.start_wait(*woken["restart"])
        self.assert_running(process)
        self.record.lane_state("lane-2", "blocked")
        code, result = _finish(process)
        self.assertEqual(code, 0)
        self.assertEqual([lane["id"] for lane in result["blockedLanes"]], ["lane-2"])
        self.assertEqual(len(_seen_blocked(result)), 2)

    def test_a_lane_that_blocks_again_with_nothing_reading_it_clear_wakes_it(self) -> None:
        self.record.add_lanes("lane-1")
        self.record.lane_state("lane-1", "blocked", text="question one")
        _, woken = self.record.wait()
        self.record.lane_state("lane-1", "working")
        time.sleep(0.01)
        self.record.lane_state("lane-1", "blocked", text="question two")
        code, result = self.record.wait(*woken["restart"])
        self.assertEqual(code, 0)
        self.assertEqual(result["blockedLanes"][0]["focus"], "question two")

    def test_a_lane_that_clears_and_returns_inside_one_poll_wakes_it(self) -> None:
        self.record.add_lanes("lane-1")
        self.record.lane_state("lane-1", "blocked")
        _, woken = self.record.wait()
        process = subprocess.Popen(
            [sys.executable, "-B", str(TOOL), "--dir", str(self.record.directory), "wait", *woken["restart"],
             "--poll", "1.5", "--timeout", "8"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        time.sleep(0.5)
        self.record.lane_state("lane-1", "working")
        self.record.lane_state("lane-1", "blocked")
        code, result = _finish(process)
        self.assertEqual(code, 0)
        self.assertEqual([lane["id"] for lane in result["blockedLanes"]], ["lane-1"])

    def test_a_changed_question_or_addressee_while_still_blocked_wakes_it(self) -> None:
        self.record.add_lanes("lane-1")
        self.record.lane_state("lane-1", "blocked", text="for the operator", waiting_on="operator")
        _, woken = self.record.wait()
        self.record.lane_state("lane-1", "blocked", text="for the operator", waiting_on="lead")
        code, woken = self.record.wait(*woken["restart"])
        self.assertEqual(code, 0)
        self.record.lane_state("lane-1", "blocked", text="another question", waiting_on="lead")
        code, result = self.record.wait(*woken["restart"])
        self.assertEqual(code, 0)
        self.assertEqual(result["blockedLanes"][0]["focus"], "another question")

    def test_a_lane_id_starting_with_a_dash_survives_the_restart(self) -> None:
        self.record.add_lanes("-odd")
        self.record.lane_state("-odd", "blocked")
        code, woken = self.record.wait()
        self.assertEqual(code, 0)
        code, _ = self.record.wait(*woken["restart"])
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)

    def test_a_seen_lane_without_its_token_is_refused(self) -> None:
        code, result = self.record.wait("--seen-blocked", "lane-1")
        self.assertEqual(code, 1)
        self.assertEqual(result["code"], "wait_invalid")


class QuietLaneTests(RecordCase):
    """A lane quiet past the threshold is reported as information, once a stretch, and ends no wait."""

    def quiet(self, *arguments: str, timeout: str = "0.3") -> tuple[int, dict]:
        return self.record.wait("--quiet-seconds", "1", *arguments, timeout=timeout)

    def lane_saying(self, state: str = "working") -> None:
        self.record.add_lanes("lane-1")
        self.record.tool("focus", "--lane=lane-1", "--text", "working on it", "--state", state, "--waiting-on=")

    def age(self) -> None:
        # The threshold is a second, so a lane has been quiet past it after this one.
        time.sleep(1.1)

    def test_a_quiet_lane_is_reported_when_the_wait_ends_and_doesnt_end_it(self) -> None:
        self.lane_saying()
        self.age()
        code, result = self.quiet()
        self.assertEqual((code, result["woke"]), (state_tool.WAIT_EXPIRED_EXIT, "expired"))
        self.assertEqual([lane["id"] for lane in result["quietLanes"]], ["lane-1"])
        self.assertEqual(result["quietLanes"][0]["focus"], "working on it")
        self.assertGreaterEqual(result["quietLanes"][0]["quietSeconds"], 1)
        self.assertEqual((result["pressure"], result["blockedLanes"]), ([], []))

    def test_an_event_carries_the_report_with_it(self) -> None:
        self.lane_saying()
        self.age()
        process = self.record.start_wait("--quiet-seconds", "1")
        time.sleep(0.3)
        self.record.journal("candidate.merged")
        code, result = _finish(process)
        self.assertEqual((code, result["woke"]), (0, "event"))
        self.assertEqual([lane["id"] for lane in result["quietLanes"]], ["lane-1"])

    def test_a_restart_doesnt_report_the_same_stretch_again(self) -> None:
        self.lane_saying()
        self.age()
        _, first = self.quiet()
        self.assertTrue(first["quietLanes"])
        done = subprocess.run([sys.executable, "-B", str(TOOL), "--dir", str(self.record.directory), "wait", *first["restart"]],
                              capture_output=True, text=True, check=False, timeout=30)
        again = json.loads(done.stdout)
        self.assertEqual((done.returncode, again["quietLanes"]), (state_tool.WAIT_EXPIRED_EXIT, []))
        # It stays marked as seen across as many restarts as the stretch lasts.
        self.assertEqual(again["restart"], first["restart"])

    def test_the_lane_speaking_ends_the_stretch_and_a_later_one_is_reported(self) -> None:
        self.lane_saying()
        self.age()
        _, first = self.quiet()
        self.record.tool("focus", "--lane=lane-1", "--text", "working on it", "--state", "working", "--waiting-on=")
        done = subprocess.run([sys.executable, "-B", str(TOOL), "--dir", str(self.record.directory), "wait", *first["restart"]],
                              capture_output=True, text=True, check=False, timeout=30)
        self.assertEqual(json.loads(done.stdout)["quietLanes"], [])
        self.age()
        done = subprocess.run([sys.executable, "-B", str(TOOL), "--dir", str(self.record.directory), "wait", *first["restart"]],
                              capture_output=True, text=True, check=False, timeout=30)
        self.assertEqual([lane["id"] for lane in json.loads(done.stdout)["quietLanes"]], ["lane-1"])

    def test_a_lane_resting_blocked_or_handed_is_not_quiet(self) -> None:
        for state in ("idle", "done", "handed"):
            with self.subTest(state):
                self.lane_saying(state)
                self.age()
                self.assertEqual(self.quiet()[1]["quietLanes"], [])
        self.record.lane_state("lane-1", "blocked")
        self.age()
        self.assertEqual(self.quiet()[1]["quietLanes"], [])

    def test_a_lane_under_the_threshold_is_not_quiet(self) -> None:
        self.lane_saying()
        self.assertEqual(self.record.wait("--quiet-seconds", "3600")[1]["quietLanes"], [])

    def test_a_record_with_no_time_counts_from_when_the_lane_last_changed_state(self) -> None:
        self.record.add_lanes("lane-1")
        path = self.record.directory / "state.json"
        state = state_tool.read_state(self.record.directory)
        del state["lanes"][0]["focusAt"]
        path.write_text(state_tool.dumps(state), encoding="utf-8")
        code, result = self.quiet()
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        self.assertEqual([lane["id"] for lane in result["quietLanes"]], ["lane-1"])

    def test_an_impossible_time_doesnt_stop_a_wait_ending(self) -> None:
        self.record.add_lanes("lane-1")
        state = state_tool.read_state(self.record.directory)
        state["lanes"][0]["since"] = "2026-13-45T00:00:00Z"
        (self.record.directory / "state.json").write_text(state_tool.dumps(state), encoding="utf-8")
        code, result = self.quiet()
        self.assertEqual((code, result["quietLanes"]), (state_tool.WAIT_EXPIRED_EXIT, []))

    def test_the_threshold_must_be_finite_and_not_negative(self) -> None:
        for value in ("-1", "nan", "inf"):
            with self.subTest(value):
                done = subprocess.run([sys.executable, "-B", str(TOOL), "--dir", str(self.record.directory), "wait", f"--quiet-seconds={value}", "--timeout", "0"],
                                      capture_output=True, text=True, check=False, timeout=30)
                self.assertNotEqual(done.returncode, 0)
                self.assertNotEqual(done.returncode, state_tool.WAIT_EXPIRED_EXIT)


class RestartTests(RecordCase):
    def test_a_printed_restart_alone_keeps_the_leads_limits(self) -> None:
        done = subprocess.run(
            [sys.executable, "-B", str(TOOL), "--dir", str(self.record.directory), "wait", "--timeout", "0.2", "--poll",
             "0.05", "--load-multiple", "1000000", "--min-free-bytes", "0", "--swap-growth-bytes", str(2**60)],
            capture_output=True, text=True, check=False, timeout=30,
        )
        restart = json.loads(done.stdout)["restart"]
        parsed = state_tool.wait_arguments(restart)
        self.assertEqual((parsed.timeout, parsed.poll, parsed.load_multiple, parsed.min_free_bytes, parsed.swap_growth_bytes),
                         (0.2, 0.05, 1000000.0, 0, 2**60))
        again = subprocess.run(
            [sys.executable, "-B", str(TOOL), "--dir", str(self.record.directory), "wait", *restart],
            capture_output=True, text=True, check=False, timeout=30,
        )
        self.assertEqual(again.returncode, state_tool.WAIT_EXPIRED_EXIT, again.stdout + again.stderr)
        self.assertEqual(json.loads(again.stdout)["restart"], restart)

    def test_an_unknown_held_signal_is_a_usage_error(self) -> None:
        done = subprocess.run(
            [sys.executable, "-B", str(TOOL), "--dir", str(self.record.directory), "wait", "--seen-pressure=swap"],
            capture_output=True, text=True, check=False, timeout=30,
        )
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)


def _seen_blocked(result: dict) -> list[str]:
    return [argument for argument in result["restart"] if argument.startswith("--seen-blocked=")]


def _reading(**values) -> dict:
    return {"cores": 16, "load5": 1.0, "diskFreeBytes": 500 * GIB, "swapUsedBytes": 1 * GIB, **values}


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class MachineCase(RecordCase):
    def run_wait(self, readings: list[dict], *, timeout: float = 100, **options) -> tuple[int, dict]:
        queued = iter(readings)
        last = [readings[-1]]

        def read_machine(_directory: Path) -> dict:
            last[0] = next(queued, last[0])
            return last[0]

        clock = Clock()
        options.setdefault("after", None)
        return state_tool.wait_for_lead(self.record.directory, timeout=timeout, poll=10, read_machine=read_machine,
                                        clock=clock, sleep=clock.sleep, **options)

    def restart(self, readings: list[dict], woken: dict, **options) -> tuple[int, dict]:
        return self.run_wait(readings, **_restart_options(woken), **options)


def _restart_options(result: dict) -> dict:
    """The printed arguments, read as the command line would read them."""
    parsed = state_tool.wait_arguments(result["restart"])
    return {"after": parsed.after, "seen_blocked": parsed.seen_blocked, "seen_pressure": parsed.seen_pressure,
            "swap_baseline": parsed.swap_baseline, "load_multiple": parsed.load_multiple,
            "min_free_bytes": parsed.min_free_bytes, "swap_growth_bytes": parsed.swap_growth_bytes}


class PressureTests(MachineCase):
    def test_sustained_load_ends_it_and_names_the_value(self) -> None:
        code, result = self.run_wait([_reading(), _reading(load5=37.0)])
        self.assertEqual(code, 0)
        self.assertEqual(result["woke"], "pressure")
        self.assertEqual(result["pressure"][0]["signal"], "load")
        self.assertEqual(result["pressure"][0]["value"], 37.0)
        self.assertEqual(result["pressure"][0]["limit"], 32.0)

    def test_load_at_the_multiple_does_not_end_it(self) -> None:
        code, _ = self.run_wait([_reading(load5=32.0)])
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)

    def test_low_free_disk_ends_it_and_names_the_value(self) -> None:
        code, result = self.run_wait([_reading(), _reading(diskFreeBytes=5 * GIB)])
        self.assertEqual(code, 0)
        self.assertEqual(result["pressure"][0]["signal"], "disk")
        self.assertEqual(result["pressure"][0]["value"], 5 * GIB)
        self.assertEqual(result["pressure"][0]["limit"], state_tool.WAIT_MIN_FREE_BYTES)

    def test_swap_growth_since_the_start_ends_it_and_names_the_value(self) -> None:
        code, result = self.run_wait([_reading(swapUsedBytes=10 * GIB), _reading(swapUsedBytes=15 * GIB)])
        self.assertEqual(code, 0)
        self.assertEqual(result["pressure"][0]["signal"], "swap")
        self.assertEqual(result["pressure"][0]["value"], 15 * GIB)
        self.assertEqual(result["pressure"][0]["baseline"], 10 * GIB)

    def test_steady_high_swap_does_not_wake_it_at_all(self) -> None:
        code, result = self.run_wait([_reading(swapUsedBytes=63 * GIB)])
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        self.assertEqual(result["pressure"], [])

    def test_steady_high_swap_does_not_wake_it_again_and_again(self) -> None:
        high = _reading(swapUsedBytes=20 * GIB)
        _, woken = self.run_wait([_reading(swapUsedBytes=10 * GIB), high])
        for _ in range(3):
            code, woken = self.restart([high], woken)
            self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
            self.assertEqual(woken["pressure"], [])

    def test_slow_swap_growth_across_many_waits_still_wakes_it(self) -> None:
        code, woken = self.run_wait([_reading(swapUsedBytes=10 * GIB)])
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        for used in (11, 12, 13):
            code, woken = self.restart([_reading(swapUsedBytes=used * GIB)], woken)
            self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        code, woken = self.restart([_reading(swapUsedBytes=15 * GIB)], woken)
        self.assertEqual(code, 0)
        self.assertEqual(woken["pressure"][0]["signal"], "swap")

    def test_swap_that_falls_and_grows_back_wakes_it_again(self) -> None:
        _, woken = self.run_wait([_reading(swapUsedBytes=10 * GIB), _reading(swapUsedBytes=20 * GIB)])
        code, woken = self.restart([_reading(swapUsedBytes=20 * GIB), _reading(swapUsedBytes=5 * GIB),
                                    _reading(swapUsedBytes=20 * GIB)], woken)
        self.assertEqual(code, 0)
        self.assertEqual(woken["pressure"][0]["signal"], "swap")

    def test_a_held_condition_is_seen_on_restart_and_wakes_again_after_clearing(self) -> None:
        for signal, high, low in (("load", {"load5": 40.0}, {"load5": 1.0}),
                                  ("disk", {"diskFreeBytes": 1 * GIB}, {"diskFreeBytes": 500 * GIB})):
            with self.subTest(signal=signal):
                _, woken = self.run_wait([_reading(**high)])
                self.assertEqual(woken["pressure"][0]["signal"], signal)
                code, held = self.restart([_reading(**high)], woken)
                self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT, "a held condition woke it again")
                code, returned = self.restart([_reading(**high), _reading(**low), _reading(**high)], held)
                self.assertEqual(code, 0, "a condition that cleared and returned did not wake it")
                self.assertEqual(returned["pressure"][0]["signal"], signal)

    def test_a_held_signal_that_worsens_stays_quiet_until_it_clears(self) -> None:
        for signal, first, worse in (
            ("load", {"load5": 40.0}, {"load5": 400.0}),
            ("disk", {"diskFreeBytes": 18 * GIB}, {"diskFreeBytes": 0}),
        ):
            with self.subTest(signal=signal):
                _, woken = self.run_wait([_reading(**first)])
                held = woken
                for _ in range(3):
                    code, held = self.restart([_reading(**worse)], held)
                    self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT, "a seen condition woke it before clearing")
                self.assertEqual(held["machine"], _reading(**worse))

    def test_a_value_hovering_at_its_limit_wakes_it_once(self) -> None:
        for high, near in (({"load5": 33.0}, {"load5": 31.0}), ({"diskFreeBytes": 19 * GIB}, {"diskFreeBytes": 21 * GIB})):
            with self.subTest(high=high):
                _, woken = self.run_wait([_reading(**high)])
                code, _ = self.restart([_reading(**near), _reading(**high), _reading(**near), _reading(**high)], woken)
                self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)

    def test_a_restart_keeps_an_overridden_threshold(self) -> None:
        reading = _reading(diskFreeBytes=15 * GIB)
        code, expired = self.run_wait([reading], min_free_bytes=5 * GIB)
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        code, _ = self.restart([reading], expired)
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT, "the restart went back to the default limit")
        code, result = self.restart([_reading(diskFreeBytes=4 * GIB)], expired)
        self.assertEqual(code, 0)
        self.assertEqual(result["pressure"][0]["limit"], 5 * GIB)

    def test_a_new_signal_wakes_it_while_another_is_seen(self) -> None:
        _, woken = self.run_wait([_reading(load5=40.0)])
        code, result = self.restart([_reading(load5=40.0, diskFreeBytes=1 * GIB)], woken)
        self.assertEqual(code, 0)
        self.assertEqual([item["signal"] for item in result["pressure"]], ["disk"])

    def test_a_lead_can_override_the_thresholds(self) -> None:
        reading = _reading(load5=5.0, diskFreeBytes=50 * GIB, swapUsedBytes=1 * GIB)
        self.assertEqual(self.run_wait([reading])[0], state_tool.WAIT_EXPIRED_EXIT)
        self.assertEqual(self.run_wait([reading], load_multiple=0.25)[1]["pressure"][0]["signal"], "load")
        self.assertEqual(self.run_wait([reading], min_free_bytes=100 * GIB)[1]["pressure"][0]["signal"], "disk")
        code, result = self.run_wait([reading, _reading(load5=5.0, diskFreeBytes=50 * GIB, swapUsedBytes=2 * GIB)],
                                     swap_growth_bytes=GIB // 2)
        self.assertEqual(code, 0)
        self.assertEqual(result["pressure"][0]["signal"], "swap")

    def test_an_event_and_pressure_are_both_named(self) -> None:
        identifier = self.record.journal("candidate.merged")
        code, result = self.run_wait([_reading(load5=40.0)], after=identifier - 1)
        self.assertEqual(code, 0)
        self.assertEqual(result["woke"], "event")
        self.assertEqual(result["pressure"][0]["signal"], "load")

    def test_an_unreadable_value_is_reported_and_not_treated_as_healthy(self) -> None:
        reading = _reading(load5=None, cores=None, swapUsedBytes=None, diskFreeBytes=None)
        code, result = self.run_wait([reading])
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        self.assertEqual(result["unavailable"], ["disk", "load", "swap"])
        self.assertEqual(result["machine"]["swapUsedBytes"], None)

    def test_a_value_that_becomes_readable_starts_its_swap_baseline_then(self) -> None:
        code, result = self.run_wait([_reading(swapUsedBytes=None), _reading(swapUsedBytes=30 * GIB),
                                      _reading(swapUsedBytes=31 * GIB)])
        self.assertEqual(code, state_tool.WAIT_EXPIRED_EXIT)
        self.assertEqual(result["pressure"], [])


class RealMachineTests(RecordCase):
    def test_a_real_disk_signal_ends_a_real_wait(self) -> None:
        process = subprocess.run(
            [sys.executable, "-B", str(TOOL), "--dir", str(self.record.directory), "wait", "--poll", "0.05",
             "--timeout", "5", "--load-multiple", "1000000", "--swap-growth-bytes", str(2**60),
             "--min-free-bytes", str(2**62)],
            capture_output=True, text=True, check=False, timeout=30,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["pressure"][0]["signal"], "disk")
        self.assertIsInstance(result["pressure"][0]["value"], int)

    def test_the_real_reading_has_every_value_or_says_none(self) -> None:
        reading = state_tool.machine_reading(self.record.directory)
        self.assertEqual(set(reading), {"cores", "load5", "diskFreeBytes", "swapUsedBytes"})
        self.assertIsInstance(reading["diskFreeBytes"], int)

    def test_an_unreadable_value_is_said_on_stderr_when_it_starts(self) -> None:
        process = self.record.start_wait(timeout="0.2")
        _, stderr = process.communicate(timeout=30)
        reading = state_tool.machine_reading(self.record.directory)
        for name, key in (("load", "load5"), ("swap", "swapUsedBytes")):
            if reading[key] is None:
                self.assertIn(name, stderr)
            else:
                self.assertNotIn(f"{name} unavailable", stderr)

    def test_swap_readings_parse_or_say_none(self) -> None:
        self.assertEqual(state_tool._swap_from_meminfo("SwapTotal:  2048 kB\nSwapFree:  1024 kB\n"), 1024 * 1024)
        self.assertEqual(state_tool._swap_from_meminfo("SwapTotal:  0 kB\nSwapFree:  0 kB\n"), 0)
        self.assertIsNone(state_tool._swap_from_meminfo("MemTotal:  2048 kB\n"))
        self.assertEqual(
            state_tool._swap_from_swapusage("total = 64512.00M  used = 2048.50M  free = 100.00M  (encrypted)"),
            int(2048.5 * 2**20),
        )
        self.assertEqual(state_tool._swap_from_swapusage("total = 4.00G  used = 1.00G  free = 3.00G"), GIB)
        self.assertIsNone(state_tool._swap_from_swapusage("sysctl: unknown oid"))


RUN_DELIVERY = ROOT / "skills/run-delivery/scripts/run_delivery.py"
RUN_DELIVERY_SKILL = ROOT / "skills/run-delivery/SKILL.md"
LEASE_FIELDS = {"pid", "startedAt", "updatedAt", "after", "checkout"}


def _load_run_delivery():
    spec = importlib.util.spec_from_file_location("run_delivery_for_the_lease", RUN_DELIVERY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


flow = _load_run_delivery()


def _dead_pid() -> int:
    """A pid that was a process of this case's and has ended."""
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid


class FakeWait:
    """A wait that times out `ticks` times, calling `on_tick` each time, then ends with `code` after `on_end` (or raises
    `raises`)."""

    def __init__(self, ticks: int, code: int = 0, on_tick=None, raises: BaseException | None = None,
                 on_end=None) -> None:
        self.ticks, self.code, self.on_tick, self.raises, self.on_end = ticks, code, on_tick, raises, on_end
        self.ended = False
        self.terminated = False

    def wait(self, timeout: float | None = None) -> int:
        if self.terminated:
            self.ended = True
            return -15
        if self.ticks > 0:
            self.ticks -= 1
            if self.on_tick:
                self.on_tick()
            raise subprocess.TimeoutExpired("wait", timeout)
        if self.raises is not None:
            raise self.raises
        if self.on_end:
            self.on_end()
        self.ended = True
        return self.code

    def poll(self) -> int | None:
        return self.code if self.ended else None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True


class LeaseTests(unittest.TestCase):
    """The lead's listener: the lease `lead-wait` keeps, and the one function every reader goes through."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"
        self.state.mkdir()
        self.at = float(int(time.time()))  # whole seconds: a stamp reads back exactly, so the boundary is exact

    def lease(self, **fields) -> dict:
        lease = {"pid": os.getpid(), "startedAt": flow.utc_stamp(self.at - 300), "updatedAt": flow.utc_stamp(self.at),
                 "after": 4, "checkout": "/checkout", **fields}
        (self.state / "lead-wait.json").write_text(json.dumps(lease))
        return lease

    def test_the_lease_is_the_named_file_and_its_limits_are_the_decided_ones(self) -> None:
        self.assertEqual(flow.LEAD_WAIT_LEASE, "lead-wait.json")
        self.assertEqual((flow.LEAD_WAIT_REFRESH_SECONDS, flow.LEAD_WAIT_LIVE_SECONDS), (60, 180))

    def test_no_lease_is_no_listener(self) -> None:
        self.assertIsNone(flow.lead_listener(self.state, self.at))

    def test_a_lease_179_seconds_old_with_a_live_pid_is_live(self) -> None:
        lease = self.lease(updatedAt=flow.utc_stamp(self.at - 179))
        self.assertEqual(flow.lead_listener(self.state, self.at), lease)

    def test_a_lease_180_seconds_old_is_absent_though_its_pid_lives(self) -> None:
        self.lease(updatedAt=flow.utc_stamp(self.at - 180))
        self.assertIsNone(flow.lead_listener(self.state, self.at))

    def test_a_lease_whose_pid_has_died_is_absent_though_it_is_fresh(self) -> None:
        self.lease(pid=_dead_pid())
        self.assertIsNone(flow.lead_listener(self.state, self.at))

    def test_a_stamp_without_its_fraction_reads_the_same(self) -> None:
        moment = int(self.at) - 100
        self.lease(updatedAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment)))
        self.assertIsNotNone(flow.lead_listener(self.state, moment + 179.5))
        self.assertIsNone(flow.lead_listener(self.state, moment + 180))

    def test_a_malformed_lease_is_absent(self) -> None:
        cases = {"not json": "{", "a list": "[]", "no pid": json.dumps({"updatedAt": flow.utc_stamp(self.at)})}
        for name, text in cases.items():
            with self.subTest(name):
                (self.state / "lead-wait.json").write_text(text)
                self.assertIsNone(flow.lead_listener(self.state, self.at))
        for name, fields in {"a boolean pid": {"pid": True}, "pid 1": {"pid": 1}, "a text pid": {"pid": str(os.getpid())},
                             "no stamp": {"updatedAt": None}, "a bad stamp": {"updatedAt": "soon"},
                             "a stamp past a refresh ahead": {"updatedAt": flow.utc_stamp(self.at + 61)}}.items():
            with self.subTest(name):
                self.lease(**fields)
                self.assertIsNone(flow.lead_listener(self.state, self.at))

    def test_the_lease_is_written_refreshed_through_a_temporary_file_and_removed_when_the_wait_ends(self) -> None:
        seen: list[dict] = []
        moments = iter(range(1000, 2000, 60))
        read = lambda: seen.append(json.loads((self.state / "lead-wait.json").read_text()))
        proc = FakeWait(ticks=3, code=3, on_tick=read)
        replaced: list[tuple[str, str]] = []
        real_replace = os.replace

        def replace(source, target):
            replaced.append((Path(source).name, Path(target).name))
            return real_replace(source, target)

        with mock.patch.object(flow.os, "replace", side_effect=replace):
            code = flow.hold_lease(self.state, proc, after=7, checkout=Path("/lead"), every=0.01,
                                   clock=lambda: float(next(moments)))
        self.assertEqual(code, 3)
        self.assertEqual(len(seen), 3)
        for lease in seen:
            self.assertEqual(set(lease), LEASE_FIELDS)
            self.assertEqual((lease["pid"], lease["after"], lease["checkout"]), (os.getpid(), 7, "/lead"))
            self.assertEqual(lease["startedAt"], flow.utc_stamp(1000))
        self.assertEqual([lease["updatedAt"] for lease in seen],
                         [flow.utc_stamp(1000), flow.utc_stamp(1060), flow.utc_stamp(1120)])
        # One write at the start and one per refresh, each a whole file moved onto the lease in one call.
        self.assertEqual(replaced, [(f".lead-wait.json.{os.getpid()}", "lead-wait.json")] * 4)
        self.assertFalse((self.state / "lead-wait.json").exists(), "the lease outlived its wait")
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ["state.lock"])

    def test_a_refresh_keeps_a_long_wait_live_past_the_live_limit(self) -> None:
        live: list[bool] = []
        clock = iter(range(0, 10_000, 60))
        state = self.state
        moments: list[float] = []

        def now() -> float:
            moments.append(float(next(clock)))
            return moments[-1]

        proc = FakeWait(ticks=5, on_tick=lambda: live.append(
            flow.lead_listener(state, moments[-1] + 59) is not None))
        flow.hold_lease(state, proc, after=0, checkout=Path("/lead"), every=0.01, clock=now)
        self.assertEqual(live, [True] * 5)
        self.assertGreater(moments[-1], flow.LEAD_WAIT_LIVE_SECONDS)

    def test_a_stopped_lead_wait_stops_its_wait_and_removes_the_lease(self) -> None:
        proc = FakeWait(ticks=1, raises=SystemExit(143))
        with self.assertRaises(SystemExit):
            flow.hold_lease(self.state, proc, after=0, checkout=Path("/lead"), every=0.01)
        self.assertTrue(proc.terminated, "the wait outlived the lead-wait that started it")
        self.assertFalse((self.state / "lead-wait.json").exists())

    def test_a_later_lead_wait_s_lease_is_left_in_place(self) -> None:
        other = {"pid": os.getppid(), "startedAt": "x", "updatedAt": flow.utc_stamp(self.at), "after": 9,
                 "checkout": "/other"}
        # Another lead-wait took the lease after this one's last refresh: ending, this one leaves it.
        proc = FakeWait(ticks=1, on_end=lambda: (self.state / "lead-wait.json").write_text(json.dumps(other)))
        flow.hold_lease(self.state, proc, after=0, checkout=Path("/lead"), every=0.01)
        self.assertEqual(json.loads((self.state / "lead-wait.json").read_text()), other)

    def test_what_needs_a_listener_is_a_lane_at_work_or_a_candidate_not_merged(self) -> None:
        lane = lambda name, state: {"id": name, "state": state}
        state = {"lanes": [lane("lane-1", "idle"), lane("lane-2", "done")], "queue": [{"subject": "A", "stage": "merged"}]}
        self.assertEqual(flow.listener_needed(state), [])
        for name in ("assigned", "working", "reviewing", "handed", "blocked"):
            with self.subTest(lane=name):
                self.assertEqual(flow.listener_needed({**state, "lanes": [lane("lane-1", name)]}), [f"lane-1 ({name})"])
        for stage in ("queued", "syncing", "synced", "checking", "checked", "merging", "stopped"):
            with self.subTest(stage=stage):
                self.assertEqual(flow.listener_needed({**state, "queue": [{"subject": "A", "stage": stage}]}),
                                 [f"A ({stage})"])


class StandingTextTests(unittest.TestCase):
    def assert_phrases(self, path: Path, *phrases: str) -> None:
        text = " ".join(path.read_text(encoding="utf-8").split())
        for phrase in phrases:
            with self.subTest(path=path.name, phrase=phrase):
                self.assertIn(phrase, text)

    def test_the_rule_is_in_the_skill_and_the_guide(self) -> None:
        for path in (SKILL, GUIDE):
            self.assert_phrases(
                path,
                "A lead does not end its turn while a lane is working or a candidate is queued unless a `wait` is "
                "running in the background",
                "restarts the wait after each wake",
                "The host's notes are a convenience and never the lead's only wake",
                "Where nothing can run in the background, the lead tells the operator that it won't wake on its own",
            )

    def test_the_pressure_rule_is_in_the_skill_and_the_guide(self) -> None:
        for path in (SKILL, GUIDE):
            self.assert_phrases(
                path,
                "Whenever a wait names pressure, whatever else woke it",
                "finds what is loading the machine before taking more work",
                "reduces this delivery's own load first",
                "what it can't reduce to the operator",
                "It stops nothing it didn't start without the operator's word",
            )

    def test_both_lane_briefs_tell_a_lane_to_mark_itself_blocked(self) -> None:
        guide = GUIDE.read_text(encoding="utf-8")
        brief = " ".join(guide[guide.index("## Lane brief template"):guide.index("## Lead authority and escalation")].split())
        run_delivery = " ".join(RUN_DELIVERY_BRIEF.read_text(encoding="utf-8").split())
        for text in (brief, run_delivery):
            self.assertRegex(text, r'focus --text "<your one short question>" --state blocked --waiting-on lead')
            self.assertIn("so the lead's `wait` sees it and carries the question", text)
            self.assertIn('When the answer comes, set `focus --text "<what you are doing>" --state working --waiting-on ""` '
                          "before going on", text)

    def test_both_lane_briefs_cap_local_test_load(self) -> None:
        guide = GUIDE.read_text(encoding="utf-8")
        brief = " ".join(guide[guide.index("## Lane brief template"):guide.index("## Lead authority and escalation")].split())
        run_delivery = " ".join(RUN_DELIVERY_BRIEF.read_text(encoding="utf-8").split())
        for text in (brief, run_delivery):
            self.assertRegex(text, r"at most (?:`<[^>]*default 4[^>]*>`|4) local test workers")
            self.assertNotIn("one test run at a time", text)

    def test_a_failed_claim_returns_to_a_lane_that_can_be_told(self) -> None:
        self.assert_phrases(
            GUIDE,
            "One whose report fails returns to the lane on a host that can still message it, and otherwise waits for "
            "the operator's answer",
        )

    @staticmethod
    def section(path: Path, heading: str) -> str:
        """One section's text, whitespace-normalized: from its heading to the next heading of its level or above."""
        text = path.read_text(encoding="utf-8")
        level = heading.split(" ")[0]
        start = text.index(heading + "\n")
        ends = [found.start() for found in re.finditer(rf"^#{{1,{len(level)}}} ", text[start + len(heading):], re.M)]
        return " ".join(text[start:start + len(heading) + (ends[0] if ends else len(text))].split())

    def test_lead_wait_is_named_where_the_lead_reads_the_rule(self) -> None:
        wake = self.section(GUIDE, "### The lead's wake")
        for phrase in (
            "With run-delivery, the wait a lead starts is `lead-wait`, which wraps this `wait`",
            "run_delivery.py --delivery <key> lead-wait [<wait arguments>]",
            "keeps a lease in the record, `<state>/lead-wait.json`, holding its `pid`, `startedAt`, `updatedAt`, `after` "
            "and `checkout`, rewritten through a temporary file every 60 seconds and removed when it ends or is stopped",
            "A lease is live while its pid is alive and its `updatedAt` is under 180 seconds old",
            "`lead-init`, `lead-assign`, `lead-accept` and `lead-run --resume` end, while a lane works or a candidate is "
            "queued, with the exact `lead-wait` to start in the background",
            "says \"The lead isn't listening\" with that command",
            "A running `lead-wait` clears both",
            "Codex documents hooks of its own, but this generation installs none there",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, wake)
        rows = [" ".join(line.split()) for line in RUN_DELIVERY_SKILL.read_text(encoding="utf-8").splitlines()
                if line.startswith("| Lead | `lead-wait")]
        self.assertEqual(len(rows), 1, "run-delivery's SKILL.md has no Lead row for lead-wait")
        for phrase in (
            "The lead's wake: `delivery_state.py wait`, which it wraps",
            "A lead never ends its turn while a lane works or a candidate is queued unless `lead-wait` runs in the "
            "background",
            "end with the exact `lead-wait` to start",
            "say \"The lead isn't listening\"",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, rows[0])
        authority = self.section(SKILL, "## Lead authority")
        self.assertIn("The lead keeps its wake while a lane works or a candidate is queued: with run-delivery, "
                      "`run_delivery.py lead-wait` runs in the background", authority)
        self.assertIn("With run-delivery the wait is `run_delivery.py lead-wait`, which wraps it",
                      self.section(SKILL, "## Keeping the lead's wake"))

    def test_the_guide_gives_the_command_and_its_exits(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        self.assertIsNotNone(re.search(r"delivery_state\.py --dir <state[^>]*> wait", text))
        self.assertIn(f"exits {state_tool.WAIT_EXPIRED_EXIT}", text)
        self.assertIn("reads the record and the machine only", " ".join(text.split()))


if __name__ == "__main__":
    unittest.main()
