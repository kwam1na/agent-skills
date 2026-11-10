#!/usr/bin/env python3
"""The tracker publisher: posts every tracker update the orchestration guide names, and makes every move the
configuration names, once each, through a `tracker-adapter/1` command (references/config.md).

    python3 -B tracker_publisher.py --state-dir <dir> [--repo <checkout>] [--delivery <key>]

The binding names it in `state.publishers`, with `{stateDirectory}` for `--state-dir`, so it runs whenever the render
wrapper runs and reads the record the wrapper shows it on stdin. It keeps `tracker-published.jsonl` beside the record:
one line per journal id and outcome (`comment`, `transition`, `create`, `skip`, `handled`, `failed`). A run that
finds another holding the ledger's lock exits 0 and leaves the entries to it. A transient failure stops the run, to be
retried from there by the next; a permanent one writes `failed` and the run goes on. run_delivery.py imports the
ledger, lock, mark and settings helpers from here, so lane and lead commands write the same ledger.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import contextlib
import datetime
import fcntl
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

CONTRACT = "tracker-adapter/1"
LEDGER = "tracker-published.jsonl"
LOCK = "tracker-published.lock"
# The last transient failure, for "Needs you"; removed by a run that reaches the end of the journal.
ERROR_FILE = "tracker-publisher.json"
# Flow-log files, named with the delivery key as every lead-side log is.
MARKS = "tracker-marks"
LANE_STARTS = "lane-starts"
OUTCOMES = ("comment", "transition", "create", "skip", "handled", "failed")
ACTIONS = ("comment", "transition", "create")
# When the lane's review move is made (C5): once the gates pass at the PR's head, as the PR opens, or once the
# pipeline's review is current and its checks green. Absent, the lane commands print the move for the lane to make.
REVIEW_WHEN = ("gates-passed", "pr-open", "pipeline-green")
# A lane command's review move journals nothing: its ledger lines carry this journal id, which covers no entry.
LANE_MOVE = 0
ADAPTER_TIMEOUT_SECONDS = 60
# Statuses retried by a later run; any other 4xx is permanent. A 403 is transient only when the site refuses it too.
TRANSIENT = (401, 409, 423, 429)
LINEAR_TOKEN_ENV = "LINEAR_API_KEY"
# What an earlier generation's `ack` left beside a record whose updates it posted by hand: the last journal entry.
PUBLISHED = "published.json"
SEEDED = "published before the tracker publisher"
JIRA_KEY = re.compile(r"^([A-Z][A-Z0-9_]*)-[0-9]+$")
TOOL = Path(__file__).resolve().parent.parent

# Each journal kind's update: where it goes ("ticket", "epic", "ticket-or-epic"), or None for no update, and the
# heading it carries. From the orchestration guide's tracker-update table.
UPDATES: dict[str, tuple[str, str] | None] = {
    "lane.assigned": ("ticket", "Started"),
    "review.round": None,
    "review.evidence": None,
    "candidate.handed": ("ticket", "Handed off and queued"),
    "check.started": None,
    "check.finished": None,
    "check.failed": None,
    "check.retried": ("ticket-or-epic", "Check retried"),
    "check.reused": None,
    "check.selected": None,
    "selection.refused": None,
    "candidate.merged": ("ticket", "Merged"),
    "candidate.stopped": ("ticket", "Stopped"),
    "base.verified": None,
    "base.broken": ("epic", "The delivery base is broken; the queue is stopped"),
    "base.repaired": ("epic", "The delivery base is repaired"),
    "base.rerun.started": None,
    "base.rerun.finished": None,
    "lease.granted": ("ticket-or-epic", "Lease granted"),
    "lease.released": ("ticket-or-epic", "Lease released"),
    "release.started": None,
    "release.finished": ("epic", "Released"),
    "finish.met": ("epic", "Finish line met"),
    "decision.recorded": ("ticket-or-epic", "Decision"),
    "blocker.recorded": ("ticket-or-epic", "Blocked"),
    "delivery.closed": ("epic", "Delivery closed"),
}
# Kinds the guide gives a move to; with no `lead` moves for one, its update says the move belongs to people.
MOVING = ("candidate.merged", "delivery.closed")
# What ends a story's wait for its start: its hand-off or its merge.
PAST_START = ("candidate.handed", "candidate.merged")


class Stop(Exception):
    """A refusal: the configuration or the record can't be published."""


class Transient(Exception):
    """A failure the next run retries from."""


class Permanent(Exception):
    """A failure that won't pass on a retry: written as `failed`, never retried."""


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def moment(text: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))


# The ledger, its lock and the flow-log marks: shared with run_delivery.py's lane and lead commands.

@contextlib.contextmanager
def ledger_lock(state_dir: Path, wait: bool):
    """The ledger's lock. Yields whether it was taken: a try (wait=False) yields False while another run holds it."""
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / LOCK, "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_ledger(state_dir: Path) -> list[dict]:
    path = state_dir / LEDGER
    if not path.exists():
        return []
    lines = []
    for text in path.read_text(encoding="utf-8").splitlines():
        # A line a crash cut short is ignored: its entry is processed again, and the markers keep that from posting twice.
        with contextlib.suppress(ValueError):
            line = json.loads(text)
            if isinstance(line, dict) and isinstance(line.get("journal"), int) and line.get("outcome") in OUTCOMES:
                lines.append(line)
    return lines


def _fresh_line(path: Path) -> str:
    """A newline first when a crash left the file's last line unended, so the cut line never swallows the next."""
    try:
        with open(path, "rb") as stream:
            stream.seek(-1, os.SEEK_END)
            return "" if stream.read(1) == b"\n" else "\n"
    except OSError:
        return ""


def append_ledger(state_dir: Path, line: dict) -> dict:
    """Add one line; the caller holds the ledger's lock. Every line names the record's `init` id, so a ledger left
    beside a replaced record never covers the new record's entries."""
    if line.get("outcome") not in OUTCOMES or not isinstance(line.get("journal"), int):
        raise ValueError(f"a ledger line needs a journal id and one of {', '.join(OUTCOMES)}")
    if not isinstance(line.get("init"), str):
        raise ValueError("a ledger line names the record's init id (init_id of its journal)")
    line = {**line, "at": now()}
    with open(state_dir / LEDGER, "a", encoding="utf-8") as ledger:
        ledger.write(_fresh_line(state_dir / LEDGER) + json.dumps(line, ensure_ascii=True, sort_keys=True) + "\n")
        ledger.flush()
        os.fsync(ledger.fileno())
    return line


def covered(ledger: list[dict], init: str) -> set[int]:
    """The journal ids of the record with this init id that the ledger covers: every line a command wrote, and every
    publisher line that ends its entry."""
    return {line["journal"] for line in ledger
            if line.get("init") == init and (line.get("writer") != "publisher" or line.get("final"))}


def flow_file(flow_log: Path, delivery: str, name: str) -> Path:
    return flow_log / f"{delivery}.{name}.jsonl"


def read_flow(flow_log: Path, delivery: str, name: str) -> list[dict]:
    path = flow_file(flow_log, delivery, name)
    if not path.exists():
        return []
    entries = []
    for text in path.read_text(encoding="utf-8").splitlines():
        with contextlib.suppress(ValueError):
            entry = json.loads(text)
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def append_flow(flow_log: Path, delivery: str, name: str, entry: dict) -> dict:
    flow_log.mkdir(parents=True, exist_ok=True)
    entry = {**entry, "at": now()}
    with open(flow_file(flow_log, delivery, name), "a", encoding="utf-8") as log:
        log.write(_fresh_line(flow_file(flow_log, delivery, name)) + json.dumps(entry, sort_keys=True) + "\n")
        log.flush()
        os.fsync(log.fileno())
    return entry


def mark(flow_log: Path, delivery: str, journal_id: int, followup: dict | None = None) -> dict:
    """Mark a `decision.recorded` entry as one to post: the marking command holds the ledger's lock, waiting for it,
    across its journal write and this mark. A deferral names its follow-up, {story, finding, title}, to be filed."""
    entry: dict = {"journal": journal_id}
    if followup is not None:
        entry["followup"] = {key: followup[key] for key in ("story", "finding", "title")}
    return append_flow(flow_log, delivery, MARKS, entry)


def record_lane_start(flow_log: Path, delivery: str, subject: str, lane: str | None = None) -> dict:
    """A story's start: what its `lane.assigned` waits for before its start move and update."""
    return append_flow(flow_log, delivery, LANE_STARTS, {"subject": subject, **({"lane": lane} if lane else {})})


def followup_label(story: str, finding: str) -> str:
    """The marker a created follow-up carries, searchable without an app: lowercased, as Jira labels require."""
    return re.sub(r"[^a-z0-9_.-]+", "-", f"agent-followup-{story}-{finding}".lower())


def init_id(journal: list[dict]) -> str:
    """The record's init id: the time of its first journal entry, the "Record starts at base" decision."""
    return re.sub(r"[^0-9A-Za-z]", "", journal[0]["at"]) if journal else "0"


def marker(delivery: str, init: str, journal_id: int, action: str) -> str:
    return f"agent-delivery:{delivery}:{init}:{journal_id}:{action}"


# Settings: `tracker` in .agents/run-delivery.json, with Jira's derived from the binding's tracker snapshot.

def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def own_config(root: Path) -> tuple[dict, str]:
    explicit = os.environ.get("RUN_DELIVERY_CONFIG") or os.environ.get("DELIVERY_FLOW_CONFIG")
    if explicit:
        return _json(Path(explicit)), explicit
    for rel in (".agents/run-delivery.json", ".agents/flow.json"):
        if (root / rel).exists():
            return _json(root / rel), rel
    return {}, ".agents/run-delivery.json"


def _flag(argv: list, name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def settings(root: Path, own: dict | None = None, binding: dict | None = None) -> dict:
    """How this repository's tracker is updated:
    {mode: publisher|manual|None, adapter: {name, argv?, site?, userEnv?, tokenEnv?} | None, project, transitions,
    publisher: the binding's tracker publisher id | None, unrunnable: the ids of publishers this generation can't run,
    problems}."""
    named = None
    if own is None:
        own, named = own_config(root)
    binding = _json(root / ".agents/orchestration.json") if binding is None else binding
    tracker = own.get("tracker") or {}
    problems = []
    if not isinstance(tracker, dict):
        return {"mode": None, "adapter": None, "project": None, "transitions": {}, "publisher": None, "unrunnable": [],
                "snapshot": False,
                "problems": [f"{named or 'run-delivery.json'} tracker must be an object"]}
    # A binding of the wrong shape is the schema check's to refuse: it reads here as one naming nothing.
    state = binding.get("state") if isinstance(binding, dict) else None
    state = state if isinstance(state, dict) else {}
    reader = state.get("trackerSnapshot")
    snapshot = reader.get("argv") if isinstance(reader, dict) else None
    snapshot = snapshot if isinstance(snapshot, list) and all(isinstance(part, str) for part in snapshot) else []
    kind = _flag(snapshot, "--kind")
    adapter = None
    if tracker.get("argv") is not None:
        if not isinstance(tracker["argv"], list) or not tracker["argv"] or not all(isinstance(a, str) for a in tracker["argv"]):
            problems.append("tracker.argv must be a non-empty list of strings")
        else:
            adapter = {"name": "argv", "argv": tracker["argv"]}
    elif tracker.get("adapter") not in (None, "jira", "linear"):
        problems.append(f"tracker.adapter {tracker['adapter']!r} isn't one run-delivery ships (jira, linear); give "
                        "tracker.argv")
    elif tracker.get("adapter") == "linear" or (tracker.get("adapter") is None and kind == "linear"):
        # A Linear snapshot implies Linear's adapter, as a Jira one implies Jira's: the same site and personal key,
        # or the adapter's own defaults when neither names them.
        linear = kind == "linear"
        adapter = {"name": "linear", "site": tracker.get("site") or (_flag(snapshot, "--site") if linear else None),
                   "tokenEnv": tracker.get("tokenEnv") or (_flag(snapshot, "--token-env") if linear else None)
                   or LINEAR_TOKEN_ENV}
    elif tracker.get("adapter") == "jira" or kind == "jira":
        adapter = {"name": "jira", "site": tracker.get("site") or _flag(snapshot, "--site"),
                   "userEnv": tracker.get("userEnv") or _flag(snapshot, "--user-env"),
                   "tokenEnv": tracker.get("tokenEnv") or _flag(snapshot, "--token-env")}
        missing = [field for field in ("site", "tokenEnv") if not adapter[field]]
        if missing:
            problems.append(f"the Jira tracker adapter needs tracker.{' and tracker.'.join(missing)} (or a jira "
                            "trackerSnapshot in the binding that names them)")
    updates = tracker.get("updates")
    if updates not in (None, "publisher", "manual"):
        problems.append(f"tracker.updates is publisher or manual, not {updates!r}: every tracker posts through the "
                        "tracker publisher, which lead-setup adds, unless its updates are made by hand")
        updates = None
    mode = updates or ("publisher" if adapter else None)
    if mode == "publisher" and adapter is None and not problems:
        problems.append("tracker.updates is publisher, but no tracker adapter is configured (tracker.adapter or tracker.argv)")
    publisher = None
    unrunnable = []
    entries = state.get("publishers")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        if "kind" in entry and "argv" not in entry:
            # A publisher that runs nothing: this generation leaves no outbox for anyone to post.
            unrunnable.append(entry.get("id"))
        elif isinstance(entry.get("argv"), list) and any(
                isinstance(part, str) and part.endswith("tracker_publisher.py") for part in entry["argv"]):
            publisher = entry.get("id")
    transitions = tracker.get("transitions") or {}
    if not isinstance(transitions, dict):
        problems.append("tracker.transitions must be an object")
        transitions = {}
    for part in ("start", "review"):
        value = transitions.get(part, [])
        if not isinstance(value, list) or not all(isinstance(name, str) and name for name in value):
            problems.append(f"tracker.transitions.{part} must be a list of transition names")
    when = transitions.get("reviewWhen")
    if when is not None and when not in REVIEW_WHEN:
        problems.append(f"tracker.transitions.reviewWhen must be one of {', '.join(REVIEW_WHEN)}, not {when!r}")
    elif when == "pipeline-green" and not (own.get("review") or {}).get("pr"):
        problems.append("tracker.transitions.reviewWhen is pipeline-green, which waits for the pipeline's review, but "
                        "review.pr declares none")
    targets = transitions.get("targets", {})
    if not isinstance(targets, dict) or not all(isinstance(v, str) and v for v in targets.values()):
        problems.append("tracker.transitions.targets must map transition names to the status each moves to")
    lead = transitions.get("lead", {})
    if not isinstance(lead, dict) or not all(isinstance(names, list) and all(isinstance(n, str) and n for n in names)
                                             for names in lead.values()):
        problems.append("tracker.transitions.lead must map journal kinds to lists of transition names")
    elif set(lead) - set(UPDATES):
        problems.append(f"tracker.transitions.lead names kinds the journal doesn't have: {sorted(set(lead) - set(UPDATES))}")
    return {"mode": mode, "adapter": adapter, "project": tracker.get("project"), "transitions": transitions,
            "snapshot": bool(snapshot),
            "followupType": tracker.get("followupType", "Task"), "publisher": publisher, "unrunnable": unrunnable,
            "problems": problems}


def untargeted(conf: dict) -> list[str]:
    """The configured moves with no `targets` entry: one a person already made reads as `handled` on a tracker that
    lists only the moves out of a ticket's status, as Jira does."""
    if conf.get("problems"):
        return []  # A malformed `tracker` is refused first; its moves can't be read.
    transitions = conf.get("transitions") or {}
    targets = transitions.get("targets") or {}
    names = [*(transitions.get("start") or []), *(n for moves in (transitions.get("lead") or {}).values() for n in moves)]
    return sorted({name for name in names if name not in targets})


def refusal(conf: dict) -> str | None:
    """Why this repository's tracker updates would be forgotten, with its fix, or None: doctor's row and lead-init's
    refusal."""
    if conf.get("unrunnable"):
        return (f"the binding names a publisher that runs nothing ({', '.join(map(str, conf['unrunnable']))}: a kind "
                "and no argv), which this generation doesn't support; fix: replace it in state.publishers with "
                "run-delivery's tracker publisher (lead-setup adds it), and export the tracker's token")
    if conf["problems"]:
        return "; ".join(conf["problems"]) + "; fix: correct `tracker` in the configuration (references/config.md)"
    if conf["mode"] == "publisher" and not conf["publisher"]:
        return ("the tracker has an adapter, but the binding has no tracker publisher, so nothing posts the delivery's "
                "updates and moves to it; fix: add one to state.publishers (lead-setup adds run-delivery's tracker "
                "publisher), or set tracker.updates to \"manual\" to post them by hand")
    if conf["mode"] is None and conf.get("snapshot") and not conf["publisher"]:
        # A tracker the binding reads with no adapter run-delivery knows: some publisher must post to it.
        return ("the tracker is configured, but the binding has no tracker publisher, so nothing posts the delivery's "
                "moves to it; fix: give the tracker an adapter (tracker.argv) and add run-delivery's tracker "
                "publisher to state.publishers (lead-setup adds it), or set tracker.updates to \"manual\"")
    return None


def publisher_entry(release_tool: str) -> dict:
    """The binding's `state.publishers` entry lead-setup adds: this script, from the release the repository runs."""
    return {"id": "tracker", "argv": ["python3", "-B", release_tool, "--state-dir", "{stateDirectory}"],
            "timeoutSeconds": 600}


def project_of(conf: dict, delivery: str | None) -> str | None:
    if conf.get("project"):
        return conf["project"]
    match = JIRA_KEY.match(delivery or "")
    return match.group(1) if match else None


# The adapter.

class Adapter:
    """One `tracker-adapter/1` command. Each operation prints one JSON object; a failure exits non-zero and prints
    {"error", "httpStatus"}, httpStatus null for a network error or a timeout."""

    def __init__(self, conf: dict, root: Path, environ: dict | None = None):
        spec = conf["adapter"]
        if spec is None:
            raise Stop("no tracker adapter is configured")
        if spec["name"] == "jira":
            self.prefix = [sys.executable, "-B", str(TOOL / "adapters" / "tracker_jira.py"), "--site", spec["site"],
                           "--token-env", spec["tokenEnv"], *(["--user-env", spec["userEnv"]] if spec.get("userEnv") else [])]
        elif spec["name"] == "linear":
            self.prefix = [sys.executable, "-B", str(TOOL / "adapters" / "tracker_linear.py"),
                           *(["--site", spec["site"]] if spec.get("site") else []), "--token-env", spec["tokenEnv"]]
        else:
            self.prefix = [os.path.expanduser(part) for part in spec["argv"]]
        self.root, self.environ = root, environ if environ is not None else dict(os.environ)
        self.limits: dict[str, int] = {}

    def call(self, *args: str) -> dict:
        try:
            done = subprocess.run([*self.prefix, *args], capture_output=True, text=True, cwd=self.root, env=self.environ,
                                  timeout=ADAPTER_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise Transient(f"the tracker adapter's {args[0]} timed out after {ADAPTER_TIMEOUT_SECONDS}s") from None
        except OSError as error:
            raise Transient(f"the tracker adapter {self.prefix[0]} could not run: {error}") from None
        try:
            answer = json.loads(done.stdout or "null")
        except ValueError:
            answer = None
        if done.returncode == 0 and isinstance(answer, dict):
            return answer
        if done.returncode == 0:
            raise Transient(f"the tracker adapter's {args[0]} printed no JSON object (ran {' '.join(self.prefix)})")
        status = answer.get("httpStatus") if isinstance(answer, dict) else None
        reason = (answer.get("error") if isinstance(answer, dict) else None) or (done.stderr or done.stdout).strip()[-300:]
        failure = AdapterFailure(f"{args[0]}: {reason or f'exited {done.returncode}'}", status)
        raise failure

    def status(self, ticket: str) -> dict:
        answer = self.call("status", "--ticket", ticket)
        if isinstance(answer.get("commentLimit"), int):
            self.limits["comment"] = answer["commentLimit"]
        return answer

    def markers(self, ticket: str) -> set[str]:
        return set(self.call("markers", "--ticket", ticket).get("markers") or [])

    def labelled(self, project: str, label: str) -> list[str]:
        return [item["key"] for item in self.call("markers", "--project", project, "--marker", label).get("items") or []]


class AdapterFailure(Exception):
    def __init__(self, message: str, status: int | None):
        super().__init__(message)
        self.status = status


def crash_seam(action: str) -> None:
    """For tests: die after the tracker took an action and before its ledger line, as a killed run would."""
    if os.environ.get("TRACKER_PUBLISHER_EXIT_AFTER") == action:
        os._exit(9)


# What each entry gets.

def units(text: str) -> int:
    """Length as trackers count it: UTF-16 code units (Jira's 32,767 is one)."""
    return len(text.encode("utf-16-le")) // 2


def cut(text: str, limit: int) -> str:
    return text.encode("utf-16-le")[:2 * max(0, limit)].decode("utf-16-le", errors="ignore")


def body_of(entry: dict, heading: str, notes: list[str], delivery: str, limit: int | None) -> str:
    who = f" ({entry['lane']})" if entry.get("lane") else ""
    text = f"{heading}{who}: {entry.get('summary', '')}"
    if notes:
        text += "\n\n" + "\n".join(notes)
    footer = f"\n\n{delivery} delivery record, journal entry {entry['id']} ({entry['kind']})."
    if limit is not None and units(text) + units(footer) > limit:
        pointer = (f"\n\n[Cut to the tracker's {limit}-character limit; the rest is in the {delivery} delivery "
                   f"record, journal entry {entry['id']}.]")
        text = cut(text, limit - units(pointer) - units(footer)) + pointer
    return cut(text + footer, limit) if limit is not None else text + footer


class Publisher:
    def __init__(self, doc: dict, state_dir: Path, conf: dict, adapter, delivery: str, flow_log: Path,
                 writer: str = "publisher"):
        # writer: "publisher", or "lane" when a lane command makes a move with these rules. A lane's moves share the
        # journal id LANE_MOVE, so their lines are told apart by ticket.
        self.writer = writer
        self.ticket: str | None = None
        self.state, self.journal = doc["state"], sorted(doc["journal"], key=lambda e: e["id"])
        self.state_dir, self.conf, self.adapter, self.delivery, self.flow_log = state_dir, conf, adapter, delivery, flow_log
        self.epic = self.state.get("meta", {}).get("trackerParent") or None
        self.project = project_of(conf, self.epic or delivery)
        self.init = init_id(self.journal)
        self.marks = {m["journal"]: m for m in read_flow(flow_log, delivery, MARKS) if isinstance(m.get("journal"), int)}
        self.starts = read_flow(flow_log, delivery, LANE_STARTS)
        self.held = {item["subject"] for item in [*self.state.get("queue", []), *self.state.get("planned", [])]}
        self.ledger = read_ledger(state_dir)
        self.posted = 0

    # The ledger.

    def write(self, entry: dict, outcome: str, final: bool = False, **detail) -> dict:
        line = append_ledger(self.state_dir, {"journal": entry["id"], "init": self.init, "outcome": outcome,
                                              "writer": self.writer, "kind": entry["kind"],
                                              **({"final": True} if final else {}), **detail})
        self.ledger.append(line)
        return line

    def done(self, entry: dict, action: str) -> dict | None:
        """This entry's line for the action, from a run that stopped before the entry ended, or None."""
        return next((line for line in self.ledger if line["journal"] == entry["id"] and line.get("init") == self.init
                     and line.get("action") == action and (self.ticket is None or line.get("ticket") == self.ticket)),
                    None)

    # The start wait (C3).

    def start_decision(self, entry: dict) -> str | None:
        """None to act; "wait" while the story hasn't started; otherwise the skip reason."""
        subject = entry.get("subject")
        if not subject:
            return "no story to start"
        for line in self.ledger:
            if (line.get("ticket") == subject and line.get("move") == "review"
                    and line["outcome"] in ("transition", "skip", "handled")):
                # A review move owed (`failed`) isn't one made: the start still goes ahead.
                return "the story is already in review"
            if line.get("ticket") == subject and line.get("move") == "start" and line["outcome"] == "transition":
                return "the story already started"
        at = moment(entry["at"])
        started = [moment(s["at"]) for s in self.starts if s.get("subject") == subject and moment(s["at"]) >= at]
        later = [e for e in self.journal if e["id"] > entry["id"] and e.get("subject") == subject]
        past = [moment(e["at"]) for e in later if e["kind"] in PAST_START]
        reassigned = [moment(e["at"]) for e in later if e["kind"] == "lane.assigned"]
        # Only the story's last assignment before its first start takes that start.
        if started and reassigned and min(reassigned) <= min(started):
            return "reassigned before it started"
        if started and not (past and min(past) < min(started)):
            return None
        if past:
            return "start never recorded: the story was handed off or merged first"
        if reassigned:
            return "reassigned before it started"
        if subject not in self.held:
            return "removed before it started"
        return "wait"

    # One entry.

    def decide(self, entry: dict) -> tuple[str | None, str | None]:
        """(ticket, None) to post, (None, reason) to skip, or (None, "wait")."""
        update = UPDATES.get(entry["kind"])
        if update is None:
            return None, f"{entry['kind']} has no tracker update"
        if entry["kind"] == "decision.recorded" and entry["id"] not in self.marks:
            return None, "an unmarked decision"
        if entry["kind"] == "lane.assigned":
            reason = self.start_decision(entry)
            if reason is not None:
                return None, reason
        where = update[0]
        ticket = entry.get("subject") if where in ("ticket", "ticket-or-epic") else None
        ticket = ticket or (self.epic if where != "ticket" else None)
        if ticket is None:
            return None, "no ticket or epic to post on"
        return ticket, None

    def moves(self, entry: dict) -> tuple[str | None, list[str]]:
        transitions = self.conf.get("transitions") or {}
        if entry["kind"] == "lane.assigned":
            return "start", list(transitions.get("start") or [])
        return "lead", list((transitions.get("lead") or {}).get(entry["kind"]) or [])

    def started_before(self, entry: dict, names: list[str], markers: set[str]) -> bool:
        """The ticket carries this delivery's start marker from another entry, under this init id or an earlier one."""
        own = marker(self.delivery, self.init, entry["id"], "")
        return any(m.startswith(f"agent-delivery:{self.delivery}:") and not m.startswith(own)
                   and ":transition:" in m and m.split(":transition:", 1)[1] in names for m in markers)

    def move(self, entry: dict, ticket: str, move: str, names: list[str], markers: set[str], notes: list[str]) -> None:
        status = self.adapter.status(ticket)
        offered = {t.get("name"): t for t in status.get("transitions") or [] if isinstance(t, dict)}
        targets = (self.conf.get("transitions") or {}).get("targets") or {}
        # A tracker lists only the moves out of the current status: the configured target names the rest.
        target = lambda name: (offered.get(name) or {}).get("to") or targets.get(name)
        whole = f"transition:{names[-1]}:made"
        earlier = self.done(entry, whole)
        if earlier:
            notes.append(f"Its move to {earlier.get('status')} was already made by someone else.")
            return
        ours = any(marker(self.delivery, self.init, entry["id"], f"transition:{name}") in markers for name in names)
        last = target(names[-1])
        if last is not None and last == status.get("status") and not ours and not any(
                self.done(entry, f"transition:{name}") for name in names):
            notes.append(f"Its move to {last} was already made by someone else.")
            self.write(entry, "skip", move=move, ticket=ticket, action=whole, status=last,
                       reason=f"already {last}: made by someone else")
            return
        for name in names:
            action = f"transition:{name}"
            stamp = marker(self.delivery, self.init, entry["id"], action)
            line = self.done(entry, action)
            if line is None and stamp in markers:
                line = self.write(entry, "transition", move=move, ticket=ticket, transition=name, action=action,
                                  found=True)
            if line is not None:
                # What an earlier run did for this move, said again in the update it never posted.
                if line["outcome"] == "transition":
                    notes.append(f"Moved with \"{name}\".")
                elif line["outcome"] == "skip":
                    notes.append(f"Its move \"{name}\" was already made by someone else.")
                else:
                    notes.append(f"The move \"{name}\" isn't offered from {line.get('status')}; it belongs to people now.")
                    return
                status = self.adapter.status(ticket)
                offered = {t.get("name"): t for t in status.get("transitions") or [] if isinstance(t, dict)}
                continue
            transition = offered.get(name)
            if target(name) is not None and target(name) == status.get("status"):
                notes.append(f"Its move \"{name}\" was already made by someone else.")
                self.write(entry, "skip", move=move, ticket=ticket, transition=name, action=action,
                           reason="made by someone else")
                continue
            if not transition or transition.get("offered") is False:
                notes.append(f"The move \"{name}\" isn't offered from {status.get('status')}; it belongs to people now.")
                self.write(entry, "handled", move=move, ticket=ticket, transition=name, action=action,
                           status=status.get("status"))
                return
            self.adapter.call("transition", "--ticket", ticket, "--name", name, "--marker", stamp)
            crash_seam("transition")
            self.write(entry, "transition", move=move, ticket=ticket, transition=name, action=action)
            notes.append(f"Moved with \"{name}\".")
            status = self.adapter.status(ticket)
            offered = {t.get("name"): t for t in status.get("transitions") or [] if isinstance(t, dict)}

    def followup(self, entry: dict, wanted: dict, notes: list[str]) -> None:
        label = followup_label(wanted["story"], wanted["finding"])
        line = self.done(entry, "create")
        if line:
            notes.append(f"Follow-up: {line.get('ticket')}.")
            return
        if self.project is None:
            raise Permanent("no tracker project to file the follow-up in (set tracker.project)")
        found = self.adapter.labelled(self.project, label)
        if found:
            key = found[0]
            self.write(entry, "create", ticket=key, action="create", label=label, found=True)
        else:
            with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as body:
                body.write(f"{wanted['title']}\n\nDeferred in {wanted['story']} (finding {wanted['finding']}), "
                           f"{self.delivery} delivery record, journal entry {entry['id']}.")
            try:
                answer = self.adapter.call("create", "--project", self.project, "--title", wanted["title"],
                                           "--body-file", body.name, "--marker", label,
                                           *(["--parent", self.epic] if self.epic else []),
                                           "--type", self.conf.get("followupType", "Task"))
            finally:
                os.unlink(body.name)
            crash_seam("create")
            key = answer.get("key")
            self.write(entry, "create", ticket=key, action="create", label=label)
        notes.append(f"Follow-up: {key}.")

    def publish(self, entry: dict) -> None:
        ticket, reason = self.decide(entry)
        if reason == "wait":
            return
        if ticket is None:
            self.write(entry, "skip", final=True, reason=reason)
            return
        notes: list[str] = []
        wanted = (self.marks.get(entry["id"]) or {}).get("followup") if entry["kind"] == "decision.recorded" else None
        if wanted:
            self.followup(entry, wanted, notes)
        move, names = self.moves(entry)
        markers = self.adapter.markers(ticket)
        if entry["kind"] == "lane.assigned" and names and self.started_before(entry, names, markers):
            # Started under an earlier record of this delivery, before a --replace: never moved back, never restarted.
            self.write(entry, "skip", final=True, ticket=ticket, reason="started under an earlier record")
            return
        if names:
            self.move(entry, ticket, move, names, markers, notes)
        elif entry["kind"] == "lane.assigned":
            notes.append("No start move is configured: moving the ticket belongs to people.")
        elif entry["kind"] in MOVING:
            notes.append("No move is configured for this: moving the ticket belongs to people.")
        stamp = marker(self.delivery, self.init, entry["id"], "comment")
        if stamp in markers:
            self.write(entry, "comment", final=True, ticket=ticket, action="comment", found=True)
            return
        limit = self.adapter.limits.get("comment")
        if limit is None:
            limit = self.adapter.status(ticket).get("commentLimit")
        text = body_of(entry, UPDATES[entry["kind"]][1], notes, self.delivery, limit if isinstance(limit, int) else None)
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as body:
            body.write(text)
        try:
            self.adapter.call("comment", "--ticket", ticket, "--marker", stamp, "--body-file", body.name)
        finally:
            os.unlink(body.name)
        crash_seam("comment")
        self.write(entry, "comment", final=True, ticket=ticket, action="comment")
        self.posted += 1

    def classify(self, failure: AdapterFailure, ticket: str | None) -> Exception:
        status = failure.status
        if status is None or status >= 500 or status in TRANSIENT:
            return Transient(str(failure))
        if status == 403:
            # The whole site refusing the credentials is retried; one ticket refusing them is that ticket's.
            if self.site_refuses(ticket):
                return Transient(str(failure))
            return Permanent(str(failure))
        return Permanent(str(failure)) if 400 <= status < 500 else Transient(str(failure))

    def site_refuses(self, ticket: str | None) -> bool:
        reads = []
        if self.epic and self.epic != ticket:
            reads.append(("--ticket", self.epic))
        if self.project:
            reads.append(("--project", self.project))
        for flag, value in reads:
            try:
                self.adapter.call("status", flag, value)
                return False
            except AdapterFailure as failure:
                if failure.status == 403:
                    return True
                # An epic that is itself unreadable: ask the project instead.
            except Transient:
                return True
        return False

    def published_through(self) -> int:
        """The last journal entry an earlier generation's `ack` recorded for this record, or 0. Only the publisher's first
        run over the record reads `published.json`: its seeding lines carry the value it read, so a run that stopped
        partway finishes with it, and a later `ack` never covers an entry the publisher hasn't. A record the publisher
        has written other lines for was never seeded. One acknowledged before the record's first entry belongs to an
        earlier record."""
        own = [line for line in self.ledger if line.get("init") == self.init and line.get("writer") == "publisher"]
        seeded = [line["through"] for line in own if line.get("reason") == SEEDED and isinstance(line.get("through"), int)]
        if seeded:
            return max(seeded)
        if own:
            return 0
        path = self.state_dir / PUBLISHED
        if not path.exists():
            return 0
        published = _json(path)
        through, at = published.get("journalThrough"), published.get("at")
        if (not isinstance(through, int) or isinstance(through, bool) or through < 0 or not isinstance(at, str)):
            raise Stop(f"{PUBLISHED} is malformed: the entries posted before the tracker publisher can't be told from the rest")
        try:
            if self.journal and moment(at) < moment(self.journal[0]["at"]):
                return 0
        except ValueError:
            raise Stop(f"{PUBLISHED} is malformed: its time can't be read") from None
        return through

    def run(self) -> tuple[int, str | None]:
        done = covered(self.ledger, self.init)
        try:
            through = self.published_through()
        except Stop as failure:
            return 1, str(failure)
        for entry in self.journal:
            if entry["id"] in done:
                continue
            if entry["id"] <= through:
                # Posted by hand under an earlier generation, and acknowledged: never posted again.
                self.write(entry, "skip", final=True, reason=SEEDED, through=through)
                continue
            try:
                try:
                    self.publish(entry)
                except AdapterFailure as failure:
                    raise self.classify(failure, entry.get("subject") or self.epic) from None
            except Transient as failure:
                return 1, f"journal entry {entry['id']} ({entry['kind']}): {failure}"
            except (Permanent, Stop) as failure:
                self.write(entry, "failed", final=True, error=str(failure)[:500])
        return 0, None


def write_error(state_dir: Path, error: str | None) -> None:
    path = state_dir / ERROR_FILE
    if error is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(f".{ERROR_FILE}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"at": now(), "error": error}, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_error(state_dir: Path) -> dict | None:
    value = _json(state_dir / ERROR_FILE)
    return value or None


# "Needs you", for the supervisor and doctor: derived from the ledger and the journal, never stored.

def waiting(doc: dict, state_dir: Path, flow_log: Path, delivery: str) -> tuple[list[dict], list[dict]]:
    """(lagging, waiting starts): the entries the ledger doesn't cover, split into those the publisher owes and the
    `lane.assigned` entries that wait for their story's start, which aren't lag."""
    ledger = read_ledger(state_dir)
    publisher = Publisher(doc, state_dir, {"transitions": {}}, None, delivery, flow_log)
    publisher.ledger = ledger
    done = covered(ledger, publisher.init)
    lagging, starts = [], []
    for entry in sorted(doc["journal"], key=lambda e: e["id"]):
        if entry["id"] in done:
            continue
        if entry["kind"] == "lane.assigned" and publisher.start_decision(entry) == "wait":
            starts.append(entry)
        else:
            lagging.append(entry)
    return lagging, starts


def needs_you(doc: dict, state_dir: Path, flow_log: Path, delivery: str, conf: dict, closing: bool = False) -> list[dict]:
    """The tracker's "Needs you" items: {holding, text}. A publisher whose ledger lags the journal holds, naming its
    last error; `failed` and `handled` lines are shown. A start that waits holds nothing, except while closing,
    when it is shown."""
    items: list[dict] = []
    if conf.get("mode") == "publisher":
        lagging, starts = waiting(doc, state_dir, flow_log, delivery)
        if lagging:
            error = (read_error(state_dir) or {}).get("error")
            ids = ", ".join(str(e["id"]) for e in lagging[:10]) + (" and more" if len(lagging) > 10 else "")
            items.append({"holding": True, "text": f"The tracker publisher hasn't posted journal entries {ids}"
                          + (f"; its last error: {error}" if error else "")})
        if closing:
            for entry in starts:
                items.append({"holding": False, "text": f"{entry.get('subject')}: start never recorded "
                              f"(journal entry {entry['id']} waits for its lane-start)"})
        init = init_id(sorted(doc["journal"], key=lambda e: e["id"]))
        for line in read_ledger(state_dir):
            if line.get("init") != init:
                continue
            if line["outcome"] == "failed" and line.get("move") == "review":
                items.append({"holding": False, "text": f"{line.get('ticket')}: its review move "
                              f"({', '.join(line.get('transitions') or [])}) couldn't be made at hand-off: "
                              f"{line.get('error')}; make it by hand"})
            elif line["outcome"] == "failed":
                items.append({"holding": False, "text": f"Tracker update for journal entry {line['journal']} was "
                              f"refused: {line.get('error')}"})
            elif line["outcome"] == "handled":
                items.append({"holding": False, "text": f"{line.get('ticket')}: the move \"{line.get('transition')}\" "
                              f"isn't offered from {line.get('status')}; make it by hand if it still applies"})
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--repo", type=Path, help="the checkout whose configuration to read (default: the current one)")
    parser.add_argument("--delivery", help="the delivery key (default: the record's meta.trackerParent)")
    args = parser.parse_args(argv)
    try:
        doc = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        if not isinstance(doc, dict) or not isinstance(doc.get("journal"), list) or not isinstance(doc.get("state"), dict):
            raise ValueError("not a shown record")
    except ValueError as error:
        print(f"tracker publisher: stdin isn't the record the render wrapper shows: {error}", file=sys.stderr)
        return 2
    root = args.repo or Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
                             .stdout.strip() or os.getcwd())
    conf = settings(root)
    delivery = args.delivery or doc["state"].get("meta", {}).get("trackerParent")
    if conf["mode"] in (None, "manual"):
        return 0  # Nothing for this publisher to do: the updates are made by hand.
    if conf["problems"] or not delivery:
        print("tracker publisher: " + ("; ".join(conf["problems"]) or "the record names no delivery "
              "(meta.trackerParent); pass --delivery"), file=sys.stderr)
        return 2
    state_dir = args.state_dir
    with ledger_lock(state_dir, wait=False) as taken:
        if not taken:
            return 0  # Another run holds the ledger: it, or the next pass, posts these.
        publisher = Publisher(doc, state_dir, conf, Adapter(conf, root), delivery, state_dir.parent / "flow-log")
        code, error = publisher.run()
        write_error(state_dir, error)
    if error:
        print(f"tracker publisher: stopped at {error}; the next run retries from there", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
