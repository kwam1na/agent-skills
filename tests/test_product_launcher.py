"""Native launcher help and cancellation exercise real descendant processes."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import Mock, call, patch

from agent_skills.cli import main
from agent_skills.product import (
    CancellationIncompleteError,
    _DescendantTracker,
    _LAUNCH_MARKER,
    _Process,
    _darwin_environment,
    _darwin_pipes,
    _darwin_process_table,
    _lifeline_handle,
    _observed_child_liveness,
    _proc_environment,
    _proc_process_table,
    _process_table,
    _ps_process_table,
    _run_command,
    _started_before,
    _stop_escaped_descendants,
    _stop_posix_group,
    _wait_for_posix_group,
)

ROOT = Path(__file__).resolve().parents[1]
# Every fixture process inherits this variable, even one that leaves its
# process group or session, so teardown can find whatever a test left behind.
FIXTURE_MARKER = "AGENT_SKILLS_LAUNCHER_FIXTURE"
# Fake process tables name the runner as the owner of the processes it may stop.
UID = os.geteuid() if hasattr(os, "geteuid") else 0


def _fixture_processes(token: str) -> list[int]:
    """Running processes that carry this test's fixture marker.

    macOS discloses no environment for Apple system binaries (/bin/sh and the
    like), so fixtures must run Python, as they all do, to be seen here.
    """
    marker = f"{FIXTURE_MARKER}={token}"
    found = []
    proc = Path("/proc")
    if (proc / "self" / "environ").is_file():
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                environ = (entry / "environ").read_bytes().split(b"\0")
                state = (entry / "stat").read_text().rsplit(")", 1)[1].split()[0]
            except (OSError, IndexError):
                continue
            if marker.encode() in environ and state != "Z":
                found.append(int(entry.name))
    else:
        # BSD ps prints each process's environment after its arguments.
        listing = subprocess.run(["ps", "-A", "-E", "-ww", "-o", "pid=,stat=,command="],
                                 capture_output=True, text=True, check=True).stdout
        for line in listing.splitlines():
            fields = line.split()
            if len(fields) > 2 and marker in fields[2:] and not fields[1].startswith("Z"):
                found.append(int(fields[0]))
    return sorted(pid for pid in found if pid != os.getpid())


def _reap_fixtures(token: str, settle: float = 2) -> list[int]:
    """Kill this test's surviving fixture processes; return those that survived.

    Processes still exiting when the test ends get ``settle`` seconds to go on
    their own, so only real survivors are reported.
    """
    deadline = time.monotonic() + settle
    survivors = _fixture_processes(token)
    while survivors and time.monotonic() < deadline:
        time.sleep(0.05)
        survivors = _fixture_processes(token)
    for pid in survivors:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while _fixture_processes(token) and time.monotonic() < deadline:
        time.sleep(0.05)
    return survivors


def _running(pid: int) -> bool:
    """Whether pid is a live process (not gone, and not a zombie)."""
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _await_pid(path: Path, seconds: float = 5) -> int | None:
    """The pid a worker wrote, once the whole number is there.

    write_text empties the file before writing it, so a reader can see the
    file exist and still find it empty; wait for a whole pid instead.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            return int(path.read_text())
        except (FileNotFoundError, ValueError):
            time.sleep(0.02)
    return None


POSIX_SIGNALS = (signal.SIGINT, signal.SIGTERM) + tuple(
    getattr(signal, name) for name in ("SIGHUP", "SIGQUIT") if hasattr(signal, name)
)


class ProductLauncherTests(unittest.TestCase):
    def fixture_env(self) -> dict[str, str]:
        """An environment that marks this test's processes; teardown reaps them.

        A fixture process still running after the test is a leak, and fails it.
        """
        token = uuid.uuid4().hex
        self.addCleanup(self._assert_no_fixture_survives, token)
        return dict(os.environ, **{FIXTURE_MARKER: token})

    def _assert_no_fixture_survives(self, token: str) -> None:
        survivors = _reap_fixtures(token)
        if survivors:
            self.fail(f"fixture processes outlived the test and were killed: {survivors}")

    def test_harness_help_reaches_runtime(self) -> None:
        for arguments in (["--help"], ["-h"], ["gate", "--help"]):
            with self.subTest(arguments=arguments), patch("agent_skills.cli.run_runtime", return_value=17) as runtime:
                self.assertEqual(main(["harness", *arguments]), 17)
                self.assertEqual(runtime.call_args.args[1], ["--help"] if arguments == ["-h"] else arguments)

    def test_child_poll_failure_does_not_invent_liveness(self) -> None:
        for failure in (OSError("poll failed"), subprocess.SubprocessError("poll failed")):
            with self.subTest(failure=type(failure).__name__):
                child = Mock(pid=12345)
                child.poll.side_effect = failure
                self.assertEqual(
                    _observed_child_liveness(child),
                    ("unknown", "unknown", ()),
                )

    def test_windows_cancellation_uses_native_tree_termination_and_reaps_child(self) -> None:
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signum=signum):
                handlers = {}
                child = Mock(pid=12345)
                def register(sig, handler):
                    handlers[sig] = handler
                    return signal.SIG_DFL
                def wait(*, timeout):
                    if timeout == 0.1:
                        handlers[signum](signum, None)
                        raise subprocess.TimeoutExpired("worker", timeout)
                    return 1
                child.wait.side_effect = wait
                with patch("agent_skills.product.os.name", "nt"), \
                     patch("agent_skills.product.signal.signal", side_effect=register), \
                     patch("agent_skills.product.subprocess.CREATE_NEW_PROCESS_GROUP", 512, create=True), \
                     patch("agent_skills.product.subprocess.Popen", return_value=child) as spawn, \
                     patch("agent_skills.product.subprocess.run") as terminate, \
                     patch("agent_skills.product.os.killpg", create=True) as posix_kill:
                    self.assertEqual(_run_command(["worker"], ROOT), 128 + signum)
                spawn.assert_called_once_with(["worker"], cwd=ROOT, creationflags=512)
                terminate.assert_called_once_with(["taskkill", "/PID", "12345", "/T", "/F"],
                                                  capture_output=True, timeout=2, check=True)
                self.assertEqual(child.wait.call_args.kwargs, {"timeout": 2})
                posix_kill.assert_not_called()
                self.assertEqual(handlers, {signal.SIGINT: signal.SIG_DFL, signal.SIGTERM: signal.SIG_DFL})

    def test_windows_cleanup_failure_reports_only_observed_liveness_and_restores_handlers(self) -> None:
        failures = (subprocess.CalledProcessError(128, "taskkill"), subprocess.TimeoutExpired("taskkill", 2))
        for failure in failures:
            for signum, child_status, expected_state, expected_pids in (
                (signal.SIGINT, None, "confirmed", (12345,)),
                (signal.SIGTERM, 7, "unknown", ()),
            ):
                with self.subTest(failure=type(failure).__name__, signum=signum, child_status=child_status):
                    handlers = {}
                    child = Mock(pid=12345)
                    def register(sig, handler):
                        handlers[sig] = handler
                        return signal.SIG_DFL
                    def wait(*, timeout):
                        if timeout == 0.1:
                            handlers[signum](signum, None)
                            raise subprocess.TimeoutExpired("worker", timeout)
                        return 1
                    child.wait.side_effect = wait
                    child.poll.return_value = child_status
                    with patch("agent_skills.product.os.name", "nt"), \
                         patch("agent_skills.product.signal.signal", side_effect=register), \
                         patch("agent_skills.product.subprocess.CREATE_NEW_PROCESS_GROUP", 512, create=True), \
                         patch("agent_skills.product.subprocess.Popen", return_value=child), \
                         patch("agent_skills.product.subprocess.run", side_effect=failure):
                        with self.assertRaises(CancellationIncompleteError) as raised:
                            _run_command(["worker"], ROOT)
                    self.assertEqual(raised.exception.cleanup, "taskkill-failed")
                    self.assertEqual(raised.exception.signal_number, signum)
                    self.assertEqual(raised.exception.platform, "windows")
                    self.assertEqual(raised.exception.target_pid, 12345)
                    self.assertEqual(raised.exception.liveness, expected_state)
                    self.assertEqual(raised.exception.surviving_pids, expected_pids)
                    self.assertEqual(raised.exception.target_state, "running" if child_status is None else "exited")
                    self.assertIs(raised.exception.__cause__, failure)
                    self.assertEqual(handlers, {signal.SIGINT: signal.SIG_DFL, signal.SIGTERM: signal.SIG_DFL})

    def test_windows_child_wait_failure_reports_confirmed_target_without_inventing_tree_members(self) -> None:
        for wait_failure in (subprocess.TimeoutExpired("worker", 2), OSError("wait failed")):
            with self.subTest(wait_failure=type(wait_failure).__name__):
                handlers = {}
                child = Mock(pid=12345)
                def register(sig, handler):
                    handlers[sig] = handler
                    return signal.SIG_DFL
                def wait(*, timeout):
                    if timeout == 0.1:
                        handlers[signal.SIGINT](signal.SIGINT, None)
                        raise subprocess.TimeoutExpired("worker", timeout)
                    raise wait_failure
                child.wait.side_effect = wait
                child.poll.return_value = None
                with patch("agent_skills.product.os.name", "nt"), \
                     patch("agent_skills.product.signal.signal", side_effect=register), \
                     patch("agent_skills.product.subprocess.CREATE_NEW_PROCESS_GROUP", 512, create=True), \
                     patch("agent_skills.product.subprocess.Popen", return_value=child), \
                     patch("agent_skills.product.subprocess.run"):
                    with self.assertRaises(CancellationIncompleteError) as raised:
                        _run_command(["worker"], ROOT)
                self.assertEqual(raised.exception.cleanup, "child-wait-failed")
                self.assertEqual(raised.exception.signal_number, signal.SIGINT)
                self.assertEqual(raised.exception.platform, "windows")
                self.assertEqual(raised.exception.target_pid, 12345)
                self.assertEqual(raised.exception.liveness, "confirmed")
                self.assertEqual(raised.exception.surviving_pids, (12345,))
                self.assertEqual(raised.exception.target_state, "running")
                self.assertIs(raised.exception.__cause__, wait_failure)
                self.assertEqual(handlers, {signal.SIGINT: signal.SIG_DFL, signal.SIGTERM: signal.SIG_DFL})

    def test_cli_renders_incomplete_cancellation_as_a_distinct_product_error(self) -> None:
        failure = CancellationIncompleteError(
            signal_number=signal.SIGTERM,
            platform="posix",
            cleanup="post-escalation-timeout",
            target_pid=12345,
            target_state="running",
            liveness="confirmed",
            surviving_pids=(12345,),
        )
        self.assertEqual(failure.signal_number, signal.SIGTERM)
        self.assertEqual(failure.platform, "posix")
        self.assertEqual(failure.cleanup, "post-escalation-timeout")
        self.assertEqual(failure.target_pid, 12345)
        self.assertEqual(failure.target_state, "running")
        self.assertEqual(failure.liveness, "confirmed")
        self.assertEqual(failure.surviving_pids, (12345,))
        output = io.StringIO()
        with patch("agent_skills.cli.run_runtime", side_effect=failure), redirect_stdout(output):
            self.assertEqual(main(["harness", "gate"]), 1)
        rendered = json.loads(output.getvalue())
        self.assertEqual(rendered["error"]["code"], "product.cancellation_incomplete")
        self.assertEqual(
            rendered["error"]["message"],
            "product.cancellation_incomplete: signal=SIGTERM; platform=posix;"
            "cleanup=post-escalation-timeout;targetPid=12345;targetState=running;"
            "liveness=confirmed;survivingPids=12345",
        )

    def test_signal_during_spawn_is_forwarded_after_child_assignment(self) -> None:
        for signum in POSIX_SIGNALS:
            with self.subTest(signum=signum):
                handlers = {}
                child = Mock(pid=12345)
                def register(sig, handler):
                    handlers[sig] = handler
                    return signal.SIG_DFL
                def kill_group(pid, forwarded):
                    if forwarded == 0:
                        raise ProcessLookupError
                with patch("agent_skills.product.os.name", "posix"), \
                     patch("agent_skills.product.signal.signal", side_effect=register), \
                     patch("agent_skills.product.os.killpg", side_effect=kill_group, create=True) as killpg:
                    def spawn(*args, **kwargs):
                        handlers[signum](signum, None)
                        killpg.assert_not_called()
                        return child
                    with patch("agent_skills.product.subprocess.Popen", side_effect=spawn):
                        self.assertEqual(_run_command(["worker"], ROOT), 128 + signum)
                forwarded = [call.args for call in killpg.call_args_list if call.args[1] != 0]
                self.assertEqual(forwarded, [(child.pid, signum)])
                self.assertEqual(handlers, {sig: signal.SIG_DFL for sig in POSIX_SIGNALS})

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup model")
    def test_posix_pre_escalation_observation_failure_refuses_clean_cancellation(self) -> None:
        handlers = {}
        child = Mock(pid=12345)
        observation_failure = OSError("group observation unavailable")

        def register(sig, handler):
            handlers[sig] = handler
            return signal.SIG_DFL

        def wait(*, timeout):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            raise subprocess.TimeoutExpired("worker", timeout)

        child.wait.side_effect = wait
        child.poll.return_value = None
        with patch("agent_skills.product.signal.signal", side_effect=register), \
             patch("agent_skills.product.subprocess.Popen", return_value=child), \
             patch("agent_skills.product.os.killpg"), \
             patch(
                 "agent_skills.product._wait_for_posix_group",
                 side_effect=observation_failure,
             ):
            with self.assertRaises(CancellationIncompleteError) as raised:
                _run_command(["worker"], ROOT)
        self.assertEqual(raised.exception.cleanup, "pre-escalation-wait-failed")
        self.assertEqual(raised.exception.signal_number, signal.SIGTERM)
        self.assertEqual(raised.exception.platform, "posix")
        self.assertEqual(raised.exception.target_pid, 12345)
        self.assertEqual(raised.exception.target_state, "running")
        self.assertEqual(raised.exception.liveness, "confirmed")
        self.assertEqual(raised.exception.surviving_pids, (12345,))
        self.assertIs(raised.exception.__cause__, observation_failure)
        self.assertEqual(handlers, {sig: signal.SIG_DFL for sig in POSIX_SIGNALS})

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup model")
    def test_posix_sigkill_failure_refuses_clean_cancellation(self) -> None:
        handlers = {}
        child = Mock(pid=12345)
        kill_failure = OSError("SIGKILL unavailable")

        def register(sig, handler):
            handlers[sig] = handler
            return signal.SIG_DFL

        def wait(*, timeout):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            raise subprocess.TimeoutExpired("worker", timeout)

        def kill_group(pid, forwarded):
            if forwarded == signal.SIGKILL:
                raise kill_failure

        child.wait.side_effect = wait
        child.poll.return_value = None
        with patch("agent_skills.product.signal.signal", side_effect=register), \
             patch("agent_skills.product.subprocess.Popen", return_value=child), \
             patch("agent_skills.product.os.killpg", side_effect=kill_group), \
             patch("agent_skills.product._wait_for_posix_group", return_value=False):
            with self.assertRaises(CancellationIncompleteError) as raised:
                _run_command(["worker"], ROOT)
        self.assertEqual(raised.exception.cleanup, "sigkill-failed")
        self.assertEqual(raised.exception.signal_number, signal.SIGTERM)
        self.assertEqual(raised.exception.platform, "posix")
        self.assertEqual(raised.exception.target_pid, 12345)
        self.assertEqual(raised.exception.target_state, "running")
        self.assertEqual(raised.exception.liveness, "confirmed")
        self.assertEqual(raised.exception.surviving_pids, (12345,))
        self.assertIs(raised.exception.__cause__, kill_failure)
        self.assertEqual(handlers, {sig: signal.SIG_DFL for sig in POSIX_SIGNALS})

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup model")
    def test_posix_sigkill_process_lookup_confirms_clean_cancellation(self) -> None:
        handlers = {}
        child = Mock(pid=12345)

        def register(sig, handler):
            handlers[sig] = handler
            return signal.SIG_DFL

        def wait(*, timeout):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            raise subprocess.TimeoutExpired("worker", timeout)

        def kill_group(pid, forwarded):
            if forwarded == signal.SIGKILL:
                raise ProcessLookupError

        child.wait.side_effect = wait
        with patch("agent_skills.product.signal.signal", side_effect=register), \
             patch("agent_skills.product.subprocess.Popen", return_value=child), \
             patch("agent_skills.product.os.killpg", side_effect=kill_group), \
             patch("agent_skills.product._wait_for_posix_group", return_value=False):
            self.assertEqual(_run_command(["worker"], ROOT), 128 + signal.SIGTERM)
        self.assertEqual(handlers, {sig: signal.SIG_DFL for sig in POSIX_SIGNALS})

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup model")
    def test_a_group_probe_refused_while_its_members_exit_is_polled_until_it_drains(self) -> None:
        # macOS answers a group whose last members are exiting with EPERM, not
        # ESRCH: the group is being torn down, not unobservable.
        child = Mock(pid=12345)
        with patch("agent_skills.product.os.killpg",
                   side_effect=[PermissionError, PermissionError, ProcessLookupError]) as killpg:
            self.assertTrue(_wait_for_posix_group(child, 5))
        self.assertEqual(killpg.call_count, 3)
        with patch("agent_skills.product.os.killpg", side_effect=PermissionError):
            # A group that stays unsignallable is not drained, and is never an error.
            self.assertFalse(_wait_for_posix_group(child, 0.12))
        # While it drains, the tree is observed on every tick.
        observe = Mock()
        with patch("agent_skills.product.os.killpg",
                   side_effect=[PermissionError, PermissionError, ProcessLookupError]):
            self.assertTrue(_wait_for_posix_group(child, 5, observe))
        self.assertEqual(observe.call_count, 3)

    def _cancel_with_group(self, kill_group, drains):
        handlers = {}
        child = Mock(pid=12345)

        def register(sig, handler):
            handlers[sig] = handler
            return signal.SIG_DFL

        def wait(*, timeout):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            raise subprocess.TimeoutExpired("worker", timeout)

        child.wait.side_effect = wait
        child.poll.return_value = None
        with patch("agent_skills.product.signal.signal", side_effect=register), \
             patch("agent_skills.product.subprocess.Popen", return_value=child), \
             patch("agent_skills.product.os.killpg", side_effect=kill_group), \
             patch("agent_skills.product._wait_for_posix_group", side_effect=drains):
            try:
                return _run_command(["worker"], ROOT)
            finally:
                self.assertEqual(handlers, {sig: signal.SIG_DFL for sig in POSIX_SIGNALS})

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup model")
    def test_a_sigkill_refused_while_members_exit_is_clean_once_the_group_drains(self) -> None:
        refusal = PermissionError("only exiting members remain")

        def kill_group(pid, forwarded):
            if forwarded == signal.SIGKILL:
                raise refusal

        self.assertEqual(self._cancel_with_group(kill_group, [False, True]), 128 + signal.SIGTERM)
        with self.assertRaises(CancellationIncompleteError) as raised:
            self._cancel_with_group(kill_group, [False, False])
        self.assertEqual(raised.exception.cleanup, "group-drain-refused")
        self.assertIs(raised.exception.__cause__, refusal)
        # A failure while waiting for that drain is the reported cause.
        wait_failure = OSError("group observation unavailable")
        with self.assertRaises(CancellationIncompleteError) as raised:
            self._cancel_with_group(kill_group, [False, wait_failure])
        self.assertEqual(raised.exception.cleanup, "group-drain-refused")
        self.assertIs(raised.exception.__cause__, wait_failure)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup model")
    def test_forwarding_to_a_group_whose_members_are_exiting_does_not_escape_the_handler(self) -> None:
        def kill_group(pid, forwarded):
            if forwarded == signal.SIGTERM:
                raise PermissionError("only exiting members remain")

        self.assertEqual(self._cancel_with_group(kill_group, [True]), 128 + signal.SIGTERM)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup model")
    def test_posix_post_escalation_timeout_reports_observed_child_only(self) -> None:
        handlers = {}
        child = Mock(pid=12345)
        def register(sig, handler):
            handlers[sig] = handler
            return signal.SIG_DFL
        def wait(*, timeout):
            if timeout == 0.1:
                handlers[signal.SIGTERM](signal.SIGTERM, None)
                raise subprocess.TimeoutExpired("worker", timeout)
            raise subprocess.TimeoutExpired("worker", timeout)
        child.wait.side_effect = wait
        child.poll.return_value = None
        with patch("agent_skills.product.signal.signal", side_effect=register), \
             patch("agent_skills.product.subprocess.Popen", return_value=child), \
             patch("agent_skills.product.os.killpg"), \
             patch("agent_skills.product.time.monotonic", side_effect=(0, 3)):
            with self.assertRaises(CancellationIncompleteError) as raised:
                _run_command(["worker"], ROOT)
        self.assertEqual(raised.exception.cleanup, "post-escalation-timeout")
        self.assertEqual(raised.exception.liveness, "confirmed")
        self.assertEqual(raised.exception.surviving_pids, (12345,))
        self.assertEqual(raised.exception.target_state, "running")

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup model")
    def test_posix_post_escalation_observation_failure_does_not_invent_survivors(self) -> None:
        handlers = {}
        child = Mock(pid=12345)
        def register(sig, handler):
            handlers[sig] = handler
            return signal.SIG_DFL
        waits = 0
        def wait(*, timeout):
            nonlocal waits
            waits += 1
            if waits == 1:
                handlers[signal.SIGTERM](signal.SIGTERM, None)
                raise subprocess.TimeoutExpired("worker", timeout)
            raise observation_failure
        child.wait.side_effect = wait
        child.poll.return_value = 7
        observation_failure = OSError("child wait unavailable")
        with patch("agent_skills.product.signal.signal", side_effect=register), \
             patch("agent_skills.product.subprocess.Popen", return_value=child), \
             patch("agent_skills.product.os.killpg"), \
             patch("agent_skills.product._wait_for_posix_group", return_value=False):
            with self.assertRaises(CancellationIncompleteError) as raised:
                _run_command(["worker"], ROOT)
        self.assertEqual(raised.exception.cleanup, "post-escalation-wait-failed")
        self.assertEqual(raised.exception.liveness, "unknown")
        self.assertEqual(raised.exception.surviving_pids, ())
        self.assertEqual(raised.exception.target_state, "exited")
        self.assertIs(raised.exception.__cause__, observation_failure)

    @unittest.skipUnless(os.name == "posix" and hasattr(signal, "SIGHUP"), "Requires POSIX SIGHUP")
    def test_inherited_ignored_sighup_is_preserved_until_an_explicit_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "worker.py").write_text(
                "import os, signal, time\nfrom pathlib import Path\n"
                "def received(signum, _frame):\n"
                "    Path('worker.sig').write_text(str(signum))\n"
                "    os._exit(0)\n"
                "signal.signal(signal.SIGTERM, received)\n"
                "Path('worker.pid').write_text(str(os.getpid()))\n"
                "while True: time.sleep(1)\n"
            )
            (root / "parent.py").write_text(
                "import subprocess, sys\nsubprocess.run([sys.executable, 'worker.py'])\n"
            )
            program = """import signal, sys
from pathlib import Path
from agent_skills.product import _run_command
signal.signal(signal.SIGHUP, signal.SIG_IGN)
raise SystemExit(_run_command([sys.executable, 'parent.py'], Path(sys.argv[1])))
"""
            launcher = subprocess.Popen(
                [sys.executable, "-B", "-c", program, str(root)], cwd=ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True, env=self.fixture_env(),
            )
            worker_pid = None
            try:
                worker_pid = _await_pid(root / "worker.pid")
                self.assertIsNotNone(worker_pid, "worker never wrote its pid")
                launcher.send_signal(signal.SIGHUP)
                time.sleep(0.2)
                self.assertIsNone(launcher.poll(), "inherited ignored SIGHUP was overridden")
                os.kill(worker_pid, 0)
                launcher.send_signal(signal.SIGTERM)
                stdout, stderr = launcher.communicate(timeout=7)
                self.assertEqual(launcher.returncode, 128 + signal.SIGTERM, stdout + stderr)
                self.assertEqual((root / "worker.sig").read_text(), str(signal.SIGTERM))
            finally:
                if launcher.poll() is None:
                    launcher.kill()
                launcher.communicate()
                if worker_pid:
                    try:
                        os.kill(worker_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    @unittest.skipUnless(os.name == "nt", "Requires real Windows taskkill and process handles")
    def test_windows_cancellation_stops_real_descendants(self) -> None:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "worker.py").write_text("import os, time\nfrom pathlib import Path\nPath('worker.pid').write_text(str(os.getpid()))\nwhile True: time.sleep(1)\n")
            (root / "parent.py").write_text("import os, subprocess, sys\nfrom pathlib import Path\nPath('parent.pid').write_text(str(os.getpid()))\nsubprocess.run([sys.executable, '-B', 'worker.py'])\n")
            # Deliver SIGINT inside the launcher: Windows terminate() forcibly
            # ends a process and cannot exercise a Python signal handler.
            program = """import signal, sys, threading, time
from pathlib import Path
from agent_skills.product import _run_command
root = Path(sys.argv[1])
def cancel():
    while not (root / 'worker.pid').exists(): time.sleep(0.02)
    signal.raise_signal(signal.SIGINT)
threading.Thread(target=cancel, daemon=True).start()
raise SystemExit(_run_command([sys.executable, '-B', 'parent.py'], root))
"""
            launcher = subprocess.Popen([sys.executable, "-B", "-c", program, str(root)], cwd=ROOT,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                stdout, stderr = launcher.communicate(timeout=10)
                self.assertEqual(launcher.returncode, 130, stdout + stderr)
                for name in ("parent.pid", "worker.pid"):
                    pid = int((root / name).read_text())
                    handle = kernel.OpenProcess(0x00100000, False, pid)
                    if handle:
                        try:
                            self.assertEqual(kernel.WaitForSingleObject(handle, 0), 0, f"{name} still running")
                        finally:
                            kernel.CloseHandle(handle)
            finally:
                if launcher.poll() is None:
                    launcher.kill()
                    launcher.communicate()
                for name in ("parent.pid", "worker.pid"):
                    if (root / name).exists():
                        subprocess.run(["taskkill", "/PID", (root / name).read_text(), "/T", "/F"],
                                       capture_output=True, timeout=2)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group signal and ps liveness proof")
    def test_cancellation_stops_descendants_and_escalates(self) -> None:
        grouped = tuple(
            getattr(signal, name) for name in ("SIGHUP", "SIGQUIT") if hasattr(signal, name)
        )
        deliveries = [(sig, False) for sig in POSIX_SIGNALS] + [(sig, True) for sig in grouped]
        for signum, terminal_group in deliveries:
            for resistant in (False, True):
                with self.subTest(signum=signum, resistant=resistant, terminal_group=terminal_group), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    worker = root / "worker.py"
                    signal_names = [sig.name for sig in POSIX_SIGNALS]
                    handlers = "".join(
                        f"signal.signal(signal.{name}, signal.SIG_IGN)\n" for name in signal_names
                    ) if resistant else (
                        "def received(signum, _frame):\n"
                        "    Path('worker.sig').write_text(str(signum))\n"
                        "    os._exit(0)\n" + "".join(
                            f"signal.signal(signal.{name}, received)\n" for name in signal_names
                        )
                    )
                    worker.write_text(
                        "import os, signal, time\nfrom pathlib import Path\n"
                        + handlers
                        + "Path('worker.pid').write_text(str(os.getpid()))\nwhile True: time.sleep(1)\n"
                    )
                    parent = root / "parent.py"
                    parent.write_text("import subprocess, sys\nsubprocess.run([sys.executable, 'worker.py'])\n")
                    launcher = subprocess.Popen([sys.executable, "-c",
                        "from pathlib import Path; from agent_skills.product import _run_command; import sys; "
                        "raise SystemExit(_run_command([sys.executable, 'parent.py'], Path(sys.argv[1])))", str(root)],
                        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                        env=self.fixture_env())
                    worker_pid = None
                    try:
                        worker_pid = _await_pid(root / "worker.pid")
                        self.assertIsNotNone(worker_pid, "worker never wrote its pid")
                        if terminal_group:
                            os.killpg(launcher.pid, signum)
                        else:
                            launcher.send_signal(signum)
                        self.assertEqual(launcher.wait(timeout=7), 128 + signum)
                        if not resistant:
                            self.assertTrue((root / "worker.sig").is_file(), "worker never received the forwarded signal")
                            self.assertEqual((root / "worker.sig").read_text(), str(signum))
                        deadline = time.monotonic() + 2
                        while time.monotonic() < deadline:
                            state = subprocess.run(["ps", "-o", "stat=", "-p", str(worker_pid)], capture_output=True, text=True).stdout.strip()
                            if not state or state.startswith("Z"):
                                break
                            time.sleep(0.02)
                        else:
                            self.fail(f"worker {worker_pid} is still running")
                    finally:
                        if launcher.poll() is None:
                            launcher.kill()
                            launcher.wait()
                        if worker_pid:
                            try:
                                os.kill(worker_pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass

    @unittest.skipUnless(os.name == "posix", "POSIX sessions and process groups")
    def test_a_descendant_that_leaves_the_group_is_stopped_with_it(self) -> None:
        escapes = {
            "setsid": "os.setsid()",
            "setpgid": "os.setpgid(0, 0)",
            # A classic daemon: the intermediate exits at once, so no ancestry
            # is left to see; only the inherited launch marker finds it.
            "double-fork": "if os.fork(): os._exit(0)\nos.setsid()\nif os.fork(): os._exit(0)",
        }
        resist = "".join(f"signal.signal(signal.{sig.name}, signal.SIG_IGN)\n" for sig in POSIX_SIGNALS)
        for (mode, escape), during in [(item, during) for item in escapes.items() for during in (False, True)]:
            with self.subTest(mode=mode, during=during), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                escaped = escape + "\n" + resist + "Path('escaped.pid').write_text(str(os.getpid()))\n"
                if during:
                    # It leaves the group only when the cancellation reaches it.
                    indented = "".join(f"    {line}\n" for line in escaped.splitlines())
                    body = (
                        "def received(signum, _frame):\n"
                        + "".join(f"    signal.signal(signal.{sig.name}, signal.SIG_IGN)\n" for sig in POSIX_SIGNALS)
                        + "    try:\n" + "".join(f"    {line}" for line in indented.splitlines(True))
                        + "    except OSError: pass\n"
                        + "    while True: time.sleep(1)\n"
                        + "".join(f"signal.signal(signal.{sig.name}, received)\n" for sig in POSIX_SIGNALS)
                    )
                else:
                    body = escaped
                (root / "escapee.py").write_text(
                    "import os, signal, time\nfrom pathlib import Path\n" + body
                    + "Path('ready').write_text('1')\nwhile True: time.sleep(1)\n"
                )
                # The parent outlives an escapee that exits as it daemonizes, so
                # the launched command is still running when it is cancelled.
                (root / "parent.py").write_text(
                    "import subprocess, sys, time\nsubprocess.run([sys.executable, 'escapee.py'])\n"
                    "while True: time.sleep(1)\n"
                )
                env = self.fixture_env()
                launcher = subprocess.Popen([sys.executable, "-c",
                    "from pathlib import Path; from agent_skills.product import _run_command; import sys; "
                    "raise SystemExit(_run_command([sys.executable, 'parent.py'], Path(sys.argv[1])))", str(root)],
                    cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                    env=env)
                try:
                    deadline = time.monotonic() + 5
                    while not (root / "ready").exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue((root / "ready").exists(), "the escapee never started")
                    if not during:
                        self.assertIsNotNone(_await_pid(root / "escaped.pid"), "nothing escaped")
                    # Let the launcher observe the tree at least once.
                    time.sleep(0.5)
                    launcher.send_signal(signal.SIGTERM)
                    self.assertEqual(launcher.wait(timeout=20), 128 + signal.SIGTERM)
                    self.assertEqual(_fixture_processes(env[FIXTURE_MARKER]), [],
                                     f"a {mode} escapee outlived a clean cancellation")
                finally:
                    if launcher.poll() is None:
                        launcher.kill()
                        launcher.wait()

    @unittest.skipUnless(sys.platform == "darwin", "Apple system binaries hide their environment")
    def test_a_daemon_running_a_system_binary_is_stopped_with_the_tree(self) -> None:
        # Each daemon double-forks so no ancestry is left, and runs an Apple
        # binary whose environment macOS does not disclose, so the launch
        # marker in its environment cannot be seen. Pid files show liveness,
        # since neither can the fixture marker.
        double_fork = (
            "time.sleep(0.5)\n"
            "if os.fork() == 0:\n"
            "    if os.fork(): os._exit(0)\n"
            "    os.setsid()\n"
            "    if os.fork(): os._exit(0)\n"
            "    Path('daemon.pid').write_text(str(os.getpid()))\n"
        )
        scenarios = {
            "exec-sleep": double_fork + "    os.execv('/bin/sleep', ['/bin/sleep', '1000'])\n",
            "exec-sh": double_fork + (
                "    os.execv('/bin/sh', ['/bin/sh', '-c',\n"
                "             'trap \"\" TERM INT HUP; /bin/sleep 1000 & echo $! > sleep.pid; wait'])\n"
            ),
            # A Python grandchild with close_fds (the default) starts a shell
            # that backgrounds the job and exits at once: no inherited file
            # descriptor survives either.
            "close-fds-shell": (
                "time.sleep(0.5)\n"
                "subprocess.Popen(['/bin/sh', '-c', 'trap \"\" TERM INT HUP; '\n"
                "                  '/usr/bin/nohup /bin/sleep 1000 >/dev/null 2>&1 & echo $! > daemon.pid'],\n"
                "                 start_new_session=True)\n"
            ),
        }
        for mode, body in scenarios.items():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "parent.py").write_text(
                    "import os, subprocess, time\nfrom pathlib import Path\n" + body
                    + "Path('ready').write_text('1')\nwhile True: time.sleep(1)\n"
                )
                launcher = subprocess.Popen([sys.executable, "-c",
                    "from pathlib import Path; from agent_skills.product import _run_command; import sys; "
                    "raise SystemExit(_run_command([sys.executable, 'parent.py'], Path(sys.argv[1])))", str(root)],
                    cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                    env=self.fixture_env())
                daemons = []
                try:
                    daemon = _await_pid(root / "daemon.pid")
                    self.assertIsNotNone(daemon, "the daemon never started")
                    daemons.append(daemon)
                    if mode == "exec-sh":
                        sleeper = _await_pid(root / "sleep.pid")
                        self.assertIsNotNone(sleeper, "the shell never started its job")
                        daemons.append(sleeper)
                    deadline = time.monotonic() + 5
                    while not (root / "ready").exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    time.sleep(0.3)
                    launcher.send_signal(signal.SIGTERM)
                    self.assertEqual(launcher.wait(timeout=20), 128 + signal.SIGTERM)
                    running = [pid for pid in daemons if _running(pid)]
                    self.assertEqual(running, [], f"a {mode} daemon outlived a clean cancellation")
                finally:
                    if launcher.poll() is None:
                        launcher.kill()
                        launcher.wait()
                    for pid in daemons:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_an_escaped_descendant_that_cannot_be_stopped_is_reported_by_pid(self) -> None:
        child = Mock(pid=12345)
        child.poll.return_value = 0
        tracker = Mock()
        tracker.escaped.return_value = (777,)
        tracker.refused.return_value = ()
        clock = iter(range(1000))
        for signalled, first in (((), signal.SIGTERM), ((777,), None)):
            with self.subTest(signalled=signalled), \
                 patch("agent_skills.product.os.kill") as kill, \
                 patch("agent_skills.product.time.sleep"), \
                 patch("agent_skills.product.time.monotonic", side_effect=lambda: next(clock) * 0.5):
                with self.assertRaises(CancellationIncompleteError) as raised:
                    _stop_escaped_descendants(child, tracker, signal.SIGTERM, signalled)
            sent = [call.args for call in kill.call_args_list]
            # The cancellation signal once, unless it was already forwarded, then SIGKILL.
            self.assertEqual(sent, ([(777, first)] if first else []) + [(777, signal.SIGKILL)])
            self.assertEqual(raised.exception.cleanup, "escaped-descendants-survived")
            self.assertEqual(raised.exception.surviving_pids, (777,))
            self.assertEqual(raised.exception.liveness, "confirmed")
            self.assertIn("survivingPids=777", str(raised.exception))
        tracker.escaped.return_value = ()
        with patch("agent_skills.product.os.kill") as kill:
            _stop_escaped_descendants(child, tracker, signal.SIGTERM)
        kill.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup model")
    def test_a_group_holding_a_member_nothing_may_signal_is_partially_stopped(self) -> None:
        # Another user's member: SIGKILL reaches the launcher's own members, so
        # it succeeds, but the group probe keeps answering EPERM.
        handlers = {}
        child = Mock(pid=12345)
        child.poll.return_value = -9

        def register(sig, handler):
            handlers[sig] = handler
            return signal.SIG_DFL

        def wait(*, timeout):
            if timeout == 0.1:
                handlers[signal.SIGTERM](signal.SIGTERM, None)
                raise subprocess.TimeoutExpired("worker", timeout)
            return -9

        def kill_group(pid, forwarded):
            if forwarded == 0:
                raise PermissionError("a member owned by another user")

        child.wait.side_effect = wait
        clock = iter(range(1000))
        with patch("agent_skills.product.signal.signal", side_effect=register), \
             patch("agent_skills.product.subprocess.Popen", return_value=child), \
             patch("agent_skills.product.os.killpg", side_effect=kill_group) as killpg, \
             patch("agent_skills.product.time.sleep"), \
             patch("agent_skills.product.time.monotonic", side_effect=lambda: next(clock) * 0.5), \
             patch("agent_skills.product._DescendantTracker.group_members", return_value=(12346,)):
            with self.assertRaises(CancellationIncompleteError) as raised:
                _run_command(["worker"], ROOT)
        self.assertIn(call(12345, signal.SIGKILL), killpg.call_args_list)
        self.assertEqual(raised.exception.cleanup, "group-partially-stopped")
        # The members it still sees are named; the one it cannot see is not invented.
        self.assertEqual(raised.exception.surviving_pids, (12346,))
        self.assertEqual(handlers, {sig: signal.SIG_DFL for sig in POSIX_SIGNALS})

    def test_the_tracker_remembers_descendants_by_pid_and_start_after_they_leave(self) -> None:
        me = os.getpid()
        child = Mock(pid=100)
        tables = []
        tracker = _DescendantTracker(child, table=lambda: tables[-1])
        # A process that is not this launcher's child is never tracked.
        tables.append({100: _Process(1, 100, UID, "t0", False), 101: _Process(100, 300, UID, "t1", False)})
        tracker.observe()
        self.assertEqual(tracker.escaped(), ())
        tables.append({
            100: _Process(me, 100, UID, "t0", False),
            101: _Process(100, 100, UID, "t1", False),   # the parent's worker, in the group
            102: _Process(101, 100, UID, "t2", False),   # a grandchild, still in the group
            900: _Process(1, 900, UID, "t9", False),     # unrelated
        })
        tracker.observe()
        self.assertEqual(tracker.escaped(), ())
        self.assertEqual(tracker.group_members(), (100, 101, 102))
        # The worker exits; its child calls setsid() and is reparented to init.
        tables.append({
            100: _Process(me, 100, UID, "t0", False),
            102: _Process(1, 102, UID, "t2", False),
            103: _Process(102, 102, UID, "t3", False),   # born after it escaped
            900: _Process(1, 900, UID, "t9", False),
        })
        self.assertEqual(tracker.escaped(), (102, 103))
        # A reused pid is someone else, and a zombie is not running.
        tables.append({
            100: _Process(me, 100, UID, "t0", False),
            102: _Process(1, 102, UID, "other", False),
            103: _Process(1, 103, UID, "t3", True),
        })
        self.assertEqual(tracker.escaped(), ())
        # Behind an unreadable table, descendants it knew may still be running.
        tables.append(None)
        self.assertIsNone(tracker.escaped())

    def test_the_tracker_observes_every_third_wait_tick(self) -> None:
        reads = []
        child = Mock(pid=100)
        tracker = _DescendantTracker(child, table=lambda: reads.append(1) or {})
        for _ in range(7):
            tracker.observe(throttle=True)
        self.assertEqual(len(reads), 3)

    def test_process_tables_read_parent_group_owner_start_and_zombies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            for pid, stat in (
                ("41", "41 (worker) S 7 41 41 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 5551 0 0"),
                ("42", "42 (odd) name)) Z 41 41 41 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 5552 0 0"),
            ):
                (proc / pid).mkdir()
                (proc / pid / "stat").write_text(stat)
            (proc / "self").mkdir()  # Not a pid: skipped.
            table = _proc_process_table(proc)
        owner = os.getuid() if hasattr(os, "getuid") else 0
        self.assertEqual(table, {
            41: _Process(7, 41, owner, 5551, False),
            42: _Process(41, 41, owner, 5552, True),
        })
        listing = " 41  7 41 501 S    Wed Oct 21 01:20:50 2026\n 42 41 41 501 Z+   Wed Oct 21 01:20:51 2026\n"
        with patch("agent_skills.product.subprocess.run", return_value=Mock(stdout=listing)):
            self.assertEqual(_ps_process_table(), {
                41: _Process(7, 41, UID, "Wed Oct 21 01:20:50 2026", False),
                42: _Process(41, 41, UID, "Wed Oct 21 01:20:51 2026", True),
            })
        with patch("agent_skills.product._darwin_process_table", side_effect=OSError("libproc unavailable")), \
             patch("agent_skills.product._proc_process_table", side_effect=OSError("no proc")), \
             patch("agent_skills.product._ps_process_table", side_effect=OSError("no ps")):
            self.assertIsNone(_process_table())

    @unittest.skipUnless(sys.platform == "darwin", "libproc")
    def test_the_macos_process_table_matches_this_process(self) -> None:
        table = _darwin_process_table()
        mine = table[os.getpid()]
        self.assertEqual((mine.ppid, mine.pgid, mine.uid, mine.zombie),
                         (os.getppid(), os.getpgid(0), os.getuid(), False))
        self.assertEqual(_darwin_process_table()[os.getpid()].started, mine.started)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_teardown_finds_and_reaps_a_fixture_tree_a_killed_launcher_left(self) -> None:
        # The leak behind orphaned fixture trees: a launcher killed
        # outright cannot stop its child's group, which then outlives the test.
        token = uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "worker.py").write_text(
                "import os, time\nfrom pathlib import Path\n"
                "Path('worker.pid').write_text(str(os.getpid()))\nwhile True: time.sleep(1)\n"
            )
            (root / "parent.py").write_text("import subprocess, sys\nsubprocess.run([sys.executable, 'worker.py'])\n")
            launcher = subprocess.Popen([sys.executable, "-c",
                "from pathlib import Path; from agent_skills.product import _run_command; import sys; "
                "raise SystemExit(_run_command([sys.executable, 'parent.py'], Path(sys.argv[1])))", str(root)],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                env=dict(os.environ, **{FIXTURE_MARKER: token}))
            left = []
            try:
                worker = _await_pid(root / "worker.pid")
                self.assertIsNotNone(worker, "worker never wrote its pid")
                launcher.kill()
                launcher.wait()
                left = _fixture_processes(token)
                self.assertIn(worker, left)
                self.assertEqual(len(left), 2, left)  # parent.py and worker.py
            finally:
                if launcher.poll() is None:
                    launcher.kill()
                    launcher.wait()
                reaped = _reap_fixtures(token, settle=0)
            self.assertEqual(reaped, left)
            self.assertEqual(_fixture_processes(token), [])

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup model")
    def test_known_escapees_are_killed_with_the_group_so_a_member_they_hold_can_drain(self) -> None:
        child = Mock(pid=12345)
        child.wait.return_value = -9
        tracker = Mock()
        tracker.escaped.return_value = (777,)
        events = []
        with patch("agent_skills.product._wait_for_posix_group",
                   side_effect=lambda *args: events.append("drain?") or len(events) > 1), \
             patch("agent_skills.product.os.killpg", side_effect=lambda pid, sig: events.append(("killpg", sig))), \
             patch("agent_skills.product.os.kill", side_effect=lambda pid, sig: events.append(("kill", pid, sig))):
            _stop_posix_group(child, signal.SIGTERM, tracker)
        self.assertEqual(events, ["drain?", ("kill", 777, signal.SIGKILL), ("killpg", signal.SIGKILL), "drain?"])

    def test_known_escapees_behind_an_unreadable_table_are_never_reported_stopped(self) -> None:
        me = os.getpid()
        child = Mock(pid=100)
        child.poll.return_value = 0
        tables = [{100: _Process(me, 100, UID, "t0", False), 102: _Process(100, 102, UID, "t2", False)}]
        tracker = _DescendantTracker(child, table=lambda: tables[-1])
        self.assertEqual(tracker.escaped(), (102,))
        tables.append(None)
        self.assertIsNone(tracker.escaped())
        clock = iter(range(1000))
        with patch("agent_skills.product.os.kill"), patch("agent_skills.product.time.sleep"), \
             patch("agent_skills.product.time.monotonic", side_effect=lambda: next(clock) * 0.5):
            with self.assertRaises(CancellationIncompleteError) as raised:
                _stop_escaped_descendants(child, tracker, signal.SIGTERM)
        self.assertEqual(raised.exception.cleanup, "escaped-descendants-unobservable")
        self.assertEqual(raised.exception.surviving_pids, ())
        # A table never read knows of no escapee, and changes nothing.
        unread = _DescendantTracker(child, table=lambda: None)
        self.assertEqual(unread.escaped(), ())
        # Known only from the periodic observation, never from escaped(), an
        # escapee behind a table that cannot be read at cancellation is still
        # not reported as none.
        tables = [{100: _Process(me, 100, UID, "t0", False), 102: _Process(100, 102, UID, "t2", False)}]
        observed = _DescendantTracker(child, table=lambda: tables[-1])
        observed.observe(throttle=True)
        tables.append(None)
        self.assertIsNone(observed.escaped())
        # A child with no descendant yet has nothing to hide.
        tables = [{100: _Process(me, 100, UID, "t0", False)}, None]
        alone = _DescendantTracker(child, table=lambda: tables.pop(0))
        alone.observe()
        self.assertEqual(alone.escaped(), ())

    def test_start_stamps_order_with_slack_for_a_wall_clock_step(self) -> None:
        # macOS: wall-clock (seconds, microseconds), with an hour of slack.
        self.assertFalse(_started_before((1000, 0), (1000, 5)))
        self.assertFalse(_started_before((1000 - 3599, 0), (1000, 0)))
        self.assertTrue(_started_before((1000 - 3601, 0), (1000, 0)))
        # Linux: ticks since boot, exact.
        self.assertTrue(_started_before(4, 5))
        self.assertFalse(_started_before(5, 5))
        # Stamps that do not order are never before.
        self.assertFalse(_started_before("Wed Oct 21 01:20:50 2026", "Wed Oct 21 01:20:51 2026"))

    def test_a_table_unreadable_for_a_moment_is_read_again_before_anything_is_reported(self) -> None:
        child = Mock(pid=12345)
        child.poll.return_value = 0
        for reads, sent in (([None, ()], []),
                            ([None, (777,), ()], [(777, signal.SIGTERM)])):
            tracker = Mock()
            tracker.escaped.side_effect = reads
            tracker.refused.return_value = ()
            clock = iter(range(1000))
            with self.subTest(reads=reads), \
                 patch("agent_skills.product.os.kill") as kill, \
                 patch("agent_skills.product.time.sleep"), \
                 patch("agent_skills.product.time.monotonic", side_effect=lambda: next(clock) * 0.5):
                _stop_escaped_descendants(child, tracker, signal.SIGTERM)
            # An escapee first seen once the table reads again still gets the signal.
            self.assertEqual([call.args for call in kill.call_args_list], sent)

    def test_a_reused_child_pid_never_adopts_someone_elses_processes(self) -> None:
        me = os.getpid()
        child = Mock(pid=100)
        tables = [{100: _Process(me, 100, UID, "t0", False)}]
        tracker = _DescendantTracker(child, table=lambda: tables[-1])
        tracker.observe()
        # The child is reaped and pid 100 now leads an unrelated group.
        tables.append({100: _Process(1, 100, UID, "other", False), 555: _Process(100, 100, UID, "t5", False)})
        self.assertEqual(tracker.escaped(), ())
        self.assertEqual(tracker.group_members(), ())

    def test_a_group_member_seen_only_by_its_group_is_tracked(self) -> None:
        # Re-parented to init before the first observation, still in the group.
        me = os.getpid()
        child = Mock(pid=100)
        tables = [{100: _Process(me, 100, UID, "t0", False), 104: _Process(1, 100, UID, "t4", False)}]
        tracker = _DescendantTracker(child, table=lambda: tables[-1])
        tracker.observe()
        tables.append({100: _Process(me, 100, UID, "t0", False), 104: _Process(1, 104, UID, "t4", False)})
        self.assertEqual(tracker.escaped(), (104,))

    def test_the_marker_sweep_adopts_marked_processes_started_since_the_child(self) -> None:
        me = os.getpid()
        child = Mock(pid=100)
        marker = b"AGENT_SKILLS_LAUNCH=abc"
        environments = {200: [b"HOME=/", marker], 201: [marker], 202: [b"HOME=/"], me: [marker]}
        read = []

        def environment(pid):
            read.append(pid)
            return environments.get(pid)

        table = {
            100: _Process(me, 100, UID, 10, False),
            200: _Process(1, 200, UID, 12, False),   # a double-fork daemon, marked
            201: _Process(1, 201, UID, 5, False),    # started before the child: never read
            202: _Process(1, 202, UID, 13, False),   # unmarked
            203: _Process(1, 203, UID, 14, True),    # a zombie
            me: _Process(1, me, UID, 11, False),     # the launcher itself
        }
        tracker = _DescendantTracker(child, table=lambda: table, marker=marker, environment=environment)
        self.assertEqual(tracker.escaped(), (200,))
        self.assertEqual(sorted(read), [200, 202])
        # A process that did not match is read once, not on every sweep.
        read.clear()
        self.assertEqual(tracker.escaped(), (200,))
        self.assertEqual(read, [])
        # A child reaped before the tracker ever confirmed it: the launch key is
        # unique, so the sweep still adopts what carries it, reading everything.
        read.clear()
        early = dict(table)
        del early[100]
        unconfirmed = _DescendantTracker(child, table=lambda: early, marker=marker, environment=environment)
        self.assertEqual(unconfirmed.escaped(), (200, 201))
        self.assertEqual(sorted(read), [200, 201, 202])
        # Without a marker nothing is read.
        read.clear()
        self.assertEqual(_DescendantTracker(child, table=lambda: table, environment=environment).escaped(), ())
        self.assertEqual(read, [])

    def test_cleanup_never_selects_the_caller_chain_init_or_another_users_process(self) -> None:
        # An environment reader that says every process carries the marker
        # stands in for any matching rule gone wrong.
        me = os.getpid()
        parent, grandparent = 9001, 9002
        child = Mock(pid=100)
        marker = b"AGENT_SKILLS_LAUNCH_k=1"
        table = {
            grandparent: _Process(1, grandparent, UID, 20, False),
            parent: _Process(grandparent, parent, UID, 20, False),
            me: _Process(parent, me, UID, 20, False),
            100: _Process(me, 100, UID, 20, False),
            1: _Process(0, 1, UID, 20, False),
            200: _Process(1, 200, UID, 21, False),       # this run's daemon
            300: _Process(1, 300, UID + 1, 21, False),   # another user's
        }

        def marked(pid):
            return [marker] if pid in {1, 200, 202, 300, parent, grandparent} else [b"HOME=/"]

        with patch("agent_skills.product.os.getppid", return_value=parent), \
             patch("agent_skills.product.os.kill", side_effect=AssertionError("signalled")), \
             patch("agent_skills.product.os.killpg", side_effect=AssertionError("signalled")):
            tracker = _DescendantTracker(child, table=lambda: table, marker=marker, environment=marked)
            self.assertEqual(tracker.escaped(), (200,))
            self.assertEqual(tracker.refused(), (1, 300, parent, grandparent))
            self.assertEqual(tracker.preview(), {
                "group": 100,
                "escaped": [[200, "marker"]],
                "refused": [[1, "init"], [300, "other-user"], [parent, "caller"], [grandparent, "caller"]],
            })
            # A known descendant whose pid now names a later process is not selected.
            table[201] = _Process(100, 201, UID, 22, False)
            tracker.observe()
            table[201] = _Process(1, 201, UID, 23, False)
            self.assertEqual(tracker.escaped(), (200,))
            # Nor is one that started before the run, by an exact start stamp.
            early = _DescendantTracker(child, table=lambda: {**table, 202: _Process(1, 202, UID, 19, False)},
                                       marker=marker, environment=marked)
            with patch("agent_skills.product._started_before", return_value=False):
                self.assertEqual(early.escaped(), (200,))
            self.assertIn([202, "before-run"], early.preview()["refused"])

    @unittest.skipUnless(os.name == "posix", "POSIX process tables")
    def test_the_test_runners_own_parents_are_never_selected(self) -> None:
        table = _process_table()
        self.assertIsNotNone(table)
        parents, pid = [], table[os.getpid()].ppid
        while pid > 1 and pid in table and pid not in parents:
            parents.append(pid)
            pid = table[pid].ppid
        self.assertTrue(parents)
        marker = b"AGENT_SKILLS_LAUNCH_k=1"
        # The widest selection the tracker can make: a child reaped before it
        # was confirmed, so nothing is ruled out by start time, and a reader
        # that says every process carries the marker.
        child = Mock(pid=0)
        with patch("agent_skills.product.os.kill", side_effect=AssertionError("signalled")), \
             patch("agent_skills.product.os.killpg", side_effect=AssertionError("signalled")):
            tracker = _DescendantTracker(child, marker=marker, environment=lambda pid: [marker])
            preview = tracker.preview()
        selected = {pid for pid, _ in preview["escaped"]}
        refused = {pid for pid, _ in preview["refused"]}
        self.assertIsNone(preview["group"])
        self.assertEqual(selected & set(parents), set())
        self.assertNotIn(os.getpid(), selected)
        self.assertNotIn(1, selected)
        self.assertTrue(all(table[pid].uid == UID for pid in selected if pid in table))
        self.assertLessEqual({pid for pid in parents if table[pid].uid == UID}, refused)

    def test_the_lifeline_names_only_holders_of_its_live_read_end(self) -> None:
        me = os.getpid()
        child = Mock(pid=100)
        table = {
            100: _Process(me, 100, UID, 10, False),
            200: _Process(1, 200, UID, 12, False),   # holds the read end
            201: _Process(1, 201, UID, 12, False),   # holds a pipe whose peer closed
            202: _Process(1, 202, UID, 12, False),   # pipes unreadable
        }
        pipes = {200: {0xA}, 201: {0xB, 0}}.get
        tracker = _DescendantTracker(child, table=lambda: table, lifeline=lambda: 0xA, pipes=pipes)
        self.assertEqual(tracker.escaped(), (200,))
        self.assertEqual(tracker.preview()["escaped"], [[200, "lifeline"]])
        # Once nothing holds the read end it names nobody, so a closed pipe,
        # which any process may hold, is never a match.
        for lifeline in (lambda: None, lambda: 0):
            closed = _DescendantTracker(child, table=lambda: table, lifeline=lifeline,
                                        pipes=lambda pid: {0, 0xA, 0xB})
            self.assertEqual(closed.escaped(), ())
        # A read end that closes during the scan may have its handle reused.
        handles = iter([0xA])
        flipped = _DescendantTracker(child, table=lambda: table, lifeline=lambda: next(handles, None), pipes=pipes)
        self.assertEqual(flipped.escaped(), ())
        # Before the child is confirmed, the lifeline names nobody.
        early = {pid: process for pid, process in table.items() if pid != 100}
        unconfirmed = _DescendantTracker(child, table=lambda: early, lifeline=lambda: 0xA, pipes=pipes)
        self.assertEqual(unconfirmed.escaped(), ())

    @unittest.skipUnless(sys.platform == "darwin", "macOS pipe handles")
    def test_the_lifeline_handle_is_the_read_end_while_anything_holds_it(self) -> None:
        read, write = os.pipe()
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"], pass_fds=(read,))
        os.close(read)
        try:
            handle = _lifeline_handle(write)
            self.assertIsNotNone(handle)
            self.assertIn(handle, _darwin_pipes(holder.pid))
            self.assertNotIn(handle, _darwin_pipes(os.getpid()))
        finally:
            holder.kill()
            holder.wait()
        try:
            self.assertIsNone(_lifeline_handle(write))
        finally:
            os.close(write)

    def test_the_fork_watcher_adopts_only_children_of_known_processes(self) -> None:
        me = os.getpid()
        child = Mock(pid=100)
        table = {100: _Process(me, 100, UID, 10, False)}
        tracker = _DescendantTracker(child, table=lambda: table)
        processes = {
            100: table[100],
            555: _Process(1, 555, UID, 5, False),
            300: _Process(100, 300, UID, 11, False),
            301: _Process(555, 301, UID, 11, False),
            302: _Process(300, 302, UID + 1, 12, False),
        }
        with patch("agent_skills.product._darwin_process", side_effect=processes.get):
            self.assertFalse(tracker.adopt(300, 100))   # not confirmed yet
            tracker.observe()
            self.assertTrue(tracker.adopt(300, 100))
            self.assertFalse(tracker.adopt(301, 555))   # its parent is not this run's
            self.assertFalse(tracker.adopt(301, 100))   # not that parent's child
            self.assertFalse(tracker.adopt(302, 300))   # another user's
            # A parent whose pid now names a later process vouches for nobody.
            processes[300] = _Process(100, 300, UID, 99, False)
            processes[303] = _Process(300, 303, UID, 100, False)
            self.assertFalse(tracker.adopt(303, 300))
        self.assertEqual(tracker.refused(), (302,))
        table[300] = _Process(1, 300, UID, 11, False)
        self.assertEqual(tracker.escaped(), (300,))
        self.assertEqual(tracker.preview()["escaped"], [[300, "forked"]])

    def test_a_refused_candidate_fails_cancellation_closed_and_is_never_signalled(self) -> None:
        child = Mock(pid=12345)
        child.poll.return_value = 0
        for escaped in ([()], [(777,), ()]):
            tracker = Mock()
            tracker.escaped.side_effect = escaped
            tracker.refused.return_value = (9001,)
            clock = iter(range(1000))
            with self.subTest(escaped=escaped), \
                 patch("agent_skills.product.os.kill") as kill, \
                 patch("agent_skills.product.time.sleep"), \
                 patch("agent_skills.product.time.monotonic", side_effect=lambda: next(clock) * 0.5):
                with self.assertRaises(CancellationIncompleteError) as raised:
                    _stop_escaped_descendants(child, tracker, signal.SIGTERM)
            self.assertEqual(raised.exception.cleanup, "escaped-descendants-refused")
            self.assertNotIn(9001, [call.args[0] for call in kill.call_args_list])

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup model")
    def test_a_cleanup_preview_reports_the_selection_and_stops_nothing(self) -> None:
        handlers = {}
        child = Mock(pid=12345)

        def register(sig, handler):
            handlers[sig] = handler
            return signal.SIG_DFL

        def wait(*, timeout):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            raise subprocess.TimeoutExpired("worker", timeout)

        child.wait.side_effect = wait
        tracker = Mock()
        tracker.preview.return_value = {"group": 12345, "escaped": [[777, "marker"]], "refused": []}
        seen = []
        with patch("agent_skills.product.signal.signal", side_effect=register), \
             patch("agent_skills.product.subprocess.Popen", return_value=child), \
             patch("agent_skills.product._DescendantTracker", return_value=tracker), \
             patch("agent_skills.product.os.killpg", side_effect=AssertionError("signalled")), \
             patch("agent_skills.product.os.kill", side_effect=AssertionError("signalled")), \
             patch("agent_skills.product._stop_posix_group", side_effect=AssertionError("stopped")):
            self.assertEqual(_run_command(["worker"], ROOT, preview=seen.append), 128 + signal.SIGTERM)
        self.assertEqual(seen, [tracker.preview.return_value])

    def test_process_environments_are_read_where_the_platform_exposes_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "41").mkdir()
            (proc / "41" / "environ").write_bytes(b"A=1\0AGENT_SKILLS_LAUNCH=x\0")
            self.assertEqual(_proc_environment(41, proc), [b"A=1", b"AGENT_SKILLS_LAUNCH=x", b""])
            self.assertIsNone(_proc_environment(42, proc))
        if sys.platform == "darwin":
            probe = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"],
                                     env=dict(os.environ, LAUNCHER_PROBE="seen"))
            try:
                deadline = time.monotonic() + 5
                found = None
                while time.monotonic() < deadline:
                    found = _darwin_environment(probe.pid)
                    if found and b"LAUNCHER_PROBE=seen" in found:
                        break
                    time.sleep(0.05)
                self.assertIn(b"LAUNCHER_PROBE=seen", found)
            finally:
                probe.kill()
                probe.wait()
            self.assertIsNone(_darwin_environment(1))  # another user's process

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup model")
    def test_the_tree_is_observed_at_spawn_and_while_the_command_runs(self) -> None:
        child = Mock(pid=12345)
        ticks = iter([subprocess.TimeoutExpired("worker", 0.1)] * 4 + [0])

        def wait(*, timeout):
            outcome = next(ticks)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        child.wait.side_effect = wait
        tracker = Mock()
        with patch("agent_skills.product.subprocess.Popen", return_value=child), \
             patch("agent_skills.product._DescendantTracker", return_value=tracker):
            self.assertEqual(_run_command(["worker"], ROOT), 0)
        # Once right after spawn, then once per wait tick (throttled inside).
        self.assertEqual(tracker.observe.call_args_list, [call()] + [call(throttle=True)] * 4)

    def test_the_launched_command_carries_a_fresh_launch_marker(self) -> None:
        child = Mock(pid=12345)
        child.wait.return_value = 0
        with patch("agent_skills.product.subprocess.Popen", return_value=child) as popen:
            self.assertEqual(_run_command(["worker"], ROOT), 0)
            _run_command(["worker"], ROOT)
        if os.name == "posix":
            keys = [{key for key in call.kwargs["env"] if key.startswith(_LAUNCH_MARKER + "_")}
                    for call in popen.call_args_list]
            # Each launch adds a key of its own, beside any the caller carries.
            self.assertEqual([len(found) for found in keys], [1, 1])
            self.assertNotEqual(keys[0], keys[1])
            self.assertTrue(all(len(key) == len(_LAUNCH_MARKER) + 33 for found in keys for key in found))
            outer = f"{_LAUNCH_MARKER}_{'0' * 32}"
            with patch.dict(os.environ, {outer: "1"}), \
                 patch("agent_skills.product.subprocess.Popen", return_value=child) as nested:
                _run_command(["worker"], ROOT)
            self.assertEqual(nested.call_args.kwargs["env"][outer], "1")

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup model")
    def test_escapees_get_the_signal_before_the_group_stops_and_sigkill_when_it_fails(self) -> None:
        handlers = {}
        child = Mock(pid=12345)
        events = []

        def register(sig, handler):
            handlers[sig] = handler
            return signal.SIG_DFL

        def wait(*, timeout):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            raise subprocess.TimeoutExpired("worker", timeout)

        child.wait.side_effect = wait
        tracker = Mock()
        tracker.escaped.return_value = (777,)
        failure = CancellationIncompleteError(
            signal_number=signal.SIGTERM, platform="posix", cleanup="sigkill-failed",
            target_pid=12345, target_state="running", liveness="confirmed", surviving_pids=(12345,))
        def stop_group(*_args):
            events.append("group")
            if group_stop is not None:
                raise group_stop

        for group_stop, expected in ((None, [("kill", 777, signal.SIGTERM), "group", "escapees"]),
                                     (failure, [("kill", 777, signal.SIGTERM), "group", ("kill", 777, signal.SIGKILL)])):
            events.clear()
            with self.subTest(group_fails=group_stop is not None), \
                 patch("agent_skills.product.signal.signal", side_effect=register), \
                 patch("agent_skills.product.subprocess.Popen", return_value=child), \
                 patch("agent_skills.product._DescendantTracker", return_value=tracker), \
                 patch("agent_skills.product.os.killpg"), \
                 patch("agent_skills.product.os.kill", side_effect=lambda pid, sig: events.append(("kill", pid, sig))), \
                 patch("agent_skills.product._stop_posix_group", side_effect=stop_group), \
                 patch("agent_skills.product._stop_escaped_descendants",
                       side_effect=lambda *args: events.append("escapees")):
                if group_stop is None:
                    self.assertEqual(_run_command(["worker"], ROOT), 128 + signal.SIGTERM)
                else:
                    with self.assertRaises(CancellationIncompleteError) as raised:
                        _run_command(["worker"], ROOT)
                    self.assertIs(raised.exception, failure)
            self.assertEqual(events, expected)


if __name__ == "__main__":
    unittest.main()
