"""The machine ledger: claims, admission, measured costs and capacity, shared by every repository on a machine."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/orchestrate-work/scripts"
MACHINE = SCRIPTS / "machine.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests  # noqa: E402

if os.name != "nt":
    import fcntl


def _load(name: str, path: Path):
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(path.parent))


machine = _load("machine", MACHINE)
state_tool = sys.modules["delivery_state"]


class FakeClock:
    def __init__(self) -> None:
        self.moment = 1_800_000_000.0

    def __call__(self) -> float:
        return self.moment

    def sleep(self, seconds: float) -> None:
        self.moment += seconds


def run(seconds: float, cpu: float | None, result: str = "passed"):
    return machine.Measured(0 if result == "passed" else 1, seconds, cpu, result)


class LedgerCase(unittest.TestCase):
    """Each test has its own machine directory, with capacity written explicitly, and an injected clock and sleep."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "machine"
        patched = mock.patch.dict(os.environ, {machine.DIRECTORY_VARIABLE: str(self.directory)})
        patched.start()
        self.addCleanup(patched.stop)
        os.environ.pop(machine.CLAIM_VARIABLE, None)
        self.clock = FakeClock()
        self.err = io.StringIO()
        self.ledger = machine.Ledger(self.directory, clock=self.clock, sleep=self.clock.sleep, stderr=self.err)
        self.set_capacity(4)

    def set_capacity(self, slots: int) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "capacity.json").write_text(json.dumps(
            {"schemaVersion": "machine-capacity/1", "slots": slots, "setAt": "2026-11-20T00:00:00Z",
             "why": "the test's own capacity"}, indent=2) + "\n", encoding="utf-8")

    def request(self, slots: int, label: str, **options: object):
        self.clock.sleep(1)
        claim = self.ledger.request(options.pop("kind", "run"), options.pop("subject", label), label=label,
                                    slots=slots, **options)
        self.addCleanup(claim.release)
        return claim

    def files(self) -> list[str]:
        return sorted(path.stem for path in (self.directory / "claims").glob("*.json"))

    def hold(self, path: Path) -> None:
        """Hold ``path``'s lock from another open file description, as another process would."""
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def history(self) -> list[dict]:
        return self.ledger.read_history()

    def assertShapes(self) -> None:
        def check(value: object, name: str) -> None:
            self.assertEqual(state_tool.validate(value, state_tool.load_schema(name)), [], name)

        for path in (self.directory / "claims").glob("*.json"):
            check(json.loads(path.read_text(encoding="utf-8")), "machine-claim/1")
        for name, schema in (("capacity.json", "machine-capacity/1"), ("costs.json", "machine-costs/1")):
            if (self.directory / name).exists():
                check(json.loads((self.directory / name).read_text(encoding="utf-8")), schema)
        for entry in self.history():
            check(entry, "machine-history/1")


@unittest.skipIf(os.name == "nt", "Windows is not validated")
class AdmissionTests(LedgerCase):
    def test_a_full_machine_makes_a_claim_wait_and_names_the_holder(self) -> None:
        self.ledger.record_cost(None, "run", "suite", run(600, 2400))
        holder = self.request(4, "suite", delivery="V26-1")
        self.assertTrue(holder.admitted)
        self.assertIsNotNone(holder.record["expectedEnd"])
        late = self.request(1, "probe")
        self.assertFalse(late.poll())
        self.assertFalse(late.poll())
        lines = self.err.getvalue().splitlines()
        self.assertEqual(len(lines), 1, "one line when it starts waiting, and none while nothing changes")
        self.assertIn(f"suite (delivery V26-1, 4 slots, expected end {holder.record['expectedEnd']})", lines[0])
        self.assertIn("4 of 4 in use", lines[0])
        self.assertIn("0 slots are free now.", lines[0])
        self.assertEqual(late.record["state"], "waiting")
        self.assertIsNone(late.record["admittedBy"])
        self.assertEqual(self.files(), sorted([holder.id, late.id]))
        self.assertShapes()

    def test_waiters_are_served_first_come_first_served_with_no_backfill(self) -> None:
        self.request(2, "first")
        large = self.request(3, "large")
        small = self.request(1, "small")
        self.assertFalse(large.poll())
        self.assertIn("2 slots are free now; a run of at most 2 would start at once.", self.err.getvalue())
        self.err.seek(0)
        self.err.truncate(0)
        self.assertFalse(small.poll())
        self.assertIn("1 waiter ahead", self.err.getvalue())
        self.assertIn("2 slots are free now.", self.err.getvalue())
        self.assertNotIn("would start at once", self.err.getvalue())

    def test_a_release_admits_the_waiter_by_capacity_and_the_history_keeps_the_wait(self) -> None:
        holder = self.request(4, "suite")
        late = self.request(1, "probe")
        self.assertFalse(late.poll())
        self.clock.sleep(30)
        holder.release()
        self.assertEqual(self.files(), [late.id])
        self.assertTrue(late.poll())
        self.assertEqual((late.record["state"], late.record["admittedBy"]), ("running", "capacity"))
        admitted = [entry for entry in self.history() if entry["event"] == "admitted" and entry["claim"] == late.id]
        self.assertEqual([entry["waitedSeconds"] for entry in admitted], [30.0])
        self.assertShapes()

    def test_now_runs_at_once_and_records_the_override(self) -> None:
        self.request(4, "suite")
        forced = self.request(1, "probe", now_why="x")
        self.assertTrue(forced.admitted)
        self.assertEqual((forced.record["admittedBy"], forced.record["why"]), ("now", "x"))
        overrides = [entry for entry in self.history() if entry["event"] == "override"]
        self.assertEqual([(entry["claim"], entry["admittedBy"], entry["why"]) for entry in overrides],
                         [(forced.id, "now", "x")])
        self.assertEqual(self.ledger.reading()["used"], 5)
        self.assertShapes()
        with contextlib.redirect_stderr(io.StringIO()) as said, self.assertRaises(SystemExit) as stopped:
            machine.main(["run", "--now", "--", "true"], ledger=self.ledger)
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("--now needs --why", said.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as said, self.assertRaises(SystemExit) as stopped:
            # Capped, so a mutant that accepts --why alone ends this row instead of waiting on the full machine.
            machine.main(["run", "--why", "w", "--wait-seconds", "1", "--", "true"], ledger=self.ledger)
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("--why goes with --now", said.getvalue())

    def test_a_claim_whose_lock_nobody_holds_is_removed_whatever_its_pid(self) -> None:
        record = self._written(os.getpid())
        reading = self.ledger.reading()
        self.assertEqual((reading["used"], reading["running"]), (0, []))
        self.assertFalse(self.ledger.claim_path(record["id"]).exists())

    def test_a_claim_whose_lock_is_held_is_kept_whatever_its_pid(self) -> None:
        record = self._written(2 ** 22 + 12345)
        self.hold(self.ledger.claim_path(record["id"]))
        reading = self.ledger.reading()
        self.assertEqual(reading["used"], 3)
        self.assertEqual([claim["id"] for claim in reading["running"]], [record["id"]])
        self.assertTrue(self.ledger.claim_path(record["id"]).exists())

    def test_a_nested_claim_ignores_an_ancestor_whose_lock_nobody_holds(self) -> None:
        record = self._written(os.getpid())
        with mock.patch.dict(os.environ, {machine.CLAIM_VARIABLE: record["id"]}):
            own = self.request(4, "own")
        self.assertIsNotNone(own.record)
        self.assertEqual(own.record["admittedBy"], "capacity")
        self.assertNotIn("running under claim", self.err.getvalue())
        self.assertFalse(self.ledger.claim_path(record["id"]).exists())

    def _written(self, pid: int) -> dict:
        (self.directory / "claims").mkdir(parents=True, exist_ok=True)
        record = {"schemaVersion": "machine-claim/1", "id": "a" * 32, "state": "running", "slots": 3,
                  "exclusive": False, "kind": "run", "subject": "other", "label": "other", "repository": None,
                  "delivery": None, "story": None, "pid": pid, "enqueuedAt": "2026-11-20T00:00:00.000000Z",
                  "startedAt": "2026-11-20T00:00:00.000000Z", "expectedEnd": None,
                  "estimate": {"seconds": None, "slots": 3, "from": "none"}, "admittedBy": "capacity", "why": None}
        self.ledger.claim_path(record["id"]).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return record

    def test_a_claim_larger_than_capacity_runs_alone(self) -> None:
        large = self.request(6, "large")
        self.assertEqual(large.record["admittedBy"], "alone")
        small = self.request(1, "small")
        self.assertFalse(small.poll())
        large.release()
        self.assertTrue(small.poll())
        self.assertEqual(small.record["admittedBy"], "capacity")

    def test_an_earlier_waiter_goes_before_a_large_claim_arriving_at_a_release(self) -> None:
        first = self.request(1, "first", exclusive=True)
        waiter = self.request(1, "waiter")
        self.assertFalse(waiter.poll())
        first.release()
        large = self.request(6, "large")
        self.assertFalse(large.admitted)
        self.assertFalse(large.poll())
        self.assertTrue(waiter.poll())
        self.assertEqual(waiter.record["admittedBy"], "capacity")
        self.assertFalse(large.poll())
        waiter.release()
        self.assertTrue(large.poll())
        self.assertEqual(large.record["admittedBy"], "alone")

    def test_an_exclusive_claim_runs_when_nothing_else_does_and_holds_everything_else_back(self) -> None:
        small = self.request(1, "small")
        exclusive = self.request(1, "quiet", exclusive=True)
        self.assertFalse(exclusive.poll())
        later = self.request(1, "later")
        self.assertFalse(later.poll())
        small.release()
        self.assertTrue(exclusive.poll())
        self.assertEqual(exclusive.record["admittedBy"], "alone")
        self.assertFalse(later.poll())
        self.assertIn("0 slots are free now", self.err.getvalue().splitlines()[-1])
        exclusive.release()
        self.assertTrue(later.poll())

    def test_a_claim_made_while_holding_a_slot_is_admitted_at_once_and_counts(self) -> None:
        self.request(4, "suite")
        with machine.holding_slot():
            self.assertTrue(machine.slot_held())
            turn = self.request(2, "turn")
            self.assertEqual(turn.record["admittedBy"], "slot")
            turn.resize(3)
            self.assertEqual(self.ledger.reading()["used"], 7)
        self.assertFalse(machine.slot_held())
        turn.resize(1)
        self.assertEqual(self.ledger.reading()["used"], 5)
        with self.assertRaisesRegex(ValueError, "merge slot"):
            turn.resize(2)
        outside = self.request(1, "outside")
        self.assertFalse(outside.poll())

    def test_a_capped_wait_gives_up_with_75_runs_nothing_and_names_the_holders(self) -> None:
        holder = self.request(4, "suite", delivery="V26-1")
        marker = self.root / "marker"
        began = self.clock()
        with mock.patch.object(machine, "repository_identity", return_value=None):
            code = machine.main(["run", "--wait-seconds", "5", "--label", "late", "--", sys.executable, "-c",
                                 f"open({str(marker)!r}, 'w')"], ledger=self.ledger)
        self.assertEqual(code, 75)
        self.assertFalse(marker.exists())
        self.assertGreaterEqual(self.clock() - began, 5)
        self.assertLess(self.clock() - began, 5.5)
        self.assertIn("gave up after", self.err.getvalue())
        self.assertIn("held by suite (delivery V26-1, 4 slots", self.err.getvalue())
        gave_up = [entry for entry in self.history() if entry["event"] == "gave-up"]
        self.assertEqual([(entry["label"], entry["holders"]) for entry in gave_up], [("late", [holder.id])])
        self.assertEqual(self.files(), [holder.id])
        self.assertShapes()

    def test_a_claim_released_between_listing_and_reading_is_skipped(self) -> None:
        self.request(4, "suite")
        listed = self.ledger.names

        def with_a_vanished_claim() -> list[str]:
            return sorted([*listed(), "b" * 32 + ".json"])

        with mock.patch.object(self.ledger, "names", with_a_vanished_claim):
            late = self.request(1, "probe")
            self.assertFalse(late.poll())
            reading = self.ledger.reading()
        self.assertEqual(reading["used"], 4)
        self.assertNotIn("b" * 32, [claim["id"] for claim in reading["running"]])
        self.assertNotIn("unavailable", self.err.getvalue())

    def test_a_held_ledger_lock_delays_a_claim_without_making_it_unavailable(self) -> None:
        self.request(4, "suite")
        descriptor = os.open(self.directory / "ledger.lock", os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        releaser = threading.Timer(0.3, fcntl.flock, (descriptor, fcntl.LOCK_UN))
        releaser.start()
        self.addCleanup(releaser.cancel)
        # Each try at the lock is shorter than the hold, so only a retried timeout gets the claim in.
        with mock.patch.object(machine, "LEDGER_TRY_SECONDS", 0.1):
            late = self.request(1, "probe")
        self.assertFalse(late.poll())
        self.assertEqual(late.record["state"], "waiting")
        self.assertNotIn("unavailable", self.err.getvalue())

    def test_a_malformed_claim_whose_lock_is_held_counts_with_the_slots_it_last_said(self) -> None:
        (self.directory / "claims").mkdir(parents=True, exist_ok=True)
        broken = self.directory / "claims" / ("c" * 32 + ".json")
        broken.write_text('{"slots": 4, "state": "runn', encoding="utf-8")
        self.hold(broken)
        late = self.request(1, "probe")
        self.assertFalse(late.poll())
        reading = self.ledger.reading()
        self.assertNotIn("unavailable", reading)
        self.assertEqual(reading["used"], 4)
        self.assertNotIn("unavailable", self.err.getvalue())

    def test_a_held_claim_that_states_no_slots_counts_one_and_cannot_be_admitted(self) -> None:
        (self.directory / "claims").mkdir(parents=True, exist_ok=True)
        broken = self.directory / "claims" / ("d" * 32 + ".json")
        broken.write_text("{}", encoding="utf-8")
        self.hold(broken)
        self.assertEqual(self.ledger.reading()["used"], 1)
        self.assertEqual(machine.main(["admit", "d" * 32, "--why", "y"], ledger=self.ledger), 1)
        self.assertIn("no live claim", self.err.getvalue())

    def test_a_capped_wait_admitted_at_its_deadline_keeps_the_admission(self) -> None:
        self.request(4, "suite")
        late = self.request(1, "probe")
        self.assertFalse(late.poll())
        self.ledger.admit(late.id, "y")
        self.clock.sleep(10)
        late._give_up()
        self.assertTrue(late.admitted)
        self.assertEqual(late.record["admittedBy"], "admit")
        self.assertEqual([entry for entry in self.history() if entry["event"] == "gave-up"], [])
        self.assertIn(late.id, self.files())

    def test_a_waiter_wakes_when_a_claim_is_released_before_its_poll(self) -> None:
        holder = self.request(4, "suite")
        late = self.request(1, "probe")
        self.assertFalse(late.poll())

        def released_soon(seconds: float) -> None:
            self.clock.sleep(seconds)
            holder.release()

        self.ledger.sleep = released_soon
        began = self.clock()
        self.ledger.pause(late, None)
        self.assertLessEqual(self.clock() - began, 0.2)
        self.assertTrue(late.poll())

    def test_history_keeps_its_last_thousand_entries(self) -> None:
        with self.ledger.locked():
            for index in range(1005):
                self.ledger.history({"event": "capacity", "capacity": 4, "why": f"n{index}"})
        entries = self.history()
        self.assertEqual(len(entries), 1000)
        self.assertEqual((entries[0]["why"], entries[-1]["why"]), ("n5", "n1004"))
        self.assertShapes()

    def test_capacity_is_set_and_reset_with_a_reason_and_unset_is_the_core_count(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(machine.main(["capacity", "--slots", "6", "--why", "bigger"], ledger=self.ledger), 0)
            self.assertEqual(self.ledger.capacity(), 6)
            self.assertEqual(machine.main(["capacity", "--reset", "--why", "back"], ledger=self.ledger), 0)
        self.assertEqual(self.ledger.capacity(), os.cpu_count())
        self.assertEqual([(entry["capacity"], entry["why"]) for entry in self.history()],
                         [(6, "bigger"), (None, "back")])
        self.assertShapes()

    def test_status_prints_capacity_and_pressure_beside_the_claims(self) -> None:
        self.request(4, "suite", delivery="V26-1")
        waiter = self.request(1, "probe")
        self.assertFalse(waiter.poll())
        with contextlib.redirect_stdout(io.StringIO()) as shown:
            self.assertEqual(machine.main(["status"], ledger=self.ledger), 0)
        text = shown.getvalue()
        self.assertIn("capacity: 4 slots (set 2026-11-20T00:00:00Z: the test's own capacity); 4 in use", text)
        self.assertIn("suite (delivery V26-1, 4 slots", text)
        self.assertIn("waiting 1:", text)
        self.assertIn("pressure: load5 ", text)
        shown = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        with contextlib.redirect_stdout(shown):
            self.assertEqual(machine.main(["status", "--json"], ledger=self.ledger), 0)
        reading = json.loads(shown.buffer.getvalue())
        self.assertEqual((reading["capacity"], reading["used"], len(reading["waiting"])), (4, 4, 1))
        self.assertEqual(set(reading["pressure"]), {"cores", "load5", "diskFreeBytes", "swapUsedBytes"})

    def test_no_admission_reads_the_machine_load(self) -> None:
        with mock.patch.object(machine, "machine_reading", side_effect=AssertionError("load read")), \
                mock.patch.object(os, "getloadavg", side_effect=AssertionError("load read")):
            holder = self.request(4, "suite")
            late = self.request(1, "probe")
            self.assertFalse(late.poll())
            holder.release()
            self.assertTrue(late.poll())


@unittest.skipIf(os.name == "nt", "Windows is not validated")
class EstimateTests(LedgerCase):
    def test_the_estimate_takes_medians_over_the_last_five_passed_runs(self) -> None:
        self.set_capacity(8)
        for seconds, cpu in ((100, 100), (200, 200), (300, 300), (900, 1800), (1000, 4000), (5000, 20000)):
            self.ledger.record_cost("repo", "run", "probe", run(seconds, cpu))
        self.ledger.record_cost("repo", "run", "probe", run(10000, 80000, "failed"))
        entry = self.ledger.costs()["entries"][0]
        self.assertEqual([item["seconds"] for item in entry["passed"]], [200, 300, 900, 1000, 5000])
        self.assertEqual([(item["seconds"], item["result"]) for item in entry["failed"]], [(10000, "failed")])
        self.assertEqual(self.ledger.estimate("repo", "run", "probe"), {"seconds": 900, "slots": 2, "from": "measured"})
        claim = self.request(1, "probe", repository="repo")
        self.assertEqual((claim.record["slots"], claim.record["estimate"]["from"]), (2, "measured"))
        self.assertShapes()

    def test_costs_are_kept_apart_by_repository(self) -> None:
        for _ in range(5):
            self.ledger.record_cost("a", "check", "turn", run(100, 400))
        self.assertEqual(self.ledger.estimate("a", "check", "turn")["from"], "measured")
        self.assertEqual(self.ledger.estimate("b", "check", "turn"), {"seconds": None, "slots": 1, "from": "none"})

    def test_measured_slots_never_fall_below_one(self) -> None:
        self.assertEqual(machine.estimate_from([{"seconds": 10, "cpuSeconds": 0.0}], 3, 4)["slots"], 1)

    def test_unlabelled_runs_with_one_interpreter_keep_separate_costs(self) -> None:
        first = [sys.executable, "-c", "pass  # secret-one"]
        second = [sys.executable, "-c", "x = 1  # secret-two"]
        with mock.patch.object(machine, "repository_identity", return_value=None):
            for argv in (first, second):
                self.assertEqual(machine.main(["run", "--", *argv], ledger=self.ledger), 0)
            self.assertEqual(machine.main(["run", "--subject", "s", "--label", "l", "--", sys.executable, "-c",
                                           "pass"], ledger=self.ledger), 0)
        subjects = [entry["subject"] for entry in self.ledger.costs()["entries"]]
        unlabelled = subjects[:2]
        self.assertEqual(len(set(unlabelled)), 2)
        self.assertTrue(all(subject.startswith(Path(sys.executable).name + " ") for subject in unlabelled))
        self.assertEqual(subjects[2], "s")
        ledger_text = "".join((self.directory / name).read_text(encoding="utf-8")
                              for name in ("costs.json", "history.jsonl"))
        self.assertNotIn("secret-", ledger_text, "no argument reaches the shared ledger")
        self.assertNotIn('"l"', (self.directory / "costs.json").read_text(encoding="utf-8"))
        self.assertShapes()

    def test_measured_slots_are_clamped_to_capacity(self) -> None:
        self.set_capacity(2)
        for _ in range(5):
            self.ledger.record_cost(None, "check", "suite", run(100, 400))
        self.assertEqual(self.ledger.estimate(None, "check", "suite")["slots"], 2)

    def test_runs_without_cpu_figures_measure_seconds_and_keep_the_callers_slots(self) -> None:
        for _ in range(5):
            self.ledger.record_cost(None, "run", "probe", run(120, None))
        self.assertEqual(self.ledger.estimate(None, "run", "probe", 3), {"seconds": 120, "slots": 3, "from": "measured"})
        self.assertShapes()

    def test_failed_runs_never_move_the_estimate_or_push_out_passed_runs(self) -> None:
        for _ in range(5):
            self.ledger.record_cost(None, "run", "probe", run(600, 600))
        for _ in range(6):
            self.ledger.record_cost(None, "run", "probe", run(10000, 80000, "timedOut"))
        entry = self.ledger.costs()["entries"][0]
        self.assertEqual((len(entry["passed"]), len(entry["failed"])), (5, 5))
        self.assertEqual(self.ledger.estimate(None, "run", "probe"), {"seconds": 600, "slots": 1, "from": "measured"})

    def test_no_cost_record_takes_the_callers_slots(self) -> None:
        claim = self.request(3, "fresh")
        self.assertEqual(claim.record["slots"], 3)
        self.assertEqual(claim.record["estimate"], {"seconds": None, "slots": 3, "from": "none"})
        self.assertIsNone(claim.record["expectedEnd"])


class DirectoryTests(unittest.TestCase):
    def test_every_test_process_has_its_own_machine_directory(self) -> None:
        own = tests.MACHINE_DIRECTORY
        self.assertEqual(Path(own).parent, Path(tempfile.gettempdir()))
        self.assertNotEqual(Path(own), machine.machine_directory({"HOME": str(Path.home())}))

    def test_the_resolver_takes_the_variable_then_xdg_state_then_home(self) -> None:
        both = {"AGENT_SKILLS_MACHINE_DIR": "/m", "XDG_STATE_HOME": "/x", "HOME": "/h"}
        self.assertEqual(machine.machine_directory(both), Path("/m"))
        self.assertEqual(machine.machine_directory({**both, "AGENT_SKILLS_MACHINE_DIR": ""}),
                         Path("/x/agent-skills/machine"))
        self.assertEqual(machine.machine_directory({"HOME": "/h"}), Path("/h/.local/state/agent-skills/machine"))
        self.assertEqual(machine.machine_directory({"HOME": "/h", "XDG_STATE_HOME": ""}),
                         Path("/h/.local/state/agent-skills/machine"))


@unittest.skipIf(os.name == "nt", "Windows is not validated")
class ProcessTests(LedgerCase):
    """Rows that need real processes: each waits on one exiting, so the class runs quietly."""

    run_quietly = True

    def command(self, *arguments: str, **options: object) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-B", str(MACHINE), *arguments], capture_output=True, text=True,
                              timeout=60, cwd=options.pop("cwd", self.root), **options)

    def test_a_killed_holder_is_gone_on_the_next_read_and_its_waiter_is_admitted(self) -> None:
        holder = subprocess.Popen(
            [sys.executable, "-B", "-c",
             f"import sys, time; sys.path.insert(0, {str(SCRIPTS)!r}); import machine; "
             "claim = machine.Ledger().acquire('run', 'held', slots=4); print(claim.id, flush=True); time.sleep(60)"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        held = holder.stdout.readline().strip()
        holder.stdout.close()
        late = self.request(1, "probe")
        self.assertFalse(late.poll())
        holder.kill()
        holder.wait()
        self.assertTrue(late.poll())
        self.assertNotIn(held, self.files())

    def test_admit_from_another_process_admits_a_waiter_and_records_why(self) -> None:
        self.request(4, "suite")
        late = self.request(1, "probe")
        self.assertFalse(late.poll())
        admitted = self.command("admit", late.id, "--why", "y")
        self.assertEqual(admitted.returncode, 0, admitted.stderr)
        self.assertTrue(late.poll())
        self.assertEqual((late.record["admittedBy"], late.record["why"]), ("admit", "y"))
        self.assertEqual([(entry["claim"], entry["why"]) for entry in self.history() if entry["event"] == "override"],
                         [(late.id, "y")])
        again = self.command("admit", late.id, "--why", "y")
        self.assertEqual(again.returncode, 1)
        self.assertIn("already running", again.stderr)
        self.assertShapes()

    def test_the_runner_measures_a_grandchilds_cpu_and_the_next_claim_is_measured(self) -> None:
        mine = self.root / "grandchild-cpu"
        burn = "import time\nstarted = time.process_time()\nwhile time.process_time() - started < 0.3:\n    pass\n"
        grandchild = burn + f"open({str(mine)!r}, 'w').write(str(time.process_time()))\n"
        child = burn + f"import subprocess, sys\nsubprocess.run([sys.executable, '-c', {grandchild!r}], check=True)\n"
        done = self.command("run", "--kind", "run", "--label", "probe", "--", sys.executable, "-c", child)
        self.assertEqual(done.returncode, 0, done.stderr)
        passed = self.ledger.costs()["entries"][0]["passed"]
        self.assertEqual(len(passed), 1)
        self.assertGreaterEqual(passed[0]["cpuSeconds"], float(mine.read_text()))
        self.assertGreaterEqual(passed[0]["cpuSeconds"], 0.6)
        again = self.request(1, "probe")
        self.assertEqual(again.record["estimate"]["from"], "measured")
        self.assertShapes()

    def test_a_nested_claim_runs_under_its_ancestor_and_a_gone_ancestor_is_ignored(self) -> None:
        marker = self.root / "marker"
        inner = (f"import os; open({str(marker)!r}, 'w').write(str(len(os.listdir({str(self.directory / 'claims')!r}))))")
        done = self.command("run", "--slots", "4", "--label", "outer", "--", sys.executable, "-B", str(MACHINE), "run",
                            "--slots", "4", "--label", "inner", "--wait-seconds", "10", "--", sys.executable, "-c", inner)
        self.assertEqual(done.returncode, 0, done.stderr)
        found = re.search(r"running under claim ([0-9a-f]{32}) \(outer\)", done.stderr)
        self.assertIsNotNone(found, done.stderr)
        self.assertEqual(marker.read_text(), "1")
        self.assertEqual(self.files(), [])
        inner_cost = [entry for entry in self.ledger.costs()["entries"] if entry["subject"] == "inner"]
        self.assertEqual(len(inner_cost[0]["passed"]), 1)
        with mock.patch.dict(os.environ, {machine.CLAIM_VARIABLE: found.group(1)}):
            fresh = self.request(4, "fresh")
        self.assertIsNotNone(fresh.record)
        self.assertEqual(fresh.record["admittedBy"], "capacity")
        self.assertNotIn("running under claim", self.err.getvalue())

    def test_every_ending_releases_the_claim_and_admits_the_waiter(self) -> None:
        def ending(name: str, body) -> None:
            with self.subTest(ending=name):
                self.assertEqual(self.ledger.reading()["used"], 0)
                waiter = None
                try:
                    # Capped, so a failed row can't leave the next one waiting forever on the injected clock.
                    with contextlib.suppress(KeyboardInterrupt), \
                            self.ledger.claim("run", name, slots=4, wait_seconds=10) as holder:
                        self.clock.sleep(1)
                        waiter = self.ledger.request("run", "waiter", slots=1)
                        self.assertFalse(waiter.poll())
                        body(holder)
                    self.assertNotIn(holder.id, self.files())
                    self.assertTrue(waiter.poll())
                finally:
                    if waiter is not None:
                        waiter.release()
                self.assertEqual(self.ledger.reading()["used"], 0)

        def failing(holder) -> None:
            measured = machine.measured_run([sys.executable, "-c", "raise SystemExit(1)"])
            self.assertEqual((measured.returncode, measured.result), (1, "failed"))
            holder.record_cost(measured)

        def timed_out(holder) -> None:
            measured = machine.measured_run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.2)
            self.assertEqual(measured.result, "timedOut")
            holder.record_cost(measured)

        def interrupted(holder) -> None:
            child = []
            popen = subprocess.Popen

            def remembered(*arguments, **options):
                child.append(popen(*arguments, **options))
                return child[-1]

            with mock.patch.object(machine.subprocess, "Popen", remembered), \
                    mock.patch.object(machine.os, "wait4", side_effect=KeyboardInterrupt):
                try:
                    machine.measured_run([sys.executable, "-c", "import time; time.sleep(30)"])
                finally:
                    self.assertIsNotNone(child[0].poll(), "the interrupted runner stopped its child")

        ending("failing", failing)
        ending("timed-out", timed_out)
        ending("interrupted", interrupted)
        failed = [entry for entry in self.ledger.costs()["entries"] if entry["subject"] in ("failing", "timed-out")]
        self.assertEqual(sorted(item["result"] for entry in failed for item in entry["failed"]), ["failed", "timedOut"])

    def test_run_exits_with_its_childs_exit_code(self) -> None:
        done = self.command("run", "--", sys.executable, "-c", "raise SystemExit(3)")
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertEqual(self.files(), [])

    def test_a_terminated_run_stops_its_argv_and_releases_its_claim(self) -> None:
        recorded = self.root / "child-pid"
        runner = subprocess.Popen(
            [sys.executable, "-B", str(MACHINE), "run", "--label", "stopped", "--", sys.executable, "-c",
             f"import os, time; open({str(recorded)!r}, 'w').write(str(os.getpid())); time.sleep(60)"],
            cwd=self.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(runner.wait)
        self.addCleanup(runner.kill)
        deadline = time.monotonic() + 30
        while not (recorded.exists() and recorded.read_text()) and time.monotonic() < deadline:
            time.sleep(0.02)
        child = int(recorded.read_text())
        self.assertEqual(len(self.files()), 1)
        runner.terminate()
        self.assertEqual(runner.wait(30), 128 + signal.SIGTERM)
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            survived = False
        else:
            # Checked at once, so the pid this test recorded can't yet name another process.
            survived = True
            os.kill(child, signal.SIGKILL)
        self.assertFalse(survived, "the terminated runner stopped its argv")
        self.assertEqual(self.files(), [])

    def test_a_timed_out_run_that_ignores_sigterm_is_killed(self) -> None:
        ignoring = ("import signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    "sys.stdout.write('ready'); sys.stdout.flush(); time.sleep(60)")
        with mock.patch.object(machine, "STOP_GRACE_SECONDS", 0.2):
            measured = machine.measured_run([sys.executable, "-c", ignoring], timeout=1.0,
                                            stdout=subprocess.DEVNULL)
        self.assertEqual((measured.result, measured.returncode), ("timedOut", -signal.SIGKILL))

    def test_an_unusable_machine_directory_runs_argv_unclaimed_and_says_so(self) -> None:
        blocked = self.root / "not-a-directory"
        blocked.write_text("a file where the machine directory should be\n", encoding="utf-8")
        marker = self.root / "marker"
        done = self.command("run", "--", sys.executable, "-c", f"open({str(marker)!r}, 'w')",
                            env={**os.environ, machine.DIRECTORY_VARIABLE: str(blocked)})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(marker.exists())
        self.assertIn("machine ledger unavailable: ", done.stderr)
        self.assertIn("; running unclaimed", done.stderr)

    def test_importing_the_tests_package_replaces_an_inherited_machine_directory(self) -> None:
        decoy = str(self.root / "decoy")
        seen = subprocess.run([sys.executable, "-B", "-c", "import os, tests; print(os.environ['AGENT_SKILLS_MACHINE_DIR'])"],
                              capture_output=True, text=True, cwd=ROOT, timeout=60,
                              env={**os.environ, machine.DIRECTORY_VARIABLE: decoy})
        self.assertEqual(seen.returncode, 0, seen.stderr)
        self.assertNotEqual(seen.stdout.strip(), decoy)
        self.assertEqual(Path(seen.stdout.strip()).parent, Path(tempfile.gettempdir()))

    def test_a_child_started_as_the_fixtures_start_theirs_resolves_the_tests_directory(self) -> None:
        seen = subprocess.run([sys.executable, "-B", "-c", f"import sys; sys.path.insert(0, {str(SCRIPTS)!r}); "
                               "import machine; print(machine.machine_directory())"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(seen.returncode, 0, seen.stderr)
        self.assertEqual(seen.stdout.strip(), str(self.directory))


if __name__ == "__main__":
    unittest.main()
