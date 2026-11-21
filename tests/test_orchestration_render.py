from __future__ import annotations

import contextlib
import copy
import datetime
import hashlib
import html
import http.client
import http.server
import importlib.util
import ipaddress
import json
import os
import re
import select
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

import agent_skills
from agent_skills.validate import LEAKAGE_PATTERNS


ROOT = Path(__file__).resolve().parents[1]
# The product under test: this repository, or the extracted release candidate that
# qualification puts first on the path, so its shipped scripts are the ones exercised.
PRODUCT_ROOT = Path(agent_skills.__file__).resolve().parents[1]
SCRIPTS = PRODUCT_ROOT / "skills/orchestrate-work/scripts"
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
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--text", HOSTILE, "--why", "A test.", "--lane", "lane-1")
        # What a renderer reads: the state tool's show, without its publication status.
        # What every renderer and publisher receives: the record shown, with the story the narrator tells of it.
        self.document = render_state.with_story({key: value for key, value in state_tool(self.state_dir, "show").items() if key != "publication"},
                                                self.state_dir)

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
        (signal.SIGINT,), (signal.SIGTERM,), *(() if os.name == "nt" else ((signal.SIGHUP,),)),
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
    {"id": 1, "at": "2026-10-20T10:00:00Z", "kind": "lane.assigned", "lane": "lane-1", "subject": "change-1",
     "data": {"title": "Assigned " + HOSTILE}},
    {"id": 2, "at": "2026-10-20T10:40:00Z", "kind": "review.round", "lane": "lane-1", "subject": "change-1",
     "data": {"round": 1, "startedAt": "2026-10-20T10:30:00Z", "finishedAt": "2026-10-20T10:40:00Z",
                "verdicts": {"outcome": "changes", "adversarial": "changes"},
                "findings": {"P0": 0, "P1": 1, "P2": 2, "P3": 0}}},
    {"id": 3, "at": "2026-10-20T10:50:00Z", "kind": "review.round", "lane": "lane-1", "subject": "change-1",
     "data": {"round": 2, "startedAt": "2026-10-20T10:45:00Z", "finishedAt": "2026-10-20T10:50:00Z",
                "verdicts": {"outcome": "approve", "adversarial": "approve"},
                "findings": {"P0": 0, "P1": 0, "P2": 0, "P3": 1}}},
    {"id": 4, "at": "2026-10-20T11:00:00Z", "kind": "candidate.handed", "lane": "lane-1", "subject": "change-1",
     "data": {"head": FIXED_STATE["queue"][0]["head"], "changeRequest": "change request 1", "checks": []}},
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
    "delivery.md": "b5eacc9b0d03fc036d219eca434188b86136421f6187692534f5d67f68cdc855",
    "delivery.html": "c49423aaad036dffd2ecab4960704a5b114b66d35abf7f98cc2fa2600bb5d5be",
}
PINNED_VIEWS = {
    "delivery.md": "2fc32135cd66da2136a144514b9ae7d9a78f93704b0350dd5df3ab0bf74ce29e",
    "delivery.html": "4519dc06c6329e040203c0db190779d756e8b3861b4379814508ea7e8a46f768",
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


class BindingStateTests(DeliveryStateFixture):
    """The binding's `state` member, as `show` and the wrapper read it. A publisher is an argv: one with a kind and no
    argv, which an earlier generation kept for the lead to post by hand, is refused naming the fix."""

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

    def test_every_state_field_the_binding_schema_declares_renders_and_no_other(self) -> None:
        schemas = json.loads((ROOT / "skills/orchestrate-work/references/schemas.json").read_text(encoding="utf-8"))
        fields = schemas["orchestration-binding/1"]["properties"]["state"]["properties"]
        # Values the schema allows; the wrapper checks snapshotMaxAgeSeconds and only has to accept the rest.
        samples = {"snapshotMaxAgeSeconds": 60, "statusOverrideMaxAgeSeconds": 600,
                   "trackerSnapshot": {"argv": ["tool", "{parent}"], "timeoutSeconds": 30},
                   "workHints": {"argv": ["tool", "{parent}"], "timeoutSeconds": 30},
                   "forgeSnapshot": {"argv": ["tool", "{parent}"], "timeoutSeconds": 30}, "refreshSeconds": 180}
        # liveHome names a renderer, so the base declares one for it to name.
        home = {"id": "home", "argv": python_argv("import sys\nsys.stdout.write('<html></html>')\n", self.tools, "home"),
                "output": "home.html"}
        samples["liveHome"] = "home"
        samples["liveRuns"] = {"renderer": "home", "inventory": {"argv": ["tool", "runs"], "timeoutSeconds": 5}}
        base = json.loads(self.config([home], []))
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

    def test_live_runs_must_name_a_declared_page_view_and_an_inventory_argv(self) -> None:
        home = {"id": "home", "argv": python_argv("import sys\nsys.stdout.write('<html></html>')\n", self.tools, "home"),
                "output": "home.html"}
        markdown = {"id": "md", "builtin": "markdown", "output": "delivery.md"}
        base = json.loads(self.config([home, markdown], []))
        accepted = {"renderer": "home", "inventory": {"argv": ["tool", "runs"]}}
        self.assertEqual(wrapper(self.state_dir, "--state-config", json.dumps({**base, "liveRuns": accepted}))[0], 0)
        self.assertEqual(render_state.live_runs({**base, "liveRuns": accepted})["timeout"], 10)
        refused = {
            "liveRuns.renderer": [{"renderer": "missing", "inventory": {"argv": ["tool"]}},
                                  {"renderer": "md", "inventory": {"argv": ["tool"]}},
                                  {"inventory": {"argv": ["tool"]}}],
            "liveRuns.inventory: argv": [{"renderer": "home", "inventory": {}},
                                         {"renderer": "home", "inventory": {"timeoutSeconds": 5}},
                                         {"renderer": "home", "inventory": {"argv": []}}],
            "liveRuns.inventory must": [{"renderer": "home"}],
            "liveRuns.inventory: timeoutSeconds": [{"renderer": "home", "inventory": {"argv": ["tool"], "timeoutSeconds": 0}}],
        }
        for named, values in refused.items():
            for value in values:
                with self.subTest(value=value):
                    code, result = wrapper(self.state_dir, "--state-config", json.dumps({**base, "liveRuns": value}))
                    self.assertEqual((code, result["code"]), (2, "config_invalid"))
                    self.assertIn(named, result["error"])

    def test_a_state_configuration_may_declare_a_tracker_snapshot_command(self) -> None:
        config = json.dumps({**json.loads(self.config([], [])),
                             "trackerSnapshot": {"argv": ["tool", "{parent}"], "timeoutSeconds": 30}})
        self.assertEqual(wrapper(self.state_dir, "--state-config", config)[0], 0)

    def test_a_publisher_that_runs_nothing_is_refused_naming_the_fix_and_a_max_age_is_whole(self) -> None:
        for publishers in ([{"id": "live", "kind": "host"}], [{"id": "live", "kind": "host"}, {"id": "mirror", "kind": "host"}]):
            with self.subTest(publishers=publishers):
                code, result = wrapper(self.state_dir, "--state-config", self.config([], publishers))
                self.assertEqual((code, result["code"]), (2, "config_invalid"))
                self.assertIn("publishers[0]: a publisher is an argv; one with a kind and no argv runs nothing", result["error"])
                self.assertIn("fix: name the tracker publisher instead", result["error"])
                self.assertFalse((self.state_dir / "outbox").exists(), "nothing is written for the lead to post")
        for age in (0, 1.5, True, "60"):
            with self.subTest(age=age):
                config = json.dumps({**json.loads(self.config([], [])), "snapshotMaxAgeSeconds": age})
                code, result = wrapper(self.state_dir, "--state-config", config)
                self.assertEqual((code, result["code"]), (2, "config_invalid"))
        config = json.dumps({**json.loads(self.config([], [])), "snapshotMaxAgeSeconds": 60})
        self.assertEqual(wrapper(self.state_dir, "--state-config", config)[0], 0)

    def test_a_publisher_with_a_kind_is_refused_and_no_view_may_claim_the_records_names(self) -> None:
        # The old folder stays reserved, so a record reopened with what an earlier generation left is never written over.
        for publisher, said in (({"id": "live", "kind": "host", "argv": ["x"]}, "unknown fields: kind"),
                                ({"id": "live", "kind": "shell"}, "runs nothing"),
                                ({"id": "live", "kind": "host", "timeoutSeconds": 5}, "runs nothing")):
            with self.subTest(publisher=publisher):
                code, result = wrapper(self.state_dir, "--state-config", self.config([], [publisher]))
                self.assertEqual((code, result["code"]), (2, "config_invalid"), result)
                self.assertIn(said, result["error"])
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

    def test_the_markdown_view_shows_a_story_settled_with_no_change_and_its_evidence(self) -> None:
        document = copy.deepcopy(self.document())
        no_change = {"evidence": "one-loads: test_one_loads passes", "baseHead": HEAD_A, "handedAt": "2026-10-21T09:00:00Z"}
        document["state"]["planned"] = [
            {"subject": "done-1", "title": "Done one", "lane": "lane-1", "stage": "settled",
             "noChange": {**no_change, "settledAt": "2026-10-21T10:00:00Z"}},
            {"subject": "done-2", "title": "Done two", "lane": "lane-1", "stage": "review", "noChange": no_change}]
        planned = render_state.render_markdown(document)
        planned = planned[planned.index("## Planned"):planned.index("## Lanes")]
        self.assertIn("- done-1: Done one, closed, no change needed · lane lane-1", planned)
        self.assertIn("- done-2: Done two, handed off, no change needed, waiting for the lead · lane lane-1", planned)
        shown = render_state._markdown_text(f"Evidence, run at base {HEAD_A[:10]}: one-loads: test_one_loads passes")
        self.assertEqual(planned.count(f"\n  - {shown}"), 2, planned)

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


LONG_TITLE = "Behaviour scenario: shop-storefront-composition " + "x" * 200


def titled_document() -> dict:
    """Checks carrying titles, one hostile and one long, beside old checks that carry none."""
    state = copy.deepcopy(FIXED_STATE)
    state["meta"]["checking"]["now"]["steps"] = [
        {"label": "e2e", "state": "passed", "title": "Webapp e2e <b>(Playwright)</b> & more",
         "startedAt": "2026-10-20T11:50:00Z", "finishedAt": "2026-10-20T11:50:30Z"},
        {"label": "scenario", "state": "pending", "title": LONG_TITLE},
        {"label": "fmt", "state": "pending"},
    ]
    state["queue"][0]["checks"] = [
        {"id": "suite", "state": "failed", "title": "Suite (pytest)", "failures": ["test_alpha"], "log": "logs/01-suite.log"},
        {"id": "whole", "state": "passed"},
    ]
    return {"state": state, "journal": FIXED_JOURNAL}


TITLED_LINES = (
    "Webapp e2e <b>(Playwright)</b> & more: passed · took 30s",
    LONG_TITLE[:79] + "…: pending",
    "fmt: pending",
    "Suite (pytest): failed · failing test_alpha · log logs/01-suite.log",
    "whole: passed",
)


def node_document() -> dict:
    """Checks carrying the Node they ran: below the minimum, meeting it, none on their PATH, and one from a record
    written before the runner recorded it."""
    state = copy.deepcopy(FIXED_STATE)
    state["queue"][0]["checks"] = [
        {"id": "old", "state": "failed", "node": "20.0.0", "nodeBelowMinimum": True},
        {"id": "new", "state": "passed", "node": "22.6.0"},
        {"id": "none", "state": "passed", "node": None},
        {"id": "before", "state": "passed"},
    ]
    return {"state": state, "journal": FIXED_JOURNAL}


NODE_LINES = (
    "old: failed · Node 20.0.0, below the runtime's nodeMinimum",
    "new: passed · Node 22.6.0",
    "none: passed · no node on its PATH",
)


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


def selection_document() -> dict:
    """The fixed state with one row checked by the lead's selection and one whose selection a new head voided."""
    state = copy.deepcopy(FIXED_STATE)
    first, second = state["queue"][0], state["queue"][1]
    check = {"id": "parser", "argv": ["python3", "-m", "pytest", "tests/parser"], "cwd": ".", "reasons": ["parser"],
             "prerequisites": []}
    first["leadSelection"] = {"head": first["head"], "checks": [check], "reason": "only the parser changed",
                              "recordedAt": "2026-10-20T11:00:00Z"}
    first["checkSelection"] = {"by": "lead", "head": first["head"], "reason": "only the parser changed"}
    second["leadSelection"] = {"head": "d" * 40, "checks": [check, {**check, "id": "lexer"}], "reason": "lexer too",
                               "recordedAt": "2026-10-20T11:00:00Z"}
    second["checkSelection"] = {"by": "provider"}
    stats = sys.modules[render_state.show.__module__].delivery_stats(state, FIXED_JOURNAL)
    return {"state": state, "journal": FIXED_JOURNAL, "stats": stats}


def selection_lines(document: dict) -> tuple[str, ...]:
    first, second = document["state"]["queue"][0], document["state"]["queue"][1]
    return (
        f"checks selected by the lead at {first['head']}, not the provider · why: only the parser changed",
        "checks selected by the provider",
        f"lead selection for {'d' * 40}: parser, lexer · why: lexer too · void: the reviewed head is now {second['head']}",
        "· checks selected by the lead",
    )


def fallback_document() -> dict:
    """The fixed state with one row checking on the binding's fallback because the lead selected no checks for it."""
    state = copy.deepcopy(FIXED_STATE)
    state["queue"][1]["checkSelection"] = {"by": "provider", "fallback": ["full-suite", "lint"]}
    return {"state": state, "journal": FIXED_JOURNAL}


FALLBACK_LINE = "checking on the fallback, full-suite, lint: the lead selected no checks"


def refusal_document() -> dict:
    """The fixed state with one row the provider refused under selection.refusalStops."""
    state = copy.deepcopy(FIXED_STATE)
    state["queue"][1]["checkSelection"] = {"by": "provider", "outcome": "provider_refused",
                                           "detail": "the base provider exited 3: over the budget: unit (400s)"}
    return {"state": state, "journal": FIXED_JOURNAL}


REFUSAL_LINE = ("the provider refused its checks, and it is held until the lead selects them · the base provider exited 3: "
                "over the budget: unit (400s)")


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

    def test_the_markdown_view_says_whose_checks_ran_and_why(self) -> None:
        document = selection_document()
        view = render_state.render_markdown(document)
        for line in selection_lines(document):
            with self.subTest(line=line):
                self.assertIn(render_state._markdown_text(line), view)
        # The selection the lead's checks came from is said once, not again as waiting.
        self.assertNotIn(render_state._markdown_text(f"lead selection for {document['state']['queue'][0]['head']}"), view)

    def test_the_markdown_view_shows_each_check_s_node_and_its_mark(self) -> None:
        view = render_state.render_markdown(node_document())
        for line in NODE_LINES:
            with self.subTest(line=line):
                self.assertIn(render_state._markdown_text(line), view)
        self.assertEqual(view.count("below the runtime"), 1, view)
        self.assertIn(render_state._markdown_text("before: passed") + "\n", view)

    def test_the_markdown_view_names_checks_by_title_and_by_id_without_one(self) -> None:
        view = render_state.render_markdown(titled_document())
        for line in TITLED_LINES:
            with self.subTest(line=line):
                self.assertIn(render_state._markdown_text(line), view)
        self.assertNotIn("<b>", view)
        self.assertNotIn(LONG_TITLE, view)
        self.assertNotIn("suite: failed", view)

    def test_the_markdown_view_says_a_row_checks_on_the_fallback_naming_it(self) -> None:
        view = render_state.render_markdown(fallback_document())
        self.assertIn(render_state._markdown_text(FALLBACK_LINE), view)
        self.assertNotIn(render_state._markdown_text("checks selected by the provider"), view)
        # A fallback that isn't a list of names is the provider's selection, as the page reads it.
        for odd in ("full-suite", [], {"id": "full-suite"}):
            with self.subTest(fallback=odd):
                document = fallback_document()
                document["state"]["queue"][1]["checkSelection"]["fallback"] = odd
                view = render_state.render_markdown(document)
                self.assertNotIn("on the fallback", view)
                self.assertIn(render_state._markdown_text("checks selected by the provider"), view)

    def test_the_markdown_view_says_the_provider_refused_and_never_that_it_selected(self) -> None:
        view = render_state.render_markdown(refusal_document())
        self.assertIn(render_state._markdown_text(REFUSAL_LINE), view)
        self.assertNotIn(render_state._markdown_text("checks selected by the provider"), view)

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


# The last read that changed a sibling, and when its latest outcome began.
READ_AT = "2026-10-20T15:50:00Z"
ATTEMPT_AT = "2026-10-20T15:55:00Z"
SIBLING_LINK = "https://views.example/d/" + "a" * 64


def sibling_row(name: str, stages: tuple[str, ...], outcome: str | None = "read") -> dict:
    """A sibling as reconcile leaves it: its last kept read's stories, and its latest read's outcome; None never read."""
    row = {"name": name, "link": SIBLING_LINK, "addedAt": "2026-10-20T15:00:00Z"}
    if outcome is not None:
        row.update(lastOutcome=outcome, lastAttemptAt=ATTEMPT_AT)
        row.update(readAt=READ_AT, stories=[{"subject": f"API-{index}", "title": f"Backend {index}", "stage": stage}
                                            for index, stage in enumerate(stages, 1)])
    return row


def sibling_document(*rows: dict, landed: bool = True, outside: list | None = None) -> dict:
    """The fixed state with every own story landed, or one still building, and the given siblings."""
    state = copy.deepcopy(FIXED_STATE)
    state["meta"]["trackerParent"] = "APP-10"
    for item in state["queue"]:
        item["stage"] = "merged"
    for item in state.get("planned", []):
        item["stage"] = "settled"
    if not landed:
        state["queue"][0]["stage"] = "checking"
    if rows:
        state["siblings"] = list(rows)
    if outside is not None:
        state["outside"] = outside
    return {"state": state, "journal": FIXED_JOURNAL}


class AcrossViewTests(unittest.TestCase):
    """The Markdown view prints what the record's sibling deliveries show, as the state wrapper words it once."""

    def view(self, document: dict) -> str:
        return render_state.render_markdown(document)

    def part_done(self, view: str) -> str | None:
        found = [line for line in view.splitlines() if line.startswith(render_state._markdown_text(render_state.PART_DONE))]
        return found[0] if found else None

    def test_each_sibling_has_a_group_with_since_when_it_is_as_it_is(self) -> None:
        view = self.view(sibling_document(sibling_row("backend", ("building", "merged")),
                                          sibling_row("payments", ("review",), "unavailable"),
                                          sibling_row("web", (), None)))
        section = view[view.index("## Across repositories"):view.index("## Lanes")]
        text = render_state._markdown_text
        unreached = "couldn't be read since " + ATTEMPT_AT
        self.assertEqual(section.splitlines()[2:], [
            f"- backend · {text(SIBLING_LINK)} · {text('no change since ' + READ_AT)}",
            f"  - API-1: {text('Backend 1')}, building",
            f"  - API-2: {text('Backend 2')}, merged",
            f"- payments · {text(SIBLING_LINK)} · {text(unreached)}",
            f"  - API-1: {text('Backend 1')}, review",
            f"- web · {text(SIBLING_LINK)} · never read",
            ""])
        self.assertLess(view.index("## Planned"), view.index("## Across repositories"))

    def test_the_headline_cases(self) -> None:
        text = render_state._markdown_text
        cases = {
            "a fresh sibling with two open": ((sibling_row("backend", ("building", "review", "merged")),), True,
                                              "This repository's part is done. backend has 2 open."),
            "every sibling fresh and landed": ((sibling_row("backend", ("merged", "settled")),), True, None),
            "unavailable with two open": ((sibling_row("backend", ("building", "review"), "unavailable"),), True,
                                          f"This repository's part is done. backend has 2 open, couldn't be read since {ATTEMPT_AT}."),
            "never read": ((sibling_row("backend", (), None),), True,
                           "This repository's part is done. backend couldn't be read (never read)."),
            "own stories not all landed": ((sibling_row("backend", ("building",)),), False, None),
            "unavailable, all landed": ((sibling_row("backend", ("merged",), "unavailable"),), True,
                                        f"This repository's part is done. backend couldn't be read since {ATTEMPT_AT}."),
            "stale, all landed": ((sibling_row("backend", ("merged",), "stale"),), True,
                                  f"This repository's part is done. backend couldn't be read since {ATTEMPT_AT}."),
            "another epic's sibling isn't named": ((sibling_row("backend", ("merged",)),
                                                    sibling_row("legacy", ("building",), "other-epic")), True, None),
        }
        for name, (rows, landed, expected) in cases.items():
            with self.subTest(name):
                found = self.part_done(self.view(sibling_document(*rows, landed=landed)))
                self.assertEqual(found, None if expected is None else text(expected))
                across = render_state.across(sibling_document(*rows, landed=landed))
                self.assertEqual(" ".join(across.get("partDone", [])) or None, expected)

    def test_each_outcome_is_said_since_it_began_and_one_never_read_says_so(self) -> None:
        text = render_state._markdown_text
        tried = {"name": "web", "link": SIBLING_LINK, "addedAt": "2026-10-20T15:00:00Z", "lastOutcome": "unavailable",
                 "lastAttemptAt": ATTEMPT_AT}
        document = sibling_document(sibling_row("api", ("building",), "stale"),
                                    sibling_row("pay", ("building",), "incompatible"), tried)
        view = self.view(document)
        section = view[view.index("## Across repositories"):view.index("## Lanes")]
        heads = [line for line in section.splitlines() if line.startswith("- ")]
        tried_words = "couldn't be read since " + ATTEMPT_AT + " · never read"
        self.assertEqual(heads, [f"- api · {text(SIBLING_LINK)} · {text('stale since ' + ATTEMPT_AT)}",
                                 f"- pay · {text(SIBLING_LINK)} · {text('needs a compatible snapshot since ' + ATTEMPT_AT)}",
                                 f"- web · {text(SIBLING_LINK)} · {text(tried_words)}"])
        self.assertEqual(render_state.across(document)["partDone"],
                         [render_state.PART_DONE, f"api has 1 open, couldn't be read since {ATTEMPT_AT}.",
                          f"pay has 1 open, couldn't be read since {ATTEMPT_AT}.", "web couldn't be read (never read)."])

    def test_a_record_with_no_stories_of_its_own_has_no_part_done_line(self) -> None:
        document = sibling_document(sibling_row("backend", ("building",)))
        document["state"]["queue"], document["state"]["planned"] = [], []
        self.assertNotIn("partDone", render_state.across(document))
        self.assertIsNone(self.part_done(self.view(document)))

    def test_another_epic_s_sibling_is_drawn_without_stories(self) -> None:
        view = self.view(sibling_document(sibling_row("legacy", ("building",), "other-epic")))
        section = view[view.index("## Across repositories"):view.index("## Lanes")]
        self.assertIn(render_state._markdown_text("delivers another epic since " + ATTEMPT_AT), section)
        self.assertNotIn("API-1", section)

    def test_an_outside_child_a_sibling_holds_is_drawn_in_its_group_and_the_rest_stay_elsewhere(self) -> None:
        outside = [{"subject": "API-1", "title": "[Backend] Login API", "state": "started", "status": "In Progress"},
                   {"subject": "OPS-4", "title": "Alerting", "state": "backlog", "status": "To Do"}]
        view = self.view(sibling_document(sibling_row("backend", ("building",)), outside=outside))
        section = view[view.index("## Elsewhere in APP-10"):view.index("## Lanes")]
        self.assertEqual(section.splitlines()[2:], [f"- OPS-4: Alerting, {render_state._markdown_text('To Do')}", ""])
        # Without siblings, every outside child is elsewhere.
        view = self.view(sibling_document(outside=outside))
        self.assertNotIn("## Across repositories", view)
        section = view[view.index("## Elsewhere in APP-10"):view.index("## Lanes")]
        self.assertEqual([line[:7] for line in section.splitlines()[2:4]], ["- API-1", "- OPS-4"])

    def test_the_wrapper_gives_every_view_and_the_live_stream_the_siblings_words(self) -> None:
        # The line map and the live server read `across` from the record the wrapper hands them; they never word it.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "state"
            directory.mkdir()
            document = sibling_document(sibling_row("backend", ("building", "review")))
            (directory / "state.json").write_text(json.dumps(document["state"], indent=2) + "\n", encoding="utf-8")
            (directory / "journal.jsonl").write_text("".join(json.dumps(entry) + "\n" for entry in document["journal"]),
                                                     encoding="utf-8")
            shown = render_state.with_story(render_state.show(directory), directory)
            self.assertEqual(shown["across"]["partDone"], [render_state.PART_DONE, "backend has 2 open."])
            self.assertEqual([group["name"] for group in shown["across"]["groups"]], ["backend"])
            # So does the live server, whose read is the same record.
            self.assertEqual(render_state.LiveView(directory, None).read()["across"], shown["across"])

    def test_a_record_with_no_siblings_renders_as_before(self) -> None:
        document = {"state": FIXED_STATE, "journal": FIXED_JOURNAL}
        self.assertIsNone(render_state.across(document))
        self.assertIs(render_state.with_across(document), document)
        self.assertNotIn("Across repositories", self.view(document))
        empty = copy.deepcopy(document)
        empty["state"]["siblings"] = []
        self.assertEqual(self.view(empty), self.view(document))


class LiveClient:
    """A page's view of the live command: its document and its event stream, read as a browser would."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.events: list[dict] = []
        # Each event: runs, with its data and when it came.
        self.runs: list[tuple[dict, float]] = []
        self.keepalives = 0
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
                elif line == ": keepalive":
                    with self.lock:
                        self.keepalives += 1
                elif line.startswith("data: ") and event == "runs":
                    with self.lock:
                        self.runs.append((json.loads(line[len("data: "):]), time.monotonic()))
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
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--text", "Reached the page", "--why", "A test.")
        seen = client.wait_for(lambda doc: doc["journal"] and doc["journal"][-1]["data"]["text"] == "Reached the page")
        self.assertIsNotNone(seen)
        self.assertLess(time.monotonic() - written, 3.0)
        self.assertEqual(set(seen), {"state", "journal", "stats", "estimates", "status", "focus", "story"})
        status, page = client.get("")
        self.assertEqual(status, 200)
        self.assertIn(b"EventSource(\"/events\")", page)
        self.assertIn(b'id="live-status"', page)
        status, document = client.get("document")
        self.assertEqual(json.loads(document)["journal"][-1]["data"]["text"], "Reached the page")

    def test_a_burst_of_writes_ends_at_the_final_version_with_nothing_lost_or_doubled(self) -> None:
        _, url = self.start()
        client = self.client(url)
        client.listen()
        client.wait_for(lambda doc: True)
        before = len(client.events)
        threads = [threading.Thread(target=state_tool, args=(self.state_dir, "journal", "--kind", "decision.recorded",
                                                             "--text", f"Burst {n}", "--why", "A test.")) for n in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        final = state_tool(self.state_dir, "show")
        seen = client.wait_for(lambda doc: len(doc["journal"]) == len(final["journal"]), seconds=5)
        self.assertIsNotNone(seen)
        summaries = [entry["data"].get("text", "") for entry in seen["journal"]]
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
        # A change noted with nothing in the record moved, as a write that changes nothing would leave it.
        delivery_state._note_change(self.state_dir, "patch", version=shown["state"]["version"])
        time.sleep(1.0)
        self.assertEqual(len(client.events), 1, "a change that moves nothing changes no page")

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
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--text", "Rendered, not published", "--why", "A test.")
        client.wait_for(lambda doc: doc["journal"][-1]["data"]["text"] == "Rendered, not published")
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
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--text", "While away", "--why", "A test.")
        time.sleep(0.6)
        second = self.client(url)
        second.listen()
        seen = second.wait_for(lambda doc: True, seconds=2)
        self.assertEqual(seen["journal"][-1]["data"]["text"], "While away")

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
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--text", "Recovered", "--why", "A test.")
        client.listen()
        self.assertIsNotNone(client.wait_for(lambda doc: doc["journal"][-1]["data"]["text"] == "Recovered"))

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

    @unittest.skipIf(os.name == "nt", "POSIX signals")
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
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--text", "After a bad read", "--why", "A test.")
        self.assertIsNotNone(client.wait_for(lambda doc: doc["journal"][-1]["data"]["text"] == "After a bad read"))
        self.assertIsNone(process.poll())

    def test_a_record_change_sends_its_document_only_after_its_views_are_written(self) -> None:
        # A view that takes a second: a page told of the change before it was written would read the old one.
        slow = {"id": "slow", "output": "slow.html", "argv": python_argv(
            "import json, sys, time\ndoc = json.load(sys.stdin)\ntime.sleep(1)\n"
            "sys.stdout.write('<html><head><meta name=\"delivery-state-live\"></head><body>'"
            " + doc['journal'][-1]['data']['text'] + '</body></html>')\n", self.tools, "slow")}
        _, url = self.start("--state-config", self.config([slow]))
        client = self.client(url)
        client.listen()
        client.wait_for(lambda doc: True, seconds=10)
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--text", "After the views", "--why", "A test.")
        self.assertIsNotNone(client.wait_for(lambda doc: doc["journal"][-1]["data"]["text"] == "After the views",
                                             seconds=10))
        status, page = client.get("view/slow.html")
        self.assertEqual(status, 200)
        self.assertIn(b"After the views", page)

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

    def test_a_view_named_with_characters_a_browser_encodes_is_served_and_nothing_else_is(self) -> None:
        def opted(renderer_id: str, output: str) -> dict:
            source = f"import sys\nsys.stdout.write('<html><head><meta name=\"delivery-state-live\"></head><body>{renderer_id}</body></html>')\n"
            return {"id": renderer_id, "argv": python_argv(source, self.tools, renderer_id), "output": output}
        _, url = self.start("--state-config", self.config([opted("spaced", "delivery history.html"),
                                                           opted("nested", "views/line-map.html"),
                                                           opted("percent", "a%41.html")]))
        client = self.client(url)
        deadline = time.monotonic() + 5
        status, page = client.get("view/delivery%20history.html")
        while status != 200 and time.monotonic() < deadline:
            time.sleep(0.2)
            status, page = client.get("view/delivery%20history.html")
        self.assertEqual(status, 200)
        self.assertIn(b"spaced", page)
        self.assertEqual(client.get("view/views/line-map.html")[0], 200)
        self.assertEqual(client.get("view/views/line%2Dmap.html")[0], 200)
        # A name that holds a percent sign is linked with it encoded, and served by its own name.
        self.assertEqual(client.get("view/a%2541.html")[0], 200)
        for path in ("view/..%2Fstate.json", "view/%2e%2e/state.json", "view/%2e%2e%2fstate.json",
                     "view/views%2Fline-map.html", "view/delivery%2520history.html", "view/views/..%2Fdelivery%20history.html",
                     "view/%2e/delivery%20history.html", "view/delivery%20history.html%00", "view/%ff.html"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path)[0], 404)

    def test_the_binding_s_home_view_is_the_front_page(self) -> None:
        own = {"id": "own", "argv": python_argv(
            "import sys\nsys.stdout.write('<html><head><meta name=\"delivery-state-live\"></head><body>own home</body></html>')\n",
            self.tools, "own"), "output": "own.html"}
        config = json.loads(self.config([own]))
        _, url = self.start("--state-config", json.dumps({**config, "liveHome": "own"}))
        client = self.client(url)
        deadline = time.monotonic() + 5
        status, page = client.get("")
        while status != 200 and time.monotonic() < deadline:
            time.sleep(0.2)
            status, page = client.get("")
        self.assertEqual(status, 200)
        self.assertIn(b"own home", page)
        self.assertIn(b'EventSource("/events")', page)
        self.assertEqual(client.get("index.html")[1], page)

    def test_without_a_status_file_the_page_and_its_routes_are_as_before(self) -> None:
        _, url = self.start()
        client = self.client(url)
        status, page = client.get("")
        self.assertEqual(status, 200)
        shown = json.loads(client.get("document")[1])
        # Any flag this checkout's binding raises is shown as before; the page is otherwise the built-in one, unchanged.
        flags = tuple(html.unescape(flag) for flag in re.findall(r'<p role="alert"><strong>(.*?)</strong></p>',
                                                                  page.decode("utf-8")))
        self.assertEqual(page.decode("utf-8"), render_state.render_static_page(shown, live=True, flags=flags))
        self.assertNotIn(b"supervisor-banner", page)
        self.assertEqual(client.get("supervisor")[0], 404)

    def status_file(self, **status) -> Path:
        path = self.root / "supervisor.json"
        path.write_text(json.dumps({"restart": "python3 -B tool.py lead-run --resume", **status}), encoding="utf-8")
        return path

    def banner(self, client: LiveClient) -> tuple[bytes, str | None]:
        status, page = client.get("")
        self.assertEqual(status, 200)
        return page, json.loads(client.get("supervisor")[1])["banner"]

    def test_the_banner_follows_the_status_file_on_every_page_served(self) -> None:
        path = self.status_file(pid=os.getpid(), state="running", at="2026-11-11T10:00:00Z")
        _, url = self.start("--status-file", str(path))
        client = self.client(url)
        page, said = self.banner(client)
        self.assertIsNone(said)
        self.assertIn(b'id="supervisor-banner" style="font-weight:bold" hidden></p>', page)
        self.assertIn(b'fetch("/supervisor"', page)
        # Stopped on a named signal: the same view says so on the next page, with when and how to restart it.
        self.status_file(pid=os.getpid(), state="stopped", at="2026-11-11T10:05:00Z", why="signal", signal="SIGTERM",
                         said="it received SIGTERM")
        page, said = self.banner(client)
        self.assertEqual(said, "The supervisor is down since 2026-11-11T10:05:00Z: it received SIGTERM. Restart it "
                               "with python3 -B tool.py lead-run --resume.")
        self.assertIn(said.encode("utf-8"), page)
        self.assertNotIn(b"font-weight:bold\" hidden", page)
        # Still marked running, by a pid that no longer runs: killed, so it recorded nothing.
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait(10)
        self.status_file(pid=gone.pid, state="running", at="2026-11-11T10:00:00Z")
        page, said = self.banner(client)
        self.assertEqual(said, f"The supervisor (pid {gone.pid}) stopped without recording why. Restart it with "
                               "python3 -B tool.py lead-run --resume.")
        self.assertIn(said.encode("utf-8"), page)
        # Running again: the banner clears.
        self.status_file(pid=os.getpid(), state="running", at="2026-11-11T10:10:00Z")
        self.assertIsNone(self.banner(client)[1])

    def test_the_home_view_carries_the_banner_just_inside_its_body(self) -> None:
        own = {"id": "own", "argv": python_argv(
            "import sys\nsys.stdout.write('<html><head><meta name=\"delivery-state-live\"></head><body class=\"x\">own "
            "home</body></html>')\n", self.tools, "own"), "output": "own.html"}
        config = json.loads(self.config([own]))
        path = self.status_file(pid=os.getpid(), state="stopped", at="2026-11-11T10:05:00Z", why="held",
                                said="lead-close asked it to stop")
        _, url = self.start("--state-config", json.dumps({**config, "liveHome": "own"}), "--status-file", str(path))
        client = self.client(url)
        deadline = time.monotonic() + 5
        status, page = client.get("")
        while status != 200 and time.monotonic() < deadline:
            time.sleep(0.2)
            status, page = client.get("")
        self.assertEqual(status, 200)
        text = page.decode("utf-8")
        self.assertIn('<body class="x">\n<p role="alert" id="supervisor-banner" style="font-weight:bold">The supervisor '
                      "is down since 2026-11-11T10:05:00Z: lead-close asked it to stop. Restart it with python3 -B "
                      "tool.py lead-run --resume.</p>", text)
        self.assertLess(text.index("supervisor-banner"), text.index("own home"))

    def test_without_a_home_view_the_front_page_is_the_built_in_page(self) -> None:
        _, url = self.start()
        status, page = self.client(url).get("")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Delivery state</title>", page)

    def test_a_home_view_that_names_no_html_renderer_never_starts_the_server(self) -> None:
        markdown = {"id": "md", "builtin": "markdown", "output": "delivery.md"}
        page = {"id": "page", "builtin": "static-page", "output": "delivery.html"}
        for home in ("missing", "md", "page", 7):
            with self.subTest(home=home):
                config = json.loads(self.config([markdown, page]))
                result = run([sys.executable, "-B", str(WRAPPER), "live", "--dir", str(self.state_dir),
                              "--state-config", json.dumps({**config, "liveHome": home})])
                self.assertEqual((result.returncode, json.loads(result.stdout)["code"]), (2, "config_invalid"))

    def older_is_unspecified(self):
        # As Python 3.11.9 does: a mapped address is judged as IPv6, so ::ffff:0.0.0.0 is not "unspecified".
        return mock.patch.object(ipaddress.IPv6Address, "is_unspecified", property(lambda address: int(address) == 0))

    def test_the_mapped_any_address_is_a_wildcard_on_any_python(self) -> None:
        with self.older_is_unspecified():
            self.assertFalse(ipaddress.ip_address("::ffff:0.0.0.0").is_unspecified, "the simulation holds")
            for host in ("::ffff:0.0.0.0", "::ffff:0:0", "::", "0.0.0.0"):
                with self.subTest(host=host):
                    self.assertTrue(render_state._is_wildcard(host))
            for host in ("::1", "127.0.0.1", "::ffff:127.0.0.1", "::ffff:8.8.8.8", "localhost"):
                with self.subTest(host=host):
                    self.assertFalse(render_state._is_wildcard(host))

    def test_the_mapped_any_address_is_refused_before_a_socket_exists_on_any_python(self) -> None:
        def no_socket(*args, **kwargs):
            raise AssertionError("a server was built for a wildcard")

        with self.older_is_unspecified(), mock.patch.object(render_state, "_LiveServer6", no_socket), \
                mock.patch.object(render_state, "_LiveServer", no_socket):
            code, result = render_state._live_from_args(["--dir", str(self.state_dir), "--host", "::ffff:0.0.0.0"])
        self.assertEqual((code, result["ok"], result["code"]), (2, False, "live_refused"))

    @unittest.skipIf(os.name == "nt", "the stalled lookup is modelled through a POSIX sitecustomize")
    def test_the_live_view_listens_at_once_when_name_lookups_stall(self) -> None:
        """Where a reverse lookup of the loopback address goes unanswered, as on a macOS runner, a server that looks up
        its own name before it listens announces nothing until the lookup gives up. The live view doesn't wait."""
        stalled = Path(self.enterContext(tempfile.TemporaryDirectory())) / "stalled"
        stalled.mkdir()
        (stalled / "sitecustomize.py").write_text(
            "import socket, time\n_getfqdn = socket.getfqdn\n"
            "def getfqdn(name=''):\n"
            "    if name in ('', 'localhost', '127.0.0.1', '::1', '0.0.0.0', '::'):\n        time.sleep(60)\n"
            "    return _getfqdn(name)\n"
            "socket.getfqdn = getfqdn\n")
        env = {**os.environ, "PYTHONPATH": str(stalled)}

        def first_line_within(argv: list[str], seconds: float) -> str | None:
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
            try:
                ready, _, _ = select.select([process.stdout], [], [], seconds)
                return process.stdout.readline().decode() if ready else None
            finally:  # started here, stopped by the pid recorded here
                process.kill()
                process.wait()
                process.stdout.close()

        stock = [sys.executable, "-B", "-c", "import http.server; s = http.server.ThreadingHTTPServer(('127.0.0.1', 0), "
                 "http.server.BaseHTTPRequestHandler); print('bound', flush=True)"]
        self.assertIsNone(first_line_within(stock, 3), "the stalled lookup didn't hold up the stock server")
        line = first_line_within([sys.executable, "-B", str(WRAPPER), "live", "--dir", str(self.state_dir)], 10)
        self.assertIsNotNone(line, "the live view waited for the stalled lookup")
        self.assertTrue(json.loads(line)["ok"], line)

    @unittest.skipIf(os.name == "nt", "Windows has not been seen to take this bind")
    def test_the_mapped_any_address_really_listens_on_every_ipv4_address(self) -> None:
        # Why the refusal must come first: this bind succeeds, so nothing later would stop it serving.
        server = render_state._LiveServer6(("::ffff:0.0.0.0", 0), http.server.BaseHTTPRequestHandler)
        self.addCleanup(server.server_close)
        with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5):
            pass
        with self.older_is_unspecified():
            self.assertTrue(render_state._is_wildcard(str(server.server_address[0])), "the check after the bind reads it too")

    def test_a_wildcard_bind_is_refused_before_anything_listens(self) -> None:
        for host in ("0.0.0.0", "::", "0:0:0:0:0:0:0:0", "::ffff:0.0.0.0", "", " ", "0"):
            with self.subTest(host=host):
                result = run([sys.executable, "-B", str(WRAPPER), "live", "--dir", str(self.state_dir),
                              "--host", host])
                self.assertEqual(result.returncode, 2)
                refused = json.loads(result.stdout)
                if os.name == "nt" and host == "0":
                    # Windows doesn't resolve this name, so the bind itself fails and nothing listens.
                    self.assertEqual((refused["ok"], refused["code"]), (False, "live_unavailable"))
                    continue
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
            state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--text", f"Long run {n}", "--why", "A test.")
        self.assertIsNotNone(first.wait_for(lambda doc: doc["journal"][-1]["data"]["text"] == "Long run 2"))
        self.assertLessEqual(path.stat().st_size, limit)
        first.close()
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--text", "Away past the trim", "--why", "A test.")
        time.sleep(0.6)
        second = self.client(url)
        second.listen()
        seen = second.wait_for(lambda doc: True, seconds=2)
        shown = render_state.with_story({key: value for key, value in state_tool(self.state_dir, "show").items() if key != "publication"},
                                        self.state_dir)
        self.assertEqual(seen, shown, "a page that comes back gets the whole current record, and its story")

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


# The page the runs-watch cases serve as liveRuns' renderer: what it was given, the runs flag, and the store's files.
RUNS_PAGE = """import html, json, os, sys
store, count = sys.argv[1], sys.argv[2]
given = sys.stdin.read()
with open(count, "a") as counted:
    counted.write("x")
files = {}
for folder, _, names in os.walk(store):
    for name in names:
        path = os.path.join(folder, name)
        with open(path, encoding="utf-8", errors="replace") as source:
            files[os.path.relpath(path, store)] = source.read()
said = json.dumps({"given": given, "flag": os.environ.get("DELIVERY_LIVE_RUNS"), "store": files}).replace("<", "\\\\u003c")
sys.stdout.write('<html><head><meta name="delivery-state-live"></head><body><script type="application/json" id="said">'
                 + said + '</script></body></html>')
"""

# The runtime's runs command as the live server calls it: counted, and printing a run-inventory/1 naming the store.
INVENTORY = """import json, sys, time
store, count, mode = sys.argv[1], sys.argv[2], sys.argv[3]
with open(count, "a") as counted:
    counted.write(" ".join(sys.argv[4:]) + "\\n")
if sys.argv[4:] != ["list", "--json"]:
    sys.exit(3)
if mode == "fail":
    sys.exit(4)
if mode == "slow":
    time.sleep(30)
if mode == "relative":
    import os
    store = os.path.relpath(store)
print(json.dumps({"spec": "run-inventory/1", "runsDir": store, "runs": []}))
"""

# The injected adapter under jsdom, on a real EventSource to the running server: what onRuns and subscribe are given.
ADAPTER_PAGE = """import { createRequire } from "node:module";
const [root, url, wanted] = process.argv.slice(2);
const { JSDOM } = createRequire(root + "/package.json")("jsdom");
const Native = globalThis.EventSource;
if (typeof Native !== "function") { console.log(JSON.stringify({ missing: "EventSource" })); process.exit(2); }
const page = await (await fetch(url + "view/runs.html")).text();
const runs = [];
let subscribed = 0;
const statuses = [];
const dom = new JSDOM(page, { url, runScripts: "dangerously", beforeParse(window) {
  window.EventSource = function (address) {
    const source = new Native(new URL(address, url));
    source.addEventListener("open", () => console.log(JSON.stringify({ ready: true })));
    return source;
  };
  window.fetch = fetch;
} });
const store = dom.window.deliveryStateStore;
store.subscribe(() => { subscribed += 1; });
store.onStatus((connected) => { statuses.push(connected); });
store.onRuns((said) => {
  runs.push(said);
  console.log(JSON.stringify({ runs: said, subscribed }));
  if (runs.length === Number(wanted)) {
    console.log(JSON.stringify({ done: runs, subscribed, statuses }));
    process.exit(0);
  }
});
"""


class LiveRunsTests(DeliveryStateFixture):
    """`live` with no record, the run store it follows through liveRuns, and the entries that make it one server per
    repository. Each case's state folders sit in its own git repository, whose common directory keeps the entries."""

    run_quietly = True

    def setUp(self) -> None:
        super().setUp()
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True, capture_output=True)
        self.common = (self.root / ".git").resolve()
        self.entries = self.common / "delivery-live"
        self.empty = self.root / "empty"
        self.empty.mkdir()
        self.store = self.root / "runs"
        (self.store / "run-1").mkdir(parents=True)
        (self.store / "run-1" / "events.jsonl").write_text('{"kind":"run.started"}\n', encoding="utf-8")

    def serve(self, directory: Path, *extra: str, cwd: Path | None = None) -> tuple[subprocess.Popen, str]:
        process = subprocess.Popen([sys.executable, "-B", str(WRAPPER), "live", "--dir", str(directory), *extra],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
        self.addCleanup(LiveTests.stop, self, process)
        announced = json.loads(process.stdout.readline())
        self.assertTrue(announced["ok"], announced)
        self.assertNotIn("existing", announced)
        return process, f"http://{announced['live']}/"

    def client(self, url: str) -> LiveClient:
        return LiveTests.client(self, url)

    def counter(self, name: str) -> Path:
        return self.root / f"{name}.count"

    def runs_page(self) -> dict:
        return {"id": "runs", "output": "runs.html",
                "argv": [*python_argv(RUNS_PAGE, self.tools, "runs"), str(self.store), str(self.counter("runs"))]}

    def record_page(self) -> dict:
        return {"id": "record", "output": "record.html", "argv": [*python_argv(
            "import json, os, sys\ndoc = json.load(sys.stdin)\nflag = os.environ.get('DELIVERY_LIVE_RUNS')\n"
            "text = doc['journal'][-1]['data']['text'] if 'journal' in doc else doc['spec']\n"
            "sys.stdout.write('<html><head><meta name=\"delivery-state-live\"></head><body>' + text + ' flag=' + str(flag)"
            " + '</body></html>')\n", self.tools, "record")]}

    def config_with_runs(self, mode: str = "ok", home: bool = True, timeout: int = 10) -> str:
        inventory = [*python_argv(INVENTORY, self.tools, "inventory"), str(self.store), str(self.counter("inventory")),
                     mode]
        builtin = {"id": "page", "builtin": "static-page", "output": "page.html"}
        config = {"directory": "state", "renderers": [self.runs_page(), self.record_page(), builtin], "publishers": [],
                  "liveRuns": {"renderer": "runs", "inventory": {"argv": inventory, "timeoutSeconds": timeout}}}
        return json.dumps({**config, **({"liveHome": "record"} if home else {})})

    def said(self, page: bytes) -> dict:
        found = re.search(rb'<script type="application/json" id="said">(.*?)</script>', page, re.S)
        self.assertIsNotNone(found, page[:200])
        return json.loads(found.group(1))

    def eventually(self, client: LiveClient, path: str, check, seconds: float = 10.0) -> bytes:
        deadline = time.monotonic() + seconds
        status, page = client.get(path)
        while not (status == 200 and check(page)) and time.monotonic() < deadline:
            time.sleep(0.1)
            status, page = client.get(path)
        self.assertEqual(status, 200, page[:200])
        self.assertTrue(check(page), page[:400])
        return page

    def own_entries(self) -> dict[int, dict]:
        return {int(path.stem): json.loads(path.read_text(encoding="utf-8")) for path in self.entries.glob("*.json")}

    def test_with_no_record_it_serves_the_runs_page_until_a_record_takes_the_front_page_back(self) -> None:
        process, url = self.serve(self.empty, "--state-config", self.config_with_runs())
        client = self.client(url)
        page = self.eventually(client, "", lambda page: b'id="said"' in page)
        given = self.said(page)
        self.assertEqual(json.loads(given["given"]), {"spec": "delivery-state-absent/1"})
        self.assertEqual(given["flag"], "watched")
        # The other views are given the same document; a built-in view, which draws only a record, is not run.
        self.assertIn(b"delivery-state-absent/1 flag=watched", self.eventually(client, "view/record.html", bool))
        self.assertFalse((self.empty / "page.html").exists())
        self.assertEqual(client.get("document")[0], 503)
        # An open stream with nothing to send waits: keepalives only, never a tight loop.
        client.listen()
        time.sleep(2 * render_state.LIVE_POLL_SECONDS)
        self.assertLessEqual(client.keepalives, 2)
        self.assertEqual((client.events, client.runs), ([], []))
        # Its own entry, under this repository's common directory, naming what it serves.
        entry = self.own_entries()[process.pid]
        self.assertEqual((entry["host"], entry["stateDirectory"], entry["liveRuns"]),
                         ("127.0.0.1", str(self.empty.resolve()), True))
        self.assertEqual(f"http://127.0.0.1:{entry['port']}/", url)
        health = json.loads(client.get("health")[1])
        self.assertEqual((health["server"], health["pid"], health["repository"]),
                         ("render_state.py live", process.pid, str(self.common)))
        # A record appears: liveHome takes the front page back, drawn from it.
        shutil.copytree(self.state_dir, self.empty, dirs_exist_ok=True)
        self.eventually(client, "", lambda page: b"flag=watched" in page and b"delivery-state-absent" not in page)
        self.assertEqual(client.get("document")[0], 200)
        # The record goes: the runs page is the front page again, and there is no document to serve.
        for name in ("state.json", "journal.jsonl"):
            (self.empty / name).unlink()
        self.eventually(client, "", lambda page: b'id="said"' in page)
        self.assertEqual(client.get("document")[0], 503)

    def test_a_relative_store_is_read_from_the_servers_own_folder(self) -> None:
        _, url = self.serve(self.empty, "--state-config", self.config_with_runs("relative"), cwd=self.root)
        page = self.eventually(self.client(url), "", lambda page: b'id="said"' in page)
        self.assertEqual(self.said(page)["flag"], "watched")

    def watch_the_store(self, directory: Path, recorded: bool) -> None:
        _, url = self.serve(directory, "--state-config", self.config_with_runs())
        client = self.client(url)
        self.eventually(client, "view/runs.html", lambda page: b'id="said"' in page)
        self.assertEqual(self.counter("inventory").read_text(encoding="utf-8"), "list --json\n")
        client.listen()
        if recorded:
            self.assertIsNotNone(client.wait_for(lambda doc: True, seconds=10))
        time.sleep(2 * render_state.LIVE_POLL_SECONDS)
        documents = len(client.events)
        # A partial line, not JSON: the server never opens or parses the store.
        with (self.store / "run-1" / "events.jsonl").open("a", encoding="utf-8") as events:
            events.write('{"kind":"gate.rep')
        appended = time.monotonic()
        deadline = appended + 3.0
        while not client.runs and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(client.runs, "no event: runs within three seconds of the store moving")
        page = client.get("view/runs.html")[1]
        # The view was written before the page was told.
        self.assertIn('{"kind":"gate.rep', self.said(page)["store"]["run-1/events.jsonl"])
        time.sleep(2 * render_state.LIVE_POLL_SECONDS)
        with client.lock:
            runs = [said for said, _ in client.runs]
        self.assertEqual(runs, [{"generation": 1}])
        self.assertEqual(len(client.events), documents, "a change of the store alone sends no document")
        self.assertEqual(self.counter("inventory").read_text(encoding="utf-8"), "list --json\n")

    def test_a_change_of_the_store_alone_renders_then_sends_one_runs_event_with_or_without_a_record(self) -> None:
        with self.subTest(record=False):
            self.watch_the_store(self.empty, recorded=False)
        self.counter("inventory").unlink()
        with self.subTest(record=True):
            self.watch_the_store(self.state_dir, recorded=True)

    def test_the_injected_adapter_calls_on_runs_once_per_runs_event_and_never_subscribe(self) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "this case runs the adapter under Node 22.6 or newer with jsdom: "
                             "run python3 scripts/bootstrap-test-environment.py")
        _, url = self.serve(self.state_dir, "--state-config", self.config_with_runs())
        self.eventually(self.client(url), "view/runs.html", lambda page: b'id="said"' in page)
        script = self.tools / "adapter.mjs"
        script.write_text(ADAPTER_PAGE, encoding="utf-8")
        browser = subprocess.Popen([node, "--experimental-eventsource", "--no-warnings", str(script), str(ROOT), url, "2"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(LiveTests.stop, self, browser)
        lines: list[dict] = []
        reader = threading.Thread(target=lambda: lines.extend(json.loads(line) for line in browser.stdout), daemon=True)
        reader.start()

        def seen(check, seconds: float = 10.0) -> None:
            deadline = time.monotonic() + seconds
            while not any(check(line) for line in list(lines)) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(any(check(line) for line in list(lines)), (lines, browser.stderr.read()
                                                                       if browser.poll() is not None else ""))

        seen(lambda line: line.get("ready"))
        # The record that a page gets as it connects is its one document.
        time.sleep(2 * render_state.LIVE_POLL_SECONDS)
        for n, text in enumerate(('{"kind":"wait.sta', 'rted"}\n'), start=1):
            with (self.store / "run-1" / "events.jsonl").open("a", encoding="utf-8") as events:
                events.write(text)
            seen(lambda line, n=n: line.get("runs") == {"generation": n})
        self.assertEqual(browser.wait(10), 0)
        reader.join(5)
        done = next(line for line in lines if "done" in line)
        # Each event, the document and both runs, says the stream is up.
        self.assertEqual(done, {"done": [{"generation": 1}, {"generation": 2}], "subscribed": 1,
                                "statuses": [True, True, True]})

    def test_without_live_runs_the_store_is_never_read_and_nothing_is_rendered_for_it(self) -> None:
        config = json.loads(self.config_with_runs())
        del config["liveRuns"]
        _, url = self.serve(self.state_dir, "--state-config", json.dumps(config))
        client = self.client(url)
        self.eventually(client, "view/record.html", lambda page: b"flag=None" in page)
        client.listen()
        self.assertIsNotNone(client.wait_for(lambda doc: True, seconds=10))
        time.sleep(2 * render_state.LIVE_POLL_SECONDS)
        rendered = self.counter("runs").read_text(encoding="utf-8")
        with (self.store / "run-1" / "events.jsonl").open("a", encoding="utf-8") as events:
            events.write('{"kind":"gate.rep')
        time.sleep(4 * render_state.LIVE_POLL_SECONDS)
        self.assertFalse(self.counter("inventory").exists())
        self.assertEqual(self.counter("runs").read_text(encoding="utf-8"), rendered)
        self.assertEqual(client.runs, [])
        self.assertFalse(json.loads(client.get("health")[1])["liveRuns"])

    def test_a_store_that_is_not_watched_leaves_the_record_watched_and_tells_the_views(self) -> None:
        cases = {"inventory exits non-zero": ("fail", False), "inventory passes its timeout": ("slow", False),
                 "store removed while serving": ("ok", True)}
        for case, (mode, remove) in cases.items():
            with self.subTest(case=case):
                directory = self.root / f"state {mode}"
                shutil.copytree(self.state_dir, directory)
                started = time.monotonic()
                _, url = self.serve(directory, "--state-config", self.config_with_runs(mode, timeout=1))
                self.assertLess(time.monotonic() - started, 10)
                client = self.client(url)
                if remove:
                    self.eventually(client, "view/record.html", lambda page: b"flag=watched" in page)
                    shutil.rmtree(self.store)
                self.eventually(client, "view/record.html", lambda page: b"flag=unwatched" in page)
                self.assertEqual(self.said(self.eventually(client, "view/runs.html", bool))["flag"], "unwatched")
                client.listen()
                self.assertIsNotNone(client.wait_for(lambda doc: True, seconds=10))
                state_tool(directory, "journal", "--kind", "decision.recorded", "--text", f"Still {mode}",
                           "--why", "A test.")
                self.assertIsNotNone(client.wait_for(lambda doc: doc["journal"][-1]["data"]["text"] == f"Still {mode}",
                                                     seconds=10))
                self.assertIn(f"Still {mode} flag=unwatched".encode(), client.get("view/record.html")[1])

    def sleeper(self) -> subprocess.Popen:
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        self.addCleanup(LiveTests.stop, self, process)
        return process

    def entry(self, pid: int, port: int, directory: Path) -> Path:
        self.entries.mkdir(exist_ok=True)
        path = self.entries / f"{pid}.json"
        path.write_text(json.dumps({"pid": pid, "host": "127.0.0.1", "port": port,
                                    "stateDirectory": str(directory.resolve()), "liveRuns": True}), encoding="utf-8")
        return path

    def stranger(self, pid: int) -> int:
        """A listener that answers /health as a live server of another repository, claiming ``pid``."""
        said = json.dumps({"server": "render_state.py live", "pid": pid, "repository": str(self.root / "elsewhere")})

        class Answer(http.server.BaseHTTPRequestHandler):
            # As a live server answers, so only what it says, not how, keeps it out.
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - the base class's name
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(said)))
                self.end_headers()
                self.wfile.write(said.encode("ascii"))

            def log_message(self, *args: object) -> None:
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Answer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def free_port(self) -> int:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def if_absent(self, directory: Path, *extra: str) -> tuple[subprocess.Popen, dict]:
        process = subprocess.Popen([sys.executable, "-B", str(WRAPPER), "live", "--dir", str(directory), "--if-absent",
                                    *extra], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(LiveTests.stop, self, process)
        return process, json.loads(process.stdout.readline())

    def test_if_absent_names_a_verified_server_and_otherwise_serves_itself(self) -> None:
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait(10)
        sleeper = self.sleeper()
        self.entry(dead.pid, self.free_port(), self.empty)
        self.entry(sleeper.pid, self.free_port(), self.empty)
        stranger = self.sleeper()
        self.entry(stranger.pid, self.stranger(stranger.pid), self.empty)
        # Only a stale entry, a dead PID, and a listener that answers as another repository's server: it serves.
        process, said = self.if_absent(self.state_dir)
        self.assertNotIn("existing", said)
        self.assertIsNone(process.poll())
        self.assertIn(process.pid, self.own_entries())
        # Now a verified one is there: a second start names it and exits, starting nothing, even from a worktree
        # whose state directory does not exist.
        before = set(self.own_entries())
        second, named = self.if_absent(self.root / "not made")
        self.assertEqual(second.wait(10), 0)
        self.assertEqual((named["live"], named["pid"], named["existing"]),
                         (said["live"], process.pid, True))
        self.assertEqual(set(self.own_entries()), before)
        self.assertTrue(sleeper.poll() is None and stranger.poll() is None)

    def test_each_server_keeps_its_own_entry_and_stop_stops_only_this_state_directorys(self) -> None:
        record, record_url = self.serve(self.state_dir)
        runs, runs_url = self.serve(self.empty, "--state-config", self.config_with_runs())
        self.assertEqual({record.pid, runs.pid}, set(self.own_entries()))
        # --if-absent prefers the one with liveRuns.
        named_by, named = self.if_absent(self.root / "state")
        self.assertEqual(named_by.wait(10), 0)
        self.assertEqual(f"http://{named['live']}/", runs_url)
        # A clean stop removes only its own entry; the other stays verified.
        record.send_signal(signal.SIGTERM)
        self.assertEqual(record.wait(10), 128 + signal.SIGTERM)
        self.assertEqual(set(self.own_entries()), {runs.pid})
        self.assertEqual([entry["pid"] for entry in render_state.verified_entries(self.common)], [runs.pid])
        # Both up again; --stop for the runs server's state directory stops it alone.
        record, record_url = self.serve(self.state_dir)
        stopped = run([sys.executable, "-B", str(WRAPPER), "live", "--dir", str(self.empty), "--stop"])
        self.assertEqual(stopped.returncode, 0, stopped.stdout)
        self.assertEqual(json.loads(stopped.stdout)["stopped"],
                         [{"pid": runs.pid, "live": runs_url[len("http://"):-1], "entryRemoved": True}])
        self.assertEqual(runs.wait(10), 128 + signal.SIGTERM)
        self.assertEqual(set(self.own_entries()), {record.pid})
        self.assertIsNone(record.poll())
        health = json.loads(self.client(record_url).get("health")[1])
        self.assertEqual((health["pid"], health["repository"]), (record.pid, str(self.common)))
        # The test stops the second by the PID it recorded.
        record.send_signal(signal.SIGTERM)
        self.assertEqual(record.wait(10), 128 + signal.SIGTERM)
        self.assertEqual(self.own_entries(), {})

    def test_stop_never_signals_an_entry_that_fails_verification(self) -> None:
        silent, stranger, reused = self.sleeper(), self.sleeper(), self.sleeper()
        self.entry(silent.pid, self.free_port(), self.empty)
        self.entry(stranger.pid, self.stranger(stranger.pid), self.empty)
        # A PID reused while its port went to this repository's own live server: the server answers with its PID.
        server, url = self.serve(self.empty)
        self.entry(reused.pid, int(url.rsplit(":", 1)[1].rstrip("/")), self.empty)
        stopped = run([sys.executable, "-B", str(WRAPPER), "live", "--dir", str(self.empty), "--stop"])
        self.assertEqual(stopped.returncode, 0, stopped.stdout)
        self.assertEqual([entry["pid"] for entry in json.loads(stopped.stdout)["stopped"]], [server.pid])
        self.assertEqual(server.wait(10), 128 + signal.SIGTERM)
        time.sleep(0.2)
        for process in (silent, stranger, reused):
            self.assertIsNone(process.poll())
        self.assertEqual(set(self.own_entries()), {silent.pid, stranger.pid, reused.pid})
        for process in (silent, stranger, reused):
            os.kill(process.pid, signal.SIGKILL)
            process.wait(10)


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
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--text", "A lane's entry", "--why", "A test.", "--lane", "lane-1")
        shown = state_tool(self.state_dir, "show")
        changes = self.changes()[before:]
        self.assertEqual([change["change"] for change in changes], ["patch", "journal"])
        self.assertEqual(changes[0]["version"], version + 1)
        self.assertEqual(changes[1]["journal"], shown["journal"][-1]["id"])
        # A refused write records nothing.
        result = run([sys.executable, "-B", str(STATE_TOOL), "--dir", str(self.state_dir), "patch", "--writer", "lead",
                      "--version", "1", "--patch", "{}"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.changes()), before + 2)

    def test_a_refused_patch_records_no_change(self) -> None:
        before = len(self.changes())
        version = state_tool(self.state_dir, "show")["state"]["version"]
        result = run([sys.executable, "-B", str(STATE_TOOL), "--dir", str(self.state_dir), "patch", "--writer", "lead",
                      "--version", str(version), "--patch", json.dumps({"meta": {"headline": 3}})])
        self.assertEqual(json.loads(result.stdout)["code"], "patch_invalid")
        self.assertEqual(len(self.changes()), before, "a refused write records no change")

    def test_each_write_the_tool_makes_for_a_lane_records_one_change(self) -> None:
        # A hand-off writes the record, then journals it: one change each.
        before = len(self.changes())
        state_tool(self.state_dir, "handoff", "--lane", "lane-1", "--subject", "change-1", "--head", "d" * 40,
                   "--change-request", "cr 9")
        changes = self.changes()[before:]
        self.assertEqual([change["change"] for change in changes], ["patch", "journal"])
        self.assertEqual(changes[0]["version"], state_tool(self.state_dir, "show")["state"]["version"])

    def test_the_change_log_stays_within_its_cap_and_keeps_the_latest_changes(self) -> None:
        path = self.state_dir / "changes.jsonl"
        limit = delivery_state.CHANGES_LIMIT_BYTES
        seeded = [json.dumps({"at": "2026-10-21T00:00:00Z", "change": "journal", "journal": n, "version": 1},
                             sort_keys=True, separators=(",", ":")) for n in range(2 * limit // 60)]
        path.write_text("\n".join(seeded) + "\n", encoding="utf-8")
        self.assertGreater(path.stat().st_size, limit)
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--text", "Past the cap", "--why", "A test.")
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
            state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--text", f"After {n}", "--why", "A test.")
        self.assertEqual([change["change"] for change in self.changes()[-3:]], ["journal"] * 3)
        self.assertEqual(path.stat().st_ino, trimmed.st_ino, "below the cap, a write only appends")
        self.assertGreater(path.stat().st_size, trimmed.st_size)

    def test_a_notification_that_cannot_be_written_never_fails_the_write(self) -> None:
        path = self.state_dir / "changes.jsonl"
        path.unlink(missing_ok=True)
        path.mkdir()
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--text", "Still written", "--why", "A test.")
        self.assertEqual(state_tool(self.state_dir, "show")["journal"][-1]["data"]["text"], "Still written")


class WrapperTests(DeliveryStateFixture):
    def test_a_renderer_argv_gets_the_resolved_state_directory(self) -> None:
        argv = python_argv("import sys\nsys.stdout.write(repr(sys.argv[1:]))\n", self.tools, "where")
        renderers = [{"id": "where", "argv": [*argv, "{stateDirectory}", "--state-dir={stateDirectory}"],
                      "output": "where.txt"}]
        code, result = render_state.render(self.state_dir, json.loads(self.config(renderers)))
        self.assertEqual(code, 0, result)
        resolved = str(self.state_dir.resolve())
        self.assertEqual((self.state_dir / "where.txt").read_text(), repr([resolved, f"--state-dir={resolved}"]))

    def test_a_publisher_argv_gets_the_resolved_state_directory(self) -> None:
        # A tracker publisher keeps its ledger beside the record it publishes, wherever the binding puts it.
        seen = self.tools / "seen.txt"
        argv = python_argv(f"import sys\nopen({str(seen)!r}, 'w').write(repr(sys.argv[1:]))\n", self.tools, "where")
        publishers = [{"id": "where", "argv": [*argv, "{stateDirectory}", "--state-dir={stateDirectory}"]}]
        code, result = render_state.render(self.state_dir, json.loads(self.config([], publishers)))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["publishers"], [{"exitCode": 0, "id": "where", "ok": True}])
        resolved = str(self.state_dir.resolve())
        self.assertEqual(seen.read_text(), repr([resolved, f"--state-dir={resolved}"]))

    def test_a_built_in_view_that_fails_keeps_its_previous_view(self) -> None:
        renderers = [{"id": "summary", "builtin": "markdown", "output": "delivery.md"}]
        code, result = wrapper(self.state_dir, "--state-config", self.config(renderers))
        self.assertEqual(code, 0, result)
        previous = (self.state_dir / "delivery.md").read_bytes()

        def broken(document: dict, machine: dict | None = None) -> str:
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
        self.assertEqual(set(received), {"state", "journal", "stats", "estimates", "status", "focus", "story"})
        self.assertEqual(received["story"]["schemaVersion"], "story/1")

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
        # The story's beats, one row each, under the one chapter this record holds.
        rows = [line for line in lines[lines.index("## Story") + 1 :] if line.startswith("- ")]
        self.assertEqual([row.split(":")[0] for row in rows], ["- 2026-10-20T10"] * 3)
        self.assertEqual(len([row for row in rows if row.endswith(" Review round 1 on change-1: Findings: 0 P0, 1 P1, 2 P2 and 0 P3")]), 3)

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
        for heading in ("## Checking", "## Queue", "## Lanes", "## Finish line", "## Follow-ups", "## Leases", "## Story"):
            self.assertIn(heading, view)
        self.assertIn("change-1", view)
        # Non-ASCII state text arrives exactly, whatever the console encoding.
        self.assertIn("caf\u00e9 \u2713", view)
        self.assertIn("queued", view)
        # The journal's entry reads as the story tells it: a decision in the lead's words, never the kind's id.
        self.assertIn("Z \\</script\\>", view)
        self.assertTrue(view.rstrip().endswith("indented: A test."))
        self.assertNotIn("decision.recorded", view)

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

    WATCH_SHIM = r"""
const fs = require("fs");
const [scriptPath] = process.argv.slice(2);
const program = fs.readFileSync(scriptPath, "utf8");
const banner = { textContent: "", hidden: true };
globalThis.document = { getElementById: (id) => (id === "supervisor-banner" ? banner : null) };
const answers = [{ banner: "The supervisor is down since then: it received SIGTERM." }, { banner: null }];
const asked = [];
globalThis.fetch = (url) => {
  asked.push(url);
  const answer = answers.shift();
  return Promise.resolve({ json: () => Promise.resolve(answer) });
};
globalThis.setInterval = (callback, ms) => { globalThis.__tick = callback; globalThis.__every = ms; return 1; };
new Function(program)();
(async () => {
  const seen = [];
  for (let i = 0; i < 2; i++) {
    globalThis.__tick();
    await new Promise((r) => setTimeout(r, 10));
    seen.push({ text: banner.textContent, hidden: banner.hidden });
  }
  process.stdout.write(JSON.stringify({ every: globalThis.__every, asked, seen }));
})();
"""

    def test_an_open_page_shows_and_clears_the_supervisor_banner_from_what_the_view_answers(self) -> None:
        markup = render_state._banner_markup(self.root / "no-such-status.json")
        program = re.search(r"<script>\n(.*?)\n</script>", markup, re.S).group(1)
        script, shim = self.root / "watch.js", self.root / "watch-shim.cjs"
        script.write_text(program, encoding="utf-8")
        shim.write_text(self.WATCH_SHIM, encoding="utf-8")
        result = run(["node", str(shim), str(script)])
        self.assertEqual(result.returncode, 0, result.stderr)
        shown = json.loads(result.stdout)
        self.assertEqual(shown["every"], render_state.SUPERVISOR_POLL_MS)
        self.assertEqual(shown["asked"], ["/supervisor", "/supervisor"])
        self.assertEqual(shown["seen"], [{"text": "The supervisor is down since then: it received SIGTERM.", "hidden": False},
                                         {"text": "", "hidden": True}])

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

    def test_the_page_shows_each_check_s_node_and_its_mark_as_the_markdown_does(self) -> None:
        shown = self.run_page({"now": "2026-10-20T12:00:00Z"}, node_document())
        for line in NODE_LINES:
            with self.subTest(line=line):
                self.assertIn(line, shown["text"])
        self.assertEqual(shown["text"].count("below the runtime"), 1, shown["text"])
        self.assertNotIn("before: passed", shown["text"])  # a check that recorded no Node has no detail line

    def test_the_page_names_checks_by_title_as_the_markdown_does(self) -> None:
        shown = self.run_page({"now": "2026-10-20T12:00:00Z"}, titled_document())
        # The page details only a failed check, and lists every check by name on the row.
        self.assertIn("Suite (pytest) failed, whole passed", shown["text"])
        for line in TITLED_LINES[:-1]:
            with self.subTest(line=line):
                self.assertIn(line, shown["text"])
        self.assertNotIn("B", shown["tags"])
        self.assertNotIn(LONG_TITLE, shown["text"])

    def test_the_page_says_whose_checks_ran_as_the_markdown_does(self) -> None:
        document = selection_document()
        shown = self.run_page({}, document)
        for line in selection_lines(document):
            with self.subTest(line=line):
                self.assertIn(line, shown["text"])
        self.assertNotIn(f"lead selection for {document['state']['queue'][0]['head']}", shown["text"])

    def test_the_page_says_a_row_checks_on_the_fallback_as_the_markdown_does(self) -> None:
        shown = self.run_page({}, fallback_document())
        self.assertIn(FALLBACK_LINE, shown["text"])
        self.assertNotIn("checks selected by the provider", shown["text"])

    def test_the_page_says_the_provider_refused_as_the_markdown_does(self) -> None:
        shown = self.run_page({}, refusal_document())
        self.assertIn(REFUSAL_LINE, shown["text"])
        self.assertNotIn("checks selected by the provider", shown["text"])

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

    def test_the_page_shows_a_story_settled_with_no_change_and_its_evidence_as_the_markdown_view_does(self) -> None:
        document = copy.deepcopy(self.document)
        document["state"]["planned"] = [{"subject": "done-1", "title": "Done one", "lane": "lane-1", "stage": "settled",
                                         "noChange": {"evidence": "one-loads: test_one_loads passes", "baseHead": HEAD_A,
                                                      "handedAt": "2026-10-21T09:00:00Z",
                                                      "settledAt": "2026-10-21T10:00:00Z"}}]
        shown = self.run_page({}, document)
        self.assertIn("closed, no change needed", shown["text"])
        self.assertIn(f"Evidence, run at base {HEAD_A[:10]}: one-loads: test_one_loads passes", shown["text"])

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


class PeerSectionTests(unittest.TestCase):
    """The built-in page's peers: the deliveries registered on this record's base beside the repository's git common
    directory, and who holds the base's merge slot, read on every page served."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.registry = self.repo / ".git/agent-skills/deliveries"
        self.registry.mkdir(parents=True)
        self.own = self.root / "own"
        self.register("FX-1", "main", self.own)

    def register(self, key: str, base: str, state: Path, pid: int | None = None, **record) -> None:
        (self.registry / f"{key}.json").write_text(json.dumps(
            {"key": key, "base": base, "lead": str(self.root / f"lead-{key}"), "state": str(state), "pid": pid}))
        if record:
            state.mkdir(parents=True, exist_ok=True)
            (state / "state.json").write_text(json.dumps(record))

    def test_a_delivery_alone_on_its_base_with_the_slot_free_shows_no_section(self) -> None:
        self.register("FX-7", "other", self.root / "seven", queue=[])
        self.assertEqual(render_state.peer_lines(self.own, self.repo), ("main", [], None))
        self.assertEqual(render_state.peers_markup(self.own, self.repo), "")
        # Without a repository, or without this record's own entry, nothing is listed either.
        self.register("FX-9", "main", self.root / "nine", queue=[])
        self.assertEqual(render_state.peers_markup(self.own, None), "")
        self.assertEqual(render_state.peer_lines(self.root / "elsewhere", self.repo), (None, [], None))

    def test_each_peer_on_the_base_with_its_supervisor_its_queue_and_the_slots_holder(self) -> None:
        stopped = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        stopped.kill()
        stopped.wait()
        self.register("FX-8", "main", self.root / "eight", pid=stopped.pid, queue=[])
        self.register("FX-9", "main", self.root / "nine", pid=os.getpid(),
                      meta={"checking": {"now": {"subject": "FX-92"}}},
                      queue=[{"subject": "FX-92", "stage": "checking"}, {"subject": "FX-93", "stage": "queued"},
                             {"subject": "FX-94", "stage": "merged"}])
        self.register("FX-6", "main", self.root / "six")
        (self.registry / "FX-5.json").write_text("{not json")
        lock = delivery_state.merge_slot_path(self.repo, "main")
        lock.parent.mkdir(parents=True)
        (lock.parent / "main.holder").write_text(json.dumps({"delivery": "FX-9", "pid": os.getpid(),
                                                              "since": "2026-11-15T10:00:00Z", "base": "main"}))
        base, lines, holder = render_state.peer_lines(self.own, self.repo)
        self.assertEqual(base, "main")
        self.assertEqual(lines, [
            f"FX-6 (no supervisor; led from {self.root / 'lead-FX-6'}): its record {self.root / 'six'} can't be read",
            f"FX-8 (stale: its supervisor, pid {stopped.pid}, isn't running; led from {self.root / 'lead-FX-8'}): "
            "checking nothing; queued nothing",
            f"FX-9 (live, pid {os.getpid()}; led from {self.root / 'lead-FX-9'}): checking FX-92; queued FX-93",
            "FX-5.json: unreadable"])
        self.assertEqual(holder, f"held by FX-9 (pid {os.getpid()}, since 2026-11-15T10:00:00Z)")
        markup = render_state.peers_markup(self.own, self.repo)
        self.assertIn("<h2>Deliveries on main</h2>", markup)
        self.assertIn(f"<p>main&#x27;s merge slot is held by FX-9 (pid {os.getpid()}, since", markup)
        # A holder file its pid outlived says nothing of who holds the slot.
        (lock.parent / "main.holder").write_text(json.dumps({"delivery": "FX-9", "pid": stopped.pid, "since": "x"}))
        self.assertIsNone(render_state.peer_lines(self.own, self.repo)[2])
        self.assertIn("main&#x27;s merge slot is free", render_state.peers_markup(self.own, self.repo))

    def test_the_built_in_live_page_carries_the_section_and_escapes_what_a_record_says(self) -> None:
        self.register("FX-9", "main", self.root / "nine", queue=[{"subject": "<b>FX-93</b>", "stage": "queued"}])
        markup = render_state.peers_markup(self.own, self.repo)
        self.assertIn("queued &lt;b&gt;FX-93&lt;/b&gt;", markup)
        self.assertNotIn("<b>FX-93", markup)
        page = render_state.render_static_page({"state": {}, "journal": []}, live=True, banner=markup)
        self.assertIn('<section id="peers">', page)


class StoryViewTests(unittest.TestCase):
    """The built-in views print the story the narrator tells, in the registry's words, and the wrapper tells it with
    the plan beside the record."""

    JOURNAL = [
        {"id": 1, "at": "2026-11-16T09:00:00Z", "kind": "record.started", "lane": None, "subject": None,
         "data": {"base": {"ref": "main", "head": HEAD_A}}},
        {"id": 2, "at": "2026-11-16T09:05:00Z", "kind": "lane.assigned", "lane": "lane-1", "subject": "change-1", "data": {"title": "Work one"}},
        {"id": 3, "at": "2026-11-16T10:00:00Z", "kind": "candidate.merged", "lane": "lane-1", "subject": "change-1", "data": {"head": "b" * 40}},
        {"id": 4, "at": "2026-11-16T10:01:00Z", "kind": "finish.met", "lane": None, "subject": None, "data": {"finish": "every-change"}},
    ]
    PLAN = {"finish": [{"id": "every-change", "label": "Every change"}]}

    def document(self) -> dict:
        return {"state": json.loads(json.dumps(FIXED_STATE)), "journal": copy.deepcopy(self.JOURNAL)}

    def test_the_markdown_view_and_the_page_show_the_storys_beats(self) -> None:
        document = render_state.with_story(self.document(), Path(tempfile.mkdtemp()))
        view = render_state.render_markdown(document)
        story = view[view.index("## Story"):]
        for said in ("### change-1 landed · 2026-11-16T10:00:00Z", "55m after it was picked up.",
                     "- 2026-11-16T10:00:00Z change-1 landed: At bbbbbbb", "- 2026-11-16T09:00:00Z The delivery started: On main at aaaaaaa"):
            with self.subTest(said=said):
                self.assertIn(said, story)
        self.assertNotIn("candidate.merged", view)
        page = render_state.render_static_page(document)
        embedded = json.loads(re.search(r'<script type="application/json" id="delivery-state">(.*?)</script>', page, re.S).group(1))
        self.assertEqual(embedded["story"], document["story"])

    def test_the_markdown_view_shows_a_setback_with_its_cost_and_tells_one_that_still_stands_once(self) -> None:
        # A setback still standing is told in the chapter it opened in, and no later chapter repeats it.
        for name, said, unsaid in (
            ("break-stops-a-piece", ["- 2026-11-16T09:08:00Z **A setback: the main line broke**, cost 17m 00s: A check failed on the main line too. "
                                     "It held up A. It ended when the main line was repaired."], []),
            ("standing-break", ["- 2026-11-16T09:10:00Z **A setback: the main line broke**, still standing: The full run failed at d7daad0: "
                                "The full run failed on the main line. It still stands.",
                                "### B landed · 2026-11-16T09:50:00Z\n\n48m after it was picked up.\n"],
             ["A setback still stands: the main line broke."]),
        ):
            folder = ROOT / "tests" / "fixtures" / "narrator" / name
            journal = [json.loads(line) for line in (folder / "journal.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            story = render_state.narrator.narrate(journal, state=json.loads((folder / "state.json").read_text(encoding="utf-8")))
            view = render_state.render_markdown({"state": json.loads(json.dumps(FIXED_STATE)), "journal": journal, "story": story})
            for line in said:
                with self.subTest(name=name, line=line):
                    self.assertIn(line, view[view.index("## Story"):])
            for line in unsaid:
                with self.subTest(name=name, absent=line):
                    self.assertNotIn(line, view)

    def test_the_wrapper_tells_the_story_with_the_plan_beside_the_record(self) -> None:
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        (directory / render_state.PLAN_FILE).write_text(json.dumps(self.PLAN), encoding="utf-8")
        told = render_state.with_story(self.document(), directory)
        [finish] = [beat for beat in told["story"]["beats"] if beat["kind"] == "finish.met"]
        self.assertEqual(finish["words"], {"head": "A finish line was met", "line": "Every change"})
        self.assertEqual(told["story"], render_state.narrator.narrate(self.JOURNAL, state=told["state"], plan=self.PLAN))

    def test_a_changed_head_in_the_registry_changes_the_words_both_views_show(self) -> None:
        planted = json.loads((PRODUCT_ROOT / "skills/orchestrate-work/references/journal-kinds.json").read_text(encoding="utf-8"))
        planted["kinds"]["candidate.merged"]["words"]["head"] = "{subject} reached the main line"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "journal-kinds.json"
            path.write_text(json.dumps(planted), encoding="utf-8")
            with mock.patch.object(render_state.narrator.journal_words, "REGISTRY_FILE", path):
                view = render_state.render_markdown(self.document())
                page = render_state.render_static_page(self.document())
        self.assertIn("change-1 reached the main line: At bbbbbbb", view)
        self.assertIn("### change-1 reached the main line", view)
        embedded = json.loads(re.search(r'<script type="application/json" id="delivery-state">(.*?)</script>', page, re.S).group(1))
        self.assertIn("change-1 reached the main line", [beat["words"]["head"] for beat in embedded["story"]["beats"]])



def _load_machine():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("machine_for_render", SCRIPTS / "machine.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


machine = _load_machine()
MACHINE_AT = 1_800_000_000.0
AT = machine._iso(MACHINE_AT)
PRESSURE = {"cores": 8, "load5": 2.5, "diskFreeBytes": 500 * 2**30, "swapUsedBytes": 0}


class MachineSectionTests(unittest.TestCase):
    """Scenarios 16a to 16c: the Machine section on the built-in page and in the markdown, read live at render time
    from this test's own machine directory, with an explicit capacity."""

    def setUp(self) -> None:
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())) / "machine"
        self.folder.mkdir()
        (self.folder / "capacity.json").write_text(json.dumps(
            {"schemaVersion": "machine-capacity/1", "slots": 8, "setAt": "2026-11-20T00:00:00.000Z",
             "why": "a test's own ledger"}, indent=2) + "\n", encoding="utf-8")
        self.enterContext(mock.patch.dict(os.environ, {"AGENT_SKILLS_MACHINE_DIR": str(self.folder)}))
        self.enterContext(mock.patch.object(render_state, "machine_reading", return_value=PRESSURE))
        self.document = {"state": json.loads(json.dumps(FIXED_STATE)), "journal": FIXED_JOURNAL}
        self.ledger = machine.Ledger(self.folder, clock=lambda: MACHINE_AT)

    def claim(self, slots: int, label: str, **options: object) -> object:
        claim = self.ledger.request("run", label, label=label, slots=slots,
                                    repository="/work/agent-skills/.git", **options)
        self.addCleanup(claim.release)
        return claim

    def views(self) -> tuple[str, str]:
        """The markdown and the page, each drawn as the wrapper draws a built-in view."""
        encoded = json.dumps(self.document).encode("utf-8")
        markdown = render_state._render_builtin("markdown", encoded)
        page = render_state._render_builtin("static-page", encoded)
        self.assertIsNone(markdown.error)
        self.assertIsNone(page.error)
        return markdown.stdout.decode("utf-8"), page.stdout.decode("utf-8")

    def machine_markup(self, page: str) -> str | None:
        match = re.search(r'<section id="machine">.*?</section>', page, re.S)
        return match.group(0) if match else None

    def test_a_full_section_shows_both_claims_the_waiter_and_the_last_five_overrides(self) -> None:
        for number in range(1, 8):
            self.ledger.request("run", f"forced-{number}", label=f"forced-{number}", slots=1,
                                now_why=f"reason {number}").release()
        self.claim(2, "lead-suite", delivery="V26-3689", story="V26-3683")
        self.claim(3, "fullHealth", delivery="Athena")
        self.claim(4, "big-check", delivery="V26-3689")
        markdown, page = self.views()
        section = markdown[markdown.index("## Machine"):]
        self.assertIn("Capacity 8 slots (set 2026-11-20T00:00:00.000Z: a test's own ledger); 5 in use; "
                      "load 2.5 on 8 cores", section)
        self.assertIn(f"- lead-suite · agent-skills · V26-3689 · 2 slots · since {AT} · "
                      "expected end not yet measured", section)
        self.assertIn(f"- fullHealth · agent-skills · Athena · 3 slots · since {AT}", section)
        self.assertIn(f"- 1\\. big-check · agent-skills · V26-3689 · 4 slots · waiting since {AT}",
                      section)
        reasons = re.findall(r"reason (\d)", section)
        self.assertEqual(reasons, ["3", "4", "5", "6", "7"])
        self.assertIn(f"- forced-7 · admitted now at {AT}: reason 7", section)
        markup = self.machine_markup(page)
        self.assertIsNotNone(markup)
        for text in ("Capacity 8 slots", "5 in use", "lead-suite", "fullHealth", "big-check", "reason 7"):
            with self.subTest(text=text):
                self.assertIn(text, markup)
        self.assertEqual(re.findall(r"reason (\d)", markup), ["3", "4", "5", "6", "7"])
        # A hostile label stays text on the page and in the markdown.
        self.claim(1, "<b>x</b>", delivery="V26-3689")
        markdown, page = self.views()
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", self.machine_markup(page))
        self.assertIn("\\<b\\>x\\</b\\>", markdown)

    def test_no_section_for_an_empty_ledger_or_one_with_only_a_wait_in_its_history(self) -> None:
        plain_markdown = render_state.render_markdown(json.loads(json.dumps(self.document)))
        plain_page = render_state.render_static_page(json.loads(json.dumps(self.document)))
        self.assertIsNone(render_state.machine_view())
        self.assertEqual(self.views(), (plain_markdown, plain_page))
        # A claim that waited, was admitted and ended leaves a wait in the history and no override.
        holder = self.ledger.request("run", "suite", label="suite", slots=8)
        waiter = self.ledger.request("run", "probe", label="probe", slots=1)
        self.assertFalse(waiter.admitted)
        holder.release()
        self.assertTrue(waiter.poll())
        waiter.release()
        history = self.ledger.read_history()
        self.assertIn("probe", [entry.get("label") for entry in history if entry.get("event") == "admitted"])
        self.assertFalse(any(entry.get("event") == "override" for entry in history))
        self.assertIsNone(render_state.machine_view())
        self.assertEqual(self.views(), (plain_markdown, plain_page))

    def test_an_unusable_ledger_says_so(self) -> None:
        unusable = Path(self.enterContext(tempfile.TemporaryDirectory())) / "a-file"
        unusable.write_text("not a directory", encoding="utf-8")
        with mock.patch.dict(os.environ, {"AGENT_SKILLS_MACHINE_DIR": str(unusable)}):
            markdown, page = self.views()
        section = markdown[markdown.index("## Machine"):]
        self.assertIn("The machine ledger is unavailable:", section)
        self.assertIn("The machine ledger is unavailable:", self.machine_markup(page))

    def test_an_unset_capacity_a_hand_admission_and_waiters_in_order(self) -> None:
        (self.folder / "capacity.json").unlink()
        cores = os.cpu_count() or 1
        ticks = [0]

        def clock() -> float:
            # Each read a moment later, so the waiters enqueue in order.
            ticks[0] += 1
            return MACHINE_AT + ticks[0]

        ledger = machine.Ledger(self.folder, clock=clock)
        holder = ledger.request("run", "suite", label="suite", slots=cores)
        self.addCleanup(holder.release)
        waiters = [ledger.request("run", name, label=name, slots=1) for name in ("first", "second", "third")]
        for waiter in waiters:
            self.addCleanup(waiter.release)
        self.assertFalse(any(waiter.admitted for waiter in waiters))
        ledger.admit(waiters[2].id, "the release can't wait")
        markdown, page = self.views()
        section = markdown[markdown.index("## Machine"):]
        self.assertIn(f"Capacity {cores} slot" + ("" if cores == 1 else "s") + " (unset, the core count)", section)
        self.assertLess(section.index("- 1\\. first"), section.index("- 2\\. second"))
        self.assertNotIn("third · ", section[section.index("Waiting:"):section.index("Overrides:")])
        self.assertIn("- third · admitted by hand at ", section)
        self.assertIn(": the release can't wait", section)
        markup = self.machine_markup(page)
        self.assertLess(markup.index("<li>first"), markup.index("<li>second"))

    def test_an_absent_machine_directory_shows_no_section_and_is_not_created(self) -> None:
        absent = Path(self.enterContext(tempfile.TemporaryDirectory())) / "never-made"
        plain = (render_state.render_markdown(json.loads(json.dumps(self.document))),
                 render_state.render_static_page(json.loads(json.dumps(self.document))))
        with mock.patch.dict(os.environ, {"AGENT_SKILLS_MACHINE_DIR": str(absent)}):
            self.assertIsNone(render_state.machine_view())
            self.assertEqual(self.views(), plain)
        self.assertFalse(os.path.lexists(absent))

    def test_the_live_page_shows_the_section_beside_the_peers(self) -> None:
        self.claim(2, "lead-suite", delivery="V26-3689")
        state_dir = Path(self.enterContext(tempfile.TemporaryDirectory())) / "state"
        state_tool(state_dir, "init", "--headline", "Live machine")
        view = render_state.LiveView(state_dir, None)
        view.refresh()
        server = render_state._LiveServer(("127.0.0.1", 0), render_state._live_handler(view, lambda header: True))
        serving = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        serving.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(view.stop)
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/", timeout=10) as answer:
            page = answer.read().decode("utf-8")
        markup = self.machine_markup(page)
        self.assertIsNotNone(markup)
        self.assertIn("lead-suite", markup)


if __name__ == "__main__":
    unittest.main()
