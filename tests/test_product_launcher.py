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
from unittest.mock import Mock, patch

from agent_skills.cli import main
from agent_skills.product import CancellationIncompleteError, _run_command

ROOT = Path(__file__).resolve().parents[1]
POSIX_SIGNALS = (signal.SIGINT, signal.SIGTERM) + tuple(
    getattr(signal, name) for name in ("SIGHUP", "SIGQUIT") if hasattr(signal, name)
)


class ProductLauncherTests(unittest.TestCase):
    def test_harness_help_reaches_runtime(self) -> None:
        for arguments in (["--help"], ["-h"], ["gate", "--help"]):
            with self.subTest(arguments=arguments), patch("agent_skills.cli.run_runtime", return_value=17) as runtime:
                self.assertEqual(main(["harness", *arguments]), 17)
                self.assertEqual(runtime.call_args.args[1], ["--help"] if arguments == ["-h"] else arguments)

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
        output = io.StringIO()
        with patch("agent_skills.cli.run_runtime", side_effect=failure), redirect_stdout(output):
            self.assertEqual(main(["harness", "gate"]), 1)
        rendered = json.loads(output.getvalue())
        self.assertEqual(rendered["error"]["code"], "product.cancellation_incomplete")
        self.assertIn("survivingPids=12345", rendered["error"]["message"])

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
                start_new_session=True,
            )
            worker_pid = None
            try:
                deadline = time.monotonic() + 5
                while not (root / "worker.pid").exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue((root / "worker.pid").exists())
                worker_pid = int((root / "worker.pid").read_text())
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
                        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                    worker_pid = None
                    try:
                        deadline = time.monotonic() + 5
                        while not (root / "worker.pid").exists() and time.monotonic() < deadline:
                            time.sleep(0.02)
                        self.assertTrue((root / "worker.pid").exists())
                        worker_pid = int((root / "worker.pid").read_text())
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


if __name__ == "__main__":
    unittest.main()
