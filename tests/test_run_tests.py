from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run-tests.py"


class ParallelRunnerTests(unittest.TestCase):
    """scripts/run-tests.py gives unittest discover's verdict, across workers, and never hides a failure."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "suite").mkdir()
        (self.root / "suite" / "__init__.py").write_text("", encoding="utf-8")
        self.trace = self.root / "trace.log"

    def module(self, name: str, body: str) -> None:
        (self.root / "suite" / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")

    def run_runner(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-B", str(RUNNER), "-s", "suite", "-t", ".", *args],
                              cwd=self.root, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "TRACE": str(self.trace)})

    def discover(self) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "suite", "-t", "."],
                              cwd=self.root, capture_output=True, text=True, timeout=120,
                              env={**os.environ, "TRACE": str(self.trace)})

    def passing_suite(self) -> None:
        self.module("test_alpha", """
            import unittest
            class One(unittest.TestCase):
                def test_a(self): pass
                def test_b(self): pass
            class Two(unittest.TestCase):
                def test_c(self): pass
                @unittest.skip("not here")
                def test_d(self): pass
                @unittest.expectedFailure
                def test_e(self): self.fail("expected")
        """)
        self.module("test_beta", """
            import unittest
            def setUpModule(): pass
            class Three(unittest.TestCase):
                def test_f(self): pass
            class Four(unittest.TestCase):
                def test_g(self): pass
        """)

    def test_a_passing_suite_has_discover_s_verdict_and_count(self) -> None:
        self.passing_suite()
        run, reference = self.run_runner("--workers", "3"), self.discover()
        self.assertEqual((run.returncode, reference.returncode), (0, 0), run.stdout + run.stderr)
        self.assertIn("Ran 7 tests", run.stdout)
        self.assertIn("Ran 7 tests", reference.stderr)
        self.assertIn("OK (skipped=1, expected failures=1)", run.stdout)

    def test_classes_are_units_and_a_module_with_module_fixtures_stays_whole(self) -> None:
        self.passing_suite()
        listed = self.run_runner("--list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        names = [line.split()[0] for line in listed.stdout.splitlines()]
        self.assertEqual(names, ["suite.test_alpha.One", "suite.test_alpha.Two", "suite.test_beta"])
        chunked = self.run_runner("--list", "--chunk", "1")
        self.assertEqual([line.split()[0] for line in chunked.stdout.splitlines()], [
            "suite.test_alpha.One[1/2]", "suite.test_alpha.One[2/2]", "suite.test_alpha.Two[1/3]",
            "suite.test_alpha.Two[2/3]", "suite.test_alpha.Two[3/3]", "suite.test_beta"], chunked.stdout)
        self.module("test_fixture", """
            import unittest
            class Shared(unittest.TestCase):
                @classmethod
                def setUpClass(cls): cls.value = 1
                def test_x(self): pass
                def test_y(self): pass
        """)
        names = [line.split()[0] for line in self.run_runner("--list", "--chunk", "1").stdout.splitlines()]
        self.assertIn("suite.test_fixture.Shared", names, "a class fixture keeps its class whole")
        run = self.run_runner("--chunk", "1", "--workers", "3")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Ran 9 tests", run.stdout)

    def test_a_failing_test_in_any_worker_fails_the_run_and_is_named(self) -> None:
        self.passing_suite()
        self.module("test_gamma", """
            import unittest
            class Broken(unittest.TestCase):
                def test_wrong(self): self.assertEqual(1, 2)
                def test_raises(self): raise RuntimeError("boom")
            class Lucky(unittest.TestCase):
                @unittest.expectedFailure
                def test_passes_anyway(self): pass
        """)
        run, reference = self.run_runner("--workers", "4"), self.discover()
        self.assertEqual((run.returncode, reference.returncode), (1, 1))
        self.assertIn("FAIL: suite.test_gamma.Broken.test_wrong", run.stdout)
        self.assertIn("ERROR: suite.test_gamma.Broken.test_raises", run.stdout)
        self.assertIn("UNEXPECTED SUCCESS: suite.test_gamma.Lucky.test_passes_anyway", run.stdout)
        self.assertIn("FAILED (failures=1, errors=1, unexpected successes=1", run.stdout)

    def test_a_module_that_cannot_import_fails_the_run(self) -> None:
        self.passing_suite()
        self.module("test_delta", "import no_such_module_anywhere\n")
        run, reference = self.run_runner(), self.discover()
        self.assertEqual((run.returncode, reference.returncode), (1, 1))
        self.assertIn("no_such_module_anywhere", run.stdout)

    def test_a_crashed_worker_fails_the_run_and_names_its_unit(self) -> None:
        self.passing_suite()
        self.module("test_crash", """
            import os, unittest
            class Dies(unittest.TestCase):
                def test_exit(self): os._exit(7)
        """)
        run = self.run_runner("--workers", "2")
        self.assertEqual(run.returncode, 1)
        self.assertIn("CRASHED: suite.test_crash.Dies (exit 7)", run.stdout)
        self.assertIn("crashed=1", run.stdout)

    def test_a_serial_unit_never_runs_beside_another(self) -> None:
        body = """
            import os, time, unittest
            def mark(name):
                with open(os.environ["TRACE"], "a") as trace:
                    trace.write(f"{name} start {time.time()}\\n")
                time.sleep(0.6)
                with open(os.environ["TRACE"], "a") as trace:
                    trace.write(f"{name} end {time.time()}\\n")
        """
        self.module("test_busy", body + """
            class A(unittest.TestCase):
                def test_a(self): mark("A")
            class B(unittest.TestCase):
                def test_b(self): mark("B")
            class Alone(unittest.TestCase):
                run_serially = True
                def test_alone(self): mark("Alone")
        """)
        self.module("test_whole", body + """
            RUN_SERIALLY = True
            class C(unittest.TestCase):
                def test_c(self): mark("C")
        """)
        self.module("test_more", body + """
            class D(unittest.TestCase):
                def test_d(self): mark("D")
        """)
        run = self.run_runner("--workers", "4")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        spans: dict[str, list[float]] = {}
        for line in self.trace.read_text().splitlines():
            name, _, moment = line.split()
            spans.setdefault(name, []).append(float(moment))
        self.assertEqual(set(spans), {"A", "B", "Alone", "C", "D"})
        overlapping = [(first, second) for first in ("Alone", "C") for second in spans if second != first
                       and spans[first][0] < spans[second][1] and spans[second][0] < spans[first][1]]
        self.assertEqual(overlapping, [], spans)
        # The others did share the machine.
        self.assertTrue(spans["A"][0] < spans["B"][1] and spans["B"][0] < spans["A"][1], spans)

    def quiet_suite(self) -> None:
        body = """
            import os, time, unittest
            def mark(name):
                with open(os.environ["TRACE"], "a") as trace:
                    trace.write(f"{name} start {time.time()}\\n")
                time.sleep(0.6)
                with open(os.environ["TRACE"], "a") as trace:
                    trace.write(f"{name} end {time.time()}\\n")
        """
        self.module("test_busy", body + """
            class A(unittest.TestCase):
                def test_a(self): mark("A")
            class B(unittest.TestCase):
                def test_b(self): mark("B")
            class E(unittest.TestCase):
                def test_e(self): mark("E")
            class F(unittest.TestCase):
                def test_f(self): mark("F")
            class Hushed(unittest.TestCase):
                run_quietly = True
                def test_hushed(self): mark("Hushed")
            class Alone(unittest.TestCase):
                run_quietly = True
                run_serially = True
                def test_alone(self): mark("Alone")
        """)
        self.module("test_hush", body + """
            RUN_QUIETLY = True
            class C(unittest.TestCase):
                def test_c(self): mark("C")
            class D(unittest.TestCase):
                def test_d(self): mark("D")
        """)

    def spans(self) -> dict[str, list[float]]:
        spans: dict[str, list[float]] = {}
        for line in self.trace.read_text().splitlines():
            name, _, moment = line.split()
            spans.setdefault(name, []).append(float(moment))
        return spans

    def busiest(self, spans: dict[str, list[float]], name: str) -> int:
        """The most units running at once at any moment ``name`` was."""
        start, end = spans[name]
        moments = [moment for begin, _finish in spans.values() for moment in (begin,) if start <= moment < end]
        return max(sum(1 for begin, finish in spans.values() if begin <= moment < finish) for moment in moments)

    def test_while_a_quiet_unit_runs_no_more_than_its_bound_run_in_all(self) -> None:
        self.quiet_suite()
        listed = self.run_runner("--list").stdout.splitlines()
        # A quiet module is a unit per class, and a class marked both ways runs alone.
        self.assertEqual(listed, ["suite.test_busy.A  1", "suite.test_busy.Alone  (serial)  1", "suite.test_busy.B  1",
                                  "suite.test_busy.E  1", "suite.test_busy.F  1", "suite.test_busy.Hushed  (quiet)  1",
                                  "suite.test_hush.C  (quiet)  1", "suite.test_hush.D  (quiet)  1"])
        run = self.run_runner("--workers", "3", "--quiet-workers", "2")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        spans = self.spans()
        self.assertEqual(set(spans), {"A", "B", "E", "F", "Hushed", "Alone", "C", "D"})
        quiet = ("Hushed", "C", "D")
        self.assertEqual(max(self.busiest(spans, name) for name in quiet), 2, spans)
        self.assertEqual(self.busiest(spans, "Alone"), 1, spans)
        # The quiet units start first, and the others fill in to their own bound once none is running.
        self.assertLess(min(spans[name][0] for name in quiet), min(spans[name][0] for name in "ABEF"), spans)
        self.assertEqual(max(self.busiest(spans, name) for name in "ABEF"), 3, spans)
        self.assertLess(max(spans[name][1] for name in spans if name != "Alone"), spans["Alone"][0], spans)

    def test_a_quiet_unit_runs_alone_with_one_quiet_worker(self) -> None:
        self.quiet_suite()
        run = self.run_runner("--workers", "4", "--quiet-workers", "1")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        spans = self.spans()
        self.assertEqual({name: self.busiest(spans, name) for name in ("Hushed", "C", "D")},
                         {"Hushed": 1, "C": 1, "D": 1}, spans)
        refused = self.run_runner("--quiet-workers", "0")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("--quiet-workers must be at least 1", refused.stderr)

    def test_never_more_than_the_workers_run_beside_a_quiet_unit(self) -> None:
        self.quiet_suite()
        run = self.run_runner("--workers", "2", "--quiet-workers", "5")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        spans = self.spans()
        self.assertEqual(max(self.busiest(spans, name) for name in spans), 2, spans)

    def load_runner(self):
        spec = importlib.util.spec_from_file_location("run_tests_for_direct_git", RUNNER)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        return runner

    def test_where_git_is_only_a_launcher_the_workers_path_starts_with_git_s_own_programs(self) -> None:
        runner = self.load_runner()
        programs = self.root / "git-core"
        programs.mkdir()
        (programs / "git").write_text("#!/bin/sh\n", encoding="utf-8")
        (programs / "git").chmod(0o755)
        asked: list[list[str]] = []

        def exec_path(argv, **_options):
            asked.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout=f"{programs}\n", stderr="")

        before = {"PATH": "/usr/bin:/bin", "KEPT": "as it was"}
        with mock.patch.object(runner.sys, "platform", "darwin"), \
                mock.patch.object(runner.shutil, "which", return_value="/usr/bin/git"), \
                mock.patch.object(runner.subprocess, "run", side_effect=exec_path):
            after = runner._direct_git(dict(before))
            self.assertEqual(after, {"PATH": f"{programs}{os.pathsep}/usr/bin:/bin", "KEPT": "as it was"})
            self.assertEqual(asked, [["/usr/bin/git", "--exec-path"]])
            # No real git where it says: the launcher stays.
            (programs / "git").unlink()
            self.assertEqual(runner._direct_git(dict(before)), before)
        refusals = {
            "another platform": ("linux", "/usr/bin/git", exec_path),
            "a git of the machine's own": ("darwin", "/opt/homebrew/bin/git", exec_path),
            "no git": ("darwin", None, exec_path),
            "a launcher that cannot run": ("darwin", "/usr/bin/git", OSError("no launcher")),
            "a launcher that hangs": ("darwin", "/usr/bin/git", subprocess.TimeoutExpired("git", 60)),
        }
        (programs / "git").write_text("#!/bin/sh\n", encoding="utf-8")
        (programs / "git").chmod(0o755)
        for name, (platform, found, answer) in refusals.items():
            with self.subTest(name), mock.patch.object(runner.sys, "platform", platform), \
                    mock.patch.object(runner.shutil, "which", return_value=found), \
                    mock.patch.object(runner.subprocess, "run", side_effect=answer):
                self.assertEqual(runner._direct_git(dict(before)), before)

    def test_a_worker_runs_the_same_git_as_the_runner(self) -> None:
        self.module("test_git", """
            import os, shutil, subprocess, unittest
            class Git(unittest.TestCase):
                def test_git(self):
                    version = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True).stdout
                    with open(os.environ["TRACE"], "a") as trace:
                        trace.write(f"{shutil.which('git')}\\n{version}")
        """)
        run = self.run_runner()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        found, version = self.trace.read_text().splitlines()
        self.assertEqual(version, subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip())
        if sys.platform == "darwin" and shutil.which("git") == "/usr/bin/git":
            self.assertNotEqual(found, "/usr/bin/git", "the worker still calls the launcher")
        else:
            self.assertEqual(found, shutil.which("git"))

    def interrupted_run(self, *signals: int) -> tuple[subprocess.Popen, str, list[int]]:
        # Each worker ignores SIGTERM and leaves a grandchild in its group that ignores it too.
        self.module("test_slow", """
            import os, signal, subprocess, sys, time, unittest
            def hold():
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                child = subprocess.Popen([sys.executable, "-c",
                    "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])
                with open(os.environ["TRACE"], "a") as trace:
                    trace.write(f"{os.getpid()} {child.pid}\\n")
                time.sleep(60)
            class Slow(unittest.TestCase):
                def test_wait(self): hold()
            class Slower(unittest.TestCase):
                def test_wait(self): hold()
        """)
        runner = subprocess.Popen([sys.executable, "-B", str(RUNNER), "-s", "suite", "-t", ".", "--workers", "2"],
                                  cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env={**os.environ, "TRACE": str(self.trace)})
        recorded: list[int] = []

        def stop_recorded() -> None:
            # Only the workers this test's runner started, by the pids they wrote.
            for pid in recorded:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

        self.addCleanup(stop_recorded)
        self.addCleanup(lambda: runner.poll() is None and runner.kill())
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.trace.exists():
                recorded[:] = [int(word) for word in self.trace.read_text().split()]
                if len(recorded) == 4:
                    break
            time.sleep(0.05)
        self.assertEqual(len(recorded), 4, "both workers and their children started")
        for signum in signals:
            runner.send_signal(signum)
            time.sleep(0.3)
        _, err = runner.communicate(timeout=30)
        return runner, err, recorded

    @unittest.skipIf(os.name == "nt", "signals the runner")
    def test_an_interrupt_stops_every_worker_and_what_it_started_and_exits_non_zero(self) -> None:
        for signals in ((signal.SIGINT,), (signal.SIGINT, signal.SIGINT), (signal.SIGTERM,)):
            with self.subTest(signals=signals):
                if self.trace.exists():
                    self.trace.unlink()
                runner, err, recorded = self.interrupted_run(*signals)
                self.assertEqual(runner.returncode, 130, err)
                self.assertIn("interrupted", err)
                self.assert_gone(recorded)

    @unittest.skipIf(os.name == "nt", "signals the runner")
    def test_a_worker_is_asked_to_stop_before_it_is_killed(self) -> None:
        marker = self.root / "asked"
        self.module("test_polite", f"""
            import os, signal, sys, time, unittest
            class Polite(unittest.TestCase):
                def test_wait(self):
                    def asked(signum, frame):
                        open({str(marker)!r}, "w").write("asked")
                        sys.exit(0)
                    signal.signal(signal.SIGTERM, asked)
                    with open(os.environ["TRACE"], "a") as trace:
                        trace.write(f"{{os.getpid()}}\\n")
                    time.sleep(60)
        """)
        runner = subprocess.Popen([sys.executable, "-B", str(RUNNER), "-s", "suite", "-t", "."], cwd=self.root,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env={**os.environ, "TRACE": str(self.trace)})
        self.addCleanup(lambda: runner.poll() is None and runner.kill())
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (self.trace.exists() and self.trace.read_text().strip()):
            time.sleep(0.05)
        recorded = [int(word) for word in self.trace.read_text().split()]
        runner.send_signal(signal.SIGINT)
        runner.communicate(timeout=30)
        self.assertEqual(runner.returncode, 130)
        self.assertTrue(marker.exists(), "the worker got SIGTERM, and time to act on it, before any SIGKILL")
        self.assert_gone(recorded)

    @unittest.skipIf(os.name == "nt", "signals the runner")
    def test_a_terminate_during_discovery_stops_the_run(self) -> None:
        self.signal_during_discovery(signal.SIGTERM)

    @unittest.skipIf(os.name == "nt", "signals the runner")
    def test_an_interrupt_during_discovery_stops_the_run(self) -> None:
        self.signal_during_discovery(signal.SIGINT)

    def signal_during_discovery(self, signum: int) -> None:
        """The test loader takes an interrupt raised while it imports a module for that module's import failure."""
        started, ran = self.root / "importing", self.root / "ran"
        self.module("test_slow_import", f"""
            import pathlib, time, unittest
            pathlib.Path({str(started)!r}).write_text("x")
            time.sleep(5)
            class Slow(unittest.TestCase):
                def test_runs(self):
                    pathlib.Path({str(ran)!r}).write_text("x")
        """)
        runner = subprocess.Popen([sys.executable, "-B", str(RUNNER), "-s", "suite", "-t", "."], cwd=self.root,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env={**os.environ, "TRACE": str(self.trace)})
        self.addCleanup(lambda: runner.poll() is None and runner.kill())
        deadline = time.monotonic() + 60
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(started.exists(), "discovery reached the slow import")
        runner.send_signal(signum)
        out, err = runner.communicate(timeout=60)
        self.assertEqual(runner.returncode, 130, out + err)
        self.assertIn("interrupted", err)
        self.assertFalse(ran.exists(), "no test ran after the interrupt")
        self.assertNotIn("unit timeout:", out, "the run never started")

    def test_an_interrupt_raised_outside_an_import_during_discovery_exits_130(self) -> None:
        spec = importlib.util.spec_from_file_location("run_tests_for_discovery_interrupt", RUNNER)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        self.passing_suite()
        errors = io.StringIO()
        with mock.patch.object(runner, "discover_units", side_effect=KeyboardInterrupt), \
                mock.patch.object(runner, "run_suite") as run_suite, contextlib.redirect_stderr(errors):
            code = runner.main(["-s", str(self.root / "suite"), "-t", str(self.root)])
        self.assertEqual(code, 130)
        self.assertIn("interrupted", errors.getvalue())
        run_suite.assert_not_called()

    def assert_gone(self, recorded: list[int]) -> None:
        for pid in recorded:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail(f"worker {pid} survived the interrupt")

    def test_timings_are_written_per_unit_module_and_test(self) -> None:
        self.passing_suite()
        run = self.run_runner("--timings", "timings.json")
        self.assertEqual(run.returncode, 0, run.stdout)
        timings = json.loads((self.root / "timings.json").read_text())
        self.assertEqual(timings["tests"], 7)
        self.assertEqual(set(timings["units"]), {"suite.test_alpha.One", "suite.test_alpha.Two", "suite.test_beta"})
        ordered = self.run_runner("--timings-from", "timings.json", "--workers", "1")
        self.assertEqual(ordered.returncode, 0, ordered.stdout)
        self.assertEqual(set(timings["modules"]), {"suite.test_alpha", "suite.test_beta"})
        self.assertIn("suite.test_alpha.One.test_a", timings["testTimes"])
        self.assertGreater(timings["total"], 0)

    def test_a_module_s_own_load_tests_builds_the_tests_it_runs(self) -> None:
        self.passing_suite()
        self.module("test_params", """
            import unittest
            class P(unittest.TestCase):
                def __init__(self, n=0):
                    super().__init__("runTest")
                    self.n = n
                def runTest(self):
                    self.assertLess(self.n, 10)
            def load_tests(loader, tests, pattern):
                return unittest.TestSuite(P(n) for n in (1, 2, 30))
        """)
        run, reference = self.run_runner("--workers", "3"), self.discover()
        self.assertEqual((run.returncode, reference.returncode), (1, 1), run.stdout)
        self.assertIn("Ran 10 tests", run.stdout)
        self.assertIn("FAILED (failures=1", run.stdout, "only the n=30 instance fails, as under discover")

    def test_a_module_that_skips_itself_on_import_is_one_skipped_test(self) -> None:
        self.passing_suite()
        self.module("test_skipped", """
            import unittest
            raise unittest.SkipTest("not on this platform")
        """)
        run, reference = self.run_runner(), self.discover()
        self.assertEqual((run.returncode, reference.returncode), (0, 0), run.stdout + run.stderr)
        self.assertIn("Ran 8 tests", run.stdout)
        self.assertIn("OK (skipped=2, expected failures=1)", run.stdout)

    def test_the_run_leaves_no_files_behind(self) -> None:
        self.passing_suite()
        scratch = self.root / "tmp"
        scratch.mkdir()
        run = subprocess.run([sys.executable, "-B", str(RUNNER), "-s", "suite", "-t", "."], cwd=self.root,
                             capture_output=True, text=True, timeout=120, env={**os.environ, "TMPDIR": str(scratch)})
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertEqual(list(scratch.iterdir()), [])

    def test_a_relative_top_level_directory_means_the_same_to_every_worker(self) -> None:
        nested = self.root / "sub"
        nested.mkdir()
        (self.root / "suite").rename(nested / "suite")
        self.module = lambda name, body: (nested / "suite" / f"{name}.py").write_text(textwrap.dedent(body))
        self.passing_suite()
        run = subprocess.run([sys.executable, "-B", str(RUNNER), "-s", "sub/suite", "-t", "sub"], cwd=self.root,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Ran 7 tests", run.stdout)

    def test_entering_the_test_package_stops_bytecode_for_it_and_what_it_starts(self) -> None:
        environment = {key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"}
        script = "import os, sys, tests; print(sys.dont_write_bytecode, os.environ.get('PYTHONDONTWRITEBYTECODE'))"
        done = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=60, env=environment)
        self.assertEqual(done.stdout.split(), ["True", "1"], done.stderr)

    def test_no_tests_is_no_pass(self) -> None:
        self.passing_suite()
        run, reference = self.run_runner("-p", "nothing_*.py"), subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "suite", "-t", ".", "-p", "nothing_*.py"],
            cwd=self.root, capture_output=True, text=True, timeout=120)
        # unittest itself reports an empty run as a failure only from Python 3.12.
        self.assertEqual((run.returncode, reference.returncode), (5, 5 if sys.version_info >= (3, 12) else 0), run.stdout)
        self.assertIn("NO TESTS RAN", run.stdout)

    def test_a_serial_module_and_a_class_with_only_a_teardown_stay_whole(self) -> None:
        self.module("test_serial_module", """
            import unittest
            RUN_SERIALLY = True
            class First(unittest.TestCase):
                def test_a(self): pass
                def test_b(self): pass
            class Second(unittest.TestCase):
                def test_c(self): pass
        """)
        self.module("test_teardown", """
            import unittest
            class Closing(unittest.TestCase):
                @classmethod
                def tearDownClass(cls): pass
                def test_x(self): pass
                def test_y(self): pass
        """)
        listed = self.run_runner("--list", "--chunk", "1").stdout.splitlines()
        self.assertIn("suite.test_serial_module  (serial)  3", listed)
        self.assertIn("suite.test_teardown.Closing  2", listed)

    def test_timings_from_start_the_slowest_unit_first(self) -> None:
        self.passing_suite()
        earlier = {"testTimes": {"suite.test_beta.Four.test_g": 50.0, "suite.test_alpha.One.test_a": 1.0}}
        (self.root / "earlier.json").write_text(json.dumps(earlier))
        run = self.run_runner("--timings-from", "earlier.json", "--workers", "1")
        self.assertEqual(run.returncode, 0, run.stdout)
        started = [line.split()[1] for line in run.stdout.splitlines() if line.startswith(("ok", "FAIL"))]
        self.assertEqual(started[:2], ["suite.test_beta", "suite.test_alpha.One"], run.stdout)

    @unittest.skipIf(os.name == "nt", "signal masks are POSIX")
    def test_a_test_runs_with_no_signal_blocked_and_can_stop_its_own_child(self) -> None:
        self.module("test_signals", """
            import signal, subprocess, sys, unittest
            class Signals(unittest.TestCase):
                def test_nothing_blocked(self):
                    blocked = signal.pthread_sigmask(signal.SIG_BLOCK, set())
                    self.assertFalse({signal.SIGINT, signal.SIGTERM} & blocked, blocked)
                def test_a_child_stops_when_asked(self):
                    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
                    child.terminate()
                    self.assertEqual(child.wait(timeout=10), -signal.SIGTERM)
        """)
        run = self.run_runner()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    @unittest.skipIf(os.name == "nt", "process groups are POSIX here")
    def test_a_hung_unit_times_out_named_and_the_rest_of_the_run_completes(self) -> None:
        self.passing_suite()
        self.module("test_hang", """
            import os, signal, subprocess, sys, time, unittest
            class Hang(unittest.TestCase):
                def test_forever(self):
                    # A child that ignores SIGTERM, in the worker's group: both must be gone afterwards.
                    child = subprocess.Popen([sys.executable, "-c",
                        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"])
                    with open(os.environ["TRACE"], "a") as trace:
                        trace.write(f"{os.getpid()} {child.pid}\\n")
                    time.sleep(120)
        """)
        started = time.monotonic()
        run = self.run_runner("--workers", "2", "--unit-timeout", "3")
        elapsed = time.monotonic() - started
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("TIMED OUT: suite.test_hang.Hang after 3s", run.stdout)
        self.assertIn("suite.test_hang.Hang.test_forever", run.stdout)
        self.assertIn("timed out=1", run.stdout)
        self.assertIn("Ran 7 tests", run.stdout, "every other unit completed")
        self.assertLess(elapsed, 30)
        self.assert_gone([int(word) for word in self.trace.read_text().split()])

    @unittest.skipIf(os.name == "nt", "process groups are POSIX here")
    def test_a_run_whose_only_unit_hangs_times_out_named_by_its_tests(self) -> None:
        self.module("test_only", """
            import sys, time, unittest
            class Hang(unittest.TestCase):
                def test_first(self):
                    print("about to hang", file=sys.stderr, flush=True)
                    time.sleep(120)
                def test_second(self):
                    pass
        """)
        run = self.run_runner("--unit-timeout", "2", "--chunk", "1")
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertNotIn("NO TESTS RAN", run.stdout)
        self.assertIn("TIMED OUT suite.test_only.Hang[1/2] (after 2s)", run.stdout, "the live line")
        self.assertIn("TIMED OUT: suite.test_only.Hang[1/2] after 2s", run.stdout)
        self.assertIn("  suite.test_only.Hang.test_first", run.stdout, "named by its test, not its class")
        self.assertIn("time.sleep(120)", run.stdout, "with its log, which shows where it hung")
        self.assertIn("Ran 1 test", run.stdout)

    @unittest.skipIf(os.name == "nt", "signals the runner")
    def test_an_interrupt_while_a_unit_is_being_stopped_still_stops_the_run(self) -> None:
        self.module("test_stubborn", """
            import os, signal, time, unittest
            class Stubborn(unittest.TestCase):
                def test_hang(self):
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    with open(os.environ["TRACE"], "a") as trace:
                        trace.write(f"{os.getpid()}\\n")
                    time.sleep(120)
            class Busy(unittest.TestCase):
                def test_wait(self):
                    time.sleep(30)
        """)
        runner = subprocess.Popen([sys.executable, "-B", str(RUNNER), "-s", "suite", "-t", ".", "--workers", "2",
                                   "--unit-timeout", "1"], cwd=self.root, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, env={**os.environ, "TRACE": str(self.trace)})
        self.addCleanup(lambda: runner.poll() is None and runner.kill())
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (self.trace.exists() and self.trace.read_text().strip()):
            time.sleep(0.05)
        recorded = [int(word) for word in self.trace.read_text().split()]
        # The unit's limit passes at one second; its worker ignores SIGTERM, so its stop lasts the grace.
        time.sleep(2.2)
        started = time.monotonic()
        runner.send_signal(signal.SIGINT)
        _, err = runner.communicate(timeout=30)
        self.assertEqual(runner.returncode, 130, err)
        self.assertLess(time.monotonic() - started, 15, "not after the rest of the run")
        self.assert_gone(recorded)

    def test_the_unit_timeout_defaults_from_earlier_timings_and_the_option_overrides_it(self) -> None:
        self.passing_suite()
        slow = {"testTimes": {"suite.test_beta.Four.test_g": 100.0, "suite.test_beta.Three.test_f": 20.0,
                              "suite.test_alpha.One.test_a": 1.0}}
        # A unit whose recorded wall time, fixtures included, exceeds its tests' sum.
        setup = {"testTimes": {"suite.test_alpha.One.test_a": 1.0}, "units": {"suite.test_alpha.One": 90.0}}
        (self.root / "setup.json").write_text(json.dumps(setup))
        fast = {"testTimes": {"suite.test_alpha.One.test_a": 1.0}}
        (self.root / "slow.json").write_text(json.dumps(slow))
        (self.root / "fast.json").write_text(json.dumps(fast))
        (self.root / "nothing.json").write_text(json.dumps({"testTimes": {"other.module.Test.test_x": 99.0}}))
        # A negative time is dropped, not summed against the rest of its unit.
        (self.root / "negative.json").write_text(json.dumps(
            {"testTimes": {"suite.test_alpha.One.test_a": -500.0, "suite.test_alpha.One.test_b": 100.0}}))
        # Only unusable times for current tests: boolean, too large to multiply.
        (self.root / "boolean.json").write_text(json.dumps({"testTimes": {"suite.test_alpha.One.test_a": True}}))
        (self.root / "huge.json").write_text(json.dumps({"units": {"suite.test_beta": 1e308}}))
        (self.root / "garbled.json").write_text("not json")
        for extra, expected in ((("--timings-from", "slow.json"), "unit timeout: 600s"),
                                (("--timings-from", "fast.json"), "unit timeout: 300s"),
                                (("--timings-from", "slow.json", "--unit-timeout", "42"), "unit timeout: 42s"),
                                ((), "unit timeout: 1800s"),
                                (("--unit-timeout", "0"), "unit timeout: none"),
                                (("--timings-from", "missing.json"),
                                 "unit timeout: 1800s (default: missing.json could not be read)"),
                                (("--timings-from", "garbled.json"),
                                 "unit timeout: 1800s (default: garbled.json could not be read)"),
                                (("--timings-from", "negative.json"), "unit timeout: 500s"),
                                (("--timings-from", "boolean.json"), "unit timeout: 1800s (default: boolean.json"),
                                (("--timings-from", "huge.json"), "unit timeout: 1800s (default: huge.json"),
                                (("--timings-from", "setup.json"), "unit timeout: 450s"),
                                (("--timings-from", "nothing.json"),
                                 "unit timeout: 1800s (default: nothing.json records no usable time")):
            with self.subTest(extra=extra):
                run = self.run_runner(*extra)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn(expected, run.stdout)
                self.assertEqual(run.stdout.count("unit timeout:"), 1, "one line states the limit")
        for bad in ("-1", "inf", "nan"):
            self.assertEqual(self.run_runner("--unit-timeout", bad).returncode, 2, bad)

    def test_workers_below_one_are_a_usage_error(self) -> None:
        self.passing_suite()
        self.assertEqual(self.run_runner("--workers", "0").returncode, 2)


if __name__ == "__main__":
    unittest.main()
