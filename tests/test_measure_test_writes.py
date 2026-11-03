"""scripts/measure-test-writes.py counts what each test module writes to temp folders, and what it leaves there."""

from __future__ import annotations

import errno
import importlib.util
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "measure-test-writes.py"


def _load():
    spec = importlib.util.spec_from_file_location("measure_test_writes", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


measure = _load()


class CountingTests(unittest.TestCase):
    def test_only_a_written_file_or_a_created_folder_counts_as_one(self) -> None:
        for flags, expected in (
            (measure.ITEM_IS_FILE | measure.ITEM_CREATED, (True, False)),
            (measure.ITEM_IS_FILE | measure.ITEM_MODIFIED, (True, False)),
            (measure.ITEM_IS_FILE | measure.ITEM_RENAMED, (True, False)),
            (measure.ITEM_IS_FILE, (False, False)),
            (measure.ITEM_IS_DIR | measure.ITEM_CREATED, (False, True)),
            (measure.ITEM_IS_DIR | measure.ITEM_MODIFIED, (False, False)),
        ):
            self.assertEqual(measure.classify(flags), expected, hex(flags))

    def test_events_go_to_the_folder_they_happened_in(self) -> None:
        tally = measure.Tally(Path("/base/units"))
        written = measure.ITEM_IS_FILE | measure.ITEM_CREATED
        tally.add(b"/base/units/0/tmp/a", written)
        tally.add(b"/base/units/0/tmp/b", measure.ITEM_IS_DIR | measure.ITEM_CREATED)
        tally.add(b"/base/units/1/c", written)
        tally.add(b"/base/units/0", measure.ITEM_IS_DIR | measure.ITEM_CREATED)  # the runner making the folder
        tally.add(b"/base/runner/d", written)  # the runner's own scratch
        tally.add(b"/base/units-not/0/e", written)
        self.assertEqual(tally.counts, {"0": {"events": 2, "filesWritten": 1, "foldersCreated": 1},
                                        "1": {"events": 1, "filesWritten": 1, "foldersCreated": 0}})

    def test_a_dropped_batch_is_reported_wherever_it_landed(self) -> None:
        tally = measure.Tally(Path("/base/units"))
        tally.add(b"/elsewhere", 0x00000002)
        tally.add(b"/base/units/0/a", 0x00000004)
        tally.add(b"/base/units/0/b", 0x00000001)
        self.assertEqual(tally.dropped_batches, 3)

    def test_a_test_id_names_its_module(self) -> None:
        self.assertEqual(measure.module_of("tests.test_alpha.One.test_a"), "tests.test_alpha")
        self.assertEqual(measure.module_of("tests.test_alpha"), "tests.test_alpha")
        self.assertEqual(measure.module_of("suite.deeper.test_beta.Two.test_b"), "suite.deeper.test_beta")

    def test_units_of_one_module_add_up_and_their_leftovers_are_named(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "0").mkdir()
            (base / "1/left-behind").mkdir(parents=True)
            (base / "1/left-behind/file").write_text("x\n", encoding="utf-8")
            (base / "2").mkdir()
            summary = measure.summarize(
                [{"name": "tests.test_a.One[1/2]", "ids": ["tests.test_a.One.test_x"]},
                 {"name": "tests.test_a.One[2/2]", "ids": ["tests.test_a.One.test_y"]},
                 {"name": "tests.test_b", "ids": ["tests.test_b.Two.test_z"]},
                 {"name": "never.started", "ids": ["tests.test_c.Three.test_w"]}],
                {"0": {"events": 5, "filesWritten": 2, "foldersCreated": 1},
                 "1": {"events": 7, "filesWritten": 3, "foldersCreated": 0},
                 "2": {"events": 100, "filesWritten": 90, "foldersCreated": 9}},
                {"tests.test_a.One[1/2]": str(base / "0"), "tests.test_a.One[2/2]": str(base / "1"),
                 "tests.test_b": str(base / "2")},
            )
        self.assertEqual(summary["modules"]["tests.test_a"],
                         {"events": 12, "filesWritten": 5, "foldersCreated": 1, "left": 2, "leftFolders": ["left-behind"]})
        self.assertEqual(list(summary["modules"]), ["tests.test_b", "tests.test_a"], "the largest writer comes first")
        self.assertEqual(summary["total"], {"events": 112, "filesWritten": 95, "foldersCreated": 10, "left": 2})


class CompareTests(unittest.TestCase):
    def document(self, **written: int) -> dict:
        return {"total": {"filesWritten": sum(written.values()), "events": sum(written.values()) * 2, "foldersCreated": 0,
                          "left": 0},
                "modules": {name: {"filesWritten": value} for name, value in written.items()}}

    def test_each_module_shows_before_and_after_with_the_totals(self) -> None:
        out = io.StringIO()
        measure.compare(self.document(test_a=100, test_gone=50), self.document(test_a=25, test_new=5), out=out)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[1].split(), ["100", "25", "-75", "test_a"])
        self.assertEqual(lines[2].split(), ["50", "0", "-50", "test_gone"])
        self.assertEqual(lines[3].split(), ["0", "5", "+5", "test_new"])
        self.assertIn("150        30      -120  total files written", out.getvalue())
        self.assertIn("files written: 20.0% of before", out.getvalue())

    def test_a_run_that_cannot_be_taken_at_face_value_says_so(self) -> None:
        clean = {**self.document(test_a=100), "passed": True, "droppedBatches": 0, "settled": True, "testsRun": 10}
        out = io.StringIO()
        measure.compare(clean, clean, out=out)
        self.assertNotIn("before:", out.getvalue())
        self.assertNotIn("after:", out.getvalue())
        for field, word in ({"passed": False}, "did not pass"), ({"droppedBatches": 40}, "40 event batches"), \
                ({"settled": False}, "never arrived"):
            with self.subTest(field=field):
                out = io.StringIO()
                measure.compare(clean, {**self.document(test_a=1), "testsRun": 3, **field}, out=out)
                self.assertRegex(out.getvalue(), rf"after: .*{word}")
                self.assertRegex(out.getvalue(), r"10 +3 +-7  tests run")
                out = io.StringIO()
                measure.compare({**clean, **field}, clean, out=out)
                self.assertRegex(out.getvalue(), rf"before: .*{word}")

    def test_an_output_it_could_not_write_is_refused_before_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            closed = Path(temporary) / "closed"
            closed.mkdir()
            os.chmod(closed, 0o500)
            read_only = Path(temporary) / "read-only.json"
            read_only.write_text("{}\n", encoding="utf-8")
            os.chmod(read_only, 0o400)
            for output in ("/nonexistent-folder/counts.json", temporary, str(closed / "counts.json"), str(read_only)):
                with self.subTest(output=output), mock.patch("sys.stderr", io.StringIO()) as err, \
                        mock.patch.object(measure, "measure", return_value=0) as measured, \
                        self.assertRaises(SystemExit) as refused:
                    measure.main(["--output", output])
                self.assertEqual(refused.exception.code, 2)
                self.assertIn("--output", err.getvalue())
                measured.assert_not_called()
            os.chmod(closed, 0o700)

    def test_a_compare_of_files_runs_on_any_system(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            before, after = Path(temporary) / "before.json", Path(temporary) / "after.json"
            before.write_text(json.dumps(self.document(test_a=10)), encoding="utf-8")
            after.write_text(json.dumps(self.document(test_a=4)), encoding="utf-8")
            with mock.patch.object(measure.sys, "platform", "linux"), mock.patch("sys.stdout", io.StringIO()) as out:
                self.assertEqual(measure.main(["--compare", str(before), str(after)]), 0)
        self.assertIn("40.0% of before", out.getvalue())

    def test_measuring_elsewhere_refuses_rather_than_reporting_nothing(self) -> None:
        # Patched so that a refusal lost fails here rather than measuring the suite this test is in.
        with mock.patch.object(measure.sys, "platform", "linux"), mock.patch("sys.stderr", io.StringIO()) as err, \
                mock.patch.object(measure, "measure", return_value=0) as measured:
            self.assertEqual(measure.main([]), 2)
        self.assertIn("macOS", err.getvalue())
        measured.assert_not_called()


class UnitTempTests(unittest.TestCase):
    """The runner's unit_temp gives each unit its own folder; without it nothing changes."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "suite").mkdir()
        (self.root / "suite/__init__.py").write_text("", encoding="utf-8")
        for name in ("alpha", "beta"):
            (self.root / f"suite/test_{name}.py").write_text(textwrap.dedent(f"""
                import os, tempfile, unittest
                class Case(unittest.TestCase):
                    def test_it(self):
                        with open(os.path.join(os.environ["TRACE"], "{name}"), "w") as trace:
                            trace.write(tempfile.gettempdir())
            """), encoding="utf-8")
        self.trace = self.root / "trace"
        self.trace.mkdir()

    def runner(self):
        spec = importlib.util.spec_from_file_location("run_tests_for_unit_temp", ROOT / "scripts" / "run-tests.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_suite(self, **options: object) -> dict:
        runner = self.runner()
        units = [{"name": f"suite.test_{name}", "names": [f"suite.test_{name}"], "serial": False, "tests": 1,
                  "ids": [f"suite.test_{name}.Case.test_it"], "pattern": "test_*.py"} for name in ("alpha", "beta")]
        with mock.patch.dict(os.environ, {"TRACE": str(self.trace), "PYTHONPATH": str(ROOT)}):
            return runner.run_suite(units, workers=2, top=str(self.root), out=io.StringIO(), **options)

    def test_each_unit_writes_under_its_own_folder(self) -> None:
        temps = self.root / "temps"
        temps.mkdir()
        combined = self.run_suite(unit_temp=temps)
        self.assertEqual(combined["testsRun"], 2)
        seen = {name: (self.trace / name).read_text(encoding="utf-8") for name in ("alpha", "beta")}
        self.assertNotEqual(seen["alpha"], seen["beta"])
        self.assertEqual(os.path.realpath(seen["alpha"]), os.path.realpath(combined["unitTemp"]["suite.test_alpha"]))
        self.assertEqual(os.path.realpath(seen["beta"]), os.path.realpath(combined["unitTemp"]["suite.test_beta"]))
        for folder in seen.values():
            self.assertEqual(Path(folder).parent.resolve(), temps.resolve())

    def test_without_it_units_keep_the_runners_temp_folder(self) -> None:
        combined = self.run_suite()
        self.assertEqual(combined["unitTemp"], {})
        expected = os.path.realpath(tempfile.gettempdir())
        for name in ("alpha", "beta"):
            self.assertEqual(os.path.realpath((self.trace / name).read_text(encoding="utf-8")), expected)


@unittest.skipUnless(sys.platform == "darwin", "file events are counted on macOS")
class MeasuringTests(unittest.TestCase):
    """The whole command on a small suite: real file events, per module, and the runner's verdict."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "suite").mkdir()
        (self.root / "suite/__init__.py").write_text("", encoding="utf-8")
        self.output = self.root / "counts.json"

    def module(self, name: str, body: str) -> None:
        (self.root / f"suite/{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")

    def measure(self) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-B", str(SCRIPT), "-s", "suite", "-t", ".", "--workers", "2",
                               "--output", str(self.output)],
                              cwd=self.root, capture_output=True, text=True, timeout=120,
                              env={**os.environ, "PYTHONPATH": str(ROOT)})

    def test_writes_are_counted_by_module_and_leftovers_are_named(self) -> None:
        self.module("test_writer", """
            import pathlib, tempfile, unittest
            class Writer(unittest.TestCase):
                def test_writes(self):
                    with tempfile.TemporaryDirectory() as folder:
                        for number in range(25):
                            (pathlib.Path(folder) / f"file-{number}").write_text("x")
        """)
        self.module("test_child", """
            import subprocess, sys, unittest
            class Child(unittest.TestCase):
                def test_a_child_process_is_counted_too(self):
                    subprocess.run(["sh", "-c", 'd="$TMPDIR/child"; mkdir "$d"; for n in 1 2 3 4 5 6 7; do : > "$d/$n"; done; rm -rf "$d"'], check=True)
        """)
        self.module("test_leaver", """
            import pathlib, tempfile, unittest
            class Leaver(unittest.TestCase):
                def test_leaves_a_folder(self):
                    folder = tempfile.mkdtemp(prefix="left-behind-")
                    (pathlib.Path(folder) / "file").write_text("x")
        """)
        self.module("test_quiet", """
            import unittest
            class Quiet(unittest.TestCase):
                def test_writes_nothing(self): pass
        """)
        result = self.measure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        counts = json.loads(self.output.read_text(encoding="utf-8"))
        modules = counts["modules"]
        # At least one count per file, and at most its create, its write and its removal in separate batches.
        self.assertGreaterEqual(modules["suite.test_writer"]["filesWritten"], 25)
        self.assertLessEqual(modules["suite.test_writer"]["filesWritten"], 75)
        self.assertEqual(modules["suite.test_writer"]["left"], 0)
        self.assertGreaterEqual(modules["suite.test_child"]["filesWritten"], 7)
        self.assertLessEqual(modules["suite.test_child"]["filesWritten"], 21)
        self.assertEqual(modules["suite.test_quiet"]["events"], 0)
        self.assertEqual(modules["suite.test_leaver"]["left"], 2)
        self.assertEqual(len(modules["suite.test_leaver"]["leftFolders"]), 1)
        self.assertTrue(modules["suite.test_leaver"]["leftFolders"][0].startswith("left-behind-"))
        self.assertEqual(counts["total"]["left"], 2)
        self.assertEqual(counts["testsRun"], 4)
        self.assertTrue(counts["passed"])
        self.assertIn("suite.test_writer", result.stdout)

    def test_a_failing_suite_keeps_its_verdict_and_its_counts(self) -> None:
        self.module("test_failing", """
            import unittest
            class Failing(unittest.TestCase):
                def test_fails(self): self.fail("no")
        """)
        result = self.measure()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(json.loads(self.output.read_text(encoding="utf-8"))["passed"])

    def test_no_test_found_is_the_runners_exit_five(self) -> None:
        self.assertEqual(self.measure().returncode, 5)

    def test_a_terminate_stops_the_workers_and_removes_the_root(self) -> None:
        temps = self.root / "temps"
        temps.mkdir()
        pid_file = self.root / "worker.pid"
        self.module("test_hanging", f"""
            import os, pathlib, tempfile, time, unittest
            class Hanging(unittest.TestCase):
                def test_hangs(self):
                    closed = tempfile.mkdtemp(prefix="closed-")
                    (pathlib.Path(closed) / "file").write_text("x")
                    os.chmod(closed, 0)
                    pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))
                    time.sleep(120)
        """)
        process = subprocess.Popen([sys.executable, "-B", str(SCRIPT), "-s", "suite", "-t", "."], cwd=self.root,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env={**os.environ, "PYTHONPATH": str(ROOT), "TMPDIR": str(temps)})
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 60
        while not (pid_file.exists() and pid_file.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)
        worker = int(pid_file.read_text())
        process.terminate()
        self.assertEqual(process.wait(timeout=30), 130)
        self.assertEqual(list(temps.iterdir()), [])
        with self.assertRaises(ProcessLookupError):
            os.kill(worker, 0)

    def test_a_terminate_during_discovery_stops_the_run(self) -> None:
        self._signal_during_discovery(signal.SIGTERM)

    def test_an_interrupt_during_discovery_stops_the_run(self) -> None:
        self._signal_during_discovery(signal.SIGINT)

    def _signal_during_discovery(self, signum: int) -> None:
        started, ran = self.root / "importing", self.root / "ran"
        self.module("test_slow_import", f"""
            import pathlib, time, unittest
            pathlib.Path({str(started)!r}).write_text("x")
            time.sleep(5)
            class Slow(unittest.TestCase):
                def test_runs(self):
                    pathlib.Path({str(ran)!r}).write_text("x")
        """)
        process = subprocess.Popen([sys.executable, "-B", str(SCRIPT), "-s", "suite", "-t", "."], cwd=self.root,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 60
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        process.send_signal(signum)
        self.assertEqual(process.wait(timeout=60), 130)
        self.assertFalse(ran.exists())

    def test_a_path_too_long_to_reach_costs_nothing(self) -> None:
        (self.root / "held").mkdir()
        too_long = OSError(errno.ENAMETOOLONG, "File name too long")
        with mock.patch.object(measure.Path, "is_symlink", side_effect=too_long) as checked:
            measure._open_folders(self.root)
        checked.assert_called()

    def test_a_folder_that_cannot_be_opened_or_is_gone_costs_nothing(self) -> None:
        (self.root / "held").mkdir()
        for failure in (PermissionError("cannot change this one"), FileNotFoundError("a leftover child removed it")):
            with self.subTest(failure=failure), mock.patch.object(measure.os, "chmod", side_effect=failure) as chmod:
                measure._open_folders(self.root)
                chmod.assert_called()

    def test_a_link_a_test_left_does_not_open_what_it_points_to(self) -> None:
        temps = self.root / "temps"
        temps.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        os.chmod(outside, 0o500)
        self.addCleanup(os.chmod, outside, 0o700)
        self.module("test_linker", f"""
            import pathlib, tempfile, unittest
            class Linker(unittest.TestCase):
                def test_links(self):
                    (pathlib.Path(tempfile.gettempdir()) / "link").symlink_to({str(outside)!r})
        """)
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "-s", "suite", "-t", "."], cwd=self.root,
                                capture_output=True, text=True, timeout=120,
                                env={**os.environ, "PYTHONPATH": str(ROOT), "TMPDIR": str(temps)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o500)
        self.assertEqual(list(temps.iterdir()), [])

    def test_a_folder_a_test_closed_is_counted_and_removed(self) -> None:
        temps = self.root / "temps"
        temps.mkdir()
        self.module("test_closer", """
            import os, pathlib, tempfile, unittest
            class Closer(unittest.TestCase):
                def test_closes(self):
                    folder = tempfile.mkdtemp(prefix="closed-")
                    (pathlib.Path(folder) / "file").write_text("x")
                    os.chmod(folder, 0)
        """)
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "-s", "suite", "-t", ".", "--output", str(self.output)],
                                cwd=self.root, capture_output=True, text=True, timeout=120,
                                env={**os.environ, "PYTHONPATH": str(ROOT), "TMPDIR": str(temps)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8"))["total"]["left"], 2)
        self.assertEqual(list(temps.iterdir()), [])

    def test_its_own_temp_root_is_removed(self) -> None:
        temps = self.root / "temps"
        temps.mkdir()
        self.module("test_leaver", """
            import tempfile, unittest
            class Leaver(unittest.TestCase):
                def test_leaves_a_folder(self): tempfile.mkdtemp()
        """)
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "-s", "suite", "-t", "."], cwd=self.root,
                                capture_output=True, text=True, timeout=120,
                                env={**os.environ, "PYTHONPATH": str(ROOT), "TMPDIR": str(temps)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(temps.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
