#!/usr/bin/env python3
"""Run the corpus test suite across worker processes, with the same verdict as ``unittest discover``.

Tests are discovered as ``unittest discover`` finds them, then split into
units of at most ``--chunk`` tests of one class. A class with its own
``setUpClass``/``tearDownClass`` is one unit, and a module with its own
``setUpModule``/``tearDownModule``, or that marks itself ``RUN_SERIALLY =
True``, is one unit, so no fixture runs more often than under discover. A
class that sets ``run_serially = True``, or a module marked ``RUN_SERIALLY``,
runs alone: no other worker is running while it does. Every unit runs in its
own process, so a crash or a hang ends that unit only; the run then fails,
naming it. With ``--timings-from``, the slowest units start first.

    .venv/bin/python scripts/run-tests.py                 # cores / 2 workers
    .venv/bin/python scripts/run-tests.py --workers 1     # one at a time
    .venv/bin/python scripts/run-tests.py --timings timings.json
    .venv/bin/python scripts/run-tests.py --timings-from timings.json

The exit code is 0 when every test passed, 1 when any failed, errored, passed
unexpectedly or its unit crashed, 2 on a usage error, and 130 when the run was
interrupted, after every worker is stopped. ``--timings`` writes the total,
per-unit, per-module and per-test wall times. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
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
        if type(test).__name__ == "_FailedTest":
            # A module that failed to import: its worker imports it again and reports the error.
            prefix = "unittest.loader._FailedTest."
            name = test.id()[len(prefix):] if test.id().startswith(prefix) else test.id()
            groups.setdefault(name, {"name": name, "whole": True, "serial": False, "ids": []})["ids"].append(name)
            continue
        cls = type(test)
        module = sys.modules.get(cls.__module__)
        module_whole = module is not None and (hasattr(module, "setUpModule") or hasattr(module, "tearDownModule")
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
                          "tests": len(group["ids"])})
            continue
        parts = [group["ids"][index:index + chunk] for index in range(0, len(group["ids"]), chunk)]
        for number, part in enumerate(parts, start=1):
            units.append({"name": f"{group['name']}[{number}/{len(parts)}]", "names": part,
                          "serial": group["serial"], "tests": len(part)})
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


def run_unit(names_path: Path, result_path: Path, top: str) -> int:
    """Worker mode: run one unit's tests, named in ``names_path``, and write its result as JSON."""
    sys.path.insert(0, os.path.abspath(top))
    names = json.loads(Path(names_path).read_text(encoding="utf-8"))
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    runner = unittest.TextTestRunner(resultclass=_TimedResult, stream=sys.stderr, verbosity=0)
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


def _stop(process: subprocess.Popen) -> None:
    """Stop a worker and everything it started; it leads its own process group."""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=STOP_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass


def run_suite(units: list[dict], *, workers: int, top: str, out=sys.stdout) -> dict:
    """Run every unit, at most ``workers`` at once, serial units alone; return the combined result."""
    scratch = Path(tempfile.mkdtemp(prefix="run-tests-"))
    parallel = [unit for unit in units if not unit["serial"]]
    serial = [unit for unit in units if unit["serial"]]
    pending = [*parallel]
    running: dict[str, tuple[subprocess.Popen, Path, Path, float]] = {}
    combined = {"testsRun": 0, "failures": [], "errors": [], "skipped": 0, "expectedFailures": 0,
                "unexpectedSuccesses": [], "crashed": [], "timings": {}, "units": {}}

    def start(unit: dict) -> None:
        index = len(combined["units"]) + len(running)
        result_path, log_path = scratch / f"{index}.json", scratch / f"{index}.log"
        names_path = scratch / f"{index}.names.json"
        names_path.write_text(json.dumps(unit["names"]), encoding="utf-8")
        with open(log_path, "wb") as log:
            process = subprocess.Popen(
                [sys.executable, "-B", os.path.abspath(__file__), "--unit", str(names_path),
                 "--result", str(result_path), "--top-level-dir", top],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=top,
                **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                   else {"start_new_session": True}))
        running[unit["name"]] = (process, result_path, log_path, time.perf_counter())

    def finish(name: str) -> None:
        process, result_path, log_path, started = running.pop(name)
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

    def drain(limit: int) -> None:
        while len(running) >= limit or (limit == 1 and running):
            for name, (process, *_rest) in list(running.items()):
                if process.poll() is not None:
                    finish(name)
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
        for process, *_rest in running.values():
            _stop(process)
        raise
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
    print("-" * 70, file=out)
    print(f"Ran {combined['testsRun']} tests in {seconds:.3f}s", file=out)
    counts = [f"{name}={len(combined[key])}" for name, key in
              (("failures", "failures"), ("errors", "errors"), ("crashed", "crashed"),
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
    parser.add_argument("--list", action="store_true", help="print the units, serial ones marked, and run nothing")
    parser.add_argument("--unit", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.unit:
        return run_unit(args.unit, args.result, args.top_level_dir)
    if args.workers < 1 or args.chunk < 1:
        parser.error("--workers and --chunk must be at least 1")
    units = discover_units(args.start_directory, args.top_level_dir, args.pattern, args.chunk)
    if args.timings_from is not None:
        try:
            earlier = json.loads(args.timings_from.read_text(encoding="utf-8")).get("testTimes", {})
        except (OSError, ValueError):
            earlier = {}

        def cost(unit: dict) -> float:
            return sum(seconds for test, seconds in earlier.items()
                       if any(test == name or test.startswith(name + ".") for name in unit["names"]))

        units.sort(key=cost, reverse=True)
    if args.list:
        for unit in units:
            print(f"{unit['name']}{'  (serial)' if unit['serial'] else ''}  {unit['tests']}")
        return 0
    started = time.perf_counter()
    try:
        combined = run_suite(units, workers=args.workers, top=args.top_level_dir)
    except KeyboardInterrupt:
        print("interrupted: every worker was stopped", file=sys.stderr)
        return 130
    seconds = time.perf_counter() - started
    passed = report(combined, seconds)
    if args.timings is not None:
        write_timings(args.timings, combined, seconds, args.workers)
    return 0 if passed else 1


if __name__ == "__main__":
    if os.name != "nt":
        # A terminate stops the workers the same way an interrupt does.
        signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
