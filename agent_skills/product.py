"""Ordinary product commands over the existing repository lifecycle."""
from __future__ import annotations

import json
import os
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid
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


_LAUNCH_MARKER = "AGENT_SKILLS_LAUNCH"


class _Stamp(NamedTuple):
    """A macOS process start: wall-clock seconds and microseconds, and the
    kernel's unique id, which is never reused while the system is up and
    grows with every process created."""
    seconds: int
    micros: int
    unique: int


class _Process(NamedTuple):
    ppid: int
    pgid: int
    uid: int
    # Compared for equality: the same pid with a different start is a
    # different process. A ``_Stamp`` on macOS, so a reused pid differs
    # even within the clock's resolution.
    started: object
    zombie: bool
    # The unique id of the process that forked this one (macOS), which
    # reparenting does not change; None where it cannot be read.
    parent_unique: int | None = None


_LIBPROC: list = []


def _libproc():
    import ctypes

    if not _LIBPROC:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        libproc.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
        libproc.proc_listchildpids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        libproc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        libproc.proc_pidfdinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        _LIBPROC.append(libproc)
    return _LIBPROC[0]


def _darwin_process(pid: int, info=None) -> _Process | None:
    """One process from libproc, or None when it cannot be read.

    PROC_PIDT_BSDINFOWITHUNIQID adds the process's unique id and its
    parent's to PROC_PIDTBSDINFO; where it cannot be read, the start stamp
    alone identifies the process. Both flavors take the same permission
    check, so on a given system one process is read the same way each time;
    a stamp read either way still matches (see ``_same_process``).
    """
    import ctypes

    # struct proc_bsdinfo: pbi_status at 4, pbi_ppid and pbi_uid at 16,
    # pbi_pgid at 100, pbi_start_tvsec and _tvusec at 120; then
    # proc_uniqidentifierinfo: p_uuid, then p_uniqueid at 152 and
    # p_puniqueid at 160.
    info = info if info is not None else ctypes.create_string_buffer(192)
    if pid <= 0:
        return None
    libproc = _libproc()
    unique = parent_unique = None
    if libproc.proc_pidinfo(pid, 18, 0, info, 192) == 192:  # PROC_PIDT_BSDINFOWITHUNIQID
        unique, parent_unique = struct.unpack_from("QQ", info.raw, 152)
    elif libproc.proc_pidinfo(pid, 3, 0, info, 136) != 136:  # PROC_PIDTBSDINFO
        return None
    raw = info.raw
    (status,) = struct.unpack_from("I", raw, 4)
    ppid, uid = struct.unpack_from("II", raw, 16)
    (pgid,) = struct.unpack_from("I", raw, 100)
    seconds, micros = struct.unpack_from("QQ", raw, 120)
    started = (seconds, micros) if unique is None else _Stamp(seconds, micros, unique)
    return _Process(ppid, pgid, uid, started, status == 5, parent_unique)  # SZOMB


def _darwin_children(pid: int) -> list[int]:
    import ctypes

    capacity = 256
    while True:
        pids = (ctypes.c_int * capacity)()
        count = _libproc().proc_listchildpids(pid, pids, ctypes.sizeof(pids))
        if count < capacity:
            return [child for child in pids[:max(count, 0)] if child > 0]
        capacity *= 2


def _darwin_process_table() -> dict[int, _Process]:
    """Every process the caller may inspect, from libproc, in a few milliseconds.

    macOS hides other users' processes from this call, so they never appear.
    """
    import ctypes

    libproc = _libproc()
    capacity = libproc.proc_listallpids(None, 0)
    while True:
        if capacity <= 0:
            raise OSError(ctypes.get_errno(), "proc_listallpids failed")
        pids = (ctypes.c_int * (capacity + 256))()
        count = libproc.proc_listallpids(pids, ctypes.sizeof(pids))
        if count <= 0:
            raise OSError(ctypes.get_errno(), "proc_listallpids failed")
        # A full buffer may have dropped pids; ask again with more room.
        if count < len(pids):
            break
        capacity = count * 2
    info = ctypes.create_string_buffer(192)
    table = {}
    for pid in pids[:count]:
        process = _darwin_process(pid, info)
        if process is not None:
            table[pid] = process
    return table


def _proc_process_table(proc: Path = Path("/proc")) -> dict[int, _Process]:
    """Every process in a Linux-style /proc."""
    table = {}
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            uid = _proc_effective_uid(entry)
        except OSError:
            continue  # It exited while we looked.
        # The command name is parenthesised and may itself hold spaces.
        fields = stat[stat.rindex(")") + 2:].split()
        table[int(entry.name)] = _Process(int(fields[1]), int(fields[2]), uid, int(fields[19]), fields[0] == "Z")
    return table


def _proc_effective_uid(entry: Path) -> int:
    """The effective uid from status; the entry's owner is root for a non-dumpable process."""
    try:
        for line in (entry / "status").read_text().splitlines():
            if line.startswith("Uid:"):
                return int(line.split()[2])
    except (OSError, IndexError, ValueError):
        pass
    return entry.stat().st_uid


_ARGUMENTS_BUFFER: list = []


def _darwin_environment(pid: int) -> list[bytes] | None:
    """A process's arguments and environment, from KERN_PROCARGS2.

    None when it cannot be read: gone, or another user's. The entries after
    the executable path are returned whole, arguments included; the marker
    is a fresh random key, so it is matched wherever it appears.
    """
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                            ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    if not _ARGUMENTS_BUFFER:
        argmax = ctypes.c_int()
        size = ctypes.c_size_t(ctypes.sizeof(argmax))
        if libc.sysctl((ctypes.c_int * 2)(1, 8), 2, ctypes.byref(argmax), ctypes.byref(size), None, 0) != 0:
            return None  # CTL_KERN, KERN_ARGMAX
        _ARGUMENTS_BUFFER.append(ctypes.create_string_buffer(argmax.value))
    buffer = _ARGUMENTS_BUFFER[0]
    size = ctypes.c_size_t(len(buffer))
    if libc.sysctl((ctypes.c_int * 3)(1, 49, pid), 3, buffer, ctypes.byref(size), None, 0) != 0:
        return None  # CTL_KERN, KERN_PROCARGS2
    raw = buffer.raw[4:size.value]  # after argc
    return [entry for entry in raw[raw.index(b"\0"):].split(b"\0") if entry]


def _proc_environment(pid: int, proc: Path = Path("/proc")) -> list[bytes] | None:
    try:
        return (proc / str(pid) / "environ").read_bytes().split(b"\0")
    except OSError:
        return None


def _process_environment(pid: int) -> list[bytes] | None:
    """A process's environment where the platform exposes it, else None."""
    try:
        if sys.platform == "darwin":
            return _darwin_environment(pid)
        if Path("/proc/self/environ").is_file():
            return _proc_environment(pid)
    except (OSError, ValueError, struct.error):
        pass
    return None


def _darwin_pipes(pid: int) -> set[int] | None:
    """The kernel handles of the pipe ends open in pid (not of their peers)."""
    import ctypes

    libproc = _libproc()
    size = libproc.proc_pidinfo(pid, 1, 0, None, 0)  # PROC_PIDLISTFDS
    if size <= 0:
        return None
    listing = ctypes.create_string_buffer(size + 8 * 64)
    size = libproc.proc_pidinfo(pid, 1, 0, listing, len(listing))
    if size <= 0:
        return None
    # struct pipe_fdinfo: a 24-byte proc_fileinfo, then pipe_info, whose
    # 136-byte vinfo_stat is followed by pipe_handle and pipe_peerhandle.
    info = ctypes.create_string_buffer(256)
    handles = set()
    for offset in range(0, size - size % 8, 8):
        fd, kind = struct.unpack_from("iI", listing.raw, offset)
        if kind != 6:  # PROX_FDTYPE_PIPE
            continue
        if libproc.proc_pidfdinfo(pid, fd, 6, info, 256) >= 176:  # PROC_PIDFDPIPEINFO
            handles.add(struct.unpack_from("Q", info.raw, 160)[0])
    return handles


def _process_pipes(pid: int) -> set[int] | None:
    """The pipe ends open in pid, on macOS; None when they cannot be read."""
    try:
        if sys.platform == "darwin":
            return _darwin_pipes(pid)
    except (OSError, ValueError, struct.error):
        pass
    return None


def _lifeline_handle(fd: int) -> int | None:
    """The handle of the read end facing this write end, while anything holds it.

    None once every holder has closed it. A pipe whose other end is closed
    reports a peer of 0, and any process may hold such a pipe, so 0 never
    names anyone. A live read end's handle cannot be reused while this end
    still faces it.
    """
    import ctypes

    try:
        info = ctypes.create_string_buffer(256)
        if _libproc().proc_pidfdinfo(os.getpid(), fd, 6, info, 256) < 176:  # PROC_PIDFDPIPEINFO
            return None
        (peer,) = struct.unpack_from("Q", info.raw, 168)
    except (OSError, ValueError, struct.error):
        return None
    return peer or None


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


class _ForkWatcher(threading.Thread):
    """Learn each new descendant as it is forked, through kqueue NOTE_FORK.

    A double-fork daemon's intermediate parent may exit long before the next
    table read, taking the only ancestry with it. A kqueue on every known
    process reports each fork at once, and the new child's own children are
    listed as soon as it is registered. This is best effort: NOTE_FORK names
    only the parent, so a child is found only while that parent is still
    there to list it, and one that forks and exits before it is registered
    takes the grandchild's ancestry with it. Each child is offered to the
    tracker with the parent that forked it, and the tracker decides whether
    that parent may vouch for it. Each exit is reported too, with when it
    was seen, so the tracker knows how long that process's group was its own.
    """

    def __init__(self, adopt, ended=None) -> None:
        super().__init__(name="launcher-fork-watcher", daemon=True)
        import select

        self._select = select
        self._queue = select.kqueue()
        self._adopt = adopt
        self._ended = ended
        # By pid and start, so a reused pid is registered again.
        self._watched: set[tuple[int, object]] = set()
        # The start each pid's registration was made for.
        self._starts: dict[int, object] = {}
        self._closed = False

    def watch(self, pid: int, started: object) -> None:
        if self._closed or (pid, started) in self._watched:
            return
        self._watched.add((pid, started))
        # Before the registration: its exit may be reported at once.
        self._starts[pid] = started
        notes = self._select.KQ_NOTE_FORK | self._select.KQ_NOTE_EXIT
        try:
            # The pid may have been reused before the registration: keep it
            # only on the process that was learned, read before and after.
            # One gone by the second read keeps it, so its exit is still
            # reported; that relies on no pid being reused between the reads.
            before = _darwin_process(pid)
            if before is None or not _same_process(before.started, started):
                self._forget(pid, started)
                return
            self._queue.control([self._select.kevent(
                pid, self._select.KQ_FILTER_PROC, self._select.KQ_EV_ADD, notes)], 0, 0)
            current = _darwin_process(pid)
            if current is not None and not _same_process(current.started, started):
                self._forget(pid, started)
                self._queue.control([self._select.kevent(
                    pid, self._select.KQ_FILTER_PROC, self._select.KQ_EV_DELETE, notes)], 0, 0)
                return
        except (OSError, ValueError):
            self._forget(pid, started)
            return  # Gone already, not ours to watch, or the watcher closed.
        self._children_of(pid)

    def _forget(self, pid: int, started: object) -> None:
        if self._starts.get(pid) == started:
            del self._starts[pid]

    def _children_of(self, pid: int) -> None:
        for child in _darwin_children(pid):
            self._adopt(child, pid)

    def run(self) -> None:
        try:
            while not self._closed:
                try:
                    events = self._queue.control(None, 64, 0.1)
                except (OSError, ValueError):
                    return
                # One stamp per batch, taken as it arrives: the closest
                # bound on when each exit in it happened.
                seen = _wall_stamp()
                for event in events:
                    if event.fflags & self._select.KQ_NOTE_EXIT and self._ended is not None:
                        self._ended(event.ident, self._starts.get(event.ident), seen)
                    if event.fflags & self._select.KQ_NOTE_FORK:
                        try:
                            self._children_of(event.ident)
                        except (OSError, ValueError, struct.error):
                            pass  # That parent is gone; the table still sees its tree.
        finally:
            # Closed here, by the only thread that waits on it, so its
            # descriptor is never reused under a wait in progress.
            self._closed = True
            self._queue.close()

    def close(self) -> None:
        self._closed = True
        if self.is_alive() and threading.current_thread() is not self:
            self.join(1)


def _started_before(started: object, reference: object) -> bool:
    """Whether a process certainly started before the reference.

    macOS start stamps are wall-clock (seconds, microseconds), so an hour of
    slack keeps a clock stepped back during a run from hiding descendants.
    Linux stamps count ticks since boot. Stamps that do not order (ps text)
    are never before. Two macOS stamps with unique ids order exactly by
    them, whatever the clock did.
    """
    if isinstance(started, _Stamp) and isinstance(reference, _Stamp):
        return started.unique < reference.unique
    if isinstance(started, tuple) and isinstance(reference, tuple):
        return started[0] < reference[0] - 3600
    if isinstance(started, int) and isinstance(reference, int):
        return started < reference
    return False


def _same_process(started: object, known: object) -> bool:
    """Whether two start stamps name the same process.

    By unique id where both have one. A stamp read with its unique id and one
    read without (a read that fell back) are compared by the clock alone, so
    a process is never taken for a different one, and a refusal never
    dropped, because one read fell back.
    """
    if isinstance(started, _Stamp) and isinstance(known, _Stamp):
        return started.unique == known.unique
    if isinstance(started, tuple) and isinstance(known, tuple):
        return tuple(started[:2]) == tuple(known[:2])
    return started is not None and started == known


def _wall_stamp() -> tuple[int, int]:
    """Now, as a macOS start stamp: wall-clock (seconds, microseconds)."""
    seconds, micros = divmod(time.time_ns() // 1000, 1_000_000)
    return seconds, micros


def _precedes(started: object, reference: object) -> bool:
    """Whether a start stamp is exactly earlier than the reference, with no slack.

    By unique id where both have one; otherwise by the clock.
    """
    if isinstance(started, _Stamp) and isinstance(reference, _Stamp):
        return started.unique < reference.unique
    if isinstance(started, tuple) and isinstance(reference, tuple):
        return tuple(started[:2]) < tuple(reference[:2])
    if isinstance(started, int) and isinstance(reference, int):
        return started < reference
    return False


class _DescendantTracker:
    """Remember every process the launched tree has had, by pid and start time.

    Signalling the child's process group reaches only the members still in it.
    A descendant that calls setsid() or setpgid() leaves the group, and once its
    parent exits it is no longer anyone's descendant. The launcher therefore
    keeps watching the tree while it runs, so it can still recognise such a
    process by its pid and start time when cancellation comes.

    It observes every third wait tick (0.3 s) while the command runs, and
    every drain tick (0.05 s) once cancellation starts. Ancestry alone misses
    a descendant born, escaped and orphaned between two observations, such
    as a classic double-fork daemon, so cancellation also sweeps for the
    launch's environment marker (see ``sweep``). On macOS, which discloses
    no environment for Apple system binaries (/bin/sh, /bin/sleep and the
    like), a fork watcher learns each child as it is forked, best effort,
    and the sweep also recognises a holder of the launch's lifeline pipe,
    and a job left in the group of a descendant that has exited.
    A descendant that escapes and is orphaned between two observations,
    with no readable marker and no lifeline (it closed its descriptors),
    and whose fork the watcher did not see, is still missed. On Linux
    neither the fork watcher, the lifeline nor the group of an exited
    descendant applies.

    Every process is learned for a reason: ``group`` (in the child's process
    group), ``descendant`` (by ancestry in the table), ``forked`` (seen being
    forked by a known process), ``marker`` or ``lifeline``. Whatever the
    reason, cleanup never selects pid 0 or 1, the launcher or any of its
    ancestors, or, to signal on its own, another user's process; nor a
    ``marker`` or ``lifeline`` match that started before the child, or,
    while the child is unconfirmed, before the launcher itself, nor any
    other that started more than an hour before the child (see
    ``_refusal``). On macOS, where each process's unique id is read, "before"
    is exact by that id, with no slack, and a descendant is also known by its
    parent's unique id after it is reparented. Pid and start are matched at each table read. A refused
    candidate is remembered and never signalled, and while one is still
    running cancellation reports itself incomplete.
    """

    # The launcher's wait loop ticks every 0.1 s; observe every third tick.
    EVERY = 3

    def __init__(self, child: subprocess.Popen, table=_process_table, marker: bytes | None = None,
                 environment=_process_environment, lifeline=None, pipes=_process_pipes,
                 watcher=None) -> None:
        self._root = child.pid
        self._table = table
        self._marker = marker
        self._environment = environment
        # Called for the lifeline's current read-end handle, or None.
        self._lifeline = lifeline
        self._pipes = pipes
        # Called with the tracker's adopt method once the child is confirmed.
        self._watcher_factory = watcher
        self._watcher = None
        self._lock = threading.RLock()
        self._known: dict[int, object] = {}
        # The unique id of every process ever learned, the child's included,
        # so one forked by a descendant since gone is still recognised.
        self._uniques: set[int] = set()
        self._why: dict[int, str] = {}
        # Known processes the fork watcher saw exit: pid -> (start, seen at).
        self._ends: dict[int, tuple[object, object]] = {}
        self._refused: dict[tuple[int, object], str] = {}
        self._confirmed = False
        self._root_started: object = None
        self._root_present = False
        # The launcher's own start: no descendant started before it.
        self._floor: object = None
        self._last_table: dict[int, _Process] = {}
        # How the last table told processes apart (see ``preview``).
        self._identity = "unread"
        self._unmarked: set[tuple[int, object]] = set()
        self._ticks = 0

    def observe(self, *, throttle: bool = False) -> dict[int, _Process] | None:
        with self._lock:
            return self._observe(throttle)

    def _observe(self, throttle: bool) -> dict[int, _Process] | None:
        if throttle:
            self._ticks += 1
            if self._ticks % self.EVERY != 1:
                return None
        table = self._table()
        if table is None:
            return None
        self._last_table = table
        stamped = {isinstance(process.started, _Stamp) for process in table.values()}
        self._identity = {frozenset({True}): "unique-id", frozenset({False}): "start-time"}.get(
            frozenset(stamped), "mixed" if stamped else self._identity)
        if self._floor is None and os.getpid() in table:
            self._floor = table[os.getpid()].started
        root = table.get(self._root)
        if not self._confirmed:
            # Only a process that is really this launcher's child is tracked,
            # so a stale or fake pid never names someone else's tree.
            if root is None or root.ppid != os.getpid():
                return table
            self._confirmed = True
            self._root_started = root.started
            if isinstance(root.started, _Stamp):
                self._uniques.add(root.started.unique)
            if self._watcher_factory is not None:
                try:
                    watcher = self._watcher_factory(self.adopt, self.ended)
                    watcher.start()
                except (OSError, RuntimeError):
                    watcher = None  # Best effort; the table still sees the tree.
                self._watcher = watcher
                if watcher is not None:
                    watcher.watch(self._root, root.started)
        live = {pid for pid, started in self._known.items()
                if pid in table and _same_process(table[pid].started, started)}
        seeds = set(live)
        # Once the child is reaped its pid may be reused, so the root and its
        # process group count only while the same child is still there.
        root_present = root is not None and _same_process(root.started, self._root_started)
        self._root_present = root_present
        if root_present:
            seeds.add(self._root)
        for pid, process in table.items():
            if _same_process(process.started, self._known.get(pid)) or process.zombie \
                    or (pid, process.started) in self._refused:
                continue
            if root_present and process.pgid == self._root:
                self._learn(pid, process, "group", table)
            elif process.parent_unique in self._uniques or self._descends(pid, table, seeds) \
                    or self._in_known_group(process, table):
                self._learn(pid, process, "descendant", table)
        return table

    def _in_known_group(self, process: _Process, table: dict[int, _Process]) -> bool:
        """Whether a process is in the group a known descendant created.

        Only the process holding a pid can create the group of that id, and
        no pid is reused while a group of its id remains. So a process in the
        group named by a known descendant's pid, started after that
        descendant and while it was still there (it is now, or the fork
        watcher saw it exit later), joined that descendant's group, in a
        session the run created. This finds a job left in a short-lived
        shell's group after the shell exited, when the process that forked
        the job was never seen (a job whose parent was seen is found by its
        parent's unique id instead). The exit is bounded by when the watcher
        received it, so this relies on no pid being reused within that
        instant. It applies only where the fork watcher runs (macOS).
        """
        if self._watcher_factory is None:
            return False
        leader = process.pgid
        started = self._known.get(leader)
        if started is None or leader == self._root or _precedes(process.started, started):
            return False
        current = table.get(leader)
        if current is not None and _same_process(current.started, started):
            return True
        end = self._ends.get(leader)
        return end is not None and _same_process(end[0], started) and _precedes(process.started, end[1])

    def ended(self, pid: int, started: object, at: object) -> None:
        """Remember when the fork watcher saw a known process exit.

        ``started`` is the start its registration was made for; an exit of
        any other process holding that pid is not this one's.
        """
        with self._lock:
            known = self._root_started if pid == self._root else self._known.get(pid)
            if known is not None and _same_process(started, known):
                self._ends[pid] = (started, at)

    def _callers(self, table: dict[int, _Process]) -> set[int]:
        """The launcher and every ancestor of it."""
        callers = {os.getpid(), os.getppid()}
        for pid in tuple(callers):
            while pid in table and table[pid].ppid not in callers:
                pid = table[pid].ppid
                callers.add(pid)
        return callers

    def _in_group(self, process: _Process) -> bool:
        return self._root_present and process.pgid == self._root

    def _refusal(self, pid: int, process: _Process, table: dict[int, _Process], why: str) -> str | None:
        """Why cleanup may not select this process, or None when it may.

        A member of the child's process group owned by another user (one
        running a setuid program, say) is still learned, so a group that
        cannot drain names it, but it is never signalled on its own. A
        ``marker`` or ``lifeline`` match is only as good as the match, so
        it must also have started no earlier than the child, by its exact
        stamp, or, before the child is confirmed, than the launcher.
        Ancestry and the fork watcher show parentage directly, so for them
        an hour of slack covers a clock stepped back during the run, and
        still refuses a long-running process of the caller's.
        """
        if pid <= 1:
            return "init"
        if pid in self._callers(table):
            return "caller"
        if not self._in_group(process) and hasattr(os, "geteuid") and process.uid != os.geteuid():
            return "other-user"
        reference = self._root_started if self._root_started is not None else self._floor
        if reference is not None:
            # A match by marker or lifeline by its exact stamp; any other,
            # whose parentage is shown, with the hour of slack a clock
            # stepped back during a run needs.
            precedes = _precedes if why in ("marker", "lifeline") else _started_before
            if precedes(process.started, reference):
                return "before-run"
        return None

    def _learn(self, pid: int, process: _Process, why: str, table: dict[int, _Process] | None) -> bool:
        refusal = self._refusal(pid, process, table or self._last_table, why)
        if refusal is not None:
            self._refused[(pid, process.started)] = refusal
            return False
        self._known[pid] = process.started
        self._why[pid] = why
        if isinstance(process.started, _Stamp):
            self._uniques.add(process.started.unique)
        if self._watcher is not None:
            self._watcher.watch(pid, process.started)
        return True

    def adopt(self, pid: int, parent: int) -> bool:
        """Remember a child the fork watcher saw parent fork; False if not adopted.

        The parent vouches for it only while it is the same process the
        tracker knows: the child is read first, so a parent still there
        afterwards, with the start it was known by, was its parent.
        """
        process = _darwin_process(pid)
        if process is None or process.zombie or process.ppid != parent:
            return False
        with self._lock:
            if not self._confirmed:
                return False
            vouched = self._root_started if parent == self._root else self._known.get(parent)
            current = _darwin_process(parent)
            if vouched is None or current is None or not _same_process(current.started, vouched):
                return False
            if isinstance(vouched, _Stamp) and process.parent_unique not in (None, vouched.unique):
                return False  # Forked by another process that held that pid.
            if _same_process(process.started, self._known.get(pid)):
                return True
            if (pid, process.started) in self._refused:
                return False
            return self._learn(pid, process, "forked", None)

    def close(self) -> None:
        with self._lock:
            self._lifeline = None
            watcher = self._watcher
        # Outside the lock: the watcher may be waiting on it in adopt.
        if watcher is not None:
            watcher.close()

    @staticmethod
    def _descends(pid: int, table: dict[int, _Process], seeds: set[int]) -> bool:
        seen = set()
        while pid in table and pid not in seen:
            seen.add(pid)
            pid = table[pid].ppid
            if pid in seeds:
                return True
        return False

    def sweep(self, table: dict[int, _Process]) -> None:
        """Adopt every process that carries one of this launch's markers.

        The marker survives what ancestry does not: a double-fork daemon,
        re-parented to init in a session of its own, still carries it, unless
        it cleared its environment. Every descendant started after the child,
        so only processes started since then are read. A process whose
        environment cannot be read at that moment (another user's, or one
        caught mid-exec) is not adopted, and is read again on the next sweep.

        macOS returns no environment at all for Apple system binaries, so
        there the child also holds the read end of a lifeline pipe, which
        every descendant inherits unless it closes its descriptors. A process
        holds it only when one of its own pipe ends is that read end, read
        while the launcher's write end still faces it both before and after;
        once it closes, the lifeline names nobody. It is used only once the
        child is confirmed.
        """
        if self._marker is None and self._lifeline is None:
            return
        lifeline = (self._lifeline() or None) if self._lifeline is not None and self._confirmed else None
        for pid, process in table.items():
            if _same_process(process.started, self._known.get(pid)) or process.zombie \
                    or pid in (os.getpid(), self._root):
                continue
            if (pid, process.started) in self._unmarked or (pid, process.started) in self._refused:
                continue
            reference = self._root_started if self._root_started is not None else self._floor
            if _started_before(process.started, reference):
                continue
            environment = self._environment(pid) if self._marker is not None else []
            pipes = self._pipes(pid) if lifeline is not None else set()
            marked = bool(environment) and self._marker in environment
            held = bool(pipes) and lifeline in pipes and self._lifeline is not None \
                and self._lifeline() == lifeline
            if marked or held:
                self._learn(pid, process, "marker" if marked else "lifeline", table)
            elif environment is not None and pipes is not None:
                # Remembered by pid and start, so each process is read once.
                self._unmarked.add((pid, process.started))
            # Otherwise not readable now; tried again on the next sweep.

    def escaped(self) -> tuple[int, ...] | None:
        """Known descendants still running outside the child's process group.

        None when the table cannot be read although descendants are known:
        they may still be running, so that is never reported as none. The
        marker sweep runs even before the child is confirmed, since only this
        launch's descendants carry its key. Each is checked against the
        selection rule again, now, before it is returned.
        """
        with self._lock:
            table = self._observe(False)
            if table is None:
                # Any descendant known besides the child may have left the group.
                return None if set(self._known) - {self._root} else ()
            self.sweep(table)
            selected = []
            for pid, started in self._known.items():
                process = table.get(pid)
                if process is None or not _same_process(process.started, started) or process.zombie \
                        or process.pgid == self._root:
                    continue
                refusal = self._refusal(pid, process, table, self._why[pid])
                if refusal is None:
                    selected.append(pid)
                else:
                    self._refused[(pid, started)] = refusal
            return tuple(sorted(selected))

    def _live_refusals(self, table: dict[int, _Process] | None) -> list[tuple[int, str]]:
        """Refused candidates still running outside the child's group, by pid.

        One that exited, or whose pid now names a later process, no longer
        stands between cancellation and a clean result; behind a table that
        cannot be read, every one still does.
        """
        return sorted({
            (pid, why) for (pid, started), why in self._refused.items()
            if table is None or (pid in table and _same_process(table[pid].started, started)
                                 and not table[pid].zombie and table[pid].pgid != self._root)
        })

    def refused(self) -> tuple[int, ...]:
        """Running pids the selection rule kept cleanup from stopping."""
        with self._lock:
            return tuple(sorted({pid for pid, _ in self._live_refusals(self._table())}))

    def preview(self) -> dict:
        """What cleanup would stop now, and why, without signalling anything.

        ``group`` is the process group signalled as a whole (the child's own,
        while it is there), ``escaped`` each process signalled on its own
        with the reason it was learned, and ``refused`` each candidate the
        selection rule refused, with the rule it broke.
        """
        with self._lock:
            escaped = self.escaped()
            return {
                # How the last table told processes apart: the kernel's unique
                # id, the start stamp where it cannot be read, or a mix.
                "identity": self._identity,
                "group": self._root if self._root_present else None,
                "escaped": None if escaped is None else [[pid, self._why[pid]] for pid in escaped],
                # Behind an unreadable table every refusal still counts.
                "refused": [[pid, why] for pid, why in
                            self._live_refusals(None if escaped is None else self._last_table)],
            }

    def group_members(self) -> tuple[int, ...]:
        """Known descendants still visible in the child's process group."""
        with self._lock:
            table = self._table()
            if table is None or not self._confirmed:
                return ()
            return tuple(sorted(
                pid for pid, started in self._known.items()
                if pid in table and _same_process(table[pid].started, started)
                and not table[pid].zombie and table[pid].pgid == self._root
            ))


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
    """Stop descendants that left the group, then fail closed on any refusal.

    A candidate the selection rule refused was never signalled, so the tree
    is not shown to be stopped: the cancellation is reported incomplete.
    """
    _stop_selected_escapees(child, tracker, interrupted, signalled)
    if tracker.refused():
        raise _incomplete_cancellation(
            child,
            interrupted=interrupted,
            platform="posix",
            cleanup="escaped-descendants-refused",
        )


def _stop_selected_escapees(
    child: subprocess.Popen,
    tracker: _DescendantTracker,
    interrupted: int,
    signalled: tuple[int, ...],
) -> None:
    """Stop descendants that left the group, or report them by pid.

    Those not already signalled directly (``signalled``) get the cancellation
    signal; one that left the group on receiving it may so get it twice. Each
    still running two seconds later gets SIGKILL, and any running two seconds
    after that is reported as a survivor. A process table that cannot be read
    is retried within the same bounds; only one still unreadable at the end
    of a bound is reported as unobservable.
    """
    told = set(signalled)

    def tell(pids: tuple[int, ...] | None) -> None:
        fresh = tuple(pid for pid in pids or () if pid not in told)
        told.update(fresh)
        _signal_each(fresh, interrupted)

    survivors = tracker.escaped()
    if survivors == ():
        return
    tell(survivors)
    for escalation in (signal.SIGKILL, None):
        deadline = time.monotonic() + 2
        while survivors != () and time.monotonic() < deadline:
            time.sleep(0.05)
            survivors = tracker.escaped()
            if escalation is not None:
                tell(survivors)
        if survivors == ():
            return
        if survivors is None:
            # Escapees may still be running; nothing shows whether they are.
            raise _incomplete_cancellation(
                child,
                interrupted=interrupted,
                platform="posix",
                cleanup="escaped-descendants-unobservable",
            )
        if escalation is not None:
            _signal_each(survivors, escalation)
    raise _incomplete_cancellation(
        child,
        interrupted=interrupted,
        platform="posix",
        cleanup="escaped-descendants-survived",
        survivors=survivors,
    )


def _wait_for_posix_group(child: subprocess.Popen, timeout: float, observe=None) -> bool:
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
        if observe is not None:
            # A member may fork on the cancellation signal and its fork leave
            # the group; watching every tick catches one whose parent lives
            # for at least one tick.
            observe()
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
        drained = _wait_for_posix_group(child, 2, tracker.observe)
    except (OSError, subprocess.SubprocessError) as error:
        raise _incomplete_cancellation(
            child,
            interrupted=interrupted,
            platform="posix",
            cleanup="pre-escalation-wait-failed",
        ) from error
    if drained:
        return
    # Known escapees are escalated with the group. An escaped parent that
    # ignores signals never reaps a killed member, and a group held only by
    # that zombie would never drain.
    _signal_each(tracker.escaped() or (), signal.SIGKILL)
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError as error:
        # Only members that cannot be signalled remain: exiting ones,
        # which drain within the same bound, or another user's.
        cause: BaseException = error
        try:
            drained = _wait_for_posix_group(child, 2, tracker.observe)
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
        drained = _wait_for_posix_group(child, 2, tracker.observe)
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


def _run_command(arguments: list[str], root: Path, *, preview=None) -> int:
    """Keep native process-group cancellation inside the installed launcher.

    With ``preview``, a POSIX cancellation signals nothing: ``preview`` is
    called with what cleanup would stop (``_DescendantTracker.preview``) and
    the command is left running for the caller to stop.
    """
    interrupted = 0
    child = None
    tracker = None
    lifeline_end = None
    windows = os.name == "nt"

    def interrupt(signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = interrupted or signum
        if child is not None and not windows and preview is None:
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
            # Every descendant inherits this marker unless it clears its
            # environment, so cancellation can find even a double-fork daemon.
            # Each launch has a key of its own, so a launch inside the command
            # adds its marker beside this one rather than replacing it.
            key = f"{_LAUNCH_MARKER}_{uuid.uuid4().hex}"
            # On macOS the child also holds the read end of a pipe the
            # launcher keeps open. Descendants inherit it unless they close
            # their descriptors, and it shows where the environment is hidden.
            darwin = sys.platform == "darwin"
            passed: tuple[int, ...] = ()
            if darwin:
                read_end, lifeline_end = os.pipe()
                passed = (read_end,)
            try:
                child = subprocess.Popen(arguments, cwd=root, start_new_session=True,
                                         env=dict(os.environ, **{key: "1"}), pass_fds=passed)
            finally:
                for fd in passed:
                    os.close(fd)
            write_end = lifeline_end
            tracker = _DescendantTracker(
                child, marker=f"{key}=1".encode(),
                lifeline=(lambda: _lifeline_handle(write_end)) if darwin else None,
                watcher=_ForkWatcher if darwin else None,
            )
            # Confirm the child at once, so a cancellation that comes before
            # the first wait tick still knows when the tree began.
            tracker.observe()
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
        if preview is not None:
            preview(tracker.preview())
            return 128 + interrupted
        # Descendants that already left the group get the same signal now, so
        # they stop alongside it rather than after it.
        signalled = tracker.escaped() or ()
        _signal_each(signalled, interrupted)
        try:
            _stop_posix_group(child, interrupted, tracker)
        except CancellationIncompleteError:
            _signal_each(tracker.escaped() or (), signal.SIGKILL)
            raise
        _stop_escaped_descendants(child, tracker, interrupted, signalled)
        return 128 + interrupted
    finally:
        if tracker is not None:
            tracker.close()
        if lifeline_end is not None:
            os.close(lifeline_end)
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
