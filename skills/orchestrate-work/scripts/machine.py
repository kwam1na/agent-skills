#!/usr/bin/env python3
"""Keep one machine's ledger of claims, measured costs and a capacity.

Run with ``python3 -B``. Every repository on a machine shares one machine
directory. It holds ``ledger.lock``, ``claims/``, ``capacity.json``,
``costs.json`` and ``history.jsonl``.

A claim is one file in ``claims/``, locked by the process that made it for as
long as that process lives. Liveness comes from that lock alone: a reader that
can take a claim file's lock knows its holder is gone and removes the file,
whatever its ``pid`` says, and a reader that can't take it counts the claim as
live. The pid is shown, never signalled or probed. Every change to the ledger
happens under ``ledger.lock``.

The ledger gives agents facts to act on. Commands claim and wait by default,
an agent steps off with ``--now --why`` or ``admit --why``, and the reason is
recorded. Nothing here reads the machine's load to decide an admission, and a
ledger that can't be used never stops the run: the command says so and runs
unclaimed.

Subcommands: ``status``, ``run``, ``admit`` and ``capacity``.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import contextlib
import datetime
import hashlib
import json
import math
import os
import re
import shlex
import signal
import statistics
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))

import delivery_state  # noqa: E402
from delivery_state import StateError, dumps, file_lock, machine_reading  # noqa: E402

if os.name == "nt":
    import msvcrt
else:
    import fcntl


DIRECTORY_VARIABLE = "AGENT_SKILLS_MACHINE_DIR"
CLAIM_VARIABLE = "AGENT_SKILLS_MACHINE_CLAIM"
KINDS = ("check", "fullHealth", "suite", "run")
LEDGER_LOCK = "ledger.lock"
CLAIMS = "claims"
CAPACITY_FILE = "capacity.json"
COSTS_FILE = "costs.json"
HISTORY_FILE = "history.jsonl"
HISTORY_LIMIT = 1000
COST_RUNS = 5
# A waiter checks again this often, and sooner whenever a claim is released or its own claim changes.
POLL_SECONDS = 2.0
WAKE_SECONDS = 0.1
# One try at the ledger's lock. A try that times out is ordinary contention, retried as part of the wait.
LEDGER_TRY_SECONDS = 5.0
EX_TEMPFAIL = 75
# How long a stopped run has to end after SIGTERM before it is killed.
STOP_GRACE_SECONDS = 5.0
OVERRIDES = ("now", "admit")

_SLOT_LOCK = threading.Lock()
_SLOT_DEPTH = 0


class LedgerUnavailable(Exception):
    """The machine directory, or its ledger lock, can't be created or opened."""


class GaveUp(Exception):
    """A capped wait ended unadmitted."""

    def __init__(self, message: str, holders: list[dict]) -> None:
        super().__init__(message)
        self.holders = holders


def machine_directory(environ: dict | None = None) -> Path:
    """Where this machine's ledger lives: the first of these that applies."""
    environ = os.environ if environ is None else environ
    if environ.get(DIRECTORY_VARIABLE):
        return Path(environ[DIRECTORY_VARIABLE])
    if environ.get("XDG_STATE_HOME"):
        return Path(environ["XDG_STATE_HOME"]) / "agent-skills" / "machine"
    if os.name == "nt" and environ.get("LOCALAPPDATA"):
        return Path(environ["LOCALAPPDATA"]) / "agent-skills" / "machine"
    home = Path(environ["HOME"]) if environ.get("HOME") else Path.home()
    return home / ".local" / "state" / "agent-skills" / "machine"


def repository_identity(cwd: Path | str | None = None) -> str | None:
    """A repository's resolved git common directory, the identity its merge slots use; None outside git."""
    try:
        found = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=cwd, capture_output=True, text=True,
                               check=False)
    except OSError:
        return None
    if found.returncode != 0 or not found.stdout.strip():
        return None
    return str((Path(cwd or ".") / found.stdout.strip()).resolve())


@contextlib.contextmanager
def holding_slot() -> Iterator[None]:
    """Marks, for this process only, that it holds a merge slot: a claim made or grown inside is admitted at once."""
    global _SLOT_DEPTH
    with _SLOT_LOCK:
        _SLOT_DEPTH += 1
    try:
        yield
    finally:
        with _SLOT_LOCK:
            _SLOT_DEPTH -= 1


def slot_held() -> bool:
    return _SLOT_DEPTH > 0


def _iso(moment: float) -> str:
    return datetime.datetime.fromtimestamp(moment, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _held(path: Path) -> bool | None:
    """True when another open file description holds ``path``'s lock, False when nobody does, None when it is gone."""
    try:
        descriptor = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return None
    except OSError:
        return True
    try:
        try:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            return True
        return False
    finally:
        os.close(descriptor)


def _write_in_place(path: Path, record: dict) -> None:
    # A claim file is rewritten in place, never replaced, so the lock its owner holds stays on the file readers see.
    with open(path, "r+b") as stream:
        stream.write(dumps(record).encode("utf-8"))
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())


def _malformed(path: Path, text: str | None) -> dict:
    """A claim file that can't be read as a claim, counted with the slots it last said, or 1."""
    slots = 1
    found = re.search(r'"slots"\s*:\s*([0-9]+)', text or "")
    if found and int(found.group(1)) >= 1:
        slots = int(found.group(1))
    return {"id": path.stem, "state": "running", "slots": slots, "exclusive": False, "kind": None, "subject": None,
            "label": f"unreadable claim {path.stem}", "repository": None, "delivery": None, "story": None,
            "pid": None, "enqueuedAt": "", "startedAt": None, "expectedEnd": None, "estimate": None,
            "admittedBy": None, "why": None, "malformed": True}


def _order(claim: dict) -> tuple[str, str]:
    return (claim.get("enqueuedAt") or "", claim["id"])


def admission(mine: dict, live: list[dict], capacity: int) -> str | None:
    """How a waiting claim is admitted now, or None: strictly first come, first served, with no backfill."""
    others = [claim for claim in live if claim["id"] != mine["id"]]
    if any(claim["state"] == "waiting" and _order(claim) < _order(mine) for claim in others):
        return None
    running = [claim for claim in others if claim["state"] == "running"]
    if mine.get("exclusive"):
        return None if running else "alone"
    if not running:
        return "capacity" if mine["slots"] <= capacity else "alone"
    if any(claim.get("exclusive") for claim in running):
        return None
    used = sum(claim["slots"] for claim in running)
    return "capacity" if used + mine["slots"] <= capacity else None


def estimate_from(passed: list[dict], default_slots: int, capacity: int) -> dict:
    """A run's estimate from its kind's passed runs: medians, never means, and never from a failed run."""
    if not passed:
        return {"seconds": None, "slots": default_slots, "from": "none"}
    seconds = statistics.median(run["seconds"] for run in passed)
    ratios = [math.ceil(run["cpuSeconds"] / run["seconds"]) for run in passed
              if run.get("cpuSeconds") is not None and run["seconds"] > 0]
    slots = default_slots
    if ratios:
        slots = min(max(math.ceil(statistics.median(ratios)), 1), max(capacity, 1))
    return {"seconds": round(seconds, 3), "slots": slots, "from": "measured"}


@dataclass
class Measured:
    returncode: int
    seconds: float
    cpuSeconds: float | None
    result: str  # passed, failed or timedOut


def measured_run(argv: list[str], *, env: dict | None = None, cwd: Path | str | None = None,
                 timeout: float | None = None, **popen: object) -> Measured:
    """Run ``argv`` to its end and measure it. The child is reaped with ``os.wait4``, polled so a timeout still
    applies, so ``cpuSeconds`` covers the child and every descendant it waited for; None where ``wait4`` is missing."""
    started = time.monotonic()
    process = subprocess.Popen(argv, env=env, cwd=cwd, **popen)
    kill_at = None
    try:
        while True:
            if hasattr(os, "wait4"):
                pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                if pid:
                    code = os.waitstatus_to_exitcode(status)
                    process.returncode = code
                    cpu = round(usage.ru_utime + usage.ru_stime, 3)
                    break
            else:
                code = process.poll()
                if code is not None:
                    cpu = None
                    break
            moment = time.monotonic()
            if timeout is not None and kill_at is None and moment - started >= timeout:
                kill_at = moment + STOP_GRACE_SECONDS
                with contextlib.suppress(OSError):
                    process.terminate()
            elif kill_at is not None and moment >= kill_at:
                with contextlib.suppress(OSError):
                    process.kill()
            time.sleep(0.02)
    except BaseException:
        if process.returncode is None:
            with contextlib.suppress(OSError):
                process.terminate()
            try:
                process.wait(STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    process.kill()
                with contextlib.suppress(OSError, ChildProcessError):
                    process.wait()
            except (OSError, ChildProcessError):
                pass
        raise
    seconds = round(time.monotonic() - started, 3)
    result = "timedOut" if kill_at is not None else ("passed" if code == 0 else "failed")
    return Measured(code, seconds, cpu, result)


class Claim:
    """One request in the ledger, or a stand-in when it runs under an ancestor's claim or unclaimed."""

    def __init__(self, ledger: "Ledger", record: dict | None, *, stack: contextlib.ExitStack | None = None,
                 under: dict | None = None, identity: dict | None = None, unavailable: str | None = None) -> None:
        self.ledger = ledger
        self.record = record
        self.under = under
        self.identity = identity
        self.unavailable = unavailable
        self._stack = stack
        self._released = False
        self._shown: object = None
        self._wait_started = ledger.clock()

    @property
    def id(self) -> str | None:
        if self.record is not None:
            return self.record["id"]
        return self.under["id"] if self.under else None

    @property
    def admitted(self) -> bool:
        return self.record is None or self.record["state"] == "running"

    def child_environment(self) -> dict[str, str]:
        """What a process this claim starts must carry, so its own claims run under this one."""
        return {CLAIM_VARIABLE: self.id} if self.id else {}

    def __enter__(self) -> "Claim":
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def poll(self) -> bool:
        """Try once to be admitted; print a waiting line when the ledger changed. True once admitted."""
        if self.admitted:
            return True
        ledger = self.ledger
        with ledger.locked():
            live = ledger.live()
            mine = next((claim for claim in live if claim["id"] == self.id), None)
            if mine is None:
                raise LedgerUnavailable(f"claim {self.id} left the ledger while it waited")
            if mine["state"] == "running":
                self.record = mine
                return True
            capacity = ledger.capacity()
            how = admission(mine, live, capacity)
            if how is not None:
                self.record = ledger.start(self, mine, how, waited=ledger.clock() - self._wait_started)
                return True
            self.record = mine
            self._show(live, capacity)
        return False

    def wait(self, wait_seconds: float | None = None) -> None:
        """Wait until admitted, uncapped unless ``wait_seconds`` caps it."""
        deadline = None if wait_seconds is None else self._wait_started + wait_seconds
        while not self.poll():
            if deadline is not None and self.ledger.clock() >= deadline:
                self._give_up()
                continue
            self.ledger.pause(self, deadline)

    def resize(self, slots: int) -> None:
        """Change a running claim's slots. It grows at once only inside ``holding_slot()``."""
        if self.record is None or slots == self.record["slots"]:
            return
        ledger = self.ledger
        with ledger.locked():
            record = dict(self.record)
            if slots > record["slots"]:
                if not slot_held():
                    raise ValueError("a claim grows only while its process holds a merge slot")
                record["admittedBy"] = "slot"
                record["why"] = None
            record["slots"] = slots
            _write_in_place(ledger.claim_path(record["id"]), record)
            self.record = record

    def record_cost(self, measured: Measured) -> None:
        """Keep a finished run's cost under its own kind and subject, nested or not."""
        identity = self.record or self.identity
        if identity is None or self.unavailable:
            return
        try:
            self.ledger.record_cost(identity.get("repository"), identity["kind"], identity["subject"], measured)
        except LedgerUnavailable:
            pass

    def release(self) -> None:
        """Remove the claim and drop its lock, whatever happened to the run."""
        if self._released:
            return
        self._released = True
        if self.record is None or self._stack is None:
            return
        path = self.ledger.claim_path(self.record["id"])
        try:
            with self.ledger.locked(patient=False):
                # The lock is dropped first, so a system that can't remove an open file can still remove it.
                self._stack.close()
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
        except LedgerUnavailable:
            pass
        finally:
            # Unlocked, the file is a dead claim that the next reader removes.
            self._stack.close()

    def _show(self, live: list[dict], capacity: int) -> None:
        mine = self.record
        running = [claim for claim in live if claim["state"] == "running"]
        ahead = [claim for claim in live if claim["state"] == "waiting" and _order(claim) < _order(mine)]
        signature = (tuple((claim["id"], claim["slots"]) for claim in running), tuple(claim["id"] for claim in ahead),
                     capacity)
        if signature == self._shown:
            return
        self._shown = signature
        self.ledger.say(waiting_line(mine, running, ahead, capacity))

    def _give_up(self) -> None:
        ledger = self.ledger
        waited = ledger.clock() - self._wait_started
        holders: list[dict] = []
        with contextlib.suppress(LedgerUnavailable), ledger.locked():
            live = ledger.live()
            mine = next((claim for claim in live if claim["id"] == self.id), None)
            if mine is not None and mine["state"] == "running":
                # Admitted by another agent since the last poll: that decision stands.
                self.record = mine
                return
            holders = [claim for claim in live if claim["state"] == "running"]
            ledger.history({"event": "gave-up", **_identity(self.record), "waitedSeconds": round(waited, 3),
                            "holders": [claim["id"] for claim in holders]})
        self.release()
        names = "; ".join(_described(claim) for claim in holders) or "nothing"
        raise GaveUp(f"machine: gave up after {waited:g}s waiting for {_plural(self.record['slots'], 'slot')}; "
                     f"held by {names}", holders)


def _described(claim: dict) -> str:
    return (f"{claim.get('label')} (delivery {claim.get('delivery') or 'none'}, {_plural(claim['slots'], 'slot')}, "
            f"expected end {claim.get('expectedEnd') or 'unknown'})")


def waiting_line(mine: dict, running: list[dict], ahead: list[dict], capacity: int) -> str:
    used = sum(claim["slots"] for claim in running)
    free = max(capacity - used, 0)
    if any(claim.get("exclusive") for claim in running):
        free = 0
    holders = "; ".join(_described(claim) for claim in running) or "nothing"
    line = (f"machine: {mine.get('label')} waits for {_plural(mine['slots'], 'slot')}; {used} of {capacity} in use. "
            f"Running: {holders}. {_plural(len(ahead), 'waiter')} ahead. ")
    line += f"{_plural(free, 'slot')} {'is' if free == 1 else 'are'} free now"
    if free and not ahead:
        line += f"; a run of at most {free} would start at once"
    return line + "."


def _identity(record: dict) -> dict:
    keys = ("kind", "subject", "label", "repository", "delivery", "story", "slots")
    return {"claim": record["id"], **{key: record.get(key) for key in keys}}


class Ledger:
    """The machine directory's ledger. ``clock`` and ``sleep`` are injectable, as is where waiting lines go."""

    def __init__(self, directory: Path | str | None = None, *, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, stderr: TextIO | None = None,
                 poll_seconds: float = POLL_SECONDS) -> None:
        self.directory = Path(directory) if directory is not None else machine_directory()
        self.clock = clock
        self.sleep = sleep
        self.stderr = stderr
        self.poll_seconds = poll_seconds

    # Files.

    def claim_path(self, claim_id: str) -> Path:
        return self.directory / CLAIMS / f"{claim_id}.json"

    def prepare(self) -> None:
        try:
            (self.directory / CLAIMS).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise LedgerUnavailable(f"cannot create {self.directory / CLAIMS}: {error.strerror or error}") from None

    @contextlib.contextmanager
    def locked(self, *, patient: bool = True) -> Iterator[None]:
        """Hold ``ledger.lock``. A try that times out is retried while ``patient``; one that can't open is fatal."""
        self.prepare()
        stack = contextlib.ExitStack()
        while True:
            try:
                stack.enter_context(file_lock(self.directory / LEDGER_LOCK, LEDGER_TRY_SECONDS))
                break
            except StateError as error:
                if error.code != "lock_timeout":
                    raise LedgerUnavailable(str(error)) from None
                if not patient:
                    raise LedgerUnavailable(str(error)) from None
        with stack:
            yield

    def say(self, line: str) -> None:
        stream = self.stderr if self.stderr is not None else sys.stderr
        stream.write(line + "\n")
        stream.flush()

    def names(self) -> list[str]:
        try:
            return sorted(name for name in os.listdir(self.directory / CLAIMS) if name.endswith(".json"))
        except FileNotFoundError:
            return []

    def live(self) -> list[dict]:
        """Every live claim in order, removing each whose lock nobody holds. Call under ``locked()``."""
        claims = []
        for name in self.names():
            path = self.directory / CLAIMS / name
            held = _held(path)
            if held is None:
                continue
            if held is False:
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            except (OSError, UnicodeDecodeError):
                claims.append(_malformed(path, None))
                continue
            try:
                record = json.loads(text)
            except ValueError:
                record = None
            if not isinstance(record, dict) or record.get("state") not in ("waiting", "running") or not (
                    isinstance(record.get("slots"), int) and record["slots"] >= 1) or record.get("id") != path.stem:
                claims.append(_malformed(path, text))
                continue
            claims.append(record)
        return sorted(claims, key=_order)

    def capacity_setting(self) -> dict | None:
        try:
            value = json.loads((self.directory / CAPACITY_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(value, dict) or not isinstance(value.get("slots"), int) or value["slots"] < 1:
            return None
        return value

    def capacity(self) -> int:
        setting = self.capacity_setting()
        return setting["slots"] if setting else (os.cpu_count() or 1)

    def set_capacity(self, slots: int | None, why: str) -> dict:
        """Set capacity to ``slots``, or remove the setting with None. Either way a history entry says why."""
        with self.locked():
            path = self.directory / CAPACITY_FILE
            if slots is None:
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
                setting = None
            else:
                setting = {"schemaVersion": "machine-capacity/1", "slots": slots, "setAt": _iso(self.clock()),
                           "why": why}
                delivery_state._write_atomically(path, dumps(setting))
            self.history({"event": "capacity", "capacity": slots, "why": why})
        return {"slots": self.capacity(), "setting": setting}

    def history(self, entry: dict) -> None:
        """Append one history entry, keeping the last ``HISTORY_LIMIT``. Call under ``locked()``."""
        path = self.directory / HISTORY_FILE
        line = json.dumps({"schemaVersion": "machine-history/1", "at": _iso(self.clock()), **entry},
                          ensure_ascii=False, sort_keys=True) + "\n"
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(line)
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) > HISTORY_LIMIT:
            delivery_state._write_atomically(path, "".join(lines[-HISTORY_LIMIT:]))

    def read_history(self) -> list[dict]:
        try:
            lines = (self.directory / HISTORY_FILE).read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries = []
        for line in lines:
            with contextlib.suppress(ValueError):
                entries.append(json.loads(line))
        return entries

    def costs(self) -> dict:
        try:
            value = json.loads((self.directory / COSTS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = None
        if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
            value = {"schemaVersion": "machine-costs/1", "entries": []}
        return value

    def _entry(self, costs: dict, repository: str | None, kind: str, subject: str) -> dict | None:
        return next((entry for entry in costs["entries"] if isinstance(entry, dict) and
                     (entry.get("repository"), entry.get("kind"), entry.get("subject")) == (repository, kind, subject)),
                    None)

    def record_cost(self, repository: str | None, kind: str, subject: str, measured: Measured) -> None:
        """Keep the last five passed runs and, apart, the last five that failed or timed out."""
        with self.locked():
            costs = self.costs()
            entry = self._entry(costs, repository, kind, subject)
            if entry is None:
                entry = {"repository": repository, "kind": kind, "subject": subject, "passed": [], "failed": []}
                costs["entries"].append(entry)
            run = {"seconds": measured.seconds, "cpuSeconds": measured.cpuSeconds, "result": measured.result,
                   "at": _iso(self.clock())}
            which = "passed" if measured.result == "passed" else "failed"
            entry[which] = [*entry.get(which, []), run][-COST_RUNS:]
            delivery_state._write_atomically(self.directory / COSTS_FILE, dumps(costs))

    def estimate(self, repository: str | None, kind: str, subject: str, default_slots: int = 1) -> dict:
        entry = self._entry(self.costs(), repository, kind, subject)
        return estimate_from((entry or {}).get("passed", []), default_slots, self.capacity())

    # Claims.

    def request(self, kind: str, subject: str, *, label: str | None = None, slots: int = 1,
                exclusive: bool = False, repository: str | None = None, delivery: str | None = None,
                story: str | None = None, now_why: str | None = None) -> Claim:
        """Enqueue a claim without waiting. It is admitted at once when its process holds a merge slot, when
        ``now_why`` steps off the default, or when the admission rule allows; otherwise ``poll`` or ``wait``."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if slots < 1:
            raise ValueError("slots must be at least 1")
        label = label or subject
        try:
            ancestor = self._ancestor()
            if ancestor is not None:
                self.say(f"running under claim {ancestor['id']} ({ancestor.get('label')})")
                return Claim(self, None, under=ancestor,
                             identity={"repository": repository, "kind": kind, "subject": subject})
            return self._enqueue(kind, subject, label, slots, exclusive, repository, delivery, story, now_why)
        except LedgerUnavailable as error:
            return self._unclaimed(error)

    def acquire(self, kind: str, subject: str, *, wait_seconds: float | None = None, **request: object) -> Claim:
        """Enqueue and wait until admitted. A capped wait that ends unadmitted raises ``GaveUp``."""
        claim = self.request(kind, subject, **request)  # type: ignore[arg-type]
        try:
            claim.wait(wait_seconds)
        except LedgerUnavailable as error:
            claim.release()
            return self._unclaimed(error)
        except BaseException:
            claim.release()
            raise
        return claim

    @contextlib.contextmanager
    def claim(self, kind: str, subject: str, **request: object) -> Iterator[Claim]:
        handle = self.acquire(kind, subject, **request)  # type: ignore[arg-type]
        try:
            yield handle
        finally:
            handle.release()

    def _unclaimed(self, error: Exception) -> Claim:
        self.say(f"machine ledger unavailable: {error}; running unclaimed")
        return Claim(self, None, unavailable=str(error))

    def _ancestor(self) -> dict | None:
        named = os.environ.get(CLAIM_VARIABLE)
        if not named or not re.fullmatch(r"[0-9a-f]{32}", named):
            return None
        with self.locked():
            return next((claim for claim in self.live() if claim["id"] == named), None)

    def _enqueue(self, kind: str, subject: str, label: str, slots: int, exclusive: bool, repository: str | None,
                 delivery: str | None, story: str | None, now_why: str | None) -> Claim:
        with self.locked():
            estimate = self.estimate(repository, kind, subject, slots)
            record = {
                "schemaVersion": "machine-claim/1", "id": uuid.uuid4().hex, "state": "waiting",
                "slots": estimate["slots"], "exclusive": exclusive, "kind": kind, "subject": subject, "label": label,
                "repository": repository, "delivery": delivery, "story": story, "pid": os.getpid(),
                "enqueuedAt": _iso(self.clock()), "startedAt": None, "expectedEnd": None, "estimate": estimate,
                "admittedBy": None, "why": None,
            }
            path = self.claim_path(record["id"])
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(dumps(record).encode("utf-8"))
            except OSError as error:
                raise LedgerUnavailable(f"cannot write {path.name}: {error.strerror or error}") from None
            stack = contextlib.ExitStack()
            try:
                stack.enter_context(file_lock(path, 1.0))
            except StateError as error:
                with contextlib.suppress(OSError):
                    path.unlink()
                raise LedgerUnavailable(str(error)) from None
            claim = Claim(self, record, stack=stack)
            how = "slot" if slot_held() else ("now" if now_why is not None else None)
            if how is None:
                how = admission(record, self.live(), self.capacity())
            if how is not None:
                claim.record = self.start(claim, record, how, waited=0.0, why=now_why if how == "now" else None)
        return claim

    def start(self, claim: Claim, record: dict, how: str, *, waited: float, why: str | None = None) -> dict:
        """Mark a claim running and record how it was admitted. Call under ``locked()``."""
        moment = self.clock()
        seconds = (record.get("estimate") or {}).get("seconds")
        record = {**record, "state": "running", "startedAt": _iso(moment), "admittedBy": how, "why": why,
                  "expectedEnd": _iso(moment + seconds) if seconds is not None else None}
        _write_in_place(self.claim_path(record["id"]), record)
        entry = {"event": "override" if how in OVERRIDES else "admitted", **_identity(record), "admittedBy": how,
                 "waitedSeconds": round(waited, 3)}
        if how in OVERRIDES:
            entry["why"] = why
        self.history(entry)
        return record

    def admit(self, claim_id: str, why: str) -> dict:
        """Admit another agent's waiting claim, recording why."""
        with self.locked():
            record = next((claim for claim in self.live() if claim["id"] == claim_id), None)
            if record is None or record.get("malformed"):
                raise ValueError(f"no live claim {claim_id}")
            if record["state"] != "waiting":
                raise ValueError(f"claim {claim_id} is already running")
            enqueued = record.get("enqueuedAt")
            waited = 0.0
            with contextlib.suppress(ValueError, TypeError):
                waited = self.clock() - datetime.datetime.strptime(enqueued, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                    tzinfo=datetime.timezone.utc).timestamp()
            stand_in = Claim(self, record)
            return self.start(stand_in, record, "admit", waited=max(waited, 0.0), why=why)

    def pause(self, claim: Claim, deadline: float | None) -> None:
        """Sleep up to ``poll_seconds``, waking early when a claim is released or this claim changes."""
        def mark() -> tuple:
            marks = []
            for path in (self.directory / CLAIMS, self.claim_path(claim.id)):
                try:
                    marks.append(os.stat(path).st_mtime_ns)
                except OSError:
                    marks.append(None)
            return tuple(marks)

        before = mark()
        began = self.clock()
        while self.clock() - began < self.poll_seconds:
            if deadline is not None and self.clock() >= deadline:
                return
            self.sleep(min(WAKE_SECONDS, self.poll_seconds))
            if mark() != before:
                return

    def reading(self) -> dict:
        """What any agent or view reads: capacity, running claims, waiters in order and the last five overrides."""
        try:
            with self.locked():
                live = self.live()
        except LedgerUnavailable as error:
            return {"directory": str(self.directory), "unavailable": str(error)}
        running = [claim for claim in live if claim["state"] == "running"]
        setting = self.capacity_setting()
        return {
            "directory": str(self.directory),
            "capacity": self.capacity(),
            "capacitySetting": setting,
            "used": sum(claim["slots"] for claim in running),
            "running": running,
            "waiting": [claim for claim in live if claim["state"] == "waiting"],
            "overrides": [entry for entry in self.read_history() if entry.get("event") == "override"][-5:],
        }


# The command line.


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="machine.py", description="This machine's ledger of claims, costs and "
                                     "capacity, shared by every repository on it.")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="show capacity, claims, waiters and live pressure")
    status.add_argument("--json", action="store_true")
    run = commands.add_parser("run", help="claim slots, wait by default, then run argv")
    run.add_argument("--kind", choices=KINDS, default="run")
    run.add_argument("--subject")
    run.add_argument("--label")
    run.add_argument("--slots", type=int, default=1)
    run.add_argument("--exclusive", action="store_true")
    run.add_argument("--now", action="store_true", help="run at once, with --why")
    run.add_argument("--why")
    run.add_argument("--wait-seconds", type=float)
    run.add_argument("--timeout-seconds", type=float)
    run.add_argument("--repository")
    run.add_argument("--delivery")
    run.add_argument("--story")
    run.add_argument("argv", nargs=argparse.REMAINDER)
    admit = commands.add_parser("admit", help="admit a waiting claim, with a reason")
    admit.add_argument("claim")
    admit.add_argument("--why", required=True)
    capacity = commands.add_parser("capacity", help="set or reset this machine's capacity, with a reason")
    which = capacity.add_mutually_exclusive_group(required=True)
    which.add_argument("--slots", type=int)
    which.add_argument("--reset", action="store_true")
    capacity.add_argument("--why", required=True)
    return parser


def _status_text(reading: dict, pressure: dict) -> str:
    lines = [f"machine directory: {reading['directory']}"]
    if "unavailable" in reading:
        lines.append(f"machine ledger unavailable: {reading['unavailable']}")
    else:
        setting = reading["capacitySetting"]
        source = f"set {setting['setAt']}: {setting['why']}" if setting else "unset, the core count"
        lines.append(f"capacity: {reading['capacity']} slots ({source}); {reading['used']} in use")
        for claim in reading["running"]:
            lines.append(f"running: {claim['id']} {_described(claim)}, since {claim.get('startedAt')}, "
                         f"admitted by {claim.get('admittedBy')}")
        for position, claim in enumerate(reading["waiting"], start=1):
            lines.append(f"waiting {position}: {claim['id']} {claim.get('label')} for "
                         f"{_plural(claim['slots'], 'slot')}, since {claim.get('enqueuedAt')}")
        for entry in reading["overrides"]:
            lines.append(f"override: {entry.get('label')} by {entry.get('admittedBy')} at {entry.get('at')}: "
                         f"{entry.get('why')}")
    load = pressure.get("load5")
    lines.append(f"pressure: load5 {load if load is not None else 'unknown'} on {pressure.get('cores')} cores, "
                 f"{pressure.get('diskFreeBytes')} bytes free, {pressure.get('swapUsedBytes')} bytes of swap used")
    return "\n".join(lines) + "\n"


def _status(ledger: Ledger, as_json: bool) -> int:
    reading = ledger.reading()
    probe = ledger.directory if ledger.directory.is_dir() else Path.cwd()
    pressure = machine_reading(probe)
    if as_json:
        delivery_state.emit({**reading, "pressure": pressure})
    else:
        sys.stdout.write(_status_text(reading, pressure))
    return 0


def _run(ledger: Ledger, arguments: argparse.Namespace) -> int:
    argv = arguments.argv[1:] if arguments.argv[:1] == ["--"] else arguments.argv
    if not argv:
        raise SystemExit("machine.py run: give the command after --")
    label = arguments.label or Path(argv[0]).name
    # An unlabelled run is measured by a digest of its whole command line, so two commands that share an interpreter
    # never share one cost entry, and no argument, a secret included, reaches the shared ledger.
    subject = arguments.subject or arguments.label or (
        f"{Path(argv[0]).name} {hashlib.sha256(shlex.join(argv).encode('utf-8')).hexdigest()[:16]}")
    repository = arguments.repository or repository_identity()
    try:
        claim = ledger.acquire(arguments.kind, subject, label=label, slots=arguments.slots,
                               exclusive=arguments.exclusive, repository=repository, delivery=arguments.delivery,
                               story=arguments.story, now_why=arguments.why if arguments.now else None,
                               wait_seconds=arguments.wait_seconds)
    except GaveUp as error:
        ledger.say(str(error))
        return EX_TEMPFAIL
    with claim:
        measured = measured_run(argv, env={**os.environ, **claim.child_environment()},
                                timeout=arguments.timeout_seconds)
        claim.record_cost(measured)
    code = measured.returncode
    return 128 - code if code < 0 else code


def _signalled(signum: int, _frame: object) -> None:
    # A handled signal unwinds like an exit, so every claim is released in its finally.
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None, *, ledger: Ledger | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "run":
        if arguments.now and not arguments.why:
            parser.error("--now needs --why")
        if arguments.why and not arguments.now:
            parser.error("--why goes with --now")
        if arguments.slots < 1:
            parser.error("--slots must be at least 1")
    if arguments.command == "capacity" and arguments.slots is not None and arguments.slots < 1:
        parser.error("--slots must be at least 1")
    ledger = ledger or Ledger()
    if arguments.command == "status":
        return _status(ledger, arguments.json)
    if arguments.command == "run":
        return _run(ledger, arguments)
    if arguments.command == "admit":
        try:
            record = ledger.admit(arguments.claim, arguments.why)
        except (ValueError, LedgerUnavailable) as error:
            ledger.say(f"machine.py admit: {error}")
            return 1
        sys.stdout.write(f"admitted {record['id']} ({record.get('label')}): {arguments.why}\n")
        return 0
    try:
        result = ledger.set_capacity(None if arguments.reset else arguments.slots, arguments.why)
    except LedgerUnavailable as error:
        ledger.say(f"machine.py capacity: machine ledger unavailable: {error}")
        return 1
    sys.stdout.write(f"capacity: {result['slots']} slots\n")
    return 0


if __name__ == "__main__":
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _signalled)
    sys.exit(main())
