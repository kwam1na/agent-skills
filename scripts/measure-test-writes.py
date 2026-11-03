#!/usr/bin/env python3
"""Count what the test suite writes to temp folders, by test module.

Runs the suite through ``scripts/run-tests.py`` with every unit in a temp
folder of its own under one fresh root, watches that root with the operating
system's file events, and reports per module and in total:

- ``events``: every file event, as the system's watchers receive them;
- ``filesWritten``: events that created, changed or renamed a file;
- ``foldersCreated``: events that created a folder;
- ``left``: files and folders still in the unit's temp folder once it ended.

A write inside one event batch to a path already written counts once, as a
watcher would see it, so the counts are comparable between runs and are not an
exact tally of system calls. A test that clears ``TMPDIR`` for a child writes
outside the root, and that child's writes are not counted. Events the system
dropped are reported as ``droppedBatches``; a run that has any undercounts.

    .venv/bin/python scripts/measure-test-writes.py --output before.json --workers 4
    .venv/bin/python scripts/measure-test-writes.py --output after.json --workers 4
    .venv/bin/python scripts/measure-test-writes.py --compare before.json after.json

File events come from FSEvents, so measuring runs on macOS only; elsewhere the
command refuses with exit 2. ``--compare`` runs anywhere. The exit code is
run-tests.py's verdict: 0 when every test passed, 1 when any did not, 2 on a
usage error or a refusal, 5 when no test ran, 130 when interrupted. Standard
library only.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The FSEvents constants this needs, from <CoreServices/FSEvents.h>.
SINCE_NOW = 0xFFFFFFFFFFFFFFFF
CREATE_FLAG_NO_DEFER = 0x02
CREATE_FLAG_FILE_EVENTS = 0x10
DROPPED = 0x00000001 | 0x00000002 | 0x00000004  # must-scan-subdirs, user-dropped, kernel-dropped
ITEM_CREATED = 0x00000100
ITEM_RENAMED = 0x00000800
ITEM_MODIFIED = 0x00001000
ITEM_IS_FILE = 0x00010000
ITEM_IS_DIR = 0x00020000
LATENCY_SECONDS = 0.1
COUNTS = ("events", "filesWritten", "foldersCreated", "left")


def classify(flags: int) -> tuple[bool, bool]:
    """Whether one event's flags say a file was written, and whether they say a folder was created."""
    written = bool(flags & ITEM_IS_FILE and flags & (ITEM_CREATED | ITEM_MODIFIED | ITEM_RENAMED))
    return written, bool(flags & ITEM_IS_DIR and flags & ITEM_CREATED)


class Tally:
    """Counts file events under a root, by the first folder under it. Everything else is the runner's."""

    def __init__(self, root: Path) -> None:
        self.prefix = os.fsencode(root) + b"/"
        self.counts: dict[str, dict[str, int]] = {}
        self.dropped_batches = 0

    def add(self, path: bytes, flags: int) -> None:
        if flags & DROPPED:
            self.dropped_batches += 1
        if not path.startswith(self.prefix):
            return
        owner, separator, _rest = path[len(self.prefix):].partition(b"/")
        # An event on the unit's folder itself is the runner making it, not the unit writing.
        if not separator:
            return
        count = self.counts.setdefault(owner.decode(errors="replace"),
                                       {"events": 0, "filesWritten": 0, "foldersCreated": 0})
        written, created = classify(flags)
        count["events"] += 1
        count["filesWritten"] += written
        count["foldersCreated"] += created


class Watch:
    """A running FSEvents stream over ``root``, feeding ``add(path, flags)`` from its own thread."""

    def __init__(self, root: Path, add: Callable[[bytes, int], None]) -> None:
        foundation = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        services = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")
        system = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        self.services = services
        callback_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                         ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_uint32),
                                         ctypes.c_void_p)

        def delivered(_stream, _info, count, paths, flags, _ids) -> None:
            for index in range(count):
                add(paths[index], flags[index])

        # Kept on the object: FSEvents calls it until the stream is released.
        self.callback = callback_type(delivered)
        foundation.CFStringCreateWithCString.restype = ctypes.c_void_p
        foundation.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        foundation.CFArrayCreate.restype = ctypes.c_void_p
        foundation.CFArrayCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_long,
                                             ctypes.c_void_p]
        utf8 = 0x08000100
        path = foundation.CFStringCreateWithCString(None, os.fsencode(root), utf8)
        paths = foundation.CFArrayCreate(None, (ctypes.c_void_p * 1)(path), 1, None)
        services.FSEventStreamCreate.restype = ctypes.c_void_p
        services.FSEventStreamCreate.argtypes = [ctypes.c_void_p, callback_type, ctypes.c_void_p, ctypes.c_void_p,
                                                 ctypes.c_uint64, ctypes.c_double, ctypes.c_uint32]
        self.stream = services.FSEventStreamCreate(None, self.callback, None, paths, SINCE_NOW, LATENCY_SECONDS,
                                                   CREATE_FLAG_FILE_EVENTS | CREATE_FLAG_NO_DEFER)
        if not self.stream:
            raise OSError(f"FSEvents refused to watch {root}")
        system.dispatch_queue_create.restype = ctypes.c_void_p
        system.dispatch_queue_create.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
        queue = system.dispatch_queue_create(b"measure-test-writes", None)
        for name in ("FSEventStreamFlushSync", "FSEventStreamStop", "FSEventStreamInvalidate",
                     "FSEventStreamRelease"):
            getattr(services, name).argtypes = [ctypes.c_void_p]
        services.FSEventStreamSetDispatchQueue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        services.FSEventStreamStart.argtypes = [ctypes.c_void_p]
        services.FSEventStreamStart.restype = ctypes.c_bool
        services.FSEventStreamSetDispatchQueue(self.stream, queue)
        if not services.FSEventStreamStart(self.stream):
            services.FSEventStreamInvalidate(self.stream)
            services.FSEventStreamRelease(self.stream)
            raise OSError(f"FSEvents could not start watching {root}")

    def stop(self) -> None:
        """Deliver every event so far, then stop."""
        self.services.FSEventStreamFlushSync(self.stream)
        self.services.FSEventStreamStop(self.stream)
        self.services.FSEventStreamInvalidate(self.stream)
        self.services.FSEventStreamRelease(self.stream)


def _runner():
    spec = importlib.util.spec_from_file_location("run_tests", ROOT / "scripts" / "run-tests.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def module_of(test_id: str) -> str:
    """The test module a test id belongs to: everything up to its first ``test_`` part."""
    parts = test_id.split(".")
    for index, part in enumerate(parts):
        if part.startswith("test_"):
            return ".".join(parts[:index + 1])
    return parts[0]


def left_in(folder: Path) -> list[str]:
    """Every file and folder still in ``folder``, relative to it."""
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*"))


def summarize(units: list[dict], counts: dict[str, dict[str, int]], temps: dict[str, str]) -> dict:
    """Per-module and total counts from the per-folder tally and each unit's leftovers."""
    modules: dict[str, dict] = {}
    for unit in units:
        temp = temps.get(unit["name"])
        if temp is None:
            continue
        module = modules.setdefault(module_of(unit["ids"][0] if unit["ids"] else unit["name"]),
                                    {key: 0 for key in COUNTS} | {"leftFolders": []})
        count = counts.get(Path(temp).name, {})
        for key in ("events", "filesWritten", "foldersCreated"):
            module[key] += count.get(key, 0)
        left = left_in(Path(temp))
        module["left"] += len(left)
        module["leftFolders"] += sorted({entry.split(os.sep, 1)[0] for entry in left})
    ordered = dict(sorted(modules.items(), key=lambda item: (-item[1]["filesWritten"], item[0])))
    return {"total": {key: sum(module[key] for module in modules.values()) for key in COUNTS}, "modules": ordered}


def table(summary: dict, out) -> None:
    print(f"{'files':>9} {'events':>9} {'folders':>8} {'left':>6}  module", file=out)
    for name, module in summary["modules"].items():
        print(f"{module['filesWritten']:>9} {module['events']:>9} {module['foldersCreated']:>8} "
              f"{module['left']:>6}  {name}", file=out)
    total = summary["total"]
    print(f"{total['filesWritten']:>9} {total['events']:>9} {total['foldersCreated']:>8} {total['left']:>6}  total",
          file=out)


def compare(before: dict, after: dict, out=sys.stdout) -> None:
    """Per-module files written before and after, largest reduction first, then the totals."""
    names = {*before["modules"], *after["modules"]}

    def written(summary: dict, name: str) -> int:
        return summary["modules"].get(name, {}).get("filesWritten", 0)

    print(f"{'before':>9} {'after':>9} {'change':>9}  module", file=out)
    for name in sorted(names, key=lambda item: (written(after, item) - written(before, item), item)):
        print(f"{written(before, name):>9} {written(after, name):>9} "
              f"{written(after, name) - written(before, name):>+9}  {name}", file=out)
    first, second = before["total"], after["total"]
    print(f"{first['filesWritten']:>9} {second['filesWritten']:>9} "
          f"{second['filesWritten'] - first['filesWritten']:>+9}  total files written", file=out)
    print(f"{first['events']:>9} {second['events']:>9} {second['events'] - first['events']:>+9}  total events",
          file=out)
    print(f"{first['left']:>9} {second['left']:>9} {second['left'] - first['left']:>+9}  total left behind", file=out)
    if first["filesWritten"]:
        print(f"files written: {second['filesWritten'] / first['filesWritten']:.1%} of before", file=out)


def measure(args: argparse.Namespace) -> int:
    runner = _runner()
    base = Path(tempfile.mkdtemp(prefix="m-")).resolve()
    try:
        (base / "units").mkdir()
        (base / "runner").mkdir()
        # Discovery, and the runner's own scratch, stay out of every unit's count.
        tempfile.tempdir = None
        for name in ("TMPDIR", "TEMP", "TMP"):
            os.environ[name] = str(base / "runner")
        tally = Tally(base / "units")
        lock = threading.Lock()

        def add(path: bytes, flags: int) -> None:
            with lock:
                tally.add(path, flags)

        watch = Watch(base, add)
        try:
            units = runner.discover_units(os.path.abspath(args.start_directory), args.top_level_dir, args.pattern)
            started = time.perf_counter()
            combined = runner.run_suite(units, workers=args.workers, top=args.top_level_dir,
                                        unit_timeout=args.unit_timeout or None, unit_temp=base / "units")
            seconds = time.perf_counter() - started
            passed = runner.report(combined, seconds)
        except KeyboardInterrupt:
            print("interrupted: every worker was stopped", file=sys.stderr)
            return 130
        finally:
            watch.stop()
        with lock:
            summary = summarize(units, tally.counts, combined["unitTemp"])
        summary |= {"seconds": round(seconds, 3), "workers": args.workers, "testsRun": combined["testsRun"],
                    "passed": passed, "droppedBatches": tally.dropped_batches}
        table(summary, sys.stdout)
        print(f"dropped event batches: {tally.dropped_batches}")
        if args.output:
            args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    if not combined["testsRun"] and not combined["crashed"] and not combined["timedOut"]:
        return 5
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-s", "--start-directory", default="tests")
    parser.add_argument("-t", "--top-level-dir", default=".")
    parser.add_argument("-p", "--pattern", default="test_*.py")
    parser.add_argument("--workers", type=int, default=4, help="how many units run at once; 4 when absent")
    parser.add_argument("--unit-timeout", type=float, default=1800, help="seconds a unit may run; 0 sets no limit")
    parser.add_argument("--output", type=Path, help="write the counts here as JSON, for --compare")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"),
                        help="report two earlier --output files side by side, and run nothing")
    args = parser.parse_args(argv)
    if args.compare:
        before, after = (json.loads(path.read_text(encoding="utf-8")) for path in args.compare)
        compare(before, after)
        return 0
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if sys.platform != "darwin":
        print("measure-test-writes: measuring needs FSEvents, so macOS; --compare runs anywhere", file=sys.stderr)
        return 2
    args.top_level_dir = os.path.abspath(args.top_level_dir)
    return measure(args)


if __name__ == "__main__":
    raise SystemExit(main())
