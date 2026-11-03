"""The lead's own wake: `wait` reads the record and the machine, and returns on what needs the lead."""

from __future__ import annotations

import hashlib
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


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/orchestrate-work/SKILL.md"
GUIDE = ROOT / "skills/orchestrate-work/references/orchestration-guide.md"
RUN_DELIVERY_BRIEF = ROOT / "skills/run-delivery/templates/lane-brief.md"
TOOL = ROOT / "skills/orchestrate-work/scripts/delivery_state.py"
EVENT_KINDS = ("candidate.handed", "candidate.merged", "candidate.stopped", "base.broken", "blocker.recorded")
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

    def test_the_guide_gives_the_command_and_its_exits(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        self.assertIsNotNone(re.search(r"delivery_state\.py --dir <state[^>]*> wait", text))
        self.assertIn(f"exits {state_tool.WAIT_EXPIRED_EXIT}", text)
        self.assertIn("reads the record and the machine only", " ".join(text.split()))


if __name__ == "__main__":
    unittest.main()
