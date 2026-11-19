"""An ownership transfer waits for a lead acceptance that holds the ownership lock.

`lead-accept` takes the ownership lock and, inside it, asks the forge adapter for the pull request before it journals.
The fixture's forge pauses there: it records its pid and waits for SIGUSR1. While the acceptance is paused, a
`lead-own --move` is started; it must neither write the record nor exit. Released, the acceptance journals first and
the move completes after it. Production code has no pause point: the pause is the fixture's own adapter.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

from tests.test_run_delivery import POSIX_ONLY, Fixture, commit, installed_release, sh
from tests.test_run_delivery_supervisor import hand_off, record, supervised

# Prepended to the fixture's forge adapter. Only a `get` run with FX_FORGE_PAUSE set pauses, and only the first one,
# so every other forge call the fixture makes answers at once. SIGUSR1 is blocked before the pid is written, so a
# release sent as soon as the pid is read is never lost. An adapter nobody releases gives up after two minutes.
PAUSE = r'''
import os as _os, signal as _signal, sys as _sys
_pause = _os.environ.get("FX_FORGE_PAUSE")
if _pause and _sys.argv[1:2] == ["get"] and not _os.path.exists(_pause):
    _signal.pthread_sigmask(_signal.SIG_BLOCK, {_signal.SIGUSR1, _signal.SIGALRM})
    open(_pause + ".tmp", "w").write(str(_os.getpid()))
    _os.replace(_pause + ".tmp", _pause)
    _signal.alarm(120)
    if _signal.sigwait({_signal.SIGUSR1, _signal.SIGALRM}) != _signal.SIGUSR1:
        _sys.exit("the fixture never released the paused forge get")
    _signal.alarm(0)
'''

# How long the paused acceptance holds the move off before the test releases it. A move that took no lock finishes in
# well under this, so the red run (the lock made a no-op) fails here.
HELD_SECONDS = 2.0


def holds_open(pid: int, path: Path) -> bool:
    """Whether process `pid` has `path` open: the move opens the lock file just before it waits on it."""
    target = str(path.resolve())
    fds = Path(f"/proc/{pid}/fd")
    if fds.is_dir():
        return any(os.path.realpath(fd) == target for fd in fds.iterdir())
    done = subprocess.run(["lsof", "-a", "-p", str(pid), "-Fn"], capture_output=True, text=True)
    return any(line[1:] and os.path.realpath(line[1:]) == target for line in done.stdout.splitlines()
               if line.startswith("n"))


def record_bytes(folder: Path) -> dict[str, bytes]:
    """Every file of the record but the lock file, as bytes."""
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*"))
            if p.is_file() and p.name != "lane-ownership.lock"}


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class OwnershipLockRaceTests(unittest.TestCase):
    # It waits on real processes within a bound a busy machine can stretch; it keeps to its own folders and processes.
    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def started(self, fx: Fixture, *argv: str, **env: str) -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-B", str(fx.tool), *argv], cwd=fx.main, env={**fx.env, **env},
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        def stop() -> None:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            if proc.stdout and not proc.stdout.closed:
                proc.stdout.close()
        self.addCleanup(stop)
        return proc

    def test_a_move_waits_for_a_paused_acceptance_and_completes_after_its_journal_entry(self) -> None:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        supervised(fx)
        forge = fx.main / "tools/fake_forge.py"
        forge.write_text(PAUSE + forge.read_text())
        commit(fx.main, "FX-1: a forge that can pause its get")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        hand_off(fx, "FX-2", "lane-1")
        folder = fx.tmp / "record"
        lock = folder / "lane-ownership.lock"
        before = record(fx)
        self.assertTrue(before["state"]["queue"][0]["pending"], before["state"]["queue"])
        self.assertIn("src/one*", before["state"]["laneOwnership"]["lane-1"])

        pid_file = fx.tmp / "forge-get.pid"
        accept = self.started(fx, "lead-accept", "FX-2", FX_FORGE_PAUSE=str(pid_file))
        deadline = time.monotonic() + 120
        while not pid_file.exists():
            if accept.poll() is not None:
                self.fail(f"lead-accept ended before its forge get: {accept.communicate()[0]}")
            self.assertLess(time.monotonic(), deadline, "lead-accept never reached its forge get")
            time.sleep(0.05)
        adapter = int(pid_file.read_text())

        def release() -> None:
            if accept.poll() is not None:  # the adapter, its child, has gone with it: its pid may name another
                return
            try:
                os.kill(adapter, signal.SIGUSR1)
            except ProcessLookupError:
                pass
        self.addCleanup(release)

        paused = record_bytes(folder)
        move = self.started(fx, "lead-own", "--move", "src/one*", "--from", "lane-1", "--lane", "lane-2")
        while not holds_open(move.pid, lock):
            if move.poll() is not None:
                self.fail(f"lead-own --move ended while lead-accept held the lock: {move.communicate()[0]}")
            self.assertLess(time.monotonic(), deadline, "lead-own --move never opened the ownership lock")
            time.sleep(0.05)
        held_until = time.monotonic() + HELD_SECONDS
        while time.monotonic() < held_until:
            if move.poll() is not None:
                self.fail(f"lead-own --move ended while lead-accept held the lock: {move.communicate()[0]}")
            self.assertEqual(record_bytes(folder), paused, "lead-own --move wrote the record while lead-accept held "
                                                           "the lock")
            time.sleep(0.05)

        release()
        accepted = accept.communicate(timeout=300)[0]
        self.assertEqual(accept.returncode, 0, accepted)
        moved = move.communicate(timeout=300)[0]
        self.assertEqual(move.returncode, 0, moved)
        self.assertIn("lane-2 owns src/one*, moved from lane-1", moved)

        doc = record(fx)
        journal = doc["journal"]
        [checked] = [e for e in journal if e["kind"] == "handoff.checked" and e.get("subject") == "FX-2"]
        [accepted_entry] = [e for e in journal if e["kind"] == "handoff.accepted" and e.get("subject") == "FX-2"]
        [granted] = [e for e in journal if e["kind"] == "ownership.granted" and e["data"].get("movedFrom") == "lane-1"]
        self.assertLess(checked["id"], accepted_entry["id"])
        self.assertLess(accepted_entry["id"], granted["id"], "the move journalled before the acceptance")
        self.assertEqual(granted["data"], {"path": "src/one*", "movedFrom": "lane-1"})
        self.assertFalse(doc["state"]["queue"][0].get("pending"), doc["state"]["queue"])
        self.assertNotIn("src/one*", doc["state"]["laneOwnership"]["lane-1"])
        self.assertIn("src/one*", doc["state"]["laneOwnership"]["lane-2"])


if __name__ == "__main__":
    unittest.main()
