"""Ordinary product commands over the existing repository lifecycle."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

from .lifecycle import Lifecycle
from .locking import RepositoryLock


class CancellationIncompleteError(subprocess.SubprocessError):
    """Cancellation ended without evidence that the launched tree drained."""

    def __init__(
        self,
        *,
        signal_number: int,
        platform: str,
        cleanup: str,
        target_pid: int,
        target_state: str,
        liveness: str,
        surviving_pids: tuple[int, ...],
    ) -> None:
        self.signal_number = signal_number
        self.platform = platform
        self.cleanup = cleanup
        self.target_pid = target_pid
        self.target_state = target_state
        self.liveness = liveness
        self.surviving_pids = surviving_pids
        try:
            signal_name = signal.Signals(signal_number).name
        except ValueError:
            signal_name = str(signal_number)
        survivors = ",".join(str(pid) for pid in surviving_pids) if surviving_pids else "unknown"
        super().__init__(
            "product.cancellation_incomplete: "
            f"signal={signal_name}; platform={platform};cleanup={cleanup};"
            f"targetPid={target_pid};targetState={target_state};"
            f"liveness={liveness};survivingPids={survivors}"
        )


def _observed_child_liveness(child: subprocess.Popen) -> tuple[str, str, tuple[int, ...]]:
    """Return only liveness directly observed through the child handle."""
    try:
        status = child.poll()
    except (OSError, subprocess.SubprocessError):
        return "unknown", "unknown", ()
    if status is None:
        return "running", "confirmed", (child.pid,)
    return "exited", "unknown", ()


def _incomplete_cancellation(
    child: subprocess.Popen,
    *,
    interrupted: int,
    platform: str,
    cleanup: str,
    survivors: tuple[int, ...] = (),
) -> CancellationIncompleteError:
    target_state, liveness, surviving_pids = _observed_child_liveness(child)
    if survivors:
        # Observed directly in the process table, by pid and start time.
        liveness = "confirmed"
        surviving_pids = tuple(sorted(set(surviving_pids) | set(survivors)))
    return CancellationIncompleteError(
        signal_number=interrupted,
        platform=platform,
        cleanup=cleanup,
        target_pid=child.pid,
        target_state=target_state,
        liveness=liveness,
        surviving_pids=surviving_pids,
    )


class _Process(NamedTuple):
    ppid: int
    pgid: int
    uid: int
    # Opaque, compared only for equality: the same pid with a different start
    # is a different process.
    started: object
    zombie: bool


def _darwin_process_table() -> dict[int, _Process]:
    """Every process the caller may inspect, from libproc, in a few milliseconds.

    macOS hides other users' processes from this call, so they never appear.
    """
    import ctypes
    import struct

    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    libproc.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
    libproc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    count = libproc.proc_listallpids(None, 0)
    if count <= 0:
        raise OSError(ctypes.get_errno(), "proc_listallpids failed")
    pids = (ctypes.c_int * (count + 64))()
    count = libproc.proc_listallpids(pids, ctypes.sizeof(pids))
    if count <= 0:
        raise OSError(ctypes.get_errno(), "proc_listallpids failed")
    # struct proc_bsdinfo (PROC_PIDTBSDINFO): pbi_status at 4, pbi_ppid and
    # pbi_uid at 16, pbi_pgid at 100, pbi_start_tvsec and _tvusec at 120.
    size = 136
    info = ctypes.create_string_buffer(size)
    table = {}
    for pid in pids[:count]:
        if pid <= 0 or libproc.proc_pidinfo(pid, 3, 0, info, size) != size:
            continue
        raw = info.raw
        (status,) = struct.unpack_from("I", raw, 4)
        ppid, uid = struct.unpack_from("II", raw, 16)
        (pgid,) = struct.unpack_from("I", raw, 100)
        started = struct.unpack_from("QQ", raw, 120)
        table[pid] = _Process(ppid, pgid, uid, started, status == 5)  # SZOMB
    return table


def _proc_process_table(proc: Path = Path("/proc")) -> dict[int, _Process]:
    """Every process in a Linux-style /proc."""
    table = {}
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            uid = entry.stat().st_uid
        except OSError:
            continue  # It exited while we looked.
        # The command name is parenthesised and may itself hold spaces.
        fields = stat[stat.rindex(")") + 2:].split()
        table[int(entry.name)] = _Process(int(fields[1]), int(fields[2]), uid, fields[19], fields[0] == "Z")
    return table


def _ps_process_table() -> dict[int, _Process]:
    output = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,pgid=,uid=,stat=,lstart="],
        capture_output=True, text=True, timeout=5, check=True,
    ).stdout
    table = {}
    for line in output.splitlines():
        fields = line.split(None, 5)
        if len(fields) == 6:
            pid, ppid, pgid, uid = (int(field) for field in fields[:4])
            table[pid] = _Process(ppid, pgid, uid, fields[5].strip(), fields[4].startswith("Z"))
    return table


def _process_table() -> dict[int, _Process] | None:
    """A snapshot of the process table, or None when none can be read."""
    try:
        if sys.platform == "darwin":
            return _darwin_process_table()
        if Path("/proc/self/stat").is_file():
            return _proc_process_table()
        return _ps_process_table()
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


class _DescendantTracker:
    """Remember every process the launched tree has had, by pid and start time.

    Signalling the child's process group reaches only the members still in it.
    A descendant that calls setsid() or setpgid() leaves the group, and once its
    parent exits it is no longer anyone's descendant. The launcher therefore
    keeps watching the tree while it runs, so it can still recognise such a
    process by its pid and start time when cancellation comes.

    A descendant born, escaped and orphaned between two observations is not
    seen; the observation interval bounds that window.
    """

    # The launcher's wait loop ticks every 0.1 s; observe every third tick.
    EVERY = 3

    def __init__(self, child: subprocess.Popen, table=_process_table) -> None:
        self._root = child.pid
        self._table = table
        self._known: dict[int, object] = {}
        self._confirmed = False
        self._ticks = 0

    def observe(self, *, throttle: bool = False) -> dict[int, _Process] | None:
        if throttle:
            self._ticks += 1
            if self._ticks % self.EVERY != 1:
                return None
        table = self._table()
        if table is None:
            return None
        root = table.get(self._root)
        if not self._confirmed:
            # Only a process that is really this launcher's child is tracked,
            # so a stale or fake pid never names someone else's tree.
            if root is None or root.ppid != os.getpid():
                return table
            self._confirmed = True
        live = {pid for pid, started in self._known.items()
                if pid in table and table[pid].started == started}
        seeds = live | {self._root}
        for pid, process in table.items():
            if pid in self._known or process.zombie:
                continue
            if process.pgid == self._root or self._descends(pid, table, seeds):
                self._known[pid] = process.started
        return table

    @staticmethod
    def _descends(pid: int, table: dict[int, _Process], seeds: set[int]) -> bool:
        seen = set()
        while pid in table and pid not in seen:
            seen.add(pid)
            pid = table[pid].ppid
            if pid in seeds:
                return True
        return False

    def escaped(self) -> tuple[int, ...]:
        """Known descendants still running outside the child's process group."""
        table = self.observe()
        if table is None or not self._confirmed:
            return ()
        return tuple(sorted(
            pid for pid, started in self._known.items()
            if pid in table and table[pid].started == started
            and not table[pid].zombie and table[pid].pgid != self._root
        ))

    def group_members(self) -> tuple[int, ...]:
        """Processes still visible in the child's process group."""
        table = self._table()
        if table is None or not self._confirmed:
            return ()
        return tuple(sorted(pid for pid, process in table.items()
                            if process.pgid == self._root and not process.zombie))


def _signal_each(pids: tuple[int, ...], signum: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, signum)
        except (ProcessLookupError, PermissionError):
            pass  # Gone already, or not ours to signal; checked again later.


def _stop_escaped_descendants(
    child: subprocess.Popen,
    tracker: _DescendantTracker,
    interrupted: int,
    signalled: tuple[int, ...] = (),
) -> None:
    """Stop descendants that left the group, or report them by pid.

    Those not already signalled directly (``signalled``) get the cancellation
    signal; one that left the group on receiving it may so get it twice. Each
    still running two seconds later gets SIGKILL, and any running two seconds
    after that is reported as a survivor.
    """
    survivors = tracker.escaped()
    if not survivors:
        return
    _signal_each(tuple(pid for pid in survivors if pid not in signalled), interrupted)
    for escalation in (signal.SIGKILL, None):
        deadline = time.monotonic() + 2
        while survivors and time.monotonic() < deadline:
            time.sleep(0.05)
            survivors = tracker.escaped()
        if not survivors:
            return
        if escalation is not None:
            _signal_each(survivors, escalation)
    raise _incomplete_cancellation(
        child,
        interrupted=interrupted,
        platform="posix",
        cleanup="escaped-descendants-survived",
        survivors=survivors,
    )


def _wait_for_posix_group(child: subprocess.Popen, timeout: float) -> bool:
    """Poll the isolated process group until it drains or the bound expires.

    While the group's last members are exiting, some kernels (macOS among
    them) answer the probe with EPERM rather than ESRCH: the group still
    exists but none of its members can be signalled. That is a group being
    torn down, not a failure, so it is polled like any live group.

    EPERM can also mean a member owned by another user that will never
    drain. The bound covers that: the caller escalates, and a group that is
    still there after escalation is reported as an incomplete cancellation,
    never as a clean one.
    """
    deadline = time.monotonic() + timeout
    while True:
        child.poll()
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def runtime_root(lifecycle: Lifecycle) -> Path:
    status = lifecycle.status().as_dict()
    if status["lifecycle"] != "current" or status["blockers"]:
        raise ValueError("product.lifecycle_unready: reconcile the existing lifecycle first")
    root = lifecycle.root / ".agent-skills" / "generations" / status["active"]["generation"] / "runtime"
    if not (root / "runtime.json").is_file():
        raise ValueError("product.runtime_missing: installed generation has no delivery runtime")
    return root


def reconcile_policy(lifecycle: Lifecycle, *, check: bool, bootstrap: bool = False) -> None:
    runtime = runtime_root(lifecycle)
    policy = lifecycle.root / ".agents" / "policy"
    if not policy.exists() and not bootstrap:
        return
    arguments = ["node", str(runtime / "policy.mjs"), "--product"]
    if bootstrap:
        arguments.append("--bootstrap")
    if check:
        arguments.append("--check")
    result = subprocess.run(arguments, cwd=lifecycle.root, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError("product.policy_unready: " + (result.stderr or result.stdout)[-4000:])


def product_status(lifecycle: Lifecycle) -> dict:
    status = lifecycle.status().as_dict()
    status["productReady"] = False
    try:
        runtime = runtime_root(lifecycle)
        reconcile_policy(lifecycle, check=True)
        status["runtime"] = json.loads((runtime / "runtime.json").read_text())
        status["productReady"] = True
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        status["blockers"] = [*status["blockers"], str(error).split(":", 1)[0]]
        status["reconciliation"] = str(error)
    return status


def _stop_posix_group(child: subprocess.Popen, interrupted: int, tracker: _DescendantTracker) -> None:
    """Stop the child's process group, escalating to SIGKILL, or raise."""
    # Wait for the whole group, including workers whose parent exited first.
    try:
        drained = _wait_for_posix_group(child, 2)
    except (OSError, subprocess.SubprocessError) as error:
        raise _incomplete_cancellation(
            child,
            interrupted=interrupted,
            platform="posix",
            cleanup="pre-escalation-wait-failed",
        ) from error
    if drained:
        return
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError as error:
        # Only members that cannot be signalled remain: exiting ones,
        # which drain within the same bound, or another user's.
        cause: BaseException = error
        try:
            drained = _wait_for_posix_group(child, 2)
        except (OSError, subprocess.SubprocessError) as wait_failure:
            drained, cause = False, wait_failure
        if drained:
            return
        raise _incomplete_cancellation(
            child,
            interrupted=interrupted,
            platform="posix",
            cleanup="group-drain-refused",
            survivors=tracker.group_members(),
        ) from cause
    except OSError as error:
        raise _incomplete_cancellation(
            child,
            interrupted=interrupted,
            platform="posix",
            cleanup="sigkill-failed",
        ) from error
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired as error:
        raise _incomplete_cancellation(
            child,
            interrupted=interrupted,
            platform="posix",
            cleanup="post-escalation-timeout",
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise _incomplete_cancellation(
            child,
            interrupted=interrupted,
            platform="posix",
            cleanup="post-escalation-wait-failed",
        ) from error
    # SIGKILL succeeds when it reaches any member, so the direct child exiting
    # does not show the group is gone: a member owned by another user, which
    # nothing here may signal, can still be running in it.
    try:
        drained = _wait_for_posix_group(child, 2)
    except (OSError, subprocess.SubprocessError) as error:
        raise _incomplete_cancellation(
            child,
            interrupted=interrupted,
            platform="posix",
            cleanup="group-partially-stopped",
            survivors=tracker.group_members(),
        ) from error
    if not drained:
        raise _incomplete_cancellation(
            child,
            interrupted=interrupted,
            platform="posix",
            cleanup="group-partially-stopped",
            survivors=tracker.group_members(),
        )


def _run_command(arguments: list[str], root: Path) -> int:
    """Keep native process-group cancellation inside the installed launcher."""
    interrupted = 0
    child = None
    windows = os.name == "nt"

    def interrupt(signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = interrupted or signum
        if child is not None and not windows:
            try:
                os.killpg(child.pid, signum)
            except (ProcessLookupError, PermissionError):
                # Gone, or only members that are already exiting remain.
                pass

    signals = (signal.SIGINT, signal.SIGTERM)
    if not windows:
        if hasattr(signal, "SIGHUP") and signal.getsignal(signal.SIGHUP) is not signal.SIG_IGN:
            signals += (signal.SIGHUP,)
        if hasattr(signal, "SIGQUIT"):
            signals += (signal.SIGQUIT,)
    previous = {sig: signal.signal(sig, interrupt) for sig in signals}
    try:
        if windows:
            # Isolate console Ctrl+C from descendants so the launcher can stop
            # the whole tree before the direct child exits.
            child = subprocess.Popen(arguments, cwd=root, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            child = subprocess.Popen(arguments, cwd=root, start_new_session=True)
            tracker = _DescendantTracker(child)
        if interrupted:
            interrupt(interrupted, None)
        while not interrupted:
            try:
                code = child.wait(timeout=0.1)
                if not interrupted:
                    return code
            except subprocess.TimeoutExpired:
                pass
            if not windows:
                tracker.observe(throttle=True)
        if windows:
            # Windows has no POSIX group signals. Its native tree termination
            # command stops descendants together, without a new supervisor.
            try:
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                               capture_output=True, timeout=2, check=True)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                raise _incomplete_cancellation(
                    child,
                    interrupted=interrupted,
                    platform="windows",
                    cleanup="taskkill-failed",
                ) from error
            try:
                child.wait(timeout=2)
            except (OSError, subprocess.SubprocessError) as error:
                raise _incomplete_cancellation(
                    child,
                    interrupted=interrupted,
                    platform="windows",
                    cleanup="child-wait-failed",
                ) from error
            return 128 + interrupted
        # Descendants that already left the group get the same signal now, so
        # they stop alongside it rather than after it.
        signalled = tracker.escaped()
        _signal_each(signalled, interrupted)
        try:
            _stop_posix_group(child, interrupted, tracker)
        except CancellationIncompleteError:
            _signal_each(tracker.escaped(), signal.SIGKILL)
            raise
        _stop_escaped_descendants(child, tracker, interrupted, signalled)
        return 128 + interrupted
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run_runtime(lifecycle: Lifecycle, arguments: list[str]) -> int:
    # Share the existing maintenance lock: commands can run together, but a
    # generation cannot change between readiness and completion.
    with RepositoryLock(lifecycle.lock_path, shared=True):
        runtime = runtime_root(lifecycle)
        reconcile_policy(lifecycle, check=True)
        return _run_command([
            "node", "--experimental-strip-types", "--import", (runtime / "bootstrap.mjs").resolve().as_uri(),
            str(runtime / "cli.mjs"), *arguments,
        ], lifecycle.root)
