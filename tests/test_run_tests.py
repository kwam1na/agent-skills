from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

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

    @unittest.skipIf(os.name == "nt", "signals the runner with SIGINT")
    def test_an_interrupt_stops_every_worker_and_exits_non_zero(self) -> None:
        self.module("test_slow", """
            import os, time, unittest
            class Slow(unittest.TestCase):
                def test_wait(self):
                    with open(os.environ["TRACE"], "a") as trace:
                        trace.write(f"{os.getpid()}\\n")
                    time.sleep(60)
            class Slower(unittest.TestCase):
                def test_wait(self):
                    with open(os.environ["TRACE"], "a") as trace:
                        trace.write(f"{os.getpid()}\\n")
                    time.sleep(60)
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
                recorded = [int(line) for line in self.trace.read_text().split()]
                if len(recorded) == 2:
                    break
            time.sleep(0.05)
        self.assertEqual(len(recorded), 2, "both workers started")
        runner.send_signal(signal.SIGINT)
        _, err = runner.communicate(timeout=20)
        self.assertEqual(runner.returncode, 130, err)
        self.assertIn("interrupted", err)
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

    def test_workers_below_one_are_a_usage_error(self) -> None:
        self.passing_suite()
        self.assertEqual(self.run_runner("--workers", "0").returncode, 2)


if __name__ == "__main__":
    unittest.main()
