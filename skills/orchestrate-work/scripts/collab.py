#!/usr/bin/env python3
"""Share one delivery record between seats, each a checkout on its own machine, through git alone.

Run with ``python3 -B``. A seat is one writer of a shared record: a checkout, whose id is kept in its local git
configuration as ``agent-skills.seat.<key>``. Each seat's partition is a commit chain at
``refs/agent-skills/<key>/<seat>`` on the base remote, holding ``partition.json`` (``delivery-partition/1``: the
values its own writes set, by path, with their origins) and ``journal.jsonl`` (its own entries, their links by
origin). Peers' partitions come down by explicit refspec into ``refs/agent-skills-peers/<key>/<seat>`` and are
ingested into the local record by the sync writer: an entry once every entry its ``seen`` names is present, its links
translated to local ids, and a value when its writer may write it and it supersedes the local one. A partition from
another writer generation is held, and a checkout whose push finds its seat's chain diverged is split.

``serve`` is the sync loop a supervisor runs: it pushes this seat's partition after a local write and fetches and
ingests its peers every poll. The state tool's ``collab-push``, ``collab-fetch`` and ``collab-ingest`` run each step
alone, for tests and recovery.

Every journal write goes through the state tool's append functions, and every state write through its own writer.

A value's origin is the seat and seq of the write that set it, its seq that seat's last own entry, and the value it
replaced, if another seat's, by that seat, seq and digest. So a write that replaced a peer's value follows it on every
machine; two writes neither of which replaced the other are concurrent, and the higher seat id wins, as every machine
chooses. A removal wins over every write that does not follow it.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import contextlib
import copy
import importlib.util
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from pathlib import Path

PARTITION = "delivery-partition/1"
REFS = "refs/agent-skills"
PEER_REFS = "refs/agent-skills-peers"
SPLIT_REFS = "refs/agent-skills-split"
PARTITION_FILE = "partition.json"
JOURNAL_FILE = "journal.jsonl"
# Beside the record, machine-local: the tip of this seat's chain and the digest of what it holds, so an unchanged
# partition is never built into a commit again.
TIP_FILE = "partition-tip.json"
# Beside the record, machine-local: what this seat's partition carries since it last passed delivery on (`given`), and
# the transfer of delivery to this seat whose carried values it has taken (`taken`).
CARRIED_FILE = "partition-carried.json"
KEY = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
PATH = re.compile(r"^(?P<array>[a-z]+)\[(?P<key>[A-Za-z]+)=(?P<value>[^\]]+)\](?:/(?P<field>.+))?$")
POLL_SECONDS = 5.0
MIN_POLL_SECONDS = 2.0
# A peer's fetch time is written again only when something else changed or it is this old, so an idle loop leaves the
# record alone.
FETCHED_REFRESH_SECONDS = 60.0
GIT_TIMEOUT_SECONDS = 120
# How long a queue handover waits for the queue to be idle before it refuses.
HANDOVER_WAIT_SECONDS = 60.0
# A finished check's states that fail its run, as the queue journals check.failed for them.
FAILING = ("failed", "timed_out")
ZERO = "0" * 40

state_tool = None


def _state_tool():
    """The state tool beside this script, loaded by its own path; the state tool sets itself here when it loads this."""
    global state_tool
    if state_tool is None:
        spec = importlib.util.spec_from_file_location("delivery_state", Path(__file__).resolve().parent / "delivery_state.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        state_tool = module
    return state_tool


def _refuse(code: str, message: str):
    return _state_tool().StateError(code, message)


@contextlib.contextmanager
def _as_sync():
    """Write as the sync writer: an ingest is the tool's write, made for no seat and checked against no seat."""
    local = _state_tool()._COLLAB
    previous = getattr(local, "sync", False)
    local.sync = True
    try:
        yield
    finally:
        local.sync = previous


def _git(repo: Path, *arguments: str, data: str | None = None, check: bool = True,
         environment: dict | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *arguments], cwd=repo, input=data, text=True,
                            capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=False,
                            env={**os.environ, **(environment or {})})
    if check and result.returncode != 0:
        raise _refuse("collab.git_failed", f"git {' '.join(arguments[:2])} failed: {result.stderr.strip()[-400:]}")
    return result


def _revision(repo: Path, ref: str) -> str | None:
    found = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return found.stdout.strip() if found.returncode == 0 else None


def _check_key(key: str) -> str:
    if not isinstance(key, str) or not KEY.match(key):
        raise _refuse("collab.key_invalid", f"a shared record's key starts with a letter and holds letters, digits "
                      f"and dashes, at most 64: {key!r}")
    return key


def partition_ref(key: str, seat: str) -> str:
    return f"{REFS}/{key}/{seat}"


def peer_ref(key: str, seat: str) -> str:
    return f"{PEER_REFS}/{key}/{seat}"


def split_ref(key: str, seat: str) -> str:
    return f"{SPLIT_REFS}/{key}/{seat}"


# Seats.

def ensure_seat(repo: Path, key: str) -> str:
    """This checkout's seat for ``key``, made the first time: ``s-`` and 12 hex characters, kept in its local config."""
    seat = _state_tool().local_seat(repo, _check_key(key))
    if seat is None:
        seat = f"s-{secrets.token_hex(6)}"
        _git(repo, "config", "--local", f"agent-skills.seat.{key}", seat)
    return seat


def default_label(repo: Path, seat: str) -> str:
    """The label a new seat shows: git's user.name, or else its id."""
    found = _git(repo, "config", "--get", "user.name", check=False)
    return found.stdout.strip() or seat


def _key_of(directory: Path) -> str:
    collab = _state_tool().collab_of(_state_tool().read_state(directory))
    if collab is None:
        raise _refuse("collab.not_shared", f"the record at {directory} is not shared")
    return collab["key"]


def open_record(directory: Path, *, key: str, headline: str, base: dict, phase: str = "planning",
                label: str | None = None) -> dict:
    """Create a shared record for ``key`` with this checkout's seat as its first, admitted seat: ``record.started`` is
    its entry 1, and every value the record starts with is that entry's."""
    tool = _state_tool()
    _check_key(key)
    if (directory / tool.STATE_FILE).exists():
        raise _refuse("collab.record_exists", f"{key} already has a record at {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    seat = ensure_seat(directory, key)
    label = label or default_label(directory, seat)
    tool.init(directory, headline)
    with tool.file_lock(directory / tool.STATE_LOCK):
        current = tool.read_state(directory)
        shared = copy.deepcopy(current)
        shared["meta"]["collab"] = {"key": key, "seats": {seat: {"label": label, "admitted": True, "approver": True}},
                                    "peers": {}}
        shared["meta"]["phase"] = phase
        with _as_sync():
            tool._with_state(directory, current, shared)
    tool.append_journal(directory, kind="record.started", data={"base": base})
    with tool.file_lock(directory / tool.STATE_LOCK):
        values = tool.partition_paths(tool.read_state(directory))
        tool._write_atomically(directory / tool.FIELD_ORIGINS_FILE,
                               tool.dumps({path: {"seat": seat, "seq": 1} for path in values}))
    tool.append_journal(directory, kind="seat.admitted", data={"seat": seat, "label": label, "approver": True})
    return tool.read_state(directory)


def request_seat(directory: Path, *, label: str | None = None) -> dict:
    """Give this checkout a seat on the shared record it has ingested, and journal its request to join, whose seen
    names everything ingested so far. Asked again, it returns the request already made."""
    tool = _state_tool()
    key = _key_of(directory)
    seat = ensure_seat(directory, key)
    request = next((entry for entry in reversed(tool.read_journal(directory))
                    if entry["kind"] == "seat.requested" and entry["data"]["seat"] == seat), None)
    if request is not None:
        return request
    local = tool._COLLAB
    local.requesting = seat
    try:
        return tool.append_journal(directory, kind="seat.requested",
                                   data={"seat": seat, "label": label or default_label(directory, seat)})
    finally:
        local.requesting = None


def _own_seat(directory: Path) -> str:
    seat = _state_tool().local_seat(directory, _key_of(directory))
    if seat is None:
        raise _refuse("collab.seat_missing", "this checkout has no seat for the shared record")
    return seat


def _require_holder(directory: Path, journal: list[dict], scope: str, what: str) -> str:
    """This checkout's seat, which must hold ``scope`` to do ``what``."""
    tool = _state_tool()
    seat = _own_seat(directory)
    if not tool.holds(journal, seat, scope):
        raise _refuse("scope.not_held", f"only the holder of {scope}, seat {tool.holder(journal, scope)}, {what}; "
                      f"scope request --scope {scope} asks for it")
    return seat


def admit_seat(directory: Path, seat: str, *, approver: bool = True) -> dict:
    """The delivery holder admits a requested seat, answering its request. Admitted already with the same approver,
    the admission it has is returned; admitting it again with another approver changes it."""
    tool = _state_tool()
    journal = tool.read_journal(directory)
    _require_holder(directory, journal, "delivery", "admits a seat")
    if (tool.derived_seats(journal).get(seat) or {}).get("admitted") and \
            tool.derived_seats(journal)[seat]["approver"] == approver:
        return next(entry for entry in reversed(journal) if entry["kind"] == "seat.admitted"
                    and entry["data"]["seat"] == seat and entry["data"]["approver"] == approver)
    request = next((entry for entry in reversed(journal)
                    if entry["kind"] == "seat.requested" and entry["data"]["seat"] == seat), None)
    if request is None:
        raise _refuse("collab.seat_unknown", f"no seat {seat} has asked to join this record")
    label = tool.derived_seats(journal)[seat]["label"]
    return tool.append_journal(directory, kind="seat.admitted", data={"seat": seat, "label": label, "approver": approver},
                               answers=[request["id"]])


def give_scope(directory: Path, scope: str, to: str, *, wait: float = HANDOVER_WAIT_SECONDS,
               poll: float = 0.5, sleep=time.sleep, park=None) -> dict:
    """Pass ``scope`` from this seat, its holder, to the admitted seat ``to``. The queue is handed over only once it is
    idle, waiting up to ``wait`` seconds for that. Delivery, given ``park``, is handed over inside ``park(wait)``: a
    context that parks this machine's publishers and yields what this seat's partition carries to ``to``, which is
    kept before the transfer is journalled."""
    tool = _state_tool()
    _check_scope(scope)
    journal = tool.read_journal(directory)
    seat = _require_holder(directory, journal, scope, "passes it on")
    if not (tool.derived_seats(journal).get(to) or {}).get("admitted"):
        raise _refuse("collab.seat_unknown", f"seat {to} is not an admitted seat of this record")
    if to == seat:
        raise _refuse("scope.held", f"seat {seat} holds {scope} already")
    if scope == "queue":
        return _hand_over_queue(directory, seat, to, wait=wait, poll=poll, sleep=sleep)
    with park(wait) if scope == "delivery" and park is not None else contextlib.nullcontext() as carried:
        if carried is not None:
            _keep_carried(directory, given=carried)
        return tool.append_journal(directory, kind="scope.transferred", data={"scope": scope, "from": seat, "to": to})


def _carried(directory: Path) -> dict:
    try:
        found = json.loads((directory / CARRIED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _keep_carried(directory: Path, **changes: object) -> None:
    _state_tool()._write_atomically(directory / CARRIED_FILE, _state_tool().dumps({**_carried(directory), **changes}))


def peer_carried(directory: Path, seat: str) -> dict:
    """What ``seat``'s fetched partition carries for the seat it passed delivery to: its hosted identity and tracker
    position, each null or empty where it carried none."""
    try:
        partition, _ = read_partition(directory, peer_ref(_key_of(directory), seat))
    except (ValueError, subprocess.SubprocessError):
        partition = {}
    return partition.get("carried") or {"hostedIdentity": None, "trackerThrough": {}}


def delivery_transfer(journal: list[dict], seat: str) -> dict | None:
    """The transfer that gave ``seat`` the delivery it holds now, or None where it holds it from the record's start or
    does not hold it."""
    tool = _state_tool()
    if not tool.holds(journal, seat, "delivery"):
        return None
    return next((entry for entry in reversed(journal) if entry["kind"] == "scope.transferred"
                 and entry["data"]["scope"] == "delivery" and entry["data"]["to"] == seat), None)


def delivery_taken(directory: Path, journal: list[dict] | None = None) -> bool | None:
    """On a shared record: whether this seat holds delivery and has taken what the seat that passed it here carried,
    so its publishers may run; None on an unshared record."""
    tool = _state_tool()
    collab = tool.collab_of(tool.read_state(directory))
    if collab is None:
        return None
    journal = tool.read_journal(directory) if journal is None else journal
    seat = tool.local_seat(directory, collab["key"])
    if seat is None or not tool.holds(journal, seat, "delivery"):
        return False
    transfer = delivery_transfer(journal, seat)
    return transfer is None or _carried(directory).get("taken") == _origin(transfer)


def take_delivery(directory: Path, transfer: dict) -> None:
    """Mark ``transfer``'s carried values as taken: the caller has written them as this seat's own."""
    _keep_carried(directory, taken=_origin(transfer))


def _queue_busy(state: dict) -> str | None:
    """What keeps the queue from being handed over: a check running, or a merge in progress."""
    running = state["meta"]["checking"].get("now")
    if running:
        return f"the check of {running['subject']} ({running['title']}) is running"
    merging = [item["subject"] for item in state["queue"] if item["stage"] == "merging"]
    return f"the merge of {', '.join(merging)} is in progress" if merging else None


def _hand_over_queue(directory: Path, seat: str, to: str, *, wait: float, poll: float, sleep) -> dict:
    """D13: once the queue is idle, holding the queue lock so no turn starts, journal that this machine's queue takes
    no more work, then pass the queue on. Busy past ``wait``, refuse naming what is running."""
    tool = _state_tool()
    deadline = time.monotonic() + wait
    while True:
        busy = None
        try:
            with tool.file_lock(directory / tool.QUEUE_LOCK, timeout=min(poll, max(0.0, deadline - time.monotonic()))):
                busy = _queue_busy(tool.read_state(directory))
                if busy is None:
                    tool.append_journal(directory, kind="queue.stopped", data={"seat": seat})
                    return tool.append_journal(directory, kind="scope.transferred",
                                               data={"scope": "queue", "from": seat, "to": to})
        except tool.StateError as error:
            if error.code != "lock_timeout":
                raise
            busy = _queue_busy(tool.read_state(directory)) or "a turn of the queue is running"
        if time.monotonic() >= deadline:
            raise _refuse("scope.handover_busy", f"the queue is not idle: {busy}; hand it over once it is, or wait "
                          "longer")
        sleep(poll)


def request_scope(directory: Path, scope: str, reason: str) -> dict:
    """Ask the holder of ``scope`` to pass it to this seat, saying why."""
    tool = _state_tool()
    _check_scope(scope)
    seat = _own_seat(directory)
    if tool.holds(tool.read_journal(directory), seat, scope):
        raise _refuse("scope.held", f"seat {seat} holds {scope} already")
    return tool.append_journal(directory, kind="scope.requested", data={"scope": scope, "reason": reason})


def decline_request(directory: Path, request: int) -> dict:
    """The holder of a requested scope keeps it, answering the request by its local id."""
    tool = _state_tool()
    journal = tool.read_journal(directory)
    asked = next((entry for entry in journal if entry["id"] == request and entry["kind"] == "scope.requested"), None)
    if asked is None:
        raise _refuse("scope.request_unknown", f"journal entry {request} is not a scope.requested")
    _require_holder(directory, journal, asked["data"]["scope"], "declines a request for it")
    return tool.append_journal(directory, kind="scope.declined", data={"scope": asked["data"]["scope"]},
                               answers=[request])


def open_requests(journal: list[dict]) -> list[dict]:
    """Every scope.requested not yet answered: no decline names it, no transfer after it gave the scope to its seat,
    and its seat does not hold the scope now."""
    tool = _state_tool()
    declined = {target for entry in journal if entry["kind"] == "scope.declined" for target in entry.get("answers", [])}
    found = []
    for at, entry in enumerate(journal):
        if entry["kind"] != "scope.requested" or entry["id"] in declined:
            continue
        seat, scope = entry["origin"]["seat"], entry["data"]["scope"]
        given = any(later["kind"] == "scope.transferred" and later["data"]["scope"] == scope
                    and later["data"]["to"] == seat for later in journal[at + 1:])
        if not given and not tool.holds(journal, seat, scope):
            found.append(entry)
    return found


def auto_grants(directory: Path, *, enabled: bool) -> list[dict]:
    """With ``collab.autoGrant`` on, the delivery holder passes each requested plan section nobody holds to the seat
    that asked; never a held section and never another scope. Returns the transfers it journalled."""
    tool = _state_tool()
    journal = tool.read_journal(directory)
    seat = tool.local_seat(directory, _key_of(directory))
    if not enabled or not tool.holds(journal, seat, "delivery"):
        return []
    given = []
    for request in open_requests(journal):
        scope = request["data"]["scope"]
        _, held = tool.scope_register(journal)
        if scope.startswith("plan:") and scope not in held:
            given.append(give_scope(directory, scope, request["origin"]["seat"]))
            journal = tool.read_journal(directory)
    return given


def auto_grant_setting(settings: Path) -> bool:
    """``collab.autoGrant`` from the repository's run-delivery settings, false when absent."""
    try:
        return json.loads(settings.read_text(encoding="utf-8")).get("collab", {}).get("autoGrant") is True
    except (OSError, ValueError, AttributeError):
        return False


def _check_scope(scope: str) -> str:
    if not isinstance(scope, str) or not _state_tool().SCOPE.match(scope):
        raise _refuse("scope.invalid", f"a scope is delivery, queue, lane:<lane> or plan:<section>: {scope!r}")
    return scope


def label_seat(directory: Path, label: str) -> dict:
    """Change the label this checkout's seat shows. A label is display only."""
    tool = _state_tool()
    seat = tool.local_seat(directory, _key_of(directory))
    if seat is None:
        raise _refuse("collab.seat_missing", "this checkout has no seat for the shared record")
    return tool.append_journal(directory, kind="seat.labeled", data={"seat": seat, "label": label})


# The partition.

def _origin(entry: dict) -> dict:
    return {"seat": entry["origin"]["seat"], "seq": entry["origin"]["seq"]}


def _localized(path: str, value: object, local_ids: dict[tuple, int]) -> tuple[bool, object]:
    """A carried value as this record holds it, its origins translated to local ids; False while one is missing."""
    def local(reference: object) -> int | None:
        return local_ids.get((reference.get("seat"), reference.get("seq"))) if isinstance(reference, dict) else None
    if path == "regionsSettled" and isinstance(value, dict):
        found = local(value)
        return found is not None, found
    if path.endswith("/leadSelection") and isinstance(value, dict) and isinstance((value.get("reuse") or {}).get("decision"), dict):
        found = local(value["reuse"]["decision"])
        if found is None:
            return False, None
        value = copy.deepcopy(value)
        value["reuse"]["decision"] = found
    return True, value


def build_partition(directory: Path) -> tuple[dict, str]:
    """This seat's partition of the shared record: ``partition.json`` and the text of its ``journal.jsonl``."""
    tool = _state_tool()
    with tool.file_lock(directory / tool.STATE_LOCK):
        state = tool.read_state(directory)
        journal = tool.read_journal(directory)
        origins = tool.read_field_origins(directory)
    collab = tool.collab_of(state)
    if collab is None:
        raise _refuse("collab.not_shared", f"the record at {directory} is not shared")
    seat = tool.local_seat(directory, collab["key"])
    if seat is None:
        raise _refuse("collab.seat_missing", "this checkout has no seat for the shared record")
    origins_of = tool.journal_origins(journal)
    lines = []
    for entry in journal:
        if entry["origin"]["seat"] != seat:
            continue
        exported = {name: value for name, value in entry.items() if name not in ("id", "because", "answers")}
        for link in ("because", "answers"):
            if entry.get(link):
                exported[link] = [origins_of[target] for target in entry[link]]
        lines.append(json.dumps(exported, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
    held = []
    for path, value in tool.partition_paths(state).items():
        origin = origins.get(path)
        if origin is not None and origin["seat"] == seat and not origin.get("removed"):
            held.append({"path": path, "value": tool.carried_value(path, value, origins_of),
                         "origin": {"seat": seat, "seq": origin["seq"], **({"seen": origin["seen"]} if "seen" in origin else {})},
                         **({"replaces": origin["replaces"]} if "replaces" in origin else {})})
    for path, origin in sorted(origins.items()):
        if origin["seat"] == seat and origin.get("removed"):
            held.append({"path": path, "removed": True,
                         "origin": {"seat": seat, "seq": origin["seq"], **({"seen": origin["seen"]} if "seen" in origin else {})},
                         **({"replaces": origin["replaces"]} if "replaces" in origin else {})})
    partition = {"schemaVersion": PARTITION, "seat": seat, "generation": state["meta"].get("writerGeneration"),
                 "through": len(lines), "slice": held}
    given = _carried(directory).get("given")
    if given is not None:
        partition["carried"] = given
    errors = tool.validate(partition, tool.load_schema(PARTITION))
    if errors:
        raise _refuse("collab.partition_invalid", "; ".join(errors))
    return partition, "".join(lines)


def _identity(repo: Path, seat: str) -> dict:
    """Who a partition commit is by: the checkout's own git identity, or its seat where it has none."""
    found = dict(line.split(" ", 1) for line in _git(repo, "config", "--get-regexp", r"^user\.(name|email)$",
                                                       check=False).stdout.splitlines() if " " in line)
    name = found.get("user.name", "").strip() or seat
    email = found.get("user.email", "").strip() or f"{seat}@agent-skills.invalid"
    return {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}


def commit_partition(directory: Path) -> tuple[str, str, str, bool]:
    """Commit this seat's partition onto its local chain when it changed: the key, the seat, the chain's tip, and
    whether a commit was made."""
    tool = _state_tool()
    partition, journal_text = build_partition(directory)
    key, seat = _key_of(directory), partition["seat"]
    ref = partition_ref(key, seat)
    texts = {JOURNAL_FILE: journal_text, PARTITION_FILE: tool.dumps(partition)}
    digest = tool.value_digest(texts)
    parent = _revision(directory, ref)
    tip_file = directory / TIP_FILE
    with contextlib.suppress(OSError, ValueError, KeyError):
        tip = json.loads(tip_file.read_text(encoding="utf-8"))
        if parent is not None and tip == {"commit": parent, "digest": digest}:
            return key, seat, parent, False
    with tempfile.TemporaryDirectory(dir=directory) as folder:
        for name, text in texts.items():
            (Path(folder) / name).write_text(text, encoding="utf-8")
        hashed = _git(directory, "hash-object", "-w", *(str(Path(folder) / name) for name in sorted(texts))).stdout.split()
    tree = _git(directory, "mktree", data="".join(f"100644 blob {blob}\t{name}\n"
                                                 for name, blob in zip(sorted(texts), hashed))).stdout.strip()
    if parent is not None and _git(directory, "rev-parse", f"{parent}^{{tree}}").stdout.strip() == tree:
        tool._write_atomically(tip_file, tool.dumps({"commit": parent, "digest": digest}))
        return key, seat, parent, False
    commit = _git(directory, "commit-tree", tree, *(["-p", parent] if parent else []), "-m",
                  f"{seat} through {partition['through']}", environment=_identity(directory, seat)).stdout.strip()
    _git(directory, "update-ref", ref, commit, parent or ZERO)
    tool._write_atomically(tip_file, tool.dumps({"commit": commit, "digest": digest}))
    return key, seat, commit, True


def _ancestor(repo: Path, older: str, newer: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def _mark_split(directory: Path) -> None:
    tool = _state_tool()
    with tool.file_lock(directory / tool.STATE_LOCK), _as_sync():
        current = tool.read_state(directory, for_write=True)
        split = copy.deepcopy(current)
        split["meta"]["collab"]["heldReason"] = "seat_split"
        tool._with_state(directory, current, split)


def push(directory: Path, *, remote: str) -> dict:
    """Push this seat's partition to ``remote``, fast-forward only, never forced. A rejected push reads the remote's
    chain again: behind it, the push is tried again; diverged from it, this checkout is split from its seat."""
    tool = _state_tool()
    collab = tool.collab_of(tool.read_state(directory))
    if collab is not None and collab.get("heldReason") == "seat_split":
        raise _refuse("collab.seat_split", "this checkout's seat chain diverged from the remote's: it pushes nothing more")
    key, seat, tip, committed = commit_partition(directory)
    ref, mirror = partition_ref(key, seat), peer_ref(key, seat)
    for attempt in range(tool.RECONCILE_ATTEMPTS + 1):
        # The remote's chain as last read, from the fetch or from this seat's own last push.
        if _git(directory, "for-each-ref", "--format=%(objectname)", mirror).stdout.strip() == tip:
            return {"ok": True, "seat": seat, "head": tip, "committed": committed, "pushed": False}
        pushed = _git(directory, "push", "--porcelain", "--quiet", remote, f"{ref}:{ref}", check=False)
        if pushed.returncode == 0:
            _git(directory, "update-ref", mirror, tip)
            return {"ok": True, "seat": seat, "head": tip, "committed": committed, "pushed": True}
        if not any(line.startswith("!") for line in pushed.stdout.splitlines()):
            raise _refuse("collab.push_failed", f"git push to {remote} failed: {pushed.stderr.strip()[-400:]}")
        _git(directory, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", remote, f"+{ref}:{mirror}")
        theirs = _revision(directory, mirror)
        if theirs is not None and not _ancestor(directory, theirs, tip):
            # Whichever chain reached the remote first is the seat's: this one is kept aside, and this checkout stops.
            _git(directory, "update-ref", split_ref(key, seat), tip)
            _mark_split(directory)
            raise _refuse("collab.seat_split", f"seat {seat}'s chain on {remote} is not one this checkout wrote: "
                          f"another checkout writes as {seat}. This one's chain is kept at {split_ref(key, seat)}, and "
                          "collab join --new-seat gives this checkout a fresh seat")
    raise _refuse("collab.push_failed", f"git push to {remote} was rejected {tool.RECONCILE_ATTEMPTS + 1} times")


def fetch(directory: Path, *, remote: str, key: str | None = None) -> list[str]:
    """Fetch every seat's partition of ``key`` from ``remote`` by explicit refspec, into the peer namespace, and return
    the seats it holds. No branch or tag namespace and no configured refspec is touched."""
    key = _check_key(key or _key_of(directory))
    directory.mkdir(parents=True, exist_ok=True)
    _git(directory, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", remote,
         f"+{REFS}/{key}/*:{PEER_REFS}/{key}/*")
    listed = _git(directory, "for-each-ref", "--format=%(refname)", f"{PEER_REFS}/{key}/").stdout.split()
    return sorted(name.rsplit("/", 1)[1] for name in listed)


def _blobs(repo: Path, names: list[str]) -> list[str | None]:
    """Each named blob's text, read in one git process, or None where there is no such blob."""
    found = subprocess.run(["git", "cat-file", "--batch"], cwd=repo, input="".join(f"{name}\n" for name in names).encode("utf-8"),
                           capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=False)
    data, texts = found.stdout, []
    for _ in names:
        header, _, data = data.partition(b"\n")
        parts = header.split()
        if len(parts) != 3 or parts[1] != b"blob":
            texts.append(None)
            continue
        size = int(parts[2])
        texts.append(data[:size].decode("utf-8"))
        data = data[size + 1:]
    return texts


def read_partition(repo: Path, ref: str) -> tuple[dict, list[dict]]:
    """A fetched partition, checked as delivery-partition/1, and its entries; ValueError when it is not one."""
    tool = _state_tool()
    blobs = _blobs(repo, [f"{ref}:{PARTITION_FILE}", f"{ref}:{JOURNAL_FILE}"])
    if None in blobs:
        raise ValueError("the partition commit lacks partition.json or journal.jsonl")
    partition = json.loads(blobs[0])
    lines = type("Lines", (), {"stdout": blobs[1]})
    errors = tool.validate(partition, tool.load_schema(PARTITION))
    if errors:
        raise ValueError("; ".join(errors))
    entries = [json.loads(line) for line in lines.stdout.splitlines() if line]
    if len(entries) != partition["through"] or any(not isinstance(entry, dict) or not isinstance(entry.get("origin"), dict)
                                                   for entry in entries):
        raise ValueError("its journal does not hold its entries 1 to through, each with its origin")
    return partition, entries


# Ingest.

def _matches(reference: object, origin: dict, digest: str) -> bool:
    """Whether ``reference``, a write's ``replaces``, names the value at ``origin`` whose digest is ``digest``."""
    return (isinstance(reference, dict) and reference.get("seat") == origin["seat"]
            and reference.get("seq") == origin["seq"] and reference.get("digest") == digest)


def supersedes(incoming: dict, incoming_digest: str, local: dict, local_digest: str) -> bool:
    """Whether an incoming value replaces the local one at the same path. From the same seat, whose partition is its
    latest, it does. Otherwise the write that replaced the other is the later; between concurrent writes, the higher
    seat id, so every machine chooses the same one."""
    if incoming["seat"] == local["seat"]:
        return incoming["seq"] >= local["seq"]
    if _matches(incoming.get("replaces"), local, local_digest):
        return True
    if _matches(local.get("replaces"), incoming, incoming_digest):
        return False
    return incoming["seat"] > local["seat"]


def permitted(journal: list[dict], seat: str) -> bool:
    """Whether ``seat`` may write the record's values at all: the seat that started it, or one the delivery holder
    admitted. Each value is then checked against the scopes that may write it."""
    return (any(entry["kind"] == "record.started" and entry["origin"]["seat"] == seat for entry in journal)
            or (_state_tool().derived_seats(journal).get(seat) or {}).get("admitted", False))


def _superseding(element: dict | None, checks: object) -> bool:
    """Whether ``checks`` is ``element``'s checks with only those still running or pending marked superseded, as a
    hand-off that ends a failed run leaves them."""
    held = (element or {}).get("checks")
    if not isinstance(held, list) or not isinstance(checks, list) or len(held) != len(checks):
        return False
    return all(isinstance(new, dict) and (new == old or old.get("state") in ("running", "pending")
                                          and new == {**old, "state": "superseded"}) for old, new in zip(held, checks))


def _find(state: dict, array: str, key: str, value: str) -> dict | None:
    return next((element for element in state.get(array, []) if element.get(key) == value), None)


def _apply_slice(state: dict, origins: dict, journal: list[dict], seat: str, partition: dict, through: int) -> bool:
    """Apply one peer's slice to ``state`` and ``origins``, as far as its entries are ingested; whether it changed."""
    tool = _state_tool()
    if not permitted(journal, seat):
        return False
    local_ids = {(origin["seat"], origin["seq"]): local for local, origin in tool.journal_origins(journal).items()}
    origins_of = tool.journal_origins(journal)
    removed_digest = tool.value_digest(tool.REMOVED_VALUE)

    def held_digest(path: str) -> str:
        """The digest of the value this record holds at ``path``, as a partition carries it."""
        if origins.get(path, {}).get("removed"):
            return removed_digest
        values = tool.partition_paths(state)
        return tool.value_digest(tool.carried_value(path, values[path], origins_of)) if path in values else removed_digest

    def record(path: str, item: dict, removed: bool) -> None:
        origins[path] = {"seat": seat, "seq": item["origin"]["seq"],
                         **({"replaces": item["replaces"]} if "replaces" in item else {}),
                         **({"removed": True} if removed else {})}

    entries = [item for item in partition["slice"]
               if item["origin"]["seat"] == seat and item["origin"]["seq"] <= through]
    # The lane each element in the slice names, for an element this record does not hold yet.
    lanes = {item["path"][:-len("/lane")]: item.get("value") for item in entries if item["path"].endswith("/lane")}
    # Each queue element's stage before this slice, which a re-hand-off is judged by however the slice moves it; and a
    # checking element only a hand-off that ends a failed run may reset: one of its checks failed, and the same write
    # moves its head.
    stages = {f"queue[subject={item['subject']}]": item.get("stage") for item in state.get("queue", [])}
    for item in state.get("queue", []):
        selector = f"queue[subject={item['subject']}]"
        moved = next((entry for entry in entries if entry["path"] == f"{selector}/head"
                      and entry.get("value") != item.get("head")), None)
        if item.get("stage") == "checking" and not (
                moved is not None and any(check.get("state") in FAILING for check in item.get("checks", []))):
            stages[selector] = "running"
    # The seq of the write that made each element this slice makes.
    created = {item["path"]: item["origin"]["seq"] for item in entries
               if item["path"].endswith("]") and not item.get("removed")}
    # Elements before their fields, so a field finds its element.
    entries.sort(key=lambda item: 0 if item["path"].endswith("]") else 1)
    changed = False
    for item in entries:
        path, origin = item["path"], {"seat": seat, "seq": item["origin"]["seq"]}
        written = {**origin, **({"seen": item["origin"]["seen"]} if "seen" in item["origin"] else {})}
        removed = item.get("removed", False)
        incoming = {**origin, **({"replaces": item["replaces"]} if "replaces" in item else {})}
        digest = removed_digest if removed else tool.value_digest(item["value"])
        local = origins.get(path)
        if local is not None and (local["seat"], local["seq"]) == (seat, origin["seq"]) and held_digest(path) == digest:
            continue  # applied already
        # D7: the value counts only if its seat held a scope that may write it when it wrote it.
        selector = (path.split("]/", 1)[0] + "]" if "]/" in path else path) if PATH.match(path) else None
        element = held_element = None
        if selector is not None:
            held_element = _find(state, *PATH.match(selector).group("array", "key", "value"))
            if held_element is None or "lane" not in held_element:
                # An element this record lacks, or makes from this slice, names its lane in the slice.
                element = {**(held_element or {}), "lane": lanes.get(selector)}
        scopes = tool.path_scopes(state, path, element)
        field = path.split("]/", 1)[1] if "]/" in path else None
        runner = selector is not None and selector.startswith("queue[") and tool.FIELD_MAP["queue"].get(field) == ("queue",)
        at_start = field in tool.QUEUE_START and item.get("value") == tool.QUEUE_START[field]
        # The write that makes an element sets its starting fields, as whoever may make it; a lane's re-hand-off puts
        # its queue element's runner fields back to their starting values, as the lane.
        # A re-hand-off is legal only for a stopped or queued element, or one checking whose failed run it ends at a new
        # head; ending that run marks the checks it was still running, or had yet to start, superseded.
        held_stage = stages.get(selector)
        reset = runner and held_stage in ("stopped", "queued", "checking") and (
            at_start or held_stage == "checking" and field == "checks" and _superseding(held_element, item.get("value")))
        if field is not None and (reset or created.get(selector) == origin["seq"] and (
                not runner or field == "order" or at_start)):
            scopes = tool.path_scopes(state, selector, element)
        if isinstance(scopes, str) or not any(tool.holds(journal, seat, scope, at=written) for scope in scopes):
            continue
        value = None
        if not removed:
            ready, value = _localized(path, item["value"], local_ids)
            if not ready:
                continue
        matched = PATH.match(path)
        if path.endswith("[]"):
            array = path[:-2]
            if local is not None and not supersedes(incoming, digest, local, held_digest(path)):
                continue
            if removed and state.get(array) == []:
                del state[array]
            elif not removed:
                state.setdefault(array, [])
        elif matched and matched.group("field") is None:
            array, key, name = matched.group("array"), matched.group("key"), matched.group("value")
            element = _find(state, array, key, name)
            if removed:
                if local is not None and local["seat"] != seat and not local.get("removed") \
                        and _matches(local.get("replaces"), origin, removed_digest):
                    continue  # made again after this removal
                if local is not None and local.get("removed") and not supersedes(incoming, digest, local, removed_digest):
                    continue
                if element is not None:
                    state[array].remove(element)
                for other in [name for name in origins if name.startswith(path + "/")]:
                    del origins[other]
            else:
                if local is not None and local.get("removed"):
                    # A removal wins over every write that does not follow it; a later write by a seat that may
                    # create the element makes it again.
                    if not (local["seat"] == seat or _matches(item.get("replaces"), local, removed_digest)):
                        continue
                elif local is not None and element is not None and not supersedes(incoming, digest, local, held_digest(path)):
                    continue
                if element is None:
                    state.setdefault(array, []).append({key: name})
        elif matched:
            array, key, name, field = (matched.group("array"), matched.group("key"), matched.group("value"),
                                       matched.group("field"))
            element = _find(state, array, key, name)
            if element is None:
                made = origins.get(f"{array}[{key}={name}]")
                if made is not None and made.get("removed"):
                    continue  # a write that does not follow the removal; the element's own entry would make it again
                element = {key: name}
                state.setdefault(array, []).append(element)
                origins.setdefault(f"{array}[{key}={name}]", dict(origin))
            if local is not None and not local.get("removed") and not supersedes(incoming, digest, local, held_digest(path)):
                continue
            if local is not None and local.get("removed") and not supersedes(incoming, digest, local, removed_digest):
                continue
            if removed:
                element.pop(field, None)
            else:
                element[field] = value
        else:
            target, name = (state["meta"], path[len("meta/"):]) if path.startswith("meta/") else (state, path)
            if local is not None and not supersedes(incoming, digest, local, held_digest(path)):
                continue
            if removed:
                target.pop(name, None)
            else:
                target[name] = value
        record(path, item, removed)
        changed = True
    return changed


def _localize(entry: dict, local_ids: dict[tuple, int], next_id: int) -> dict:
    """A peer's entry as this journal holds it: the next local id, and its links by local id."""
    local = {name: value for name, value in entry.items() if name not in ("because", "answers", "id")}
    local["id"] = next_id
    for link in ("because", "answers"):
        if entry.get(link):
            targets = [local_ids.get((target.get("seat"), target.get("seq"))) if isinstance(target, dict) else None
                       for target in entry[link]]
            if None in targets:
                raise ValueError(f"its {link} names an entry this journal does not hold")
            local[link] = targets
    return local


def _journal_hold(directory: Path, current: dict, journal: list[dict], seat: str, reason: str | None) -> None:
    """Journal that a peer's partition is held, or read again, while the caller holds the state lock."""
    tool = _state_tool()
    kind = "partition.resumed" if reason is None else "partition.held"
    data = {"seat": seat} if reason is None else {"seat": seat, "reason": reason}
    entry = {"id": len(journal) + 1, "at": tool.now(), "kind": kind, "lane": None, "subject": None, "data": data}
    held = [item["id"] for item in journal if item["kind"] == "partition.held" and item["data"]["seat"] == seat]
    if reason is None and held:
        entry["answers"] = [held[-1]]
    journal.append(tool._append_journal_locked(directory, current, journal, entry))


def _new_record(directory: Path, key: str) -> None:
    """An empty shared record for a checkout with none, for its first ingest to fill."""
    tool = _state_tool()
    tool.init(directory, "")
    with tool.file_lock(directory / tool.STATE_LOCK), _as_sync():
        current = tool.read_state(directory)
        shared = copy.deepcopy(current)
        shared["meta"]["collab"] = {"key": key, "seats": {}, "peers": {}}
        tool._with_state(directory, current, shared)


def ingest(directory: Path, *, key: str | None = None) -> dict:
    """Ingest every fetched peer partition into the local record as the sync writer; with ``key`` and no record yet,
    into a new one. Entries are ingested in causal order, each once every entry its seen names is here, and an
    unadmitted seat's only as far as its request. A partition from another writer generation is held."""
    tool = _state_tool()
    if not (directory / tool.STATE_FILE).exists():
        if key is None:
            raise _refuse("state_missing", f"no state at {directory}; name the shared record's key to ingest a new one")
        listed = _git(directory, "for-each-ref", "--format=%(refname)", f"{PEER_REFS}/{_check_key(key)}/").stdout.split()
        if not listed:
            raise _refuse("collab.nothing_fetched", f"no partition of {key} has been fetched")
        _new_record(directory, key)
    key = _key_of(directory)
    own = tool.local_seat(directory, key)
    # This seat's own chain is read only by a record that holds none of it, such as one rebuilt after it was lost.
    own_held = own is not None and tool.seat_counts(tool._raw_journal(directory)).get(own, 0) > 0
    loaded: dict[str, tuple[dict, list[dict]]] = {}
    held: dict[str, str] = {}
    for name in _git(directory, "for-each-ref", "--format=%(refname)", f"{PEER_REFS}/{key}/").stdout.split():
        seat = name.rsplit("/", 1)[1]
        if seat == own and own_held:
            continue
        try:
            loaded[seat] = read_partition(directory, name)
            if loaded[seat][0]["seat"] != seat:
                raise ValueError("it names another seat")
        except (ValueError, json.JSONDecodeError):
            held[seat] = "slice_invalid"
    ingested: list[dict] = []
    with tool.file_lock(directory / tool.STATE_LOCK), _as_sync():
        current = tool.read_state(directory, for_write=True)
        if current["meta"]["collab"].get("heldReason") == "seat_split":
            raise _refuse("collab.seat_split", "this checkout's seat split: it ingests nothing more")
        journal = tool.read_journal(directory)
        pin = (current["meta"].get("writerGeneration") or {}).get("archiveSha256")
        for seat, (partition, _) in loaded.items():
            if (partition["generation"] or {}).get("archiveSha256") != pin:
                held[seat] = "generation_differs"
        progress = True
        while progress:
            progress = False
            counts = tool.seat_counts(journal)
            for seat in sorted(set(loaded) - set(held)):
                for entry in loaded[seat][1][counts.get(seat, 0):]:
                    origin = entry["origin"]
                    if origin.get("seat") != seat or origin.get("seq") != counts.get(seat, 0) + 1:
                        held[seat] = "slice_invalid"
                        break
                    seen = origin.get("seen") if isinstance(origin.get("seen"), dict) else {}
                    if any(not isinstance(seq, int) or seq > counts.get(other, 0) for other, seq in seen.items()):
                        break  # waits for an entry another seat wrote
                    # A seat's own request, and the record's start, which makes its seat the first, need no admission.
                    request = (entry.get("kind") == "seat.requested" and (entry.get("data") or {}).get("seat") == seat
                               or entry.get("kind") == "record.started" and not journal)
                    if not request and not tool.derived_seats(journal).get(seat, {}).get("admitted") \
                            and not permitted(journal, seat):
                        break  # an unadmitted seat contributes only its request
                    try:
                        local = _localize(entry, {(origin["seat"], origin["seq"]): local for local, origin in tool.journal_origins(journal).items()}, len(journal) + 1)
                        written = tool._append_journal_locked(directory, current, journal, local)
                    except (ValueError, tool.StateError):
                        held[seat] = "slice_invalid"
                        break
                    journal.append(written)
                    ingested.append(written)
                    counts[seat] = origin["seq"]
                    progress = True
                    if written["kind"] in (tool.CLOSED_KIND, "base.repaired", *tool.SEAT_KINDS):
                        current = tool.read_state(directory)  # its append wrote the record
        current = tool.read_state(directory)
        origins = tool.read_field_origins(directory)
        updated = copy.deepcopy(current)
        changed = bool(ingested)
        counts = tool.seat_counts(journal)
        for seat in sorted(set(loaded) - set(held)):
            trial, trial_origins = copy.deepcopy(updated), copy.deepcopy(origins)
            if not _apply_slice(trial, trial_origins, journal, seat, loaded[seat][0], counts.get(seat, 0)):
                continue
            tool.sort_elements(trial, trial_origins, journal)
            if tool.state_errors(trial):
                held[seat] = "slice_invalid"
                continue
            updated, origins, changed = trial, trial_origins, True
        peers = updated["meta"]["collab"]["peers"]
        before = copy.deepcopy(peers)
        at = tool.now()
        for seat in sorted((set(loaded) | set(held)) - {own}):
            previous = before.get(seat) or {}
            through = counts.get(seat, 0)
            stale = True
            with contextlib.suppress(KeyError, ValueError):
                stale = time.time() - tool._parse_time(previous["fetchedAt"]).timestamp() >= FETCHED_REFRESH_SECONDS
            if changed or stale or previous.get("through") != through or previous.get("heldReason") != held.get(seat):
                peers[seat] = {"through": through, "fetchedAt": at, "heldReason": held.get(seat)}
        if updated != current:
            # The origins go first: the state's write orders its elements by them.
            tool._write_atomically(directory / tool.FIELD_ORIGINS_FILE, tool.dumps(origins))
            current = tool._with_state(directory, current, updated)
        if own is not None and (current["meta"]["collab"]["seats"].get(own) or {}).get("admitted"):
            for seat in sorted(set(before) | set(peers)):
                was, now = (before.get(seat) or {}).get("heldReason"), (peers.get(seat) or {}).get("heldReason")
                if was != now:
                    _journal_hold(directory, current, journal, seat, now)
    return {"ok": True, "ingested": [{"id": entry["id"], **_origin(entry)} for entry in ingested],
            "held": dict(sorted(held.items())), "state": tool.read_state(directory)}


def sync_once(directory: Path, *, remote: str) -> dict:
    """One pass of the sync loop: push this seat's partition, then fetch and ingest its peers'."""
    tool = _state_tool()
    collab = tool.collab_of(tool.read_state(directory))
    pushed = None
    seat = tool.local_seat(directory, collab["key"]) if collab is not None else None
    if seat is not None and (collab["seats"].get(seat) or {}).get("admitted") or \
            seat is not None and any(entry["kind"] == "seat.requested" and entry["data"]["seat"] == seat
                                     for entry in tool.read_journal(directory)):
        try:
            pushed = push(directory, remote=remote)
        except tool.StateError as error:
            if error.code == "collab.seat_split":
                raise
            # A remote that refuses this seat's push may still serve its peers': they are still read.
            fetch(directory, remote=remote)
            ingest(directory)
            raise
    fetch(directory, remote=remote)
    return {"ok": True, "pushed": pushed, **ingest(directory)}


def _changes_mark(directory: Path) -> tuple[int, int] | None:
    with contextlib.suppress(OSError):
        stat = (directory / _state_tool().CHANGES_FILE).stat()
        return stat.st_mtime_ns, stat.st_size
    return None


def serve(directory: Path, *, remote: str, poll: float = POLL_SECONDS, stop: Path | None = None,
          passes: int | None = None, sleep=time.sleep) -> int:
    """The sync loop: each poll, push this seat's partition if the record changed since the last push, and fetch and
    ingest its peers. A split seat stops it. ``passes`` bounds it, for a test; ``stop`` ends it once it exists."""
    if poll < MIN_POLL_SECONDS:
        raise _refuse("collab.poll_invalid", f"the poll is at least {MIN_POLL_SECONDS:g} seconds")
    pushed_at: tuple[int, int] | None | bool = False
    done = 0
    while True:
        try:
            mark = _changes_mark(directory)
            if mark != pushed_at:
                result = sync_once(directory, remote=remote)
                pushed_at = mark
            else:
                fetch(directory, remote=remote)
                result = ingest(directory)
            if result["ingested"]:
                print(json.dumps({"ingested": result["ingested"], "held": result["held"]}), flush=True)
        except _state_tool().StateError as error:
            print(json.dumps({"ok": False, "code": error.code, "error": str(error)}), file=sys.stderr, flush=True)
            if error.code == "collab.seat_split":
                return 1
        done += 1
        if (passes is not None and done >= passes) or (stop is not None and stop.exists()):
            return 0
        sleep(poll)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    loop = commands.add_parser("serve", help="push this seat's partition and ingest its peers', every poll")
    loop.add_argument("--dir", type=Path, required=True, help="the shared record's state directory")
    loop.add_argument("--remote", required=True, help="the binding's base remote, where the partitions live")
    loop.add_argument("--poll", type=float, default=POLL_SECONDS, help=f"seconds between passes, at least {MIN_POLL_SECONDS:g}")
    loop.add_argument("--stop", type=Path, help="end once this file exists")
    loop.add_argument("--passes", type=int, help="end after this many passes")
    args = parser.parse_args(argv)
    try:
        return serve(args.dir, remote=args.remote, poll=args.poll, stop=args.stop, passes=args.passes)
    except _state_tool().StateError as error:
        print(json.dumps({"ok": False, "code": error.code, "error": str(error)}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
