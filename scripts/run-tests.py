#!/usr/bin/env python3
"""Run the corpus test suite across worker processes, with the same verdict as ``unittest discover``.

Tests are discovered as ``unittest discover`` finds them, then split into
units of at most ``--chunk`` tests of one class. A class with its own
``setUpClass``/``tearDownClass`` is one unit, and a module with its own
``setUpModule``/``tearDownModule`` or ``load_tests``, or that marks itself
``RUN_SERIALLY = True``, is one unit, loaded as discover loads it, so no
fixture runs more often than under discover and every test is the one
discover built. A module that skips itself as it is imported counts as one
skipped test, as under discover. A test class imported into a module that
has module fixtures runs again inside that module's unit, wherever the class
is defined. A
class that sets ``run_serially = True``, or a module marked ``RUN_SERIALLY``,
runs alone: no other worker is running while it does. Every unit runs in its
own process, so a crash or a hang ends that unit only; the run then fails,
naming it. With ``--timings-from``, the slowest units start first.

A unit that runs longer than ``--unit-timeout`` seconds is stopped with its
whole process group, as an interrupt stops it, and reported as timed out with
its tests named; the rest of the run carries on and the run fails. The limit
defaults to five times the slowest unit an earlier ``--timings-from`` file
records, at least 300 seconds, or to 1800 seconds without one or when it
records none of the current tests; 0 sets none. Only the unit's own process
group is stopped: a child a test moved into a session of its own is not.

    .venv/bin/python scripts/run-tests.py                 # cores / 2 workers
    .venv/bin/python scripts/run-tests.py --workers 1     # one at a time
    .venv/bin/python scripts/run-tests.py --timings timings.json
    .venv/bin/python scripts/run-tests.py --timings-from timings.json

The exit code is 0 when every test passed, 1 when any failed, errored, passed
unexpectedly, timed out or its unit crashed, 2 on a usage error, 5 when no test ran, as
under discover, and 130 when the run was interrupted, after every worker and
whatever it started is stopped. ``--timings`` writes the total,
per-unit, per-module and per-test wall times. Standard library only.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SERIAL_MODULE = "RUN_SERIALLY"
SERIAL_CLASS = "run_serially"
# How long a stopped worker gets to exit before it is killed.
STOP_GRACE_SECONDS = 3
# Tests per unit, when a class may be split.
CHUNK = 4
# A unit's limit: this many times the slowest unit an earlier run recorded, never less than the floor.
UNIT_TIMEOUT_FACTOR = 5
UNIT_TIMEOUT_FLOOR = 300
# Without earlier timings.
UNIT_TIMEOUT_DEFAULT = 1800


def _flatten(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def _own(cls: type, name: str) -> bool:
    return any(name in vars(klass) for klass in cls.__mro__ if klass not in (unittest.TestCase, object))


def discover_units(start: str, top: str, pattern: str, chunk: int = CHUNK) -> list[dict]:
    """The units to run, in discovery order: ``{name, names, serial, tests}``."""
    sys.path.insert(0, os.path.abspath(top))
    suite = unittest.defaultTestLoader.discover(start, pattern=pattern, top_level_dir=top)
    groups: dict[str, dict] = {}
    for test in _flatten(suite):
        if type(test).__name__ in ("_FailedTest", "ModuleSkipped"):
            # A module that failed to import, or skipped itself as it was: its worker imports it again.
            name = test.id().rsplit(f"{type(test).__name__}.", 1)[-1]
            groups.setdefault(name, {"name": name, "whole": True, "serial": False, "ids": []})["ids"].append(name)
            continue
        cls = type(test)
        module = sys.modules.get(cls.__module__)
        module_whole = module is not None and (hasattr(module, "setUpModule") or hasattr(module, "tearDownModule")
                                               or hasattr(module, "load_tests")
                                               or getattr(module, SERIAL_MODULE, False))
        name = cls.__module__ if module_whole else f"{cls.__module__}.{cls.__qualname__}"
        group = groups.setdefault(name, {"name": name, "whole": module_whole or _own(cls, "setUpClass")
                                         or _own(cls, "tearDownClass"), "serial": False, "ids": []})
        group["serial"] = group["serial"] or bool(getattr(module, SERIAL_MODULE, False)
                                                  or getattr(cls, SERIAL_CLASS, False))
        group["ids"].append(test.id())
    units = []
    for group in groups.values():
        if group["whole"] or len(group["ids"]) <= chunk:
            names = [group["name"]] if group["whole"] else group["ids"]
            units.append({"name": group["name"], "names": names, "serial": group["serial"],
                          "tests": len(group["ids"]), "ids": group["ids"], "pattern": pattern})
            continue
        parts = [group["ids"][index:index + chunk] for index in range(0, len(group["ids"]), chunk)]
        for number, part in enumerate(parts, start=1):
            units.append({"name": f"{group['name']}[{number}/{len(parts)}]", "names": part,
                          "serial": group["serial"], "tests": len(part), "ids": part, "pattern": pattern})
    return units


class _TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings: dict[str, float] = {}
        self._started: dict[str, float] = {}

    def startTest(self, test):
        self._started[test.id()] = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        started = self._started.pop(test.id(), None)
        if started is not None:
            self.timings[test.id()] = time.perf_counter() - started


def _load(name: str, pattern: str) -> unittest.TestSuite:
    """A test id or class by name; a whole module as discover loads it, through its own load_tests."""
    loader = unittest.defaultTestLoader
    try:
        module = importlib.import_module(name)
    except ImportError:
        return loader.loadTestsFromName(name)
    return loader.loadTestsFromModule(module, pattern=pattern)


def run_unit(spec_path: Path, result_path: Path, top: str) -> int:
    """Worker mode: run one unit's tests, named in ``spec_path``, and write its result as JSON."""
    sys.path.insert(0, os.path.abspath(top))
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    runner = unittest.TextTestRunner(resultclass=_TimedResult, stream=sys.stderr, verbosity=0)
    try:
        suite = unittest.TestSuite(_load(name, spec["pattern"]) for name in spec["names"])
    except unittest.SkipTest as skipped:
        # A module that skips itself as it is imported: one skipped test, as under discover.
        Path(result_path).write_text(json.dumps({
            "testsRun": 1, "failures": [], "errors": [], "skipped": 1, "expectedFailures": 0,
            "unexpectedSuccesses": [], "timings": {}, "note": str(skipped),
        }), encoding="utf-8")
        return 0
    result = runner.run(suite)

    def described(pairs) -> list[dict]:
        return [{"test": test.id(), "detail": detail} for test, detail in pairs]

    Path(result_path).write_text(json.dumps({
        "testsRun": result.testsRun,
        "failures": described(result.failures),
        "errors": described(result.errors),
        "skipped": len(result.skipped),
        "expectedFailures": len(result.expectedFailures),
        "unexpectedSuccesses": [test.id() for test in result.unexpectedSuccesses],
        "timings": result.timings,
    }), encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


def _exited(process: subprocess.Popen) -> bool:
    """Whether a worker has exited, without reaping it: its pid keeps its group's id reserved."""
    if hasattr(os, "waitid"):
        try:
            return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:
            return True
    return False


def _stop_all(processes: list[subprocess.Popen], *, ignore_interrupts: bool = True) -> None:
    """Stop every worker and everything each started; each leads its own process group.

    By default further interrupts are ignored until it is done, as when an
    interrupt is what is being handled. Every group is asked to stop, given
    one shared grace, then killed whatever its leader did, before any worker
    is reaped, so a child left in a group is killed too. A child a test moved
    into a session of its own is outside the group and is not stopped.
    """
    ignored = {}
    for signum in (signal.SIGINT, getattr(signal, "SIGTERM", None)) if ignore_interrupts else ():
        if signum is not None:
            try:
                ignored[signum] = signal.signal(signum, signal.SIG_IGN)
            except ValueError:
                pass  # not the main thread
    try:
        for process in processes:
            try:
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.monotonic() + STOP_GRACE_SECONDS
        while time.monotonic() < deadline and not all(_exited(process) for process in processes):
            time.sleep(0.05)
        for process in processes:
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
        for process in processes:
            try:
                process.wait(timeout=STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
    finally:
        for signum, handler in ignored.items():
            signal.signal(signum, handler)


def run_suite(units: list[dict], *, workers: int, top: str, unit_timeout: float | None = None,
              out=sys.stdout) -> dict:
    """Run every unit, at most ``workers`` at once, serial units alone; return the combined result.

    A unit still running after ``unit_timeout`` seconds is stopped, with
    everything it started, and recorded as timed out.
    """
    scratch = Path(tempfile.mkdtemp(prefix="run-tests-"))
    parallel = [unit for unit in units if not unit["serial"]]
    serial = [unit for unit in units if unit["serial"]]
    pending = [*parallel]
    running: dict[str, tuple[subprocess.Popen, Path, Path, float, dict]] = {}
    combined = {"testsRun": 0, "failures": [], "errors": [], "skipped": 0, "expectedFailures": 0,
                "unexpectedSuccesses": [], "crashed": [], "timedOut": [], "timings": {}, "units": {}}

    def start(unit: dict) -> None:
        index = len(combined["units"]) + len(running)
        result_path, log_path = scratch / f"{index}.json", scratch / f"{index}.log"
        names_path = scratch / f"{index}.names.json"
        names_path.write_text(json.dumps({"names": unit["names"], "pattern": unit["pattern"]}), encoding="utf-8")
        # An interrupt must not land between starting a worker and recording it: it would be left running.
        blocked = hasattr(signal, "pthread_sigmask")
        if blocked:
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            with open(log_path, "wb") as log:
                process = subprocess.Popen(
                    [sys.executable, "-B", os.path.abspath(__file__), "--unit", str(names_path),
                     "--result", str(result_path), "--top-level-dir", top],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=top,
                    **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                       else {"start_new_session": True}))
            running[unit["name"]] = (process, result_path, log_path, time.perf_counter(), unit)
        finally:
            if blocked:
                signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT, signal.SIGTERM})

    def finish(name: str) -> None:
        process, result_path, log_path, started, _unit = running.pop(name)
        combined["units"][name] = round(time.perf_counter() - started, 3)
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            combined["crashed"].append({"unit": name, "exit": process.returncode, "log": tail})
            print(f"CRASHED {name} (exit {process.returncode})", file=out, flush=True)
            return
        for key in ("testsRun", "skipped", "expectedFailures"):
            combined[key] += result[key]
        for key in ("failures", "errors", "unexpectedSuccesses"):
            combined[key] += result[key]
        combined["timings"].update(result["timings"])
        bad = len(result["failures"]) + len(result["errors"]) + len(result["unexpectedSuccesses"])
        print(f"{'FAIL' if bad else 'ok  '} {name} ({result['testsRun']} tests, {combined['units'][name]:.1f}s)",
              file=out, flush=True)

    def expire(names: list[str]) -> None:
        # Out of the running set first: an interrupt then never signals a worker that is already reaped.
        expired = {name: running.pop(name) for name in names}
        # Stopped together, as an interrupt stops them: asked, one shared grace, then killed with their groups.
        # An interrupt meanwhile is held, not lost, and is handled as soon as they are stopped. Where
        # signals cannot be held it is ignored instead, so these workers are never left unstopped.
        held = hasattr(signal, "pthread_sigmask")
        if held:
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            _stop_all([process for process, *_rest in expired.values()], ignore_interrupts=not held)
        finally:
            if held:
                signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT, signal.SIGTERM})
        for name, (_process, _result, log_path, started, unit) in expired.items():
            combined["units"][name] = round(time.perf_counter() - started, 3)
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            combined["timedOut"].append({"unit": name, "seconds": unit_timeout, "tests": unit["ids"], "log": tail})
            print(f"TIMED OUT {name} (after {unit_timeout:g}s)", file=out, flush=True)

    def drain(limit: int) -> None:
        while len(running) >= limit or (limit == 1 and running):
            late = []
            for name, (process, _result, _log, started, _unit) in list(running.items()):
                if process.poll() is not None:
                    finish(name)
                elif unit_timeout and time.perf_counter() - started > unit_timeout:
                    late.append(name)
            if late:
                expire(late)
            if len(running) >= limit or (limit == 1 and running):
                time.sleep(0.05)

    try:
        for unit in pending:
            drain(max(workers, 1))
            start(unit)
        drain(1)
        for unit in serial:
            # Alone: nothing else is running while it does.
            start(unit)
            drain(1)
    except BaseException:
        _stop_all([process for process, *_rest in running.values()])
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return combined


def report(combined: dict, seconds: float, out=sys.stdout) -> bool:
    """Print what failed, then unittest's summary line; True when the run passed."""
    for kind in ("failures", "errors"):
        for entry in combined[kind]:
            print("=" * 70, file=out)
            print(f"{'FAIL' if kind == 'failures' else 'ERROR'}: {entry['test']}", file=out)
            print("-" * 70, file=out)
            print(entry["detail"], file=out)
    for test in combined["unexpectedSuccesses"]:
        print(f"UNEXPECTED SUCCESS: {test}", file=out)
    for crash in combined["crashed"]:
        print("=" * 70, file=out)
        print(f"CRASHED: {crash['unit']} (exit {crash['exit']})", file=out)
        print("-" * 70, file=out)
        print(crash["log"], file=out)
    for late in combined["timedOut"]:
        print("=" * 70, file=out)
        print(f"TIMED OUT: {late['unit']} after {late['seconds']:g}s", file=out)
        print("-" * 70, file=out)
        for test in late["tests"]:
            print(f"  {test}", file=out)
        print(late["log"], file=out)
    print("-" * 70, file=out)
    print(f"Ran {combined['testsRun']} tests in {seconds:.3f}s", file=out)
    if not combined["testsRun"] and not combined["crashed"] and not combined["timedOut"]:
        print("NO TESTS RAN", file=out)
        return False
    counts = [f"{name}={len(combined[key])}" for name, key in
              (("failures", "failures"), ("errors", "errors"), ("crashed", "crashed"), ("timed out", "timedOut"),
               ("unexpected successes", "unexpectedSuccesses")) if combined[key]]
    passed = not counts
    extras = [f"skipped={combined['skipped']}"] if combined["skipped"] else []
    if combined["expectedFailures"]:
        extras.append(f"expected failures={combined['expectedFailures']}")
    print(("OK" if passed else "FAILED") + (f" ({', '.join(counts + extras)})" if counts + extras else ""), file=out)
    return passed


def write_timings(path: Path, combined: dict, seconds: float, workers: int) -> None:
    modules: dict[str, float] = {}
    for test, elapsed in combined["timings"].items():
        module = test.rsplit(".", 2)[0]
        modules[module] = modules.get(module, 0.0) + elapsed

    def ordered(values: dict[str, float]) -> dict[str, float]:
        return {key: round(value, 3) for key, value in sorted(values.items(), key=lambda item: -item[1])}

    Path(path).write_text(json.dumps({
        "total": round(seconds, 3), "workers": workers, "tests": combined["testsRun"],
        "units": ordered(combined["units"]), "modules": ordered(modules), "testTimes": ordered(combined["timings"]),
    }, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-s", "--start-directory", default="tests")
    parser.add_argument("-t", "--top-level-dir", default=".")
    parser.add_argument("-p", "--pattern", default="test_*.py")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2),
                        help="how many units run at once; cores / 2 when absent")
    parser.add_argument("--timings", type=Path, help="write per-unit, per-module and per-test times here")
    parser.add_argument("--timings-from", type=Path, help="an earlier --timings file: start the slowest units first")
    parser.add_argument("--chunk", type=int, default=CHUNK, help=f"tests per unit when a class is split; {CHUNK}")
    parser.add_argument("--unit-timeout", type=float,
                        help=f"seconds a unit may run; {UNIT_TIMEOUT_FACTOR}x the slowest unit in --timings-from, at "
                             f"least {UNIT_TIMEOUT_FLOOR}, or {UNIT_TIMEOUT_DEFAULT} without it; 0 sets no limit")
    parser.add_argument("--list", action="store_true", help="print the units, serial ones marked, and run nothing")
    parser.add_argument("--unit", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.top_level_dir = os.path.abspath(args.top_level_dir)
    if args.unit:
        if hasattr(signal, "pthread_sigmask"):
            # Started with interrupts blocked, so it was recorded before one could land; a child inherits
            # its parent's signal mask, so lift it before any test, or anything a test starts, runs.
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT, signal.SIGTERM})
        return run_unit(args.unit, args.result, args.top_level_dir)
    if args.workers < 1 or args.chunk < 1:
        parser.error("--workers and --chunk must be at least 1")
    if args.unit_timeout is not None and not (math.isfinite(args.unit_timeout) and args.unit_timeout >= 0):
        parser.error("--unit-timeout must be a finite number of seconds, 0 for none")
    units = discover_units(os.path.abspath(args.start_directory), args.top_level_dir, args.pattern, args.chunk)
    # Why the limit is not taken from --timings-from, when it is given and cannot be.
    timings_note = None
    if args.timings_from is not None:
        try:
            document = json.loads(args.timings_from.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            document = None
            timings_note = f"{args.timings_from.name} could not be read"
        earlier = document.get("testTimes") if isinstance(document, dict) else None
        earlier = {test: seconds for test, seconds in (earlier or {}).items()
                   if isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
                   and math.isfinite(seconds) and seconds >= 0} if isinstance(earlier, dict) else {}

        # A unit's own recorded wall time also counts its start-up and class or module fixtures.
        earlier_units = document.get("units") if isinstance(document, dict) else None
        earlier_units = {name: seconds for name, seconds in (earlier_units or {}).items()
                         if isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
                         and math.isfinite(seconds) and seconds >= 0} if isinstance(earlier_units, dict) else {}

        def cost(unit: dict) -> float:
            tests = sum(seconds for test, seconds in earlier.items()
                        if any(test == name or test.startswith(name + ".") for name in unit["names"]))
            return max(tests, earlier_units.get(unit["name"], 0))

        units.sort(key=cost, reverse=True)
    unit_timeout = UNIT_TIMEOUT_DEFAULT
    if args.unit_timeout is not None:
        unit_timeout = args.unit_timeout or None
    elif args.timings_from is not None and timings_note is None:
        derived = UNIT_TIMEOUT_FACTOR * max((cost(unit) for unit in units), default=0)
        if derived and math.isfinite(derived):
            unit_timeout = max(UNIT_TIMEOUT_FLOOR, derived)
        else:
            timings_note = f"{args.timings_from.name} records no usable time for a current test"
    if args.list:
        for unit in units:
            print(f"{unit['name']}{'  (serial)' if unit['serial'] else ''}  {unit['tests']}")
        return 0
    note = f" (default: {timings_note})" if timings_note and args.unit_timeout is None else ""
    print(f"unit timeout: {f'{unit_timeout:g}s' if unit_timeout else 'none'}{note}", flush=True)
    started = time.perf_counter()
    try:
        combined = run_suite(units, workers=args.workers, top=args.top_level_dir, unit_timeout=unit_timeout)
    except KeyboardInterrupt:
        print("interrupted: every worker was stopped", file=sys.stderr)
        return 130
    seconds = time.perf_counter() - started
    passed = report(combined, seconds)
    if args.timings is not None:
        write_timings(args.timings, combined, seconds, args.workers)
    if not combined["testsRun"] and not combined["crashed"] and not combined["timedOut"]:
        return 5
    return 0 if passed else 1


if __name__ == "__main__":
    if os.name != "nt":
        # A terminate stops the workers the same way an interrupt does.
        signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
