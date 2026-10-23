"""Inherited analysis channels stay explicit and never replace native stdio."""
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from contextlib import redirect_stdout
from io import StringIO

from agent_skills.cli import parser, main
from agent_skills.product import _run_command, run_runtime


@unittest.skipIf(os.name == "nt", "POSIX descriptor inheritance")
class ProductDescriptorTests(unittest.TestCase):
    def test_capability_discovery_does_not_load_config_or_admit(self):
        output = StringIO()
        with redirect_stdout(output), patch("agent_skills.cli.run_runtime") as runtime:
            self.assertEqual(main(["harness", "--capabilities"]), 0)
        self.assertIn('"passFileDescriptors": true', output.getvalue())
        runtime.assert_not_called()

    def test_forwarding_retains_each_native_readiness_and_policy_check(self):
        lifecycle = Mock(root=Path("/fixture"), lock_path=Path("/fixture/lock"))
        left, right = socket.socketpair()
        try:
            with patch("agent_skills.product.RepositoryLock"), patch("agent_skills.product.runtime_root", return_value=Path("/runtime")) as ready, patch("agent_skills.product.reconcile_policy") as policy, patch("agent_skills.product._run_command", return_value=7) as run:
                for command in ("gate", "verify"):
                    self.assertEqual(run_runtime(lifecycle, [command], pass_fds=(right.fileno(),)), 7)
                self.assertEqual(ready.call_count, 2)
                self.assertEqual(policy.call_count, 2)
                self.assertEqual(run.call_args.kwargs["pass_fds"], (right.fileno(),))
        finally:
            left.close()
            right.close()

    def test_parser_consumes_only_launcher_flags_before_command(self):
        args = parser().parse_args(["harness", "--pass-fd", "8", "gate", "--json"])
        self.assertEqual(args.pass_fd, [8])
        self.assertEqual(args.arguments, ["gate", "--json"])

    def test_explicit_socket_survives_two_native_commands(self):
        with tempfile.TemporaryDirectory() as root:
            left, right = socket.socketpair()
            try:
                for text in ("first", "second"):
                    code = _run_command([sys.executable, "-c",
                        "import os,sys;os.write(int(sys.argv[1]),sys.argv[2].encode());sys.exit(7)",
                        str(right.fileno()), text], Path(root), pass_fds=(right.fileno(),))
                    self.assertEqual(code, 7)
                    self.assertEqual(left.recv(1024), text.encode())
                self.assertGreaterEqual(os.fstat(right.fileno()).st_mode, 0)
            finally:
                left.close()
                right.close()

    def test_stdin_and_regular_files_are_not_analysis_channels(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryFile() as file:
            for fd in (0, 1, 2, file.fileno(), -1):
                with self.subTest(fd=fd), self.assertRaises(ValueError):
                    _run_command([sys.executable, "-c", "raise AssertionError('must not run')"],
                                 Path(root), pass_fds=(fd,))

    def test_unlisted_descriptor_is_not_inherited(self):
        with tempfile.TemporaryDirectory() as root:
            left, right = socket.socketpair()
            try:
                code = _run_command([sys.executable, "-c",
                    "import os,sys;\ntry: os.fstat(int(sys.argv[1]))\nexcept OSError: sys.exit(0)\nelse: sys.exit(9)",
                    str(right.fileno())], Path(root))
                self.assertEqual(code, 0)
            finally:
                left.close()
                right.close()

    def test_cancellation_keeps_caller_channel_open_and_reaps_native_child(self):
        with tempfile.TemporaryDirectory() as root:
            left, right = socket.socketpair()
            native = "import os,sys,time;os.write(int(sys.argv[1]),str(os.getpid()).encode());time.sleep(60)"
            launcher = "from agent_skills.product import _run_command;from pathlib import Path;import sys;sys.exit(_run_command([sys.executable,'-c',sys.argv[2],sys.argv[1]],Path.cwd(),pass_fds=(int(sys.argv[1]),)))"
            child = subprocess.Popen([sys.executable, "-c", launcher, str(right.fileno()), native], cwd=root, pass_fds=(right.fileno(),))
            try:
                left.settimeout(5)
                native_pid = int(left.recv(1024))
                child.send_signal(signal.SIGTERM)
                self.assertEqual(child.wait(timeout=10), 143)
                with self.assertRaises(ProcessLookupError):
                    os.kill(native_pid, 0)
                right.send(b"still owned")
                self.assertEqual(left.recv(1024), b"still owned")
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
                left.close()
                right.close()
