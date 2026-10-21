from __future__ import annotations

import contextlib
import copy
import datetime
import hashlib
import http.client
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from agent_skills.validate import LEAKAGE_PATTERNS


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/orchestrate-work/scripts"
STATE_TOOL = SCRIPTS / "delivery_state.py"
WRAPPER = SCRIPTS / "render_state.py"
HEAD_A = "a" * 40

HOSTILE = (
    "</script><img src=x onerror=alert(1)> [click](javascript:alert(1)) <b>bold</b>\u2028next\u2029line"
    " https://example.test & <!-- *em* _u_ `code` # ! | ~\n    indented"
)


def run(argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, encoding="utf-8", check=False)


def state_tool(directory: Path, *args: str, stdin: str | None = None) -> dict:
    result = run([sys.executable, "-B", str(STATE_TOOL), "--dir", str(directory), *args], stdin)
    # An encoding failure on stdout surfaces as empty output, not as JSON.
    assert result.stdout, f"state tool printed nothing: {result.stderr}"
    document = json.loads(result.stdout)
    if result.returncode != 0:
        raise AssertionError(f"state tool refused {args}: {document}")
    return document


def wrapper(directory: Path, *args: str, stdin: str | None = None) -> tuple[int, dict]:
    result = run([sys.executable, "-B", str(WRAPPER), "--dir", str(directory), *args], stdin)
    assert result.stdout, f"the wrapper printed nothing: {result.stderr}"
    return result.returncode, json.loads(result.stdout)


def link(test: unittest.TestCase, path: Path, target: Path) -> None:
    """Create a link, or skip where the platform does not let this user."""
    try:
        path.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as error:
        test.skipTest(f"links are not available here: {error}")


def python_argv(source: str, folder: Path, name: str) -> list[str]:
    script = folder / f"{name}.py"
    script.write_text(source, encoding="utf-8")
    return [sys.executable, "-B", str(script)]


# Names the point at which the wrapper delivers a signal to itself, and the
# signal: "<point>:<SIGNAME>".
SEAM = "ORCHESTRATE_RENDER_INTERRUPT_AT"


def _processes(script: Path) -> list[int]:
    """Every process running ``script``, a path unique to one test's own folder."""
    listing = subprocess.run(["ps", "-A", "-o", "pid=,command="], capture_output=True, text=True, check=False).stdout
    return [int(line.split(None, 1)[0]) for line in listing.splitlines() if str(script) in line]


def _wait_for_none(script: Path, seconds: float = 3.0) -> list[int]:
    """Processes still running ``script`` after a bounded wait; empty when all stopped."""
    deadline = time.monotonic() + seconds
    while True:
        found = _processes(script)
        if not found or time.monotonic() >= deadline:
            return found
        time.sleep(0.05)


def _kill_all(pids: list[int]) -> None:
    for pid in pids:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)

class DeliveryStateFixture(unittest.TestCase):
    """A real state directory, written only through the state tool."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.state_dir = self.root / "state"
        self.tools = self.root / "tools"
        self.tools.mkdir()
        state_tool(self.state_dir, "init", "--headline", HOSTILE)
        version = state_tool(self.state_dir, "show")["state"]["version"]
        changes = {
            "lanes": [
                {
                    "id": "lane-1",
                    "title": HOSTILE,
                    "focus": "Renderer [docs](https://example.test/x)",
                    "state": "working",
                    "waitingOn": None,
                    "since": "2026-10-20T10:00:00Z",
                }
            ],
            "queue": [
                {
                    "order": 1,
                    "subject": "change-1",
                    "title": HOSTILE,
                    "lane": "lane-1",
                    "stage": "queued",
                    "head": HEAD_A,
                    "syncedHead": None,
                    "checks": [],
                    "leadInputs": [],
                    "reportedRounds": 2,
                    "runId": None,
                }
            ],
            "finish": [{"id": "finish-1", "text": HOSTILE, "met": False}],
            "followups": [{"id": "follow-1", "title": "Later <i>work</i> for the caf\u00e9 \u2713", "reference": "ticket-9"}],
            "extensions": {"adopter": {"note": HOSTILE, "nested": [1, {"a": None}]}},
        }
        state_tool(self.state_dir, "patch", "--writer", "lead", "--version", str(version), "--patch", json.dumps(changes))
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", HOSTILE, "--lane", "lane-1")
        # What a renderer reads: the state tool's show, without its publication status.
        self.document = {key: value for key, value in state_tool(self.state_dir, "show").items() if key != "publication"}

    def config(self, renderers: list[dict] | None = None, publishers: list[dict] | None = None) -> str:
        return json.dumps({"directory": "state", "renderers": renderers or [], "publishers": publishers or []})

    def capture_renderer(self, name: str) -> dict:
        """An adopter renderer that echoes its stdin as its view."""
        return {
            "id": name,
            "argv": python_argv("import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n", self.tools, name),
            "output": f"{name}.json",
        }


def _load_wrapper():
    # Importing the scripts here must not leave bytecode beside them.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("render_state_under_test", WRAPPER)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))
        sys.dont_write_bytecode = previous


render_state = _load_wrapper()
# The state tool, as the wrapper imported it.
delivery_state = sys.modules["delivery_state"]

# A command that starts a child holding its {held} open, then exits straight
# away. The child outlives it by far more than any test waits, and records its
# pid so the test can stop it.
LINGERING = """import os, subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"],
                         stdout={stdout}, stderr={stderr}, stdin=subprocess.DEVNULL)
open({pids!r}, "a").write(f"{{child.pid}}\\n")
{after}
"""


def _stop_recorded(pids: Path) -> None:
    if not pids.exists():
        return
    for line in pids.read_text().split():
        with contextlib.suppress(OSError, ValueError):
            os.kill(int(line), signal.SIGTERM)


class BoundedCommandTests(DeliveryStateFixture):
    def setUp(self) -> None:
        super().setUp()
        self.pids = self.root / "children.pids"
        self.addCleanup(_stop_recorded, self.pids)

    def lingering(self, after: str, *, stdout: str = "sys.stdout", stderr: str = "sys.stderr") -> str:
        return LINGERING.format(after=after, stdout=stdout, stderr=stderr, pids=str(self.pids), held="output")

    def test_an_oversized_view_is_truncated_recorded_and_never_written(self) -> None:
        limit = render_state.VIEW_LIMIT_BYTES
        flood = {
            "id": "flood",
            "argv": python_argv(
                f"import sys\nsys.stdin.buffer.read()\nsys.stdout.buffer.write(b'x' * {limit + 1})\n",
                self.tools, "flood",
            ),
            "output": "flood.txt",
        }
        within = {
            "id": "within",
            "argv": python_argv(
                f"import sys\nsys.stdin.buffer.read()\nsys.stdout.buffer.write(b'y' * {limit})\n",
                self.tools, "within",
            ),
            "output": "within.txt",
        }
        code, result = wrapper(self.state_dir, "--state-config", self.config([flood, within]))
        self.assertEqual(code, 1, result)
        by_id = {entry["id"]: entry for entry in result["renderers"]}
        self.assertEqual(by_id["flood"]["ok"], False)
        self.assertTrue(by_id["flood"]["truncated"])
        self.assertIn(str(limit), by_id["flood"]["error"])
        self.assertFalse((self.state_dir / "flood.txt").exists())
        self.assertTrue(by_id["within"]["ok"], by_id["within"])
        self.assertNotIn("truncated", by_id["within"])
        self.assertEqual((self.state_dir / "within.txt").stat().st_size, limit)

    def test_capture_never_holds_more_than_its_limit(self) -> None:
        argv = python_argv(
            "import sys\nsys.stdout.buffer.write(b'head')\nsys.stderr.buffer.write(b'first')\n"
            "for _ in range(64):\n    sys.stdout.buffer.write(b'z' * 65536)\n"
            "    sys.stderr.buffer.write(b'e' * 65536)\n"
            "sys.stdout.buffer.write(b'tail')\nsys.stderr.buffer.write(b'last')\n",
            self.tools, "torrent",
        )
        with mock.patch.object(render_state, "VIEW_LIMIT_BYTES", 1000):
            outcome = render_state._execute(argv, b"{}", 30)
        self.assertEqual(outcome.exit_code, 0)
        self.assertTrue(outcome.truncated)
        # Stdout keeps its first bytes; stderr keeps its last.
        self.assertEqual(len(outcome.stdout), 1000)
        self.assertTrue(outcome.stdout.startswith(b"head"))
        self.assertLessEqual(len(outcome.stderr), render_state.ERROR_BYTES)
        self.assertTrue(outcome.stderr.endswith(b"last"))

    def test_a_failing_command_keeps_only_the_tail_of_its_stderr(self) -> None:
        noisy = {
            "id": "noisy",
            "argv": python_argv(
                "import sys\nsys.stderr.write('x' * 4_000_000 + ' the real reason')\nsys.exit(4)\n",
                self.tools, "noisy",
            ),
        }
        code, result = wrapper(self.state_dir, "--state-config", self.config([], [noisy]))
        self.assertEqual(code, 0, result)
        entry = result["publishers"][0]
        self.assertEqual((entry["ok"], entry["exitCode"]), (False, 4))
        self.assertTrue(entry["error"].endswith("the real reason"))
        self.assertLessEqual(len(entry["error"]), render_state.ERROR_LIMIT)

    def test_a_publisher_whose_child_keeps_its_output_open_is_ok_when_it_exits_cleanly(self) -> None:
        lingering = {
            "id": "lingering",
            "argv": python_argv(self.lingering("sys.stdin.buffer.read()"), self.tools, "lingering"),
            "timeoutSeconds": 10,
        }
        started = time.monotonic()
        code, result = wrapper(self.state_dir, "--state-config", self.config([], [lingering]))
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["publishers"], [{"exitCode": 0, "id": "lingering", "ok": True}])

    def test_a_renderer_whose_child_keeps_its_output_open_fails_without_writing(self) -> None:
        lingering = {
            "id": "lingering",
            "argv": python_argv(
                self.lingering("sys.stdin.buffer.read()\nsys.stdout.write('partial')\nsys.stdout.flush()"),
                self.tools, "lingering",
            ),
            "output": "lingering.txt",
            "timeoutSeconds": 10,
        }
        started = time.monotonic()
        code, result = wrapper(self.state_dir, "--state-config", self.config([lingering]))
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(code, 1, result)
        entry = result["renderers"][0]
        self.assertEqual(entry["ok"], False)
        self.assertIn("still open", entry["error"])
        self.assertFalse((self.state_dir / "lingering.txt").exists())
        if os.name != "nt":
            # The child holding the renderer's stdout was stopped with its group.
            deadline = time.monotonic() + 5
            alive = True
            while alive and time.monotonic() < deadline:
                alive = False
                for line in self.pids.read_text().split():
                    try:
                        os.kill(int(line), 0)
                        alive = True
                    except ProcessLookupError:
                        pass
                time.sleep(0.05)
            self.assertFalse(alive, "the renderer's lingering child was not stopped")

    def test_a_renderer_whose_child_holds_only_stderr_still_writes_its_view(self) -> None:
        logger = {
            "id": "logger",
            "argv": python_argv(
                self.lingering("sys.stdin.buffer.read()\nsys.stdout.write('complete view')",
                               stdout="subprocess.DEVNULL"),
                self.tools, "logger",
            ),
            "output": "logger.txt",
            "timeoutSeconds": 10,
        }
        code, result = wrapper(self.state_dir, "--state-config", self.config([logger]))
        self.assertEqual(code, 0, result)
        self.assertEqual((self.state_dir / "logger.txt").read_text(encoding="utf-8"), "complete view")

    def test_a_command_that_never_reads_its_input_or_exits_still_times_out(self) -> None:
        stuck = python_argv("import time\ntime.sleep(30)\n", self.tools, "stuck")
        started = time.monotonic()
        # Larger than any pipe buffer: a blocking write would never reach the timeout.
        outcome = render_state._execute(stuck, b"x" * 4_000_000, 1)
        self.assertLess(time.monotonic() - started, 1 + render_state.DRAIN_SECONDS + 3)
        self.assertEqual((outcome.exit_code, outcome.error), (None, "timed out after 1s"))

    # Points the wrapper passes in order, and whether its renderer is running
    # there. The seam delivers the signal at exactly that point, so no test
    # depends on how fast the machine is.
    INTERRUPT_POINTS = (
        ("loading", False),
        ("start", False),
        ("spawning", True),
        ("spawn", True),
        ("wait", True),
        ("drain", False),
        ("finished", False),
        ("exit", False),
    )
    # One signal, then two in quick succession: a repeated interrupt, or a
    # supervisor that terminates after interrupting.
    SIGNALS = (
        (signal.SIGINT,), (signal.SIGTERM,), (signal.SIGHUP,),
        (signal.SIGINT, signal.SIGTERM), (signal.SIGTERM, signal.SIGINT),
    )

    @unittest.skipIf(os.name == "nt", "POSIX signals")
    def test_an_interrupt_at_any_point_exits_non_zero_and_stops_its_commands(self) -> None:
        for point, running in self.INTERRUPT_POINTS:
            for numbers in self.SIGNALS:
                names = ",".join(number.name for number in numbers)
                first = numbers[0]
                with self.subTest(point=point, signals=names):
                    # A unique script name finds the command, and anything it
                    # started, however early it was stopped.
                    tag = f"probe-{point}-{names.replace(',', '-')}"
                    pause = 60 if running else 0
                    renderer = {
                        "id": "probe",
                        "argv": python_argv(
                            "import sys, time\n"
                            "sys.stdin.buffer.read()\n"
                            f"time.sleep({pause})\n"
                            "sys.stdout.write('view')\n",
                            self.tools, tag,
                        ),
                        "output": "probe.txt",
                        "timeoutSeconds": 120,
                    }
                    environment = dict(os.environ, **{SEAM: f"{point}:{names}"})
                    process = subprocess.run(
                        [sys.executable, "-B", str(WRAPPER), "--dir", str(self.state_dir),
                         "--state-config", self.config([renderer])],
                        env=environment, capture_output=True, timeout=60, check=False,
                    )
                    survivors = _wait_for_none(self.tools / f"{tag}.py")
                    self.addCleanup(_kill_all, survivors)
                    self.assertEqual(survivors, [], "a command kept running after the wrapper stopped")
                    self.assertNotEqual(process.returncode, 0, process.stderr)
                    if point == "exit":
                        # Both signals are held pending, and a pending set has
                        # no order: either one decides the exit code.
                        expected = tuple(128 + number for number in numbers)
                    else:
                        expected = (128 + first,)
                    self.assertIn(process.returncode, expected, process.stderr)
                    if point != "exit":
                        # Once the result is printed it cannot change; only
                        # the exit code still reports the interrupt.
                        result = json.loads(process.stdout)
                        self.assertEqual((result["ok"], result["code"], result["signal"]),
                                         (False, "interrupted", first.name))

    @unittest.skipIf(os.name == "nt", "POSIX signals")
    def test_a_second_signal_while_stopping_a_command_still_stops_it(self) -> None:
        for point in ("spawning", "spawn", "wait"):
            for first, second in ((signal.SIGINT, signal.SIGTERM), (signal.SIGTERM, signal.SIGINT)):
                with self.subTest(point=point, first=first.name, second=second.name):
                    tag = f"stopping-{point}-{first.name}-{second.name}"
                    renderer = {
                        "id": "probe",
                        "argv": python_argv("import sys, time\nsys.stdin.buffer.read()\ntime.sleep(60)\n", self.tools, tag),
                        "output": "probe.txt",
                        "timeoutSeconds": 120,
                    }
                    environment = dict(os.environ, **{SEAM: f"{point}:{first.name};stopping:{second.name}"})
                    process = subprocess.run(
                        [sys.executable, "-B", str(WRAPPER), "--dir", str(self.state_dir),
                         "--state-config", self.config([renderer])],
                        env=environment, capture_output=True, timeout=60, check=False,
                    )
                    survivors = _wait_for_none(self.tools / f"{tag}.py")
                    self.addCleanup(_kill_all, survivors)
                    self.assertEqual(survivors, [], "a command kept running after the wrapper stopped")
                    self.assertEqual(process.returncode, 128 + first, process.stderr)
                    self.assertEqual(json.loads(process.stdout)["signal"], first.name)

    @unittest.skipIf(os.name == "nt", "POSIX signals")
    def test_an_interrupt_while_a_publisher_runs_stops_it(self) -> None:
        tag = "publisher-wait"
        publisher = {"id": "store", "argv": python_argv("import sys, time\nsys.stdin.buffer.read()\ntime.sleep(60)\n", self.tools, tag),
                     "timeoutSeconds": 120}
        environment = dict(os.environ, **{SEAM: "wait:SIGTERM"})
        process = subprocess.run(
            [sys.executable, "-B", str(WRAPPER), "--dir", str(self.state_dir), "--state-config", self.config(publishers=[publisher])],
            env=environment, capture_output=True, timeout=60, check=False,
        )
        survivors = _wait_for_none(self.tools / f"{tag}.py")
        self.addCleanup(_kill_all, survivors)
        self.assertEqual(survivors, [], "a publisher kept running after the wrapper stopped")
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, process.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX signals")
    def test_a_swallowed_raise_still_ends_the_wait(self) -> None:
        tag = "swallowed-wait"
        renderer = {"id": "probe", "argv": python_argv("import sys, time\nsys.stdin.buffer.read()\ntime.sleep(60)\n", self.tools, tag),
                    "output": "probe.txt", "timeoutSeconds": 120}
        environment = dict(os.environ, **{SEAM: "wait:SIGINT!"})
        process = subprocess.run(
            [sys.executable, "-B", str(WRAPPER), "--dir", str(self.state_dir), "--state-config", self.config([renderer])],
            env=environment, capture_output=True, timeout=60, check=False,
        )
        survivors = _wait_for_none(self.tools / f"{tag}.py")
        self.addCleanup(_kill_all, survivors)
        self.assertEqual(survivors, [], "a command kept running after the wrapper stopped")
        self.assertEqual(process.returncode, 128 + signal.SIGINT, process.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX signals")
    def test_an_interrupt_the_parent_ignores_stays_ignored(self) -> None:
        renderer = self.capture_renderer("echo")
        environment = dict(os.environ, **{SEAM: "wait:SIGINT"})
        process = subprocess.run(
            [sys.executable, "-B", str(WRAPPER), "--dir", str(self.state_dir), "--state-config", self.config([renderer])],
            env=environment, capture_output=True, timeout=60, check=False,
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(process.stdout)["ok"])

    @unittest.skipIf(os.name == "nt", "POSIX signals")
    def test_without_an_interrupt_the_seam_changes_nothing(self) -> None:
        renderer = self.capture_renderer("echo")
        environment = dict(os.environ, **{SEAM: "nowhere:SIGINT"})
        process = subprocess.run(
            [sys.executable, "-B", str(WRAPPER), "--dir", str(self.state_dir), "--state-config", self.config([renderer])],
            env=environment, capture_output=True, timeout=60, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(process.stdout)["ok"])

    def test_a_command_that_never_reads_its_input_still_completes(self) -> None:
        deaf = {
            "id": "deaf",
            "argv": python_argv("import sys\nsys.stdout.write('done')\n", self.tools, "deaf"),
            "output": "deaf.txt",
            "timeoutSeconds": 10,
        }
        big = "x" * 2_000_000
        version = state_tool(self.state_dir, "show")["state"]["version"]
        state_tool(self.state_dir, "patch", "--writer", "lead", "--version", str(version),
                   "--patch", "-", stdin=json.dumps({"extensions": {"big": big}}))
        code, result = wrapper(self.state_dir, "--state-config", self.config([deaf]))
        self.assertEqual(code, 0, result)
        self.assertEqual((self.state_dir / "deaf.txt").read_text(encoding="utf-8"), "done")

    def test_a_timeout_stops_the_command_and_its_children(self) -> None:
        marker = self.root / "child-survived"
        child = python_argv(
            f"import pathlib, time\ntime.sleep(3)\npathlib.Path({str(marker)!r}).write_text('x')\n",
            self.tools, "child",
        )
        spawner = {
            "id": "spawner",
            "argv": python_argv(
                "import subprocess, sys, time\n"
                f"subprocess.Popen({child!r}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,"
                " stdin=subprocess.DEVNULL)\n"
                "time.sleep(30)\n",
                self.tools, "spawner",
            ),
            "timeoutSeconds": 1,
        }
        code, result = wrapper(self.state_dir, "--state-config", self.config([], [spawner]))
        self.assertEqual(result["publishers"][0]["error"], "timed out after 1s")
        time.sleep(4)
        self.assertFalse(marker.exists(), "the timed-out command's child kept running")

    def test_output_is_ascii_json_whatever_the_console_encoding(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", str(WRAPPER), "--dir", str(self.state_dir), "--state-config", "-"],
            input=("\ufeff" + self.config([{"id": "summary", "builtin": "markdown", "output": "caf\u00e9.md"}]))
            .encode("utf-8"),
            capture_output=True, env={**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}, check=False,
        )
        self.assertTrue(result.stdout, result.stderr.decode("utf-8", "replace"))
        self.assertTrue(result.stdout.isascii(), result.stdout)
        document = json.loads(result.stdout.decode("ascii"))
        self.assertEqual((result.returncode, document["renderers"][0]["output"]), (0, "caf\u00e9.md"), document)


# A fixed state and journal, written directly so every timestamp is fixed:
# the built-in views of it are pinned byte for byte.
FIXED_STATE = {
    "schemaVersion": "delivery-state/1",
    "version": 7,
    "meta": {
        "headline": "Pinned delivery " + HOSTILE,
        "updatedAt": "2026-10-20T12:00:00Z",
        "baseHead": HEAD_A,
        "releasedHead": None,
        "checking": {
            "now": {"subject": "change-1", "title": "Change one", "startedAt": "2026-10-20T11:59:00Z",
                    "steps": [{"label": "sync", "state": "passed"}, {"label": "unit", "state": "running"}]},
            "next": ["change-2"],
        },
    },
    "lanes": [
        {"id": "lane-1", "title": HOSTILE, "focus": "views", "state": "working", "waitingOn": None,
         "since": "2026-10-20T10:00:00Z"},
        {"id": "lane-2", "title": "Runner", "focus": "queue", "state": "blocked", "waitingOn": "lead",
         "since": "2026-10-20T10:30:00Z"},
    ],
    "queue": [
        {"order": 1, "subject": "change-1", "title": "Change one", "lane": "lane-1", "stage": "checking",
         "head": HEAD_A, "syncedHead": "b" * 40, "leadInputs": ["codegen"], "reportedRounds": 2,
         "runId": "run-1", "checks": [{"id": "unit", "state": "running", "detail": "3 of 9"}],
         "changeRequest": "change request 7"},
        {"order": 2, "subject": "change-2", "title": HOSTILE, "lane": None, "stage": "queued",
         "head": "c" * 40, "syncedHead": None, "leadInputs": [], "reportedRounds": None, "runId": None,
         "checks": []},
    ],
    "finish": [{"id": "finish-1", "text": "A release ships", "met": False},
               {"id": "finish-2", "text": HOSTILE, "met": True}],
    "followups": [{"id": "follow-1", "title": "Later <i>work</i>", "reference": "ticket-9"}],
    "leases": [{"id": "dev-backend", "holder": "lead", "since": "2026-10-20T09:00:00Z"}],
    "planned": [{"subject": "change-3", "title": HOSTILE, "lane": "lane-2", "stage": "review",
                 "changeRequest": HOSTILE}],
    "extensions": {"adopter": {"note": HOSTILE, "nested": [1, {"a": None}]}},
}
FIXED_JOURNAL = [
    {"id": 1, "at": "2026-10-20T10:00:00Z", "kind": "lane.assigned", "lane": "lane-1", "subject": None,
     "summary": "Assigned " + HOSTILE},
    {"id": 2, "at": "2026-10-20T10:40:00Z", "kind": "review.round", "lane": "lane-1", "subject": "change-1",
     "summary": "Round 1 " + HOSTILE,
     "review": {"round": 1, "startedAt": "2026-10-20T10:30:00Z", "finishedAt": "2026-10-20T10:40:00Z",
                "verdicts": {"outcome": "changes", "adversarial": "changes"},
                "findings": {"P0": 0, "P1": 1, "P2": 2, "P3": 0}}},
    {"id": 3, "at": "2026-10-20T10:50:00Z", "kind": "review.round", "lane": "lane-1", "subject": "change-1",
     "summary": "Round 2",
     "review": {"round": 2, "startedAt": "2026-10-20T10:45:00Z", "finishedAt": "2026-10-20T10:50:00Z",
                "verdicts": {"outcome": "approve", "adversarial": "approve"},
                "findings": {"P0": 0, "P1": 0, "P2": 0, "P3": 1}}},
    {"id": 4, "at": "2026-10-20T11:00:00Z", "kind": "candidate.handed", "lane": "lane-1", "subject": "change-1",
     "summary": "Handed over"},
]
# sha256 of each built-in view of the fixed state. A change here is a change
# to what every adopter's page shows; make it deliberately.
# The same views of a delivery with nothing in it: every section empty, nothing
# being checked, an empty journal, and a queue listed out of order.
EMPTY_STATE = {
    **FIXED_STATE,
    "meta": {**FIXED_STATE["meta"], "headline": "Empty", "baseHead": None,
             "checking": {"now": None, "next": []}},
    "lanes": [],
    "queue": [
        {**FIXED_STATE["queue"][1], "order": 2, "subject": "change-late", "title": "Late"},
        {**FIXED_STATE["queue"][0], "order": 1, "subject": "change-early", "title": "Early"},
    ],
    "finish": [],
    "followups": [],
    "leases": [],
    "planned": [],
    "extensions": {},
}
PINNED_EMPTY_VIEWS = {
    "delivery.md": "bf22eae1f7b5115650ba06265e138d9b9ed720f44158d1fc761ba4df356507c9",
    "delivery.html": "e5019251f6538c602d425de840c0391deddb1040845e5813a826369683025145",
}
PINNED_VIEWS = {
    "delivery.md": "412c01eecfbdcfba6f1f5f650a0368c29c40e3e7cf3b0ef44d0687e68115d79c",
    "delivery.html": "dae6b9f265d099a98054fa6c8caf5a66e75a76bd4cc29cbc6acb0c7ea368aeeb",
}


class PinnedViewTests(unittest.TestCase):
    def views(self, state: dict, journal: list[dict]) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "state"
            directory.mkdir()
            (directory / "state.json").write_bytes(
                (json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
            (directory / "journal.jsonl").write_bytes(b"".join(
                json.dumps(entry, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
                for entry in journal))
            code, result = wrapper(directory)
            self.assertEqual(code, 0, result)
            return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in PINNED_VIEWS}

    def test_the_built_in_views_of_a_fixed_state_are_byte_for_byte_unchanged(self) -> None:
        self.assertEqual(self.views(FIXED_STATE, FIXED_JOURNAL), PINNED_VIEWS)

    def test_the_built_in_views_of_an_empty_delivery_are_byte_for_byte_unchanged(self) -> None:
        self.assertEqual(self.views(EMPTY_STATE, []), PINNED_EMPTY_VIEWS)


class HostPublisherTests(DeliveryStateFixture):
    """A host publisher leaves an outbox the lead publishes with its host's own tool."""

    def binding(self, publishers: list[dict]) -> str:
        path = self.root / f"binding {len(list(self.root.iterdir()))}.json"
        path.write_text(json.dumps({"state": json.loads(self.config([], publishers))}), encoding="utf-8")
        return str(path)

    def test_show_refuses_a_binding_that_keeps_its_state_elsewhere(self) -> None:
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        foreign = elsewhere / "orchestration.json"
        foreign.write_text(json.dumps({"state": json.loads(self.config([], []))}), encoding="utf-8")
        # Another checkout's state exists, as it does for a real foreign binding.
        state_tool(elsewhere / "state", "init", "--headline", "Another delivery")
        refused = self.refused("show", "--binding", str(foreign))
        self.assertEqual(refused["code"], "binding_invalid")
        # Both paths are named, so the mismatch can be fixed without guessing.
        self.assertIn(str((elsewhere / "state").resolve()), refused["error"])
        self.assertIn(str(self.state_dir.resolve()), refused["error"])

    def test_show_accepts_a_binding_whose_state_is_this_directory(self) -> None:
        # Relative to the repository root, the folder holding .agents/.
        agents = self.root / ".agents"
        agents.mkdir()
        local = agents / "orchestration.json"
        local.write_text(json.dumps({"state": json.loads(self.config([], []))}), encoding="utf-8")
        self.assertIn("publication", state_tool(self.state_dir, "show", "--binding", str(local)))
        # An absolute state directory is taken as it is.
        absolute = self.root / "absolute.json"
        absolute.write_text(json.dumps({"state": {**json.loads(self.config([], [])), "directory": str(self.state_dir)}}),
                            encoding="utf-8")
        self.assertIn("publication", state_tool(self.state_dir, "show", "--binding", str(absolute)))

    def test_show_accepts_a_linked_agents_folder_of_this_repository(self) -> None:
        real = self.root / "kept-agents"
        real.mkdir()
        (real / "orchestration.json").write_text(json.dumps({"state": json.loads(self.config([], []))}), encoding="utf-8")
        try:
            (self.root / ".agents").symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"links are not available here: {error}")
        self.assertIn("publication", state_tool(self.state_dir, "show", "--binding", str(self.root / ".agents/orchestration.json")))

    def test_show_refuses_a_binding_naming_no_usable_state_directory(self) -> None:
        for name, state in (("missing", {"publishers": []}), ("empty", {"directory": "", "publishers": []}),
                            ("not text", {"directory": 7, "publishers": []}),
                            ("null byte", {"directory": "state\u0000x", "publishers": []})):
            with self.subTest(name):
                path = self.root / f"{name}.json"
                path.write_text(json.dumps({"state": state}), encoding="utf-8")
                self.assertEqual(self.refused("show", "--binding", str(path))["code"], "binding_invalid")

    def refused(self, *args: str) -> dict:
        result = run([sys.executable, "-B", str(STATE_TOOL), "--dir", str(self.state_dir), *args])
        self.assertEqual(result.returncode, 1, result.stdout)
        return json.loads(result.stdout)

    def test_an_orphaned_outbox_is_flagged_retired_explicitly_and_never_deleted_by_a_render(self) -> None:
        host = [{"id": "live", "kind": "host"}]
        wrapper(self.state_dir, "--state-config", self.config([], host))
        self.assertEqual(self.outbox()["publisher"], "live")
        publication = state_tool(self.state_dir, "show")["publication"]
        self.assertEqual((publication["outboxPublisher"], publication["outboxOrphaned"]), ("live", None))
        kept = state_tool(self.state_dir, "show", "--binding", self.binding(host))["publication"]
        self.assertFalse(kept["outboxOrphaned"])
        orphaned = state_tool(self.state_dir, "show", "--binding", self.binding([]))["publication"]
        self.assertTrue(orphaned["outboxOrphaned"])
        # A render that does not name the host publisher, with or without a configuration, leaves the outbox.
        for args in ((), ("--state-config", self.config([], []))):
            with self.subTest(args=args):
                wrapper(self.state_dir, *args)
                self.assertEqual(self.outbox()["publisher"], "live")
        # While it stands it caps ack; another publisher cannot retire it.
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", "After the outbox")
        shown = state_tool(self.state_dir, "show")
        last = shown["journal"][-1]["id"]
        capped = self.refused("ack", "--version", str(shown["state"]["version"]), "--through", str(last))
        self.assertEqual(capped["code"], "ack_invalid")
        self.assertIn("outbox --retire live", capped["error"], "the refusal names the way out")
        self.assertEqual(self.refused("outbox", "--retire", "other")["code"], "outbox_not_owned")
        retired = state_tool(self.state_dir, "outbox", "--retire", "live")
        self.assertEqual(retired["retired"]["publisher"], "live")
        self.assertFalse((self.state_dir / "outbox").exists())
        state_tool(self.state_dir, "ack", "--version", str(shown["state"]["version"]), "--through", str(last))
        self.assertEqual(self.refused("outbox", "--retire", "live")["code"], "outbox_missing")
        publication = state_tool(self.state_dir, "show", "--binding", self.binding([]))["publication"]
        self.assertEqual((publication["outboxPublisher"], publication["outboxOrphaned"]), (None, None))

    def test_an_outbox_from_before_outboxes_named_their_publisher_is_retired_by_any_id(self) -> None:
        wrapper(self.state_dir, "--state-config", self.config([], [{"id": "live", "kind": "host"}]))
        legacy = {key: value for key, value in self.outbox().items() if key != "publisher"}
        (self.state_dir / "outbox" / "outbox.json").write_text(json.dumps(legacy), encoding="utf-8")
        self.assertTrue(state_tool(self.state_dir, "show", "--binding", self.binding([{"id": "live", "kind": "host"}]))
                        ["publication"]["outboxOrphaned"], "an outbox naming no publisher belongs to none")
        self.assertIsNone(state_tool(self.state_dir, "outbox", "--retire", "anyone")["retired"]["publisher"])

    def test_an_unreadable_outbox_is_retired_only_by_force(self) -> None:
        wrapper(self.state_dir, "--state-config", self.config([], [{"id": "live", "kind": "host"}]))
        (self.state_dir / "outbox" / "outbox.json").write_text(json.dumps(
            {"version": 1, "journalThrough": 0, "publisher": 7}), encoding="utf-8")
        self.assertEqual(self.refused("outbox", "--retire", "live")["code"], "state_invalid")
        retired = state_tool(self.state_dir, "outbox", "--retire", "live", "--force")
        self.assertTrue(retired["retired"]["unreadable"])
        self.assertFalse((self.state_dir / "outbox").exists())

    def test_every_state_field_the_binding_schema_declares_renders_and_no_other(self) -> None:
        schemas = json.loads((ROOT / "skills/orchestrate-work/references/schemas.json").read_text(encoding="utf-8"))
        fields = schemas["orchestration-binding/1"]["properties"]["state"]["properties"]
        # Values the schema allows; the wrapper checks snapshotMaxAgeSeconds and only has to accept the rest.
        samples = {"snapshotMaxAgeSeconds": 60, "statusOverrideMaxAgeSeconds": 600,
                   "trackerSnapshot": {"argv": ["tool", "{parent}"], "timeoutSeconds": 30},
                   "workHints": {"argv": ["tool", "{parent}"], "timeoutSeconds": 30},
                   "forgeSnapshot": {"argv": ["tool", "{parent}"], "timeoutSeconds": 30}, "refreshSeconds": 180}
        base = json.loads(self.config([], []))
        for field in fields:
            if field in base:
                continue
            with self.subTest(field=field):
                config = json.dumps({**base, field: samples.get(field, {})})
                code, result = wrapper(self.state_dir, "--state-config", config)
                self.assertEqual(code, 0, result)
        everything = json.dumps({**base, **{field: samples.get(field, {}) for field in fields if field not in base}})
        self.assertEqual(wrapper(self.state_dir, "--state-config", everything)[0], 0)
        code, result = wrapper(self.state_dir, "--state-config", json.dumps({**base, "noSuchField": 1}))
        self.assertEqual((code, result["code"]), (2, "config_invalid"))
        self.assertIn("noSuchField", result["error"])

    def test_a_state_configuration_may_declare_a_tracker_snapshot_command(self) -> None:
        config = json.dumps({**json.loads(self.config([], [])),
                             "trackerSnapshot": {"argv": ["tool", "{parent}"], "timeoutSeconds": 30}})
        self.assertEqual(wrapper(self.state_dir, "--state-config", config)[0], 0)

    def test_one_host_publisher_and_a_whole_max_age_per_configuration(self) -> None:
        two = [{"id": "live", "kind": "host"}, {"id": "mirror", "kind": "host"}]
        code, result = wrapper(self.state_dir, "--state-config", self.config([], two))
        self.assertEqual((code, result["code"]), (2, "config_invalid"))
        for age in (0, 1.5, True, "60"):
            with self.subTest(age=age):
                config = json.dumps({**json.loads(self.config([], [])), "snapshotMaxAgeSeconds": age})
                code, result = wrapper(self.state_dir, "--state-config", config)
                self.assertEqual((code, result["code"]), (2, "config_invalid"))
        config = json.dumps({**json.loads(self.config([], [])), "snapshotMaxAgeSeconds": 60})
        self.assertEqual(wrapper(self.state_dir, "--state-config", config)[0], 0)

    def outbox(self) -> dict:
        return json.loads((self.state_dir / "outbox" / "outbox.json").read_text(encoding="utf-8"))

    def test_the_outbox_holds_the_state_and_exactly_the_unacknowledged_entries(self) -> None:
        host = [{"id": "live", "kind": "host"}]
        code, result = wrapper(self.state_dir, "--state-config", self.config([], host))
        self.assertEqual(code, 0, result)
        shown = state_tool(self.state_dir, "show")
        self.assertEqual(result["publishers"], [{"id": "live", "kind": "host", "ok": True,
                                                 "outbox": "outbox/outbox.json", "entries": len(shown["journal"]),
                                                 "version": shown["state"]["version"],
                                                 "journalThrough": shown["journal"][-1]["id"]}])
        outbox = self.outbox()
        self.assertEqual(outbox["version"], shown["state"]["version"])
        self.assertEqual(outbox["state"], shown["state"])
        self.assertEqual(outbox["stats"], shown["stats"], "host-published views carry the stats")
        self.assertEqual(outbox["journal"], shown["journal"])
        last = shown["journal"][-1]["id"]
        state_tool(self.state_dir, "ack", "--version", str(outbox["version"]), "--through", str(last))
        wrapper(self.state_dir, "--state-config", self.config([], host))
        self.assertEqual((self.outbox()["journal"], self.outbox()["journalThrough"]), ([], last))
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", "After the ack")
        wrapper(self.state_dir, "--state-config", self.config([], host))
        self.assertEqual([entry["summary"] for entry in self.outbox()["journal"]], ["After the ack"])

    def test_show_flags_an_outbox_older_than_the_record(self) -> None:
        wrapper(self.state_dir, "--state-config", self.config([], [{"id": "live", "kind": "host"}]))
        self.assertFalse(state_tool(self.state_dir, "show")["publication"]["outboxLags"])
        version = state_tool(self.state_dir, "show")["state"]["version"]
        state_tool(self.state_dir, "patch", "--writer", "lead", "--version", str(version),
                   "--patch", json.dumps({"meta": {"headline": "Moved on"}}))
        publication = state_tool(self.state_dir, "show")["publication"]
        self.assertEqual((publication["outboxVersion"], publication["outboxLags"]), (version, True))
        # A journal entry alone moves no version, and still leaves the outbox behind.
        wrapper(self.state_dir, "--state-config", self.config([], [{"id": "live", "kind": "host"}]))
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", "Journal only")
        self.assertTrue(state_tool(self.state_dir, "show")["publication"]["outboxLags"])

    def test_a_host_publisher_takes_no_argv_and_no_view_may_claim_its_names(self) -> None:
        for publisher in ({"id": "live", "kind": "host", "argv": ["x"]}, {"id": "live", "kind": "shell"},
                          {"id": "live", "kind": "host", "timeoutSeconds": 5}):
            with self.subTest(publisher=publisher):
                code, result = wrapper(self.state_dir, "--state-config", self.config([], [publisher]))
                self.assertEqual((code, result["code"]), (2, "config_invalid"), result)
        for output in ("outbox/outbox.json", "published.json", "OUTBOX/x.md", "recovery.json", "Recovery.JSON",
                       "changes.jsonl", "Changes.JSONL", "recovery.json.invalid",
                       "recovery.json.invalid.2"):
            with self.subTest(output=output):
                renderer = [{"id": "sneaky", "builtin": "markdown", "output": output}]
                code, result = wrapper(self.state_dir, "--state-config", self.config(renderer))
                self.assertEqual((code, result["code"]), (2, "config_invalid"), result)


class StatsViewTests(unittest.TestCase):
    """Both built-in views show each stop's stats, planned work and change requests."""

    def document(self) -> dict:
        return {"state": FIXED_STATE, "journal": FIXED_JOURNAL,
                "stats": sys.modules[render_state.show.__module__].delivery_stats(FIXED_STATE, FIXED_JOURNAL)}

    def test_durations_read_in_the_largest_fitting_units(self) -> None:
        for seconds, text in ((None, "unknown"), (True, "unknown"), (0, "0s"), (59, "59s"), (60, "1m 00s"),
                              (3599, "59m 59s"), (3600, "1h 00m"), (86399, "23h 59m"), (86400, "1d 00h"),
                              (90061, "1d 01h")):
            with self.subTest(seconds=seconds):
                self.assertEqual(render_state._duration(seconds), text)

    def test_the_markdown_view_shows_stats_planned_work_and_change_requests(self) -> None:
        view = render_state.render_markdown(self.document())
        self.assertIn("\n  - stats: build 30m 00s · review 20m 00s · 2 rounds, findings P0/P1/P2/P3 0/1/2/1"
                      " · wait unknown · check to merge unknown · total unknown", view)
        self.assertIn("· head " + HEAD_A + " · change request change request 7", view)
        self.assertIn("\n  - stats: build unknown · review unknown · rounds unknown", view)
        planned = view[view.index("## Planned"):view.index("## Lanes")]
        self.assertIn("\n  - stats: build unknown", planned, "planned stats nest under their row")
        self.assertIn(f"- change-3: {render_state._markdown_text(HOSTILE)}, review · lane lane-2"
                      f" · change request {render_state._markdown_text(HOSTILE)}", planned)
        reported = {**self.document(), "stats": {"change-1": {**self.document()["stats"]["change-1"],
                                                               "rounds": 4, "roundsSource": "reported", "findings": None}}}
        self.assertIn("4 rounds (reported)", render_state.render_markdown(reported))
        one = {**self.document(), "stats": {"change-1": {**self.document()["stats"]["change-1"], "rounds": 1}}}
        self.assertIn("· 1 round, findings", render_state.render_markdown(one))

    def test_a_document_without_stats_or_planned_work_still_renders(self) -> None:
        state = {key: value for key, value in FIXED_STATE.items() if key != "planned"}
        view = render_state.render_markdown({"state": state, "journal": FIXED_JOURNAL})
        self.assertNotIn("stats:", view)
        self.assertIn("Nothing planned outside the queue.", view)


def visibility_document() -> dict:
    """The fixed state with timed steps, a failed check with its log, and queue estimates; as of 12:00:00."""
    state = copy.deepcopy(FIXED_STATE)
    state["meta"]["checking"]["now"]["steps"] = [
        {"label": "fmt", "state": "passed", "startedAt": "2026-10-20T11:50:00Z", "finishedAt": "2026-10-20T11:50:30Z",
         "expectedSeconds": 40},
        {"label": "unit", "state": "running", "startedAt": "2026-10-20T11:58:20Z", "expectedSeconds": 600},
        {"label": "lint", "state": "running", "startedAt": "2026-10-20T11:48:00Z", "expectedSeconds": 600},
        {"label": "docs", "state": "running", "startedAt": "2026-10-20T11:59:00Z"},
        {"label": "full", "state": "reused", "reusedFrom": "a" * 40},
    ]
    state["queue"][0]["checks"] = [
        {"id": "suite", "state": "failed", "failures": ["test_alpha", "test_beta"], "log": "logs/01-suite.log"},
        {"id": "whole", "state": "reused", "reusedFrom": "b" * 40},
    ]
    # One run of history, 560s: the run in progress since 11:59:00 has 500s left at 12:00:00.
    state["meta"]["checkHistory"] = {"checks": {}, "runs": [560]}
    return {"state": state, "journal": FIXED_JOURNAL,
            "estimates": delivery_state.queue_estimates(state, at=state["meta"]["updatedAt"])}


def ticket_document() -> dict:
    """The fixed state with the tracker's own titles and summaries for its queued, planned and follow-up work."""
    state = copy.deepcopy(FIXED_STATE)
    state["tickets"] = {
        "change-2": {"title": "Tracker title two", "summary": "Shows each ticket's summary from the tracker."},
        "change-3": {"title": "Tracker title three", "summary": "Plans the third change."},
        "ticket-9": {"title": "Tracker follow-up", "summary": "Opens with what the work does."},
    }
    return {"state": state, "journal": FIXED_JOURNAL}


TICKET_LINES = (
    "change-2: Tracker title two",
    "Shows each ticket's summary from the tracker.",
    "change-3: Tracker title three",
    "Plans the third change.",
    "Tracker follow-up",
    "Opens with what the work does.",
)


VISIBILITY_LINES = (
    "fmt: passed · took 30s",
    "unit: running · 1m 40s of an expected 10m 00s",
    "lint: running · 12m 00s of an expected 10m 00s, over the expected time",
    "docs: running · 1m 00s, no expected time yet",
    "suite: failed · failing test_alpha, test_beta · log logs/01-suite.log",
    "full: reused · reused from the lane's run at " + "a" * 40,
    "whole: reused · reused from the lane's run at " + "b" * 40,
    "position 1 · expected to start around 2026-10-20T12:08:20Z (an estimate)",
)


def status_document(source: str = "derived") -> dict:
    """The fixed state with a status line and a derived focus for a lane that wrote none of its own."""
    state = copy.deepcopy(FIXED_STATE)
    state["lanes"][1]["focus"] = ""
    return {"state": state, "journal": FIXED_JOURNAL,
            "status": {"text": "Checking Change one · next: change-2", "source": source,
                       **({"expiresAt": "2026-10-20T16:00:00Z"} if source == "override" else {})},
            "focus": {"lane-2": "change-2: queued, 1 review round"}}


class StatusViewTests(unittest.TestCase):
    """Both views show the status line under the name, and a lane's derived focus when it wrote none."""

    def test_the_markdown_view_shows_the_status_and_a_derived_focus(self) -> None:
        view = render_state.render_markdown(status_document())
        self.assertIn("\nStatus: Checking Change one · next: change-2\n", view)
        self.assertIn(render_state._markdown_text("change-2: queued, 1 review round") + " (derived)", view)

    def test_an_override_says_it_is_the_leads(self) -> None:
        view = render_state.render_markdown(status_document("override"))
        self.assertIn("\nStatus: Checking Change one · next: change-2 (set by the lead, until 2026-10-20T16:00:00Z)\n", view)

    def test_a_document_without_a_status_renders_as_before(self) -> None:
        view = render_state.render_markdown({"state": FIXED_STATE, "journal": FIXED_JOURNAL})
        self.assertNotIn("Status:", view)
        self.assertNotIn("(derived)", view)


class CheckVisibilityViewTests(unittest.TestCase):
    """Both views show elapsed against expected, failing tests with their log, and queue estimates."""

    def test_the_markdown_view_shows_timing_failures_and_estimates(self) -> None:
        view = render_state.render_markdown(visibility_document())
        for line in VISIBILITY_LINES:
            with self.subTest(line=line):
                self.assertIn(render_state._markdown_text(line), view)

    def test_exactly_the_expected_time_is_not_over(self) -> None:
        document = visibility_document()
        document["state"]["meta"]["checking"]["now"]["steps"] = [
            {"label": "unit", "state": "running", "startedAt": "2026-10-20T11:50:00.999Z", "expectedSeconds": 600}]
        view = render_state.render_markdown(document)
        self.assertIn(render_state._markdown_text("unit: running · 10m 00s of an expected 10m 00s"), view)
        self.assertNotIn("over the expected time", view)

    def test_an_unknown_start_reads_as_unknown(self) -> None:
        document = visibility_document()
        document["estimates"] = {"change-2": {"position": 1, "expectedStart": None}}
        self.assertIn(render_state._markdown_text("position 1 · start time unknown (an estimate)"),
                      render_state.render_markdown(document))

    def test_a_document_without_estimates_or_timing_renders_as_before(self) -> None:
        document = {"state": FIXED_STATE, "journal": FIXED_JOURNAL}
        view = render_state.render_markdown(document)
        self.assertNotIn("expected", view)
        self.assertNotIn("position", view)


class TicketViewTests(unittest.TestCase):
    """Both views show each ticket's tracker title, then its summary, falling back to the record's title."""

    def test_the_markdown_view_shows_tracker_titles_then_summaries(self) -> None:
        view = render_state.render_markdown(ticket_document())
        for line in TICKET_LINES:
            with self.subTest(line=line):
                self.assertIn(render_state._markdown_text(line), view)
        self.assertLess(view.index(render_state._markdown_text("change-2: Tracker title two")),
                        view.index(render_state._markdown_text("Shows each ticket's summary from the tracker.")))
        self.assertIn("  - summary: " + render_state._markdown_text("Opens with what the work does."), view)
        self.assertNotIn(render_state._markdown_text("change-2: " + HOSTILE), view, "the tracker's title replaces the record's")
        # Work with no ticket keeps the record's own title, and no summary line.
        self.assertIn(render_state._markdown_text("change-1: Change one"), view)
        self.assertEqual(view.count("summary:"), 3)

    def test_a_follow_up_s_ticket_is_found_by_its_reference_before_its_id(self) -> None:
        document = ticket_document()
        document["state"]["tickets"]["follow-1"] = {"title": "By its id", "summary": "Found by id."}
        view = render_state.render_markdown(document)
        self.assertIn(render_state._markdown_text("Tracker follow-up"), view)
        self.assertNotIn("By its id", view)
        del document["state"]["tickets"]["ticket-9"]
        self.assertIn(render_state._markdown_text("By its id"), render_state.render_markdown(document))

    def test_without_tickets_the_views_show_the_record_s_titles(self) -> None:
        view = render_state.render_markdown({"state": FIXED_STATE, "journal": FIXED_JOURNAL})
        self.assertNotIn("summary:", view)
        self.assertIn(render_state._markdown_text("change-2: " + HOSTILE), view)


class LiveClient:
    """A page's view of the live command: its document and its event stream, read as a browser would."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.events: list[dict] = []
        self.lock = threading.Lock()
        self.response = None

    def get(self, path: str, host: str | None = None) -> tuple[int, bytes]:
        request = urllib.request.Request(self.url + path, headers={"Host": host} if host else {})
        try:
            with urllib.request.urlopen(request, timeout=5) as answer:
                return answer.status, answer.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def listen(self) -> None:
        address = urllib.parse.urlsplit(self.url)
        self.connection = http.client.HTTPConnection(address.hostname, address.port, timeout=30)
        self.connection.request("GET", "/events")
        self.response = self.connection.getresponse()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        event = None
        with contextlib.suppress(Exception):
            for raw in self.response:
                line = raw.decode("utf-8").rstrip("\n")
                if line.startswith("event: "):
                    event = line[len("event: "):]
                elif line.startswith("data: ") and event == "document":
                    with self.lock:
                        self.events.append(json.loads(line[len("data: "):]))
                elif not line:
                    event = None

    def close(self) -> None:
        """Hang up at once, as a closed tab does."""
        if self.response is not None:
            with contextlib.suppress(Exception):
                self.connection.sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(Exception):
                self.connection.close()

    def latest(self) -> dict | None:
        with self.lock:
            return self.events[-1] if self.events else None

    def wait_for(self, check, seconds: float = 3.0) -> dict | None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            latest = self.latest()
            if latest is not None and check(latest):
                return latest
            time.sleep(0.05)
        return None


class LiveTests(DeliveryStateFixture):
    """`live` re-renders on every write and pushes the latest record to open pages."""

    def start(self, *extra: str, env: dict | None = None) -> tuple[subprocess.Popen, str]:
        process = subprocess.Popen([sys.executable, "-B", str(WRAPPER), "live", "--dir", str(self.state_dir), *extra],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, **env} if env else None)
        self.addCleanup(self.stop, process)
        line = process.stdout.readline()
        announced = json.loads(line)
        self.assertTrue(announced["ok"], announced)
        return process, f"http://{announced['live']}/"

    def stop(self, process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.kill()
            process.wait(5)
        for stream in (process.stdout, process.stderr):
            with contextlib.suppress(Exception):
                stream.close()

    def client(self, url: str) -> LiveClient:
        client = LiveClient(url)
        self.addCleanup(client.close)
        return client

    def test_a_journal_write_reaches_an_open_page_within_three_seconds(self) -> None:
        _, url = self.start()
        client = self.client(url)
        client.listen()
        first = client.wait_for(lambda doc: True)
        self.assertIsNotNone(first, "a page that connects gets the record at once")
        written = time.monotonic()
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", "Reached the page")
        seen = client.wait_for(lambda doc: doc["journal"] and doc["journal"][-1]["summary"] == "Reached the page")
        self.assertIsNotNone(seen)
        self.assertLess(time.monotonic() - written, 3.0)
        self.assertEqual(set(seen), {"state", "journal", "stats", "estimates", "status", "focus"})
        status, page = client.get("")
        self.assertEqual(status, 200)
        self.assertIn(b"EventSource(\"/events\")", page)
        self.assertIn(b'id="live-status"', page)
        status, document = client.get("document")
        self.assertEqual(json.loads(document)["journal"][-1]["summary"], "Reached the page")

    def test_a_burst_of_writes_ends_at_the_final_version_with_nothing_lost_or_doubled(self) -> None:
        _, url = self.start()
        client = self.client(url)
        client.listen()
        client.wait_for(lambda doc: True)
        before = len(client.events)
        threads = [threading.Thread(target=state_tool, args=(self.state_dir, "journal", "--kind", "decision.recorded",
                                                             "--summary", f"Burst {n}")) for n in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        final = state_tool(self.state_dir, "show")
        seen = client.wait_for(lambda doc: len(doc["journal"]) == len(final["journal"]), seconds=5)
        self.assertIsNotNone(seen)
        summaries = [entry["summary"] for entry in seen["journal"]]
        self.assertEqual(sorted(summary for summary in summaries if summary.startswith("Burst")),
                         sorted(f"Burst {n}" for n in range(10)))
        self.assertEqual(len(summaries), len(set(entry["id"] for entry in seen["journal"])))
        self.assertEqual(seen["state"]["version"], final["state"]["version"])
        self.assertLessEqual(len(client.events) - before, 10, "never more events than writes")
        documents = [json.dumps(event, sort_keys=True) for event in client.events]
        self.assertEqual(len(documents), len(set(documents)), "no page update repeats another")

    def test_a_write_that_does_not_change_the_record_sends_nothing(self) -> None:
        _, url = self.start()
        client = self.client(url)
        client.listen()
        shown = client.wait_for(lambda doc: True)
        state_tool(self.state_dir, "ack", "--version", str(shown["state"]["version"]),
                   "--through", str(shown["journal"][-1]["id"] if shown["journal"] else 0))
        time.sleep(1.0)
        self.assertEqual(len(client.events), 1, "an acknowledgement changes no page")

    def test_a_record_replaced_at_the_same_version_still_reaches_the_page(self) -> None:
        _, url = self.start()
        client = self.client(url)
        client.listen()
        first = client.wait_for(lambda doc: True)
        state = json.loads((self.state_dir / "state.json").read_text(encoding="utf-8"))
        state["meta"]["headline"] = "Replaced in place"
        (self.state_dir / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        seen = client.wait_for(lambda doc: doc["state"]["meta"]["headline"] == "Replaced in place")
        self.assertIsNotNone(seen)
        self.assertEqual(seen["state"]["version"], first["state"]["version"])

    def test_live_renders_views_but_runs_no_publisher(self) -> None:
        marker = self.root / "published marks"
        publisher = {"id": "store", "argv": python_argv(
            f"import sys\nopen({str(marker)!r}, 'a').write('x')\nsys.stdin.buffer.read()\n", self.tools, "store")}
        renderer = {"id": "summary", "builtin": "markdown", "output": "delivery.md"}
        _, url = self.start("--state-config", self.config([renderer], [publisher]))
        client = self.client(url)
        client.listen()
        client.wait_for(lambda doc: True)
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", "Rendered, not published")
        client.wait_for(lambda doc: doc["journal"][-1]["summary"] == "Rendered, not published")
        deadline = time.monotonic() + 3
        while "Rendered, not published" not in (self.state_dir / "delivery.md").read_text(encoding="utf-8") \
                and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertIn("Rendered, not published", (self.state_dir / "delivery.md").read_text(encoding="utf-8"))
        self.assertFalse(marker.exists(), "the live view never publishes")

    def test_a_page_that_reconnects_gets_the_latest_at_once(self) -> None:
        _, url = self.start()
        first = self.client(url)
        first.listen()
        first.wait_for(lambda doc: True)
        first.close()
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", "While away")
        time.sleep(0.6)
        second = self.client(url)
        second.listen()
        seen = second.wait_for(lambda doc: True, seconds=2)
        self.assertEqual(seen["journal"][-1]["summary"], "While away")

    def test_a_half_written_record_is_never_served_and_never_stops_the_server(self) -> None:
        process, url = self.start()
        client = self.client(url)
        good = json.loads(client.get("document")[1])
        state_file = self.state_dir / "state.json"
        whole = state_file.read_bytes()
        state_file.write_bytes(whole[: len(whole) // 2])
        time.sleep(0.8)
        self.assertIsNone(process.poll())
        self.assertEqual(json.loads(client.get("document")[1]), good, "the last valid record stands")
        state_file.write_bytes(whole)
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", "Recovered")
        client.listen()
        self.assertIsNotNone(client.wait_for(lambda doc: doc["journal"][-1]["summary"] == "Recovered"))

    def test_it_serves_only_its_own_host_and_binds_to_this_machine_by_default(self) -> None:
        _, url = self.start()
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)
        client = self.client(url)
        port = url.rsplit(":", 1)[1].rstrip("/")
        self.assertEqual(client.get("document", host=f"localhost:{port}")[0], 200)
        # Any loopback name on its own port: the whole loopback family, alike.
        for host in (f"LOCALHOST:{port}", f"127.0.0.1:{port}", f"127.0.0.2:{port}", f"127.255.255.254:{port}",
                     f"[::1]:{port}", f"[::ffff:127.0.0.1]:{port}", f"[::FFFF:7f00:1]:{port}"):
            with self.subTest(host=host):
                self.assertEqual(client.get("document", host=host)[0], 200)
        for host in ("evil.example", f"evil.example:{port}", "127.0.0.1:1", "127.0.0.1", f"localhost.:{port}",
                     f"10.0.0.1:{port}", f"[::2]:{port}", f"[::ffff:10.0.0.1]:{port}", f"128.0.0.1:{port}",
                     f"localhost.evil.example:{port}", f"[::1]{port}", f":{port}"):
            with self.subTest(host=host):
                self.assertEqual(client.get("document", host=host)[0], 403)
        for name in ("state.json", "journal.jsonl", "changes.jsonl", "published.json", "view/state.json",
                     "view/journal.jsonl"):
            with self.subTest(name=name):
                self.assertEqual(client.get(name)[0], 404, "the record's files are never served")

    def test_an_interrupt_ends_it_non_zero_with_no_process_left(self) -> None:
        process, url = self.start()
        client = self.client(url)
        client.listen()
        client.wait_for(lambda doc: True)
        process.send_signal(signal.SIGINT)
        self.assertEqual(process.wait(10), 128 + signal.SIGINT)
        result = json.loads(process.stdout.read())
        self.assertEqual((result["code"], result["signal"]), ("interrupted", "SIGINT"))
        with self.assertRaises(OSError):
            urllib.request.urlopen(url + "document", timeout=2)

    def test_an_unreadable_record_never_stops_the_watcher(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("permissions do not bind the superuser")
        process, url = self.start()
        client = self.client(url)
        client.listen()
        client.wait_for(lambda doc: True)
        state_file = self.state_dir / "state.json"
        state_file.chmod(0)
        time.sleep(0.8)
        state_file.chmod(0o644)
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", "After a bad read")
        self.assertIsNotNone(client.wait_for(lambda doc: doc["journal"][-1]["summary"] == "After a bad read"))
        self.assertIsNone(process.poll())

    def test_a_slow_view_never_holds_back_the_page(self) -> None:
        slow = {"id": "slow", "argv": python_argv("import sys, time\ntime.sleep(6)\nsys.stdout.write('x')\n",
                                                   self.tools, "slow"), "output": "slow.txt", "timeoutSeconds": 30}
        _, url = self.start("--state-config", self.config([slow]))
        client = self.client(url)
        client.listen()
        client.wait_for(lambda doc: True, seconds=10)
        written = time.monotonic()
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", "Not held back")
        self.assertIsNotNone(client.wait_for(lambda doc: doc["journal"][-1]["summary"] == "Not held back"))
        self.assertLess(time.monotonic() - written, 3.0)

    def test_an_opted_in_view_is_served_live_and_others_are_not(self) -> None:
        opted = {"id": "own", "argv": python_argv(
            "import sys\nsys.stdout.write('<html><head><meta name=\"delivery-state-live\"></head><body>own</body></html>')\n",
            self.tools, "own"), "output": "own.html"}
        plain = {"id": "plain", "argv": python_argv("import sys\nsys.stdout.write('<html>plain</html>')\n",
                                                    self.tools, "plain"), "output": "plain.html"}
        _, url = self.start("--state-config", self.config([opted, plain]))
        client = self.client(url)
        deadline = time.monotonic() + 5
        status, page = client.get("view/own.html")
        while status != 200 and time.monotonic() < deadline:
            time.sleep(0.2)
            status, page = client.get("view/own.html")
        self.assertEqual(status, 200)
        # Its adapter reaches the stream from wherever the view is served.
        self.assertIn(b'EventSource("/events")', page)
        self.assertIn(b'fetch("/document")', page)
        self.assertEqual(client.get("view/plain.html")[0], 404)
        self.assertEqual(client.get("view/../state.json")[0], 404)

    def test_a_wildcard_bind_is_refused_before_anything_listens(self) -> None:
        for host in ("0.0.0.0", "::", "0:0:0:0:0:0:0:0", "::ffff:0.0.0.0", "", " ", "0"):
            with self.subTest(host=host):
                result = run([sys.executable, "-B", str(WRAPPER), "live", "--dir", str(self.state_dir),
                              "--host", host])
                self.assertEqual(result.returncode, 2)
                refused = json.loads(result.stdout)
                self.assertEqual((refused["ok"], refused["code"]), (False, "live_refused"))
                self.assertEqual(refused["error"], "the live view serves this machine only; bind a loopback address")

    def test_a_bind_named_localhost_takes_any_loopback_name(self) -> None:
        _, url = self.start("--host", "localhost")
        self.assertTrue(url.startswith("http://localhost:"), url)
        port = url.rsplit(":", 1)[1].rstrip("/")
        client = self.client(f"http://127.0.0.1:{port}/")
        self.assertEqual(client.get("document")[0], 200)
        self.assertEqual(client.get("document", host=f"evil.example:{port}")[0], 403)

    def test_an_ipv6_loopback_host_is_served(self) -> None:
        if not socket.has_ipv6:
            self.skipTest("no IPv6 on this machine")
        try:
            probe = socket.socket(socket.AF_INET6)
            probe.bind(("::1", 0))
            probe.close()
        except OSError:
            self.skipTest("no IPv6 loopback on this machine")
        _, url = self.start("--host", "::1")
        self.assertTrue(url.startswith("http://[::1]:"), url)
        self.assertEqual(self.client(url).get("document")[0], 200)

    def test_a_bad_configuration_or_record_never_starts_the_server(self) -> None:
        result = run([sys.executable, "-B", str(WRAPPER), "live", "--dir", str(self.state_dir), "--state-config",
                      json.dumps({"renderers": [{"id": "x", "output": "state.json", "builtin": "markdown"}]})])
        self.assertEqual((result.returncode, json.loads(result.stdout)["code"]), (2, "config_invalid"))
        result = run([sys.executable, "-B", str(WRAPPER), "live", "--dir", str(self.root / "nothing here")])
        self.assertEqual(result.returncode, 2)

    def test_a_signal_recorded_on_the_watcher_s_start_still_ends_it(self) -> None:
        # Recorded, never raised: only the serving loop's own check can end it.
        process, url = self.start(env={SEAM: "watch-start:SIGINT!"})
        self.assertEqual(process.wait(10), 128 + signal.SIGINT)
        result = json.loads(process.stdout.read())
        self.assertEqual((result["code"], result["signal"]), ("interrupted", "SIGINT"))

    def test_a_page_that_reconnects_after_the_change_log_was_trimmed_still_converges(self) -> None:
        path = self.state_dir / "changes.jsonl"
        limit = delivery_state.CHANGES_LIMIT_BYTES
        line = json.dumps({"at": "2026-10-21T00:00:00Z", "change": "journal", "journal": 1, "version": 1}) + "\n"
        path.write_text(line * (2 * limit // len(line)), encoding="utf-8")
        _, url = self.start()
        first = self.client(url)
        first.listen()
        first.wait_for(lambda doc: True)
        for n in range(3):
            state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", f"Long run {n}")
        self.assertIsNotNone(first.wait_for(lambda doc: doc["journal"][-1]["summary"] == "Long run 2"))
        self.assertLessEqual(path.stat().st_size, limit)
        first.close()
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", "Away past the trim")
        time.sleep(0.6)
        second = self.client(url)
        second.listen()
        seen = second.wait_for(lambda doc: True, seconds=2)
        shown = {key: value for key, value in state_tool(self.state_dir, "show").items() if key != "publication"}
        self.assertEqual(seen, shown, "a page that comes back gets the whole current record")

    def test_a_non_loopback_bind_answers_only_its_own_name(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Picks the address a route out would use; nothing is sent.
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
        except OSError:
            self.skipTest("no address beyond loopback on this machine")
        finally:
            probe.close()
        if render_state._is_loopback(address) or render_state._is_wildcard(address):
            self.skipTest("no address beyond loopback on this machine")
        _, url = self.start("--host", address)
        self.assertTrue(url.startswith(f"http://{address}:"), url)
        port = url.rsplit(":", 1)[1].rstrip("/")
        client = self.client(url)
        self.assertEqual(client.get("document")[0], 200)
        for host in (f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}", f"evil.example:{port}"):
            with self.subTest(host=host):
                self.assertEqual(client.get("document", host=host)[0], 403)


class LiveInProcessTests(DeliveryStateFixture):
    """The live view's parts, driven in this process where a test can step them exactly."""

    def test_a_non_loopback_bind_refuses_every_other_name(self) -> None:
        accepts = render_state._live_hosts("192.0.2.10", 8000)
        self.assertTrue(accepts("192.0.2.10:8000"))
        for header in ("localhost:8000", "127.0.0.1:8000", "[::1]:8000", "[::ffff:127.0.0.1]:8000",
                       "192.0.2.10:8001", "192.0.2.10", "evil.example:8000"):
            with self.subTest(header=header):
                self.assertFalse(accepts(header))

    def test_a_jump_of_several_generations_sends_the_latest_once(self) -> None:
        view = render_state.LiveView(self.state_dir, None)
        view.refresh()
        server = render_state._LiveServer(("127.0.0.1", 0), render_state._live_handler(view, lambda header: True))
        serving = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        serving.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(view.stop)
        client = LiveClient(f"http://127.0.0.1:{server.server_address[1]}/")
        self.addCleanup(client.close)
        # Quiet spells come often here; each must send a keepalive, never the document again.
        keepalive = mock.patch.object(render_state, "LIVE_KEEPALIVE_SECONDS", 0.1)
        keepalive.start()
        self.addCleanup(keepalive.stop)
        client.listen()
        self.assertIsNotNone(client.wait_for(lambda doc: True))
        latest = copy.deepcopy(view.document)
        latest["state"]["meta"]["headline"] = "Several writes later"
        with view.changed:
            view.document = latest
            view.encoded = json.dumps(latest, sort_keys=True).encode("ascii")
            view.generation += 5
            view.changed.notify_all()
        self.assertIsNotNone(client.wait_for(lambda doc: doc["state"]["meta"]["headline"] == "Several writes later"))
        time.sleep(0.5)
        self.assertEqual(len(client.events), 2, "a page missing several generations gets only the latest, once")

    def test_a_view_rendered_on_a_helper_thread_never_holds_back_unwinding(self) -> None:
        # Signals land on the main thread; a helper thread's command must not
        # stop that thread from unwinding, even for the instant it spawns.
        view = render_state.LiveView(self.state_dir, json.loads(self.config([self.capture_renderer("echo")])))
        seam = render_state._seam
        spawning: list[bool] = []

        def watching(point: str) -> None:
            # Inside the window between the command's spawn and its being known.
            if point == "spawning":
                spawning.append(render_state._unwinding)
            seam(point)

        with mock.patch.object(render_state, "_unwinding", True), mock.patch.object(render_state, "_seam", watching):
            watcher = threading.Thread(target=view.refresh)
            watcher.start()
            watcher.join(10)
            self.assertTrue((self.state_dir / "echo.json").exists(), "the view command ran on the helper thread")
            self.assertEqual(spawning, [True], "the main thread could unwind while the command spawned")
            self.assertTrue(render_state._unwinding)

    def test_a_helper_thread_that_resumes_after_a_signal_neither_raises_nor_unwinds(self) -> None:
        outcome: list[object] = []

        def resume() -> None:
            try:
                render_state._resume()
                outcome.append(None)
            except BaseException as error:  # noqa: BLE001 - what escaped is the result
                outcome.append(error)

        # Held back, as while the main thread spawns: a helper's resume must leave it so.
        with mock.patch.object(render_state, "_unwinding", False), \
                mock.patch.object(render_state, "_received", [signal.SIGINT]):
            helper = threading.Thread(target=resume)
            helper.start()
            helper.join(5)
            self.assertEqual(outcome, [None], "only the main thread unwinds")
            self.assertFalse(render_state._unwinding)


class ChangeNotificationTests(DeliveryStateFixture):
    """Every successful write through the state tool records exactly one change."""

    def changes(self) -> list[dict]:
        path = self.state_dir / "changes.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_each_write_by_any_writer_records_one_change(self) -> None:
        before = len(self.changes())
        version = state_tool(self.state_dir, "show")["state"]["version"]
        state_tool(self.state_dir, "patch", "--writer", "lead", "--version", str(version),
                   "--patch", json.dumps({"meta": {"headline": "Changed"}}))
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", "A lane's entry", "--lane", "lane-1")
        shown = state_tool(self.state_dir, "show")
        state_tool(self.state_dir, "ack", "--version", str(shown["state"]["version"]),
                   "--through", str(shown["journal"][-1]["id"]))
        changes = self.changes()[before:]
        self.assertEqual([change["change"] for change in changes], ["patch", "journal", "ack"])
        self.assertEqual(changes[0]["version"], version + 1)
        self.assertEqual(changes[1]["journal"], shown["journal"][-1]["id"])
        # A refused write records nothing.
        result = run([sys.executable, "-B", str(STATE_TOOL), "--dir", str(self.state_dir), "patch", "--writer", "lead",
                      "--version", "1", "--patch", "{}"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.changes()), before + 3)

    def test_retiring_an_outbox_records_a_change_and_a_refused_patch_records_none(self) -> None:
        wrapper(self.state_dir, "--state-config", self.config([], [{"id": "live", "kind": "host"}]))
        before = len(self.changes())
        state_tool(self.state_dir, "outbox", "--retire", "live")
        self.assertEqual([change["change"] for change in self.changes()[before:]], ["outbox"])
        version = state_tool(self.state_dir, "show")["state"]["version"]
        result = run([sys.executable, "-B", str(STATE_TOOL), "--dir", str(self.state_dir), "patch", "--writer", "lead",
                      "--version", str(version), "--patch", json.dumps({"meta": {"headline": 3}})])
        self.assertEqual(json.loads(result.stdout)["code"], "patch_invalid")
        self.assertEqual(len(self.changes()), before + 1, "a refused write records no change")

    def test_each_write_the_tool_makes_for_a_lane_records_one_change(self) -> None:
        # A hand-off writes the record, then journals it: one change each.
        before = len(self.changes())
        state_tool(self.state_dir, "handoff", "--lane", "lane-1", "--subject", "change-1", "--head", "d" * 40,
                   "--change-request", "cr 9")
        changes = self.changes()[before:]
        self.assertEqual([change["change"] for change in changes], ["patch", "journal"])
        self.assertEqual(changes[0]["version"], state_tool(self.state_dir, "show")["state"]["version"])

    def test_a_forced_retire_of_an_unreadable_outbox_records_a_change(self) -> None:
        outbox = self.state_dir / "outbox"
        outbox.mkdir(exist_ok=True)
        (outbox / "outbox.json").write_text("{not json", encoding="utf-8")
        before = len(self.changes())
        retired = state_tool(self.state_dir, "outbox", "--retire", "live", "--force")
        self.assertTrue(retired["retired"]["unreadable"], retired)
        self.assertEqual([change["change"] for change in self.changes()[before:]], ["outbox"])

    def test_the_change_log_stays_within_its_cap_and_keeps_the_latest_changes(self) -> None:
        path = self.state_dir / "changes.jsonl"
        limit = delivery_state.CHANGES_LIMIT_BYTES
        seeded = [json.dumps({"at": "2026-10-21T00:00:00Z", "change": "journal", "journal": n, "version": 1},
                             sort_keys=True, separators=(",", ":")) for n in range(2 * limit // 60)]
        path.write_text("\n".join(seeded) + "\n", encoding="utf-8")
        self.assertGreater(path.stat().st_size, limit)
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", "Past the cap")
        self.assertLessEqual(path.stat().st_size, limit)
        changes = self.changes()
        latest = state_tool(self.state_dir, "show")["journal"][-1]["id"]
        self.assertEqual((changes[-1]["change"], changes[-1]["journal"]), ("journal", latest))
        kept = [change["journal"] for change in changes[:-1]]
        self.assertGreater(len(kept), 100, "enough history is kept")
        self.assertEqual(kept, list(range(len(seeded) - len(kept), len(seeded))), "the latest changes, in order")
        self.assertLessEqual(path.stat().st_size, limit // 2, "a trim keeps its latest half, so it is rare")
        trimmed = path.stat()
        # It keeps appending after a trim, and stays within its cap however long the run.
        for n in range(3):
            state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", f"After {n}")
        self.assertEqual([change["change"] for change in self.changes()[-3:]], ["journal"] * 3)
        self.assertEqual(path.stat().st_ino, trimmed.st_ino, "below the cap, a write only appends")
        self.assertGreater(path.stat().st_size, trimmed.st_size)

    def test_a_notification_that_cannot_be_written_never_fails_the_write(self) -> None:
        path = self.state_dir / "changes.jsonl"
        path.unlink(missing_ok=True)
        path.mkdir()
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", "Still written")
        self.assertEqual(state_tool(self.state_dir, "show")["journal"][-1]["summary"], "Still written")


class WrapperTests(DeliveryStateFixture):
    def test_a_built_in_view_that_fails_keeps_its_previous_view(self) -> None:
        renderers = [{"id": "summary", "builtin": "markdown", "output": "delivery.md"}]
        code, result = wrapper(self.state_dir, "--state-config", self.config(renderers))
        self.assertEqual(code, 0, result)
        previous = (self.state_dir / "delivery.md").read_bytes()

        def broken(document: dict) -> str:
            raise ValueError("cannot draw")

        with mock.patch.dict(render_state.BUILTINS, {"markdown": broken}):
            code, result = render_state.render(self.state_dir, json.loads(self.config(renderers)))
        self.assertEqual(code, 1, result)
        entry = result["renderers"][0]
        self.assertEqual(entry["ok"], False)
        self.assertEqual(entry["error"], "the built-in markdown view failed: cannot draw")
        self.assertEqual((self.state_dir / "delivery.md").read_bytes(), previous)

    def test_a_built_in_view_over_the_limit_is_truncated_and_not_written(self) -> None:
        renderers = [{"id": "page", "builtin": "static-page", "output": "delivery.html"}]
        with mock.patch.object(render_state, "VIEW_LIMIT_BYTES", 1000):
            code, result = render_state.render(self.state_dir, json.loads(self.config(renderers)))
        self.assertEqual(code, 1, result)
        entry = result["renderers"][0]
        self.assertEqual((entry["ok"], entry["truncated"]), (False, True))
        self.assertIn("1000", entry["error"])
        self.assertFalse((self.state_dir / "delivery.html").exists())

    def test_the_same_state_renders_identically_twice(self) -> None:
        renderers = [
            {"id": "summary", "builtin": "markdown", "output": "delivery.md"},
            {"id": "page", "builtin": "static-page", "output": "delivery.html"},
        ]
        outputs = []
        for _ in range(2):
            code, result = wrapper(self.state_dir, "--state-config", self.config(renderers))
            self.assertEqual(code, 0, result)
            outputs.append({name: (self.state_dir / name).read_bytes() for name in ("delivery.md", "delivery.html")})
        self.assertEqual(outputs[0], outputs[1])

    def test_an_invalid_state_is_refused_before_any_renderer_runs(self) -> None:
        marker = self.root / "ran"
        renderer = {
            "id": "witness",
            "argv": python_argv(f"open({str(marker)!r}, 'w').close()\n", self.tools, "witness"),
            "output": "witness.txt",
        }
        publisher = {"id": "store", "argv": renderer["argv"]}
        state = json.loads((self.state_dir / "state.json").read_text(encoding="utf-8"))
        state["queue"][0]["stage"] = "teleported"
        (self.state_dir / "state.json").write_bytes(json.dumps(state).encode("utf-8"))

        code, result = wrapper(self.state_dir, "--state-config", self.config([renderer], [publisher]))

        self.assertEqual(code, 2)
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["code"], "state_invalid")
        self.assertFalse(marker.exists())
        self.assertFalse((self.state_dir / "witness.txt").exists())

    def test_an_invalid_journal_is_refused_before_any_renderer_runs(self) -> None:
        with (self.state_dir / "journal.jsonl").open("ab") as stream:
            stream.write(b'{"id": 9}\n')
        code, result = wrapper(self.state_dir, "--state-config", self.config([self.capture_renderer("echo")]))
        self.assertEqual(code, 2)
        self.assertEqual(result["code"], "journal_invalid")
        self.assertFalse((self.state_dir / "echo.json").exists())

    def test_an_adopter_renderer_receives_exactly_the_validated_state(self) -> None:
        code, result = wrapper(self.state_dir, "--state-config", self.config([self.capture_renderer("echo")]))
        self.assertEqual(code, 0, result)
        received = json.loads((self.state_dir / "echo.json").read_bytes().decode("utf-8"))
        self.assertEqual(received, self.document)
        self.assertEqual(set(received), {"state", "journal", "stats", "estimates", "status", "focus"})

    def test_extensions_reach_renderers_unchanged(self) -> None:
        wrapper(self.state_dir, "--state-config", self.config([self.capture_renderer("echo")]))
        received = json.loads((self.state_dir / "echo.json").read_bytes().decode("utf-8"))
        self.assertEqual(received["state"]["extensions"], {"adopter": {"note": HOSTILE, "nested": [1, {"a": None}]}})

    def test_publishers_receive_the_same_validated_state(self) -> None:
        sink = self.root / "published.json"
        publisher = {
            "id": "store",
            "argv": python_argv(
                f"import sys\nopen({str(sink)!r}, 'wb').write(sys.stdin.buffer.read())\n",
                self.tools,
                "store",
            ),
        }
        code, result = wrapper(self.state_dir, "--state-config", self.config(publishers=[publisher]))
        self.assertEqual(code, 0, result)
        self.assertEqual(json.loads(sink.read_bytes().decode("utf-8")), self.document)
        self.assertEqual(result["publishers"], [{"exitCode": 0, "id": "store", "ok": True}])

    def test_a_failing_publisher_is_recorded_and_does_not_fail_the_wrapper(self) -> None:
        failing = {"id": "down", "argv": python_argv("import sys\nsys.stderr.write('store down')\nsys.exit(3)\n", self.tools, "down")}
        slow = {
            "id": "slow",
            "argv": python_argv("import time\ntime.sleep(5)\n", self.tools, "slow"),
            "timeoutSeconds": 1,
        }
        missing = {"id": "gone", "argv": [str(self.tools / "no-such-program")]}
        renderers = [{"id": "summary", "builtin": "markdown", "output": "delivery.md"}]

        code, result = wrapper(self.state_dir, "--state-config", self.config(renderers, [failing, slow, missing]))

        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"])
        self.assertTrue((self.state_dir / "delivery.md").exists())
        by_id = {entry["id"]: entry for entry in result["publishers"]}
        self.assertEqual(by_id["down"]["ok"], False)
        self.assertEqual(by_id["down"]["exitCode"], 3)
        self.assertIn("store down", by_id["down"]["error"])
        self.assertEqual(by_id["slow"]["ok"], False)
        self.assertEqual(by_id["slow"]["error"], "timed out after 1s")
        self.assertEqual(by_id["gone"]["ok"], False)
        self.assertIsNone(by_id["gone"]["exitCode"])

    def test_a_failing_renderer_keeps_its_previous_view_and_the_others_still_render(self) -> None:
        (self.state_dir / "broken.txt").write_bytes(b"previous view\n")
        broken = {
            "id": "broken",
            "argv": python_argv("import sys\nsys.stdout.write('half')\nsys.exit(1)\n", self.tools, "broken"),
            "output": "broken.txt",
        }
        renderers = [broken, {"id": "summary", "builtin": "markdown", "output": "delivery.md"}]
        code, result = wrapper(self.state_dir, "--state-config", self.config(renderers))
        self.assertEqual(code, 1)
        self.assertEqual(result["ok"], False)
        self.assertEqual((self.state_dir / "broken.txt").read_bytes(), b"previous view\n")
        self.assertTrue((self.state_dir / "delivery.md").exists())
        self.assertEqual([entry["ok"] for entry in result["renderers"]], [False, True])

    def test_the_binding_state_member_is_read(self) -> None:
        binding = self.root / "orchestration.json"
        binding.write_text(
            json.dumps({"base": {"remote": "origin", "ref": "main"}, "state": json.loads(self.config([self.capture_renderer("echo")]))}),
            encoding="utf-8",
        )
        code, result = wrapper(self.state_dir, "--binding", str(binding))
        self.assertEqual(code, 0, result)
        self.assertTrue((self.state_dir / "echo.json").exists())

    def test_without_a_configuration_both_built_ins_render(self) -> None:
        code, result = wrapper(self.state_dir)
        self.assertEqual(code, 0, result)
        self.assertEqual([entry["output"] for entry in result["renderers"]], ["delivery.md", "delivery.html"])

    def test_an_unsafe_configuration_is_refused_before_anything_runs(self) -> None:
        cases = {
            "escaping output": [{"id": "x", "builtin": "markdown", "output": "../x.md"}],
            "absolute output": [{"id": "x", "builtin": "markdown", "output": str(self.root / "x.md")}],
            "state file": [{"id": "x", "builtin": "markdown", "output": "state.json"}],
            "journal file": [{"id": "x", "builtin": "markdown", "output": "journal.jsonl"}],
            "state file in another case": [{"id": "x", "builtin": "markdown", "output": "STATE.json"}],
            "journal file in another case": [{"id": "x", "builtin": "markdown", "output": "Journal.jsonl"}],
            "lock file in another case": [{"id": "x", "builtin": "markdown", "output": "Queue.LOCK"}],
            "unknown builtin": [{"id": "x", "builtin": "slides", "output": "x"}],
            "both kinds": [{"id": "x", "builtin": "markdown", "argv": ["true"], "output": "x"}],
            "shell string": [{"id": "x", "argv": "cat state.json", "output": "x"}],
            "duplicate output": [
                {"id": "x", "builtin": "markdown", "output": "a.md"},
                {"id": "y", "builtin": "markdown", "output": "a.md"},
            ],
        }
        for name, renderers in cases.items():
            with self.subTest(name):
                code, result = wrapper(self.state_dir, "--state-config", self.config(renderers))
                self.assertEqual(code, 2, result)
                self.assertEqual(result["code"], "config_invalid")
        self.assertEqual(sorted(path.name for path in self.state_dir.iterdir() if not path.name.endswith(".lock")),
                         ["changes.jsonl", "journal.jsonl", "state.json"])

    def test_a_view_is_never_written_through_a_link(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        link(self, self.state_dir / "views", outside)
        link(self, self.state_dir / "alias.md", self.state_dir / "state.json")
        before = (self.state_dir / "state.json").read_bytes()
        renderers = [
            {"id": "escape", "builtin": "markdown", "output": "views/escaped.md"},
            {"id": "alias", "builtin": "markdown", "output": "alias.md"},
            {"id": "plain", "builtin": "markdown", "output": "plain/delivery.md"},
        ]
        code, result = wrapper(self.state_dir, "--state-config", self.config(renderers))
        self.assertEqual(code, 1, result)
        self.assertEqual([entry["ok"] for entry in result["renderers"]], [False, False, True])
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual((self.state_dir / "state.json").read_bytes(), before)

    def test_a_link_back_to_the_state_directory_cannot_create_a_reserved_file(self) -> None:
        link(self, self.state_dir / "self", self.state_dir)
        renderers = [
            {"id": "lock", "builtin": "markdown", "output": "self/Queue.lock"},
            {"id": "state", "builtin": "markdown", "output": "self/STATE.json"},
            {"id": "fine", "builtin": "markdown", "output": "self/delivery.md"},
        ]
        code, result = wrapper(self.state_dir, "--state-config", self.config(renderers))
        self.assertEqual(code, 1, result)
        self.assertEqual([entry["ok"] for entry in result["renderers"]], [False, False, True])
        self.assertNotIn("queue.lock", {path.name.lower() for path in self.state_dir.iterdir()})
        state_tool(self.state_dir, "validate")

    def test_the_result_is_ascii_and_a_configuration_on_stdin_may_carry_a_byte_order_mark(self) -> None:
        config = self.config([self.capture_renderer("echo")]).encode("utf-8")
        result = subprocess.run(
            [sys.executable, "-B", str(WRAPPER), "--dir", str(self.state_dir), "--state-config", "-"],
            input=b"\xef\xbb\xbf" + config,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result.stdout.decode("ascii")
        self.assertTrue(json.loads(result.stdout)["ok"])
        # The renderer received the document as UTF-8, its non-ASCII text intact.
        received = json.loads((self.state_dir / "echo.json").read_bytes().decode("utf-8"))
        self.assertEqual(received, self.document)

    def test_a_result_with_non_ascii_text_reaches_any_console(self) -> None:
        publisher = {
            "id": "magasin-caf\u00e9",
            "argv": python_argv(
                "import sys\nsys.stderr.buffer.write('store\\u2028down \\u2713'.encode('utf-8'))\nsys.exit(4)\n",
                self.tools,
                "nonascii",
            ),
        }
        result = subprocess.run(
            [sys.executable, "-B", str(WRAPPER), "--dir", str(self.state_dir), "--state-config", self.config(publishers=[publisher])],
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout.decode("ascii"))
        self.assertEqual(document["publishers"][0]["id"], "magasin-caf\u00e9")
        self.assertIn("store\u2028down \u2713", document["publishers"][0]["error"])

    def test_a_configuration_that_is_not_utf8_is_refused(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", str(WRAPPER), "--dir", str(self.state_dir), "--state-config", "-"],
            input=b'{"renderers": [], "id": "\xff"}',
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["code"], "config_invalid")

    def test_the_wrapper_never_writes_the_state(self) -> None:
        before = {name: (self.state_dir / name).read_bytes() for name in ("state.json", "journal.jsonl")}
        wrapper(self.state_dir, "--state-config", self.config([self.capture_renderer("echo")]))
        after = {name: (self.state_dir / name).read_bytes() for name in ("state.json", "journal.jsonl")}
        self.assertEqual(before, after)


class MarkdownRendererTests(DeliveryStateFixture):
    def render(self) -> str:
        view = render_state.render_markdown(json.loads(json.dumps(self.document, ensure_ascii=False)))
        self.assertTrue(view)
        return view

    def test_the_record_numbers_survive_markdown_list_semantics(self) -> None:
        # Markdown renumbers an ordered list from its first marker and nests
        # one inside a bullet, so the view must never lean on its numbering.
        state = json.loads(json.dumps(FIXED_STATE))
        item = state["queue"][0]
        state["queue"] = [
            {**item, "order": order, "subject": f"change-{order}",
             "checks": [{"id": "unit", "state": "passed"}] if order == 7 else []}
            # Zero and an order longer than Markdown's nine-digit list numbers too.
            for order in (12, 3, 7, 0, 1234567890)
        ]
        journal = [{**FIXED_JOURNAL[1], "id": entry_id} for entry_id in (1, 2, 10)]
        view = render_state.render_markdown({"state": state, "journal": journal})
        lines = view.splitlines()
        for line in lines:
            with self.subTest(line=line):
                # No line, nested or not, opens an ordered list.
                self.assertNotRegex(line, r"^\s*(?:[-*+]\s+)*\d{1,9}[.)](?:\s|$)")
        queue = lines[lines.index("## Queue") + 2 : lines.index("## Planned") - 1]
        self.assertEqual(
            [line.split(":")[0] for line in queue],
            ["- 0\\. change-0", "- 3\\. change-3", "- 7\\. change-7", "  - unit", "- 12\\. change-12",
             "- 1234567890\\. change-1234567890"],
        )
        rows = lines[lines.index("## Journal") + 2 :]
        # Newest first, each with the id the journal holds.
        self.assertEqual([row.split(" ")[1] for row in rows], ["10\\.", "2\\.", "1\\."])

    def test_state_text_renders_inert(self) -> None:
        view = self.render()
        # Every character that could open a link, an image, raw HTML or an
        # autolink is backslash-escaped, so no Markdown renderer reads it as markup.
        for raw in ("</script>", "<img", "[click]", "](javascript", "<b>", "https://", "[docs]"):
            with self.subTest(raw=raw):
                self.assertNotRegex(view, r"(?<!\\)" + re.escape(raw))
        self.assertIn("\\</script\\>", view)
        self.assertIn("\\[click\\](javascript:alert(1))", view)
        self.assertNotRegex(view, r"(?<!\\)[<>\[\]]")
        # Line separators cannot start a new block.
        for separator in ("\u2028", "\u2029", "\r"):
            self.assertNotIn(separator, view)
        self.assertIn("next", view)
        # Emphasis, code, tables, strike markers, entities and comments are
        # escaped too, and a newline in state text cannot open an indented
        # code block. A "#" or "!" mid-line changes nothing, so it stays plain.
        for escaped in ("\\*em\\*", "\\_u\\_", "\\`code\\`", "\\|", "\\~", "\\&", "\\<!--", "# ! \\|"):
            self.assertIn(escaped, view)
        self.assertNotIn("\n    indented", view)
        self.assertIn("indented", view)

    def test_it_shows_the_delivery(self) -> None:
        view = self.render()
        self.assertTrue(view.startswith("# "))
        for heading in ("## Checking", "## Queue", "## Lanes", "## Finish line", "## Follow-ups", "## Leases", "## Journal"):
            self.assertIn(heading, view)
        self.assertIn("change-1", view)
        # Non-ASCII state text arrives exactly, whatever the console encoding.
        self.assertIn("caf\u00e9 \u2713", view)
        self.assertIn("queued", view)
        self.assertIn("decision.recorded", view)

    def test_only_meaningful_punctuation_is_escaped(self) -> None:
        text = render_state._markdown_text
        # Identifiers, times, prose punctuation and paths read as they are.
        for plain in ("change-1", "decision.recorded", "2026-10-20T10:00:00Z", "a/b.c (d), e; f? g! h' \"i\" = 4+1 %",
                      "a-b+c#d", "lane 2. ready", "path/to/file.md"):
            with self.subTest(plain=plain):
                self.assertEqual(text(plain), plain)
        # Inline markup, links, raw HTML and entities anywhere in a value.
        for raw, escaped in (("a\\b", "a\\\\b"), ("`c`", "\\`c\\`"), ("*e* _u_", "\\*e\\* \\_u\\_"), ("~~s~~", "\\~\\~s\\~\\~"),
                             ("[l](u)", "\\[l\\](u)"), ("![i](u)", "\\!\\[i\\](u)"), ("<b>", "\\<b\\>"), ("&amp;", "\\&amp;"),
                             ("a|b", "a\\|b"), ("a $x^2$ b", "a \\$x^2\\$ b")):
            with self.subTest(raw=raw):
                self.assertEqual(text(raw), escaped)
        # A trailing "#" run after a space would close the headline's heading.
        for raw, escaped in (("Fix issue #", "Fix issue \\#"), ("Fix ##", "Fix \\##"), ("C# and F#", "C# and F#")):
            with self.subTest(raw=raw):
                self.assertEqual(text(raw), escaped)
        # Bare autolinks: a scheme's "://", "www." and e-mail addresses.
        for raw, escaped in (("https://x.test/p", "https\\://x.test/p"), ("see WWW.x.test", "see WWW\\.x.test"),
                             ("a@b.co", "a\\@b.co"), ("mailto:a@b.co", "mailto:a\\@b.co")):
            with self.subTest(raw=raw):
                self.assertEqual(text(raw), escaped)
        # At the start of a value, which can start a line or list-item content:
        # headings, list markers, quotes, tables and ordered-list markers.
        for raw, escaped in (("# h", "\\# h"), ("- l", "\\- l"), ("+ l", "\\+ l"), ("> q", "\\> q"), ("| t", "\\| t"),
                             ("12. o", "12\\. o"), ("3) o", "3\\) o"), ("  # h", "\\# h"), ("12 o", "12 o"),
                             ("#", "\\#"), ("-", "\\-"), ("7.", "7\\."),
                             ("## h", "\\## h"), ("###### h", "\\###### h"), ("##", "\\##"),
                             ("---", "\\---"), ("-----", "\\-----"), ("-- -", "\\-- -"), ("--", "--"), ("---x", "---x"),
                             # Some renderers take any Unicode space after the marker.
                             ("#\u00a0h", "\\#\u00a0h"), ("#\u3000h", "\\#\u3000h"), ("#\ufeffh", "\\#\ufeffh"),
                             ("-\u00a0x", "\\-\u00a0x"), ("1.\u2003x", "1\\.\u2003x"),
                             # Not a block marker without a following space.
                             ("#tag", "#tag"), ("-x", "-x"), ("+1", "+1"), ("1.5", "1.5")):
            with self.subTest(raw=raw):
                self.assertEqual(text(raw), escaped)


class StaticPageTests(DeliveryStateFixture):
    def render(self, document: dict | None = None) -> str:
        page = render_state.render_static_page(json.loads(json.dumps(document or self.document, ensure_ascii=False)))
        self.assertTrue(page)
        return page

    def embedded(self, page: str) -> str:
        match = re.search(r'<script type="application/json" id="delivery-state">(.*?)</script>', page, re.S)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_the_state_is_embedded_escaped_and_round_trips(self) -> None:
        page = self.render()
        embedded = self.embedded(page)
        for raw in ("<", ">", "&", "\u2028", "\u2029"):
            self.assertNotIn(raw, embedded)
        self.assertEqual(json.loads(embedded), self.document)
        # Only the template's own script elements close.
        self.assertEqual(page.count("</script>"), 2)

    def test_the_page_makes_no_external_references(self) -> None:
        page = self.render().lower()
        for construct in (
            "src=", "href=", "url(", "@import", "<link", "<iframe", "<object", "<embed", "<img",
            "fetch(", "xmlhttprequest", "import(", "eventsource", "websocket", "sendbeacon",
            "//", "innerhtml", "outerhtml", "insertadjacenthtml", "document.write",
        ):
            with self.subTest(construct):
                self.assertNotIn(construct, page.replace(self.embedded(page).lower(), ""))

    def test_the_page_template_passes_the_leakage_scan(self) -> None:
        for path in (WRAPPER,):
            contents = path.read_text(encoding="utf-8")
            for class_id, pattern in LEAKAGE_PATTERNS:
                with self.subTest(path=path.name, pattern=class_id):
                    self.assertIsNone(pattern.search(contents))
        page = self.render(self._plain_document())
        for class_id, pattern in LEAKAGE_PATTERNS:
            with self.subTest(output=class_id):
                self.assertIsNone(pattern.search(page))
        for banned in ("http-equiv", "xmlns", "linear-gradient", "linear", "credentials"):
            self.assertNotIn(banned, page.lower())

    def _plain_document(self) -> dict:
        document = copy.deepcopy(self.document)
        document["state"]["meta"]["headline"] = "Plain headline"
        return json.loads(json.dumps(document).replace("https://example.test", "example").replace(HOSTILE, "plain"))


# Continuous integration must run the page's script; elsewhere it needs a runtime.
@unittest.skipUnless(shutil.which("node") or os.environ.get("CI"), "the page's script runs under a JavaScript runtime")
class StaticPageScriptTests(DeliveryStateFixture):
    """Run the page's own script against a minimal document object."""

    SHIM = r"""
const fs = require("fs");
const [pagePath, scenarioPath] = process.argv.slice(2);
const page = fs.readFileSync(pagePath, "utf8");
const scenario = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));
const scripts = [...page.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)];
const embedded = scripts.find((m) => m[1].includes('id="delivery-state"'))[2];
const program = scripts.find((m) => !m[1].includes("application/json"))[2];
const created = [];
function node(tag) {
  const el = { tagName: tag, children: [], attributes: {}, _text: "", className: "", hidden: false,
    set textContent(v) { this._text = String(v); this.children = []; },
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); },
    appendChild(c) { this.children.push(c); return c; },
    replaceChildren(...cs) { this.children = cs; this._text = ""; },
    setAttribute(k, v) { this.attributes[k] = String(v); } };
  created.push(el);
  return el;
}
const indicator = node("p");
indicator.hidden = true;  // as the page's own markup starts it
const byId = { "delivery-state": { textContent: embedded }, app: node("main"), "live-status": indicator };
if (scenario.now) { Date.now = () => Date.parse(scenario.now); }
// The page's redraw timer is held here and fired on demand, so no test waits on it.
globalThis.setInterval = (callback, ms) => { globalThis.__tick = callback; globalThis.__every = ms; return 1; };
globalThis.document = {
  getElementById: (id) => byId[id] || null,
  createElement: node,
  createTextNode: (t) => ({ textContent: String(t) }),
};
process.on("unhandledRejection", (error) => { process.stderr.write("unhandled " + error); process.exit(3); });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
if (scenario.adapter) {
  const adapter = scenario.adapter;
  const answer = () => {
    if (adapter.throwingGetter) {
      return { journal: [], get state() { throw new Error("hostile getter"); } };
    }
    return adapter.document;
  };
  globalThis.deliveryStateStore = {
    read: async () => {
      await wait(adapter.readDelayMs || 0);
      if (adapter.readRejects) { throw new Error("store down"); }
      return answer();
    },
    subscribe: adapter.subscribeThrows
      ? (cb) => { if (scenario.pushDuringSubscribe) { cb(scenario.pushDuringSubscribe); } throw new Error("cannot subscribe"); }
      : adapter.subscribe ? (cb) => { globalThis.__push = cb; return () => {}; } : undefined,
    onStatus: adapter.status ? (cb) => { globalThis.__status = cb; } : undefined,
  };
}
new Function(program)();
setTimeout(async () => {
  if (scenario.push && globalThis.__push) { globalThis.__push(scenario.push); }
  if (scenario.status !== undefined && globalThis.__status) { globalThis.__status(scenario.status); }
  if (scenario.tickTo && globalThis.__tick) { Date.now = () => Date.parse(scenario.tickTo); globalThis.__tick(); }
  await wait(10 + ((scenario.adapter && scenario.adapter.readDelayMs) || 0));
  const tags = [...new Set(created.map((e) => e.tagName))].sort();
  process.stdout.write(JSON.stringify({ text: byId.app.textContent, tags, every: globalThis.__every || null,
    summaries: created.filter((e) => e.className === "summary").length,
    source: byId.app.attributes["data-source"] || null,
    status: byId["live-status"].hidden ? null : byId["live-status"].textContent }));
}, 10);
"""

    def run_page(self, scenario: dict, document: dict | None = None) -> dict:
        page = self.root / "page.html"
        page.write_text(
            render_state.render_static_page(json.loads(json.dumps(document or self.document, ensure_ascii=False))),
            encoding="utf-8",
        )
        shim = self.root / "shim.cjs"
        shim.write_text(self.SHIM, encoding="utf-8")
        scenario_path = self.root / "scenario.json"
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
        result = run(["node", str(shim), str(page), str(scenario_path)])
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def embed_of(self, page: str) -> str:
        return re.search(r'<script type="application/json" id="delivery-state">(.*?)</script>', page, re.S).group(1)

    def live_document(self, headline: str, schema_version: str = "delivery-state/1") -> dict:
        document = copy.deepcopy(self.document)
        document["state"]["meta"]["headline"] = headline
        document["state"]["schemaVersion"] = schema_version
        return document

    def test_large_counts_are_whole_numbers_never_exponents(self) -> None:
        document = copy.deepcopy(self.document)
        subject = document["state"]["queue"][0]["subject"]
        stats = document["stats"][subject]
        stats.update({"rounds": 10 ** 7, "roundsSource": "journal",
                      "findings": {"P0": -0.0, "P1": 10 ** 7, "P2": 10 ** 21, "P3": 123456789012}})
        shown = self.run_page({}, document)
        self.assertIn("10000000 rounds, findings P0/P1/P2/P3 0/10000000/1000000000000000000000/123456789012",
                      shown["text"])
        self.assertNotRegex(shown["text"], r"\de[+-]?\d")


    def test_the_page_shows_timing_failures_and_estimates_as_the_markdown_does(self) -> None:
        # At the moment of the record's last write, the page reads as the markdown does.
        shown = self.run_page({"now": "2026-10-20T12:00:00Z"}, visibility_document())
        for line in VISIBILITY_LINES:
            with self.subTest(line=line):
                self.assertIn(line, shown["text"])

    def test_a_page_whose_clock_is_behind_the_record_counts_from_the_records_last_write(self) -> None:
        shown = self.run_page({"now": "2026-10-20T11:59:00Z"}, visibility_document())
        self.assertIn("unit: running · 1m 40s of an expected 10m 00s", shown["text"])

    def test_fractions_of_a_second_never_make_the_views_disagree(self) -> None:
        document = visibility_document()
        document["state"]["meta"]["updatedAt"] = "2026-10-20T12:00:00.100Z"
        document["state"]["meta"]["checking"]["now"]["steps"] = [
            {"label": "unit", "state": "running", "startedAt": "2026-10-20T11:58:20.900Z", "expectedSeconds": 600}]
        line = "unit: running · 1m 40s of an expected 10m 00s"
        self.assertIn(render_state._markdown_text(line), render_state.render_markdown(document))
        self.assertIn(line, self.run_page({"now": "2026-10-20T12:00:00.100Z"}, document)["text"])

    def test_the_page_shows_tracker_titles_then_summaries_as_the_markdown_does(self) -> None:
        text = self.run_page({}, ticket_document())["text"]
        for line in TICKET_LINES:
            with self.subTest(line=line):
                self.assertIn(line, text)
        self.assertLess(text.index("change-2: Tracker title two"), text.index("Shows each ticket's summary from the tracker."))
        self.assertLess(text.index("Shows each ticket's summary from the tracker."), text.index("Lane none"),
                        "the summary comes right after the title")
        self.assertNotIn("change-2: " + HOSTILE, text)
        self.assertIn("change-1: Change one", text)
        self.assertLess(text.index("change-3: Tracker title three"), text.index("Plans the third change."))
        # A follow-up's ticket is found by its reference before its id.
        document = ticket_document()
        document["state"]["tickets"]["follow-1"] = {"title": "By its id", "summary": "Found by id."}
        text = self.run_page({}, document)["text"]
        self.assertIn("Tracker follow-up", text)
        self.assertNotIn("By its id", text)

    def test_the_page_works_out_expected_starts_from_its_own_clock(self) -> None:
        # Nothing running and a quiet record: an hour on, the next start is now, never an hour ago.
        document = visibility_document()
        state = document["state"]
        state["meta"]["checking"] = {"now": None, "next": []}
        state["queue"][0]["stage"] = "merged"
        document["estimates"] = {"change-2": {"position": 1, "expectedStart": "2026-10-20T12:00:00Z"}}
        text = self.run_page({"now": "2026-10-20T13:00:00Z"}, document)["text"]
        self.assertIn("position 1 · expected to start around 2026-10-20T13:00:00Z (an estimate)", text)
        self.assertNotIn("12:00:00Z (an estimate)", text)

    def test_a_live_page_stays_live_and_a_fallback_keeps_its_note_when_drawn_again(self) -> None:
        later = "2026-10-20T13:00:00Z"
        shown = self.run_page({"adapter": {"document": self.live_document("Live headline")}, "tickTo": later})
        self.assertEqual(shown["source"], "live")
        self.assertIn("Live headline", shown["text"])
        shown = self.run_page({"adapter": {"document": self.live_document("Future headline", "delivery-state/2")},
                               "tickTo": later})
        self.assertEqual(shown["source"], "embedded")
        self.assertIn("Showing the saved state", shown["text"])

    def test_a_blank_summary_draws_nothing_and_both_views_agree_on_blank(self) -> None:
        document = ticket_document()
        for text in ("  ", "\u001c\u0085\u3000", "\ufeff"):
            with self.subTest(summary=repr(text)):
                document["state"]["tickets"] = {"change-2": {"title": "Tracker title two", "summary": text}}
                blank = not text.strip()
                self.assertEqual(self.run_page({}, document)["summaries"], 0 if blank else 1)
                self.assertEqual("summary:" not in render_state.render_markdown(document), blank)
        self.assertEqual(self.run_page({}, ticket_document())["summaries"], 3)

    def test_an_open_page_draws_itself_again_on_a_quiet_record(self) -> None:
        document = visibility_document()
        document["state"]["meta"]["checking"] = {"now": None, "next": []}
        document["state"]["queue"][0]["stage"] = "merged"
        shown = self.run_page({"now": "2026-10-20T12:00:00Z", "tickTo": "2026-10-20T12:45:00Z"}, document)
        self.assertEqual(shown["every"], 30000)
        self.assertIn("position 1 · expected to start around 2026-10-20T12:45:00Z (an estimate)", shown["text"])
        self.assertNotIn("12:00:00Z (an estimate)", shown["text"])

    def test_the_page_s_expected_starts_agree_with_the_state_tool_s(self) -> None:
        now = "2026-10-20T12:00:00.250Z"
        running = {"subject": "change-1", "title": "Change one", "startedAt": "2026-10-20T11:58:20.900Z", "steps": []}
        cases = {
            "a run in progress, five of six runs counted": (running, [50000, 500, 600, 600, 700, 9000]),
            "no history": (running, None),
            "an overrunning run": ({**running, "startedAt": "2026-10-20T10:00:00Z"}, [600] * 5),
            "an even count": (None, [100, 200, 300, 400]),
            "a start in the future": ({**running, "startedAt": "2026-10-20T13:00:00Z"}, [600] * 5),
            "an absurd history": (None, [10 ** 15] * 5),
            "a history past the calendar": (None, [4 * 10 ** 11] * 5),
            "a history far past the calendar": (None, [10 ** 12] * 5),
            # The second start lands one second into the year 10000, which the state tool cannot write.
            "a history just past the calendar": (None, [int((datetime.datetime(9999, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc)
                                                              - datetime.datetime(2026, 10, 20, 12, 0, tzinfo=datetime.timezone.utc)).total_seconds()) + 2] * 5),
            "a start partway through a second": ({**running, "startedAt": "2026-10-20T11:58:20.400Z"}, [600] * 5),
        }
        for name, (current, runs) in cases.items():
            with self.subTest(case=name):
                state = copy.deepcopy(FIXED_STATE)
                state["meta"]["updatedAt"] = now
                state["meta"]["checking"] = {"now": current, "next": []}
                state["queue"] = [
                    {**FIXED_STATE["queue"][0], "stage": "checking" if current else "merged"},
                    {**FIXED_STATE["queue"][1], "order": 3, "subject": "change-z"},
                    {**FIXED_STATE["queue"][1], "order": 2, "subject": "change-y"},
                    # Tied in order: the subjects decide, code point by code point.
                    {**FIXED_STATE["queue"][1], "order": 4, "subject": "\U0001F600-a"},
                    {**FIXED_STATE["queue"][1], "order": 4, "subject": "\uFFFD-b"},
                    # One subject the start of the other: the shorter goes first.
                    {**FIXED_STATE["queue"][1], "order": 5, "subject": "tie-ab"},
                    {**FIXED_STATE["queue"][1], "order": 5, "subject": "tie-a"},
                ]
                if runs is not None:
                    state["meta"]["checkHistory"] = {"checks": {}, "runs": runs}
                expected = delivery_state.queue_estimates(state, at=now)
                text = self.run_page({"now": now}, {"state": state, "journal": []})["text"]
                for subject, estimate in expected.items():
                    # Each estimate on its own row: the first after the row's subject.
                    row = text.index(f"{subject}: ")
                    shown = text[row:text.index("(an estimate)", row) + len("(an estimate)")]
                    self.assertTrue(shown.endswith(render_state._estimate_text(estimate)), (subject, shown))

    def test_the_page_counts_a_running_check_live_from_its_own_clock(self) -> None:
        # Five minutes after the last write, with no write since, the page still counts on.
        shown = self.run_page({"now": "2026-10-20T12:05:00Z"}, visibility_document())
        self.assertIn("unit: running · 6m 40s of an expected 10m 00s", shown["text"])
        self.assertIn("docs: running · 6m 00s, no expected time yet", shown["text"])

    def test_the_page_shows_the_status_and_a_derived_focus(self) -> None:
        shown = self.run_page({}, status_document("override"))
        self.assertIn("Status: Checking Change one · next: change-2 (set by the lead, until 2026-10-20T16:00:00Z)",
                      shown["text"])
        self.assertIn("change-2: queued, 1 review round (derived)", shown["text"])

    def test_without_an_adapter_the_embedded_state_is_shown(self) -> None:
        shown = self.run_page({})
        self.assertIn(HOSTILE, shown["text"])
        self.assertEqual(shown["source"], "embedded")

    def test_hostile_text_stays_text(self) -> None:
        shown = self.run_page({})
        # The hostile markup is shown as characters, and no element it names exists.
        self.assertIn("</script><img src=x onerror=alert(1)>", shown["text"])
        self.assertNotIn("img", shown["tags"])
        self.assertNotIn("script", shown["tags"])
        self.assertNotIn("a", shown["tags"])

    def test_a_live_store_with_the_same_schema_is_shown(self) -> None:
        shown = self.run_page({"adapter": {"document": self.live_document("Live headline")}})
        self.assertIn("Live headline", shown["text"])
        self.assertEqual(shown["source"], "live")

    def test_a_live_store_with_a_different_schema_version_falls_back_to_the_embedded_state(self) -> None:
        shown = self.run_page({"adapter": {"document": self.live_document("Future headline", "delivery-state/2")}})
        self.assertNotIn("Future headline", shown["text"])
        self.assertIn(HOSTILE, shown["text"])
        self.assertEqual(shown["source"], "embedded")

    def test_a_malformed_live_answer_falls_back_to_the_embedded_state(self) -> None:
        for answer in (None, {"state": None}, {"state": {"schemaVersion": "delivery-state/1"}}, "text"):
            with self.subTest(answer=answer):
                shown = self.run_page({"adapter": {"document": answer}})
                self.assertIn(HOSTILE, shown["text"])
                self.assertEqual(shown["source"], "embedded")

    def test_the_fallback_says_why(self) -> None:
        mismatch = self.run_page({"adapter": {"document": self.live_document("Future", "delivery-state/2")}})
        self.assertIn("did not match this page's state version", mismatch["text"])
        unread = self.run_page({"adapter": {"document": None, "readRejects": True}})
        self.assertIn("could not be read", unread["text"])
        self.assertEqual(unread["source"], "embedded")

    def test_a_live_answer_that_cannot_be_drawn_falls_back_to_the_embedded_state(self) -> None:
        broken = self.live_document("Broken headline")
        del broken["state"]["queue"][0]["checks"]
        shown = self.run_page({"adapter": {"document": broken}})
        self.assertEqual(shown["source"], "embedded")
        self.assertIn("could not be shown", shown["text"])
        self.assertNotIn("Broken headline", shown["text"])

    def test_the_page_writes_each_stop_s_stats_as_the_markdown_view_does(self) -> None:
        stats = {}
        planned = []
        for index, seconds in enumerate((None, 0, 59, 60, 3599, 3600, 86399, 86400, 90061)):
            subject = f"stop-{index}"
            planned.append({"subject": subject, "title": subject, "lane": None, "stage": "planned"})
            stats[subject] = {"buildSeconds": seconds, "reviewSeconds": seconds, "waitSeconds": None,
                              "checkToMergeSeconds": seconds, "totalSeconds": seconds,
                              "rounds": index or None, "roundsSource": ("journal", "reported")[index % 2] if index else None,
                              "findings": {"P0": index, "P1": 0, "P2": 1, "P3": 2} if index % 2 == 0 else None}
        document = copy.deepcopy(self.document)
        document["state"]["planned"] = planned
        document["stats"] = stats
        shown = self.run_page({}, document)
        for subject, found in stats.items():
            with self.subTest(subject=subject):
                self.assertIn(render_state._stats_text(found), shown["text"])

    def test_a_live_document_without_stats_or_planned_work_is_still_shown(self) -> None:
        live = self.live_document("Live without stats")
        live.pop("stats", None)
        live["state"].pop("planned", None)
        shown = self.run_page({"adapter": {"document": live}})
        self.assertEqual(shown["source"], "live")
        self.assertIn("Nothing planned outside the queue.", shown["text"])

    def test_a_live_store_s_connection_shows_quietly(self) -> None:
        live = {"document": self.live_document("Live"), "subscribe": True, "status": True}
        self.assertEqual(self.run_page({"adapter": live, "status": True})["status"], "Live")
        down = self.run_page({"adapter": live, "status": False})["status"]
        self.assertTrue(down.startswith("Disconnected"), down)
        self.assertIsNone(self.run_page({})["status"], "a page with no live store shows no indicator")

    def test_the_live_page_loads_nothing_but_its_own_server(self) -> None:
        page = render_state.render_static_page(json.loads(json.dumps(self.document)), live=True).lower()
        self.assertIn('eventsource("/events")', page)
        self.assertIn('fetch("/document")', page)
        for construct in ("src=", "href=", "url(", "@import", "<link", "//", "innerhtml", "document.write"):
            with self.subTest(construct):
                self.assertNotIn(construct, page.replace(self.embed_of(page), ""))

    def test_a_hostile_live_answer_falls_back_without_an_unhandled_error(self) -> None:
        shown = self.run_page({"adapter": {"throwingGetter": True}})
        self.assertEqual(shown["source"], "embedded")
        self.assertIn(HOSTILE, shown["text"])

    def test_a_pushed_update_wins_over_a_slower_read(self) -> None:
        shown = self.run_page(
            {
                "adapter": {"document": self.live_document("Older read"), "subscribe": True, "readDelayMs": 30},
                "push": self.live_document("Newer push"),
            }
        )
        self.assertIn("Newer push", shown["text"])
        self.assertNotIn("Older read", shown["text"])
        self.assertEqual(shown["source"], "live")

    def test_a_failed_read_after_a_push_keeps_the_pushed_state(self) -> None:
        shown = self.run_page(
            {
                "adapter": {"document": None, "subscribe": True, "readDelayMs": 30, "readRejects": True},
                "push": self.live_document("Newer push"),
            }
        )
        self.assertIn("Newer push", shown["text"])
        self.assertEqual(shown["source"], "live")

    def test_a_store_that_cannot_subscribe_is_still_read(self) -> None:
        shown = self.run_page({"adapter": {"document": self.live_document("Live headline"), "subscribeThrows": True}})
        self.assertIn("Live headline", shown["text"])
        self.assertEqual(shown["source"], "live")

    def test_a_push_made_while_subscribing_survives_a_failed_subscribe(self) -> None:
        shown = self.run_page(
            {
                "adapter": {"document": self.live_document("Older read"), "subscribeThrows": True, "readDelayMs": 30},
                "pushDuringSubscribe": self.live_document("Pushed while subscribing"),
            }
        )
        self.assertIn("Pushed while subscribing", shown["text"])
        self.assertNotIn("Older read", shown["text"])

    def test_a_pushed_update_with_a_different_schema_version_is_ignored(self) -> None:
        shown = self.run_page(
            {
                "adapter": {"document": self.live_document("Live headline"), "subscribe": True},
                "push": self.live_document("Pushed future", "delivery-state/2"),
            }
        )
        self.assertNotIn("Pushed future", shown["text"])
        self.assertIn("Live headline", shown["text"])


if __name__ == "__main__":
    unittest.main()
