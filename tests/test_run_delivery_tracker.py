"""The tracker publisher (C2) and its moves (C3): every journal entry gets its ledger line, and every configured update,
move and follow-up happens once, across crashes, concurrent runs and a replaced record.

Each scenario runs against a fake Jira, served on port 0 and refusing JQL on issue properties as Jira does without an
app, through the shipped Jira adapter; the adapter-contract cases run the same scenarios against a second fake adapter
that implements only `tracker-adapter/1`, with no Jira code, and expect the same tracker.
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import http.server
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/run-delivery/scripts"
PUBLISHER = SCRIPTS / "tracker_publisher.py"
STATE_TOOL = ROOT / "skills/orchestrate-work/scripts/delivery_state.py"
RENDER = ROOT / "skills/orchestrate-work/scripts/render_state.py"
POSIX_ONLY = "run-delivery is POSIX-only"
EPIC = "EP-1"
WORKFLOW = [
    {"id": "11", "name": "Start work", "from": ["Backlog"], "to": "In Progress"},
    {"id": "21", "name": "Submit for review", "from": ["In Progress"], "to": "In Review"},
    {"id": "31", "name": "Complete", "from": ["In Review"], "to": "Done"},
    {"id": "41", "name": "Close epic", "from": None, "to": "Closed"},
]


def _load_publisher():
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("tracker_publisher_under_test", PUBLISHER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


tp = _load_publisher()


def stamp(seconds: int) -> str:
    base = datetime.datetime(2026, 11, 1, 9, 0, tzinfo=datetime.timezone.utc)
    return (base + datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# The fake trackers. Both keep: issues {key: {status, comments: [{text, markers}], markers: [], labels, parent,
# summary}}, the workflow, the projects, the valid token, and failures to inject: [{op, ticket, status, times}].

def blank_store() -> dict:
    return {"issues": {}, "projects": ["EP"], "token": "good", "failures": [], "next": 100, "calls": []}


def take_failure(store: dict, op: str, ticket: str | None) -> int | None:
    for rule in store["failures"]:
        if rule["op"] == op and rule.get("ticket") in (None, ticket) and rule["times"] != 0:
            rule["times"] -= 1
            return rule["status"]
    return None


def offered(store: dict, key: str) -> list[dict]:
    """The moves out of the ticket's status, as Jira lists them: never a move from another status, and never a global
    move to the status the ticket is already in."""
    status = store["issues"][key]["status"]
    return [{"id": t["id"], "name": t["name"], "to": t["to"], "offered": True} for t in WORKFLOW
            if (t["from"] is None and t["to"] != status) or (t["from"] is not None and status in t["from"])]


class FakeJira:
    """Jira Cloud's REST API, as much as the adapter uses, on port 0."""

    def __init__(self) -> None:
        self.store = blank_store()
        self.lock = threading.Lock()
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def reply(self, status: int, document: object | None = None) -> None:
                body = json.dumps(document if document is not None else {}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def handle_any(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                document = json.loads(self.rfile.read(length)) if length else None
                parts = urllib.parse.urlsplit(self.path)
                query = dict(urllib.parse.parse_qsl(parts.query))
                path = urllib.parse.unquote(parts.path)
                with fake.lock:
                    status, answer = fake.answer(method, path, query, document, self.headers.get("Authorization", ""))
                self.reply(status, answer)

            def do_GET(self) -> None:
                self.handle_any("GET")

            def do_POST(self) -> None:
                self.handle_any("POST")

            def do_PUT(self) -> None:
                self.handle_any("PUT")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.site = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def answer(self, method: str, path: str, query: dict, document: dict | None, auth: str):
        import base64
        expected = "Basic " + base64.b64encode(f"fx-user:{self.store['token']}".encode()).decode()
        if auth != expected:
            return 401, {"errorMessages": ["unauthorized"]}
        rest = path.removeprefix("/rest/api/3/")
        issues = self.store["issues"]
        match = re.fullmatch(r"issue/([^/]+)(/.*)?", rest)
        key, tail = (match.group(1), match.group(2) or "") if match else (None, None)
        # Failures are injected per adapter operation, on the request that operation is known by.
        if rest == "issue" and method == "POST":
            op, target = "create", None
        elif rest.startswith("project/"):
            op, target = "status", rest[len("project/"):]
        elif key is not None:
            op = {("GET", ""): "status", ("GET", "/properties"): "markers", ("POST", "/comment"): "comment",
                  ("POST", "/transitions"): "transition"}.get((method, tail))
            target = key
        else:
            op = target = None
        if op:
            injected = take_failure(self.store, op, target)
            if injected:
                return injected, {"errorMessages": [f"injected {injected}"]}
        if rest.startswith("project/"):
            return (200, {"key": rest[8:]}) if rest[8:] in self.store["projects"] else (404, {})
        if rest == "search/jql":
            jql = query.get("jql", "")
            if "issue.property" in jql or "[" in jql:
                return 400, {"errorMessages": ["issue properties can't be searched without an app"]}
            label = re.search(r'labels = "([^"]+)"', jql).group(1)
            project = re.search(r'project = "([^"]+)"', jql).group(1)
            found = [{"key": k} for k, i in issues.items() if label in i["labels"] and k.startswith(project + "-")]
            return 200, {"issues": found}
        if rest == "issue" and method == "POST":
            fields = document["fields"]
            self.store["next"] += 1
            new = f"{fields['project']['key']}-{self.store['next']}"
            issues[new] = {"status": "Backlog", "comments": [], "markers": [], "labels": fields["labels"],
                           "parent": (fields.get("parent") or {}).get("key"), "summary": fields["summary"]}
            self.store["calls"].append(["create", new])
            return 201, {"key": new}
        if key not in issues:
            return 404, {"errorMessages": ["Issue does not exist"]}
        issue = issues[key]
        if method == "GET" and tail == "":
            return 200, {"key": key, "fields": {"status": {"name": issue["status"]}}}
        if method == "GET" and tail == "/transitions":
            listed = [{"id": t["id"], "name": t["name"], "to": {"name": t["to"]}, "isAvailable": t["offered"]}
                      for t in offered(self.store, key)]
            if query.get("includeUnavailableTransitions") != "true":
                listed = [t for t in listed if t["isAvailable"]]
            return 200, {"transitions": listed}
        if method == "POST" and tail == "/transitions":
            chosen = next((t for t in offered(self.store, key) if t["id"] == document["transition"]["id"]), None)
            if chosen is None:
                return 400, {"errorMessages": ["transition not offered"]}
            issue["status"] = chosen["to"]
            issue["markers"] += [p["key"] for p in document.get("properties", [])]
            self.store["calls"].append(["transition", key, chosen["name"]])
            return 204, {}
        if method == "GET" and tail == "/properties":
            return 200, {"keys": [{"key": k} for k in issue["markers"]]}
        if method == "PUT" and tail.startswith("/properties/"):
            name = tail[len("/properties/"):]
            if name not in issue["markers"]:
                issue["markers"].append(name)
            return 200, {}
        if method == "POST" and tail == "/comment":
            text = "\n".join("".join(part["text"] for part in para.get("content", [])) for para in document["body"]["content"])
            issue["comments"].append({"text": text, "markers": [p["key"] for p in document.get("properties", [])]})
            self.store["calls"].append(["comment", key])
            return 201, {"id": str(len(issue["comments"]))}
        if method == "GET" and tail == "/comment":
            start, size = int(query.get("startAt", 0)), int(query.get("maxResults", 50))
            page = issue["comments"][start:start + size]
            return 200, {"startAt": start, "total": len(issue["comments"]),
                         "comments": [{"properties": [{"key": m} for m in c["markers"]]} for c in page]}
        return 404, {}


FAKE_ADAPTER = r'''
import argparse, fcntl, json, os, sys
STORE = os.environ["FAKE_TRACKER_STORE"]
WORKFLOW = json.loads(os.environ["FAKE_TRACKER_WORKFLOW"])

def fail(message, status):
    print(json.dumps({"error": message, "httpStatus": status}))
    sys.exit(3)

p = argparse.ArgumentParser()
p.add_argument("op")
for flag in ("--ticket", "--project", "--marker", "--name", "--body-file", "--title", "--parent", "--type"):
    p.add_argument(flag)
args = p.parse_args()
with open(STORE + ".lock", "a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    store = json.load(open(STORE))
    def save():
        json.dump(store, open(STORE, "w"))
    if os.environ.get("FAKE_TRACKER_TOKEN") != store["token"]:
        fail("unauthorized", 401)
    target = args.ticket or args.project
    for rule in store["failures"]:
        if rule["op"] == args.op and rule.get("ticket") in (None, target) and rule["times"] != 0:
            rule["times"] -= 1
            save()
            fail(f"injected {rule['status']}", rule["status"])
    issues = store["issues"]
    def offered(key):
        status = issues[key]["status"]
        return [{"name": t["name"], "to": t["to"], "offered": True} for t in WORKFLOW
                if (t["from"] is None and t["to"] != status) or (t["from"] is not None and status in t["from"])]
    if args.op == "markers" and args.project:
        print(json.dumps({"items": [{"key": k} for k, i in issues.items()
                                    if args.marker in i["labels"] and k.startswith(args.project + "-")]}))
        sys.exit(0)
    if args.op == "status" and args.project:
        if args.project not in store["projects"]:
            fail("no such project", 404)
        print(json.dumps({"commentLimit": 32767}))
        sys.exit(0)
    if args.op == "create":
        store["next"] += 1
        key = f"{args.project}-{store['next']}"
        issues[key] = {"status": "Backlog", "comments": [], "markers": [], "labels": [args.marker],
                       "parent": args.parent, "summary": args.title}
        store["calls"].append(["create", key])
        save()
        print(json.dumps({"key": key}))
        sys.exit(0)
    if args.ticket not in issues:
        fail("no such ticket", 404)
    issue = issues[args.ticket]
    if args.op == "status":
        print(json.dumps({"status": issue["status"], "transitions": offered(args.ticket), "commentLimit": 32767}))
    elif args.op == "markers":
        found = set(issue["markers"])
        for comment in issue["comments"]:
            found |= set(comment["markers"])
        print(json.dumps({"markers": sorted(found)}))
    elif args.op == "comment":
        issue["comments"].append({"text": open(args.body_file).read()[:32767], "markers": [args.marker]})
        store["calls"].append(["comment", args.ticket])
        save()
        print("{}")
    elif args.op == "transition":
        chosen = next((t for t in offered(args.ticket) if t["name"] == args.name and t["offered"]), None)
        if chosen is None:
            fail("not offered", 400)
        issue["status"] = chosen["to"]
        issue["markers"].append(args.marker)
        store["calls"].append(["transition", args.ticket, args.name])
        save()
        print("{}")
'''


class JiraBackend:
    name = "jira"

    def __init__(self, folder: Path) -> None:
        self.jira = FakeJira()
        self.store = self.jira.store

    def close(self) -> None:
        self.jira.close()

    def adapter(self) -> dict:
        return {"adapter": "jira", "site": self.jira.site, "userEnv": "FX_TRACKER_USER", "tokenEnv": "FX_TRACKER_TOKEN"}

    def env(self) -> dict:
        return {"FX_TRACKER_USER": "fx-user", "FX_TRACKER_TOKEN": "good"}

    def edit(self):
        return contextlib.nullcontext(self.store)

    def read(self) -> dict:
        with self.jira.lock:
            return json.loads(json.dumps(self.store))


class FileBackend:
    """A tracker known only through `tracker-adapter/1`: no Jira code anywhere."""
    name = "contract-only"

    def __init__(self, folder: Path) -> None:
        self.path = folder / "tracker.json"
        self.path.write_text(json.dumps(blank_store()))
        self.script = folder / "fake_adapter.py"
        self.script.write_text(FAKE_ADAPTER)

    def close(self) -> None:
        pass

    def adapter(self) -> dict:
        return {"argv": [sys.executable, "-B", str(self.script)]}

    def env(self) -> dict:
        return {"FAKE_TRACKER_STORE": str(self.path), "FAKE_TRACKER_TOKEN": "good",
                "FAKE_TRACKER_WORKFLOW": json.dumps(WORKFLOW)}

    @contextlib.contextmanager
    def edit(self):
        store = json.loads(self.path.read_text())
        yield store
        self.path.write_text(json.dumps(store))

    def read(self) -> dict:
        return json.loads(self.path.read_text())


class Delivery:
    """One delivery's record, as the render wrapper shows it, with its configuration, flow log and fake tracker."""

    def __init__(self, test: unittest.TestCase, backend_class, transitions: dict | None = None, **tracker) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tracker-publisher-"))
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.backend = backend_class(self.tmp)
        test.addCleanup(self.backend.close)
        self.root = self.tmp / "repo"
        (self.root / ".agents").mkdir(parents=True)
        self.state_dir = self.root / ".orchestration/state"
        self.state_dir.mkdir(parents=True)
        self.flow_log = self.root / ".orchestration/flow-log"
        self.tracker = {**self.backend.adapter(), **tracker}
        if transitions is not None:
            self.tracker["transitions"] = transitions
        self.write_config()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("RUN_DELIVERY", "DELIVERY_FLOW", "FX_TRACKER", "FAKE_TRACKER"))}
        self.env.update(self.backend.env())
        self.clock = 0
        self.journal: list[dict] = []
        self.planned: list[dict] = []
        self.queue: list[dict] = []
        with self.backend.edit() as store:
            store["issues"][EPIC] = {"status": "In Progress", "comments": [], "markers": [], "labels": [],
                                     "parent": None, "summary": "The epic"}
        self.entry("decision.recorded", summary="Record starts at base abc1234 on origin/main.")

    def write_config(self) -> None:
        (self.root / ".agents/run-delivery.json").write_text(json.dumps(
            {"schemaVersion": "delivery-flow/1", "tracker": self.tracker}, indent=2) + "\n")

    # The tracker.

    def ticket(self, key: str, status: str = "Backlog") -> None:
        with self.backend.edit() as store:
            store["issues"][key] = {"status": status, "comments": [], "markers": [], "labels": [], "parent": EPIC,
                                    "summary": key}
        if not any(p["subject"] == key for p in self.planned):
            self.planned.append({"subject": key, "title": key, "lane": "lane-1", "stage": "planned"})

    def fail(self, op: str, status: int, ticket: str | None = None, times: int = 1) -> None:
        with self.backend.edit() as store:
            store["failures"].append({"op": op, "ticket": ticket, "status": status, "times": times})

    def comments(self, key: str) -> list[str]:
        return [c["text"] for c in self.backend.read()["issues"][key]["comments"]]

    def status(self, key: str) -> str:
        return self.backend.read()["issues"][key]["status"]

    def calls(self) -> list[list]:
        return self.backend.read()["calls"]

    # The record.

    def entry(self, kind: str, subject: str | None = None, summary: str | None = None, lane: str | None = None) -> dict:
        self.clock += 10
        entry = {"id": len(self.journal) + 1, "at": stamp(self.clock), "kind": kind, "lane": lane, "subject": subject,
                 "summary": summary or f"{kind} for {subject or 'the delivery'}"}
        self.journal.append(entry)
        return entry

    def lane_start(self, subject: str) -> None:
        self.clock += 10
        self.flow_log.mkdir(parents=True, exist_ok=True)
        with open(self.flow_log / f"{EPIC}.lane-starts.jsonl", "a") as log:
            log.write(json.dumps({"subject": subject, "lane": "lane-1", "at": stamp(self.clock)}) + "\n")

    def mark(self, entry: dict, followup: dict | None = None) -> None:
        tp.mark(self.flow_log, EPIC, entry["id"], followup)

    def doc(self) -> dict:
        return {"state": {"meta": {"trackerParent": EPIC}, "planned": self.planned, "queue": self.queue},
                "journal": self.journal}

    # The publisher.

    def publish(self, env: dict | None = None, wait: bool = True):
        process = subprocess.Popen([sys.executable, "-B", str(PUBLISHER), "--state-dir", str(self.state_dir),
                                    "--repo", str(self.root)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env={**self.env, **(env or {})}, cwd=self.root)
        if not wait:
            process.stdin.write(json.dumps(self.doc()))
            process.stdin.close()
            return process
        out, err = process.communicate(json.dumps(self.doc()), timeout=120)
        return process.returncode, out + err

    def ledger(self) -> list[dict]:
        return tp.read_ledger(self.state_dir)

    def init(self) -> str:
        return tp.init_id(self.journal)

    def command_line(self, entry: dict, outcome: str = "comment", **detail) -> None:
        """A lane or lead command's own line for an entry it journalled."""
        tp.append_ledger(self.state_dir, {"journal": entry["id"], "init": self.init(), "outcome": outcome,
                                          "writer": "lane", "ticket": entry.get("subject"), **detail})

    def unreachable(self) -> None:
        """The tracker can't be reached: the adapter reports no HTTP status."""
        if self.backend.name == "jira":
            self.tracker["site"] = "http://127.0.0.1:9"
            self.write_config()
        else:
            self.fail("comment", None, times=-1)

    def reachable(self) -> None:
        if self.backend.name == "jira":
            self.tracker["site"] = self.backend.adapter()["site"]
            self.write_config()
        else:
            with self.backend.edit() as store:
                store["failures"] = []

    def lines(self, journal_id: int) -> list[dict]:
        return [line for line in self.ledger() if line["journal"] == journal_id and line["init"] == self.init()]

    def outcomes(self, journal_id: int) -> list[str]:
        return [line["outcome"] for line in self.lines(journal_id)]

    def needs_you(self, closing: bool = False) -> list[dict]:
        return tp.needs_you(self.doc(), self.state_dir, self.flow_log, EPIC, tp.settings(self.root), closing=closing)


ALL_KINDS = list(tp.UPDATES)


class Scenarios:
    """The required scenarios, each run against one fake tracker."""

    BACKEND = None

    def delivery(self, transitions: dict | None = None, **tracker) -> Delivery:
        return Delivery(self, self.BACKEND, transitions, **tracker)

    def published(self, d: Delivery, **kwargs) -> str:
        code, out = d.publish(**kwargs)
        self.assertEqual(code, 0, out)
        return out

    # Publisher.

    def test_each_kind_gets_the_guides_update_once_and_every_entry_a_line(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        posted = {}
        for kind in ALL_KINDS:
            subject = None if tp.UPDATES[kind] and tp.UPDATES[kind][0] == "epic" else "EP-2"
            entry = d.entry(kind, subject, lane="lane-1" if kind == "review.round" else None)
            posted[entry["id"]] = kind
            if kind == "lane.assigned":
                d.lane_start("EP-2")
        marked = d.entry("decision.recorded", "EP-2", "Plan change: EP-3 removed")
        d.mark(marked)
        lane_posted = d.entry("candidate.handed", "EP-2", "Handed off by its lane")
        d.command_line(lane_posted, action="comment")
        self.published(d)
        self.published(d)  # A second run changes nothing.
        ledger = d.ledger()
        self.assertEqual({line["journal"] for line in ledger}, {e["id"] for e in d.journal}, "every entry has a line")
        comments = d.comments("EP-2") + d.comments(EPIC)
        for entry in d.journal:
            outcomes = d.outcomes(entry["id"])
            update = tp.UPDATES[entry["kind"]]
            if entry is lane_posted:
                self.assertEqual(outcomes, ["comment"], "a command's action line is the entry's")
            elif entry["kind"] == "decision.recorded":
                self.assertEqual(outcomes, ["comment"] if entry is marked else ["skip"], entry)
            elif update is None:
                self.assertEqual(outcomes, ["skip"], entry)
            else:
                self.assertEqual(outcomes, ["comment"], entry)
                self.assertEqual(sum(f"journal entry {entry['id']} " in text for text in comments), 1, entry)
        self.assertFalse(any("journal entry 1 " in text for text in comments), "the record's own start isn't posted")
        self.assertFalse(any(f"journal entry {lane_posted['id']} " in text for text in comments))

    def test_a_transient_failure_stops_the_run_and_the_next_posts_from_there(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        for n in range(2, 11):
            d.entry("finish.met", summary=f"Line {n}")
        eleven = d.entry("candidate.stopped", "EP-2", "Stopped: checks failed")
        twelve = d.entry("candidate.merged", "EP-2", "Merged at abc")
        d.fail("comment", 503, ticket="EP-2", times=1)
        code, out = d.publish()
        self.assertEqual(code, 1, out)
        self.assertIn(f"journal entry {eleven['id']}", out)
        self.assertEqual(d.lines(eleven["id"]), [])
        self.assertEqual(d.lines(twelve["id"]), [], "nothing after a transient failure is posted in that run")
        # The lane posted entry 12 meanwhile; the next run posts only 11.
        d.command_line(twelve)
        self.published(d)
        self.assertEqual(len(d.comments("EP-2")), 1)
        self.assertIn(f"journal entry {eleven['id']} ", d.comments("EP-2")[0])
        self.assertIsNone(tp.read_error(d.state_dir), "a run that reaches the end clears the last error")

    def test_two_runs_at_once_post_each_entry_once(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        for n in range(8):
            d.entry("lease.granted", "EP-2", f"Lease {n}")
        runs = [d.publish(wait=False) for _ in range(3)]
        for run in runs:
            run.wait(timeout=120)
            error = run.stderr.read()
            run.stdout.close()
            run.stderr.close()
            self.assertEqual(run.returncode, 0, error)
        self.published(d)
        self.assertEqual(len(d.comments("EP-2")), 8)
        self.assertEqual(sorted(line["journal"] for line in d.ledger()), [e["id"] for e in d.journal])

    def test_a_run_that_finds_the_ledger_held_exits_at_once(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        d.entry("lease.granted", "EP-2")
        with tp.ledger_lock(d.state_dir, wait=True):
            started = time.monotonic()
            self.published(d)
            self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(d.ledger(), [])
        self.assertEqual(d.comments("EP-2"), [])

    def test_a_crash_after_the_post_and_before_the_line_posts_nothing_again(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        entry = d.entry("candidate.handed", "EP-2", "Handed off")
        code, out = d.publish(env={"TRACKER_PUBLISHER_EXIT_AFTER": "comment"})
        self.assertNotEqual(code, 0, out)
        self.assertEqual(len(d.comments("EP-2")), 1)
        self.assertEqual(d.lines(entry["id"]), [], "the crash came before the line")
        self.published(d)
        self.assertEqual(len(d.comments("EP-2")), 1, "the marker kept the retry from posting again")
        self.assertEqual(d.outcomes(entry["id"]), ["comment"])

    def test_a_refused_token_raises_needs_you_until_it_is_fixed(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        d.entry("candidate.handed", "EP-2")
        with d.backend.edit() as store:
            store["token"] = "rotated"
        code, out = d.publish()
        self.assertEqual(code, 1, out)
        holding = [item for item in d.needs_you() if item["holding"]]
        self.assertEqual(len(holding), 1, d.needs_you())
        self.assertIn("unauthorized", holding[0]["text"])
        with d.backend.edit() as store:
            store["token"] = "good"
        self.published(d)
        self.assertEqual(d.needs_you(), [])

    def test_a_long_entry_posts_one_comment_cut_to_the_limit(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        entry = d.entry("blocker.recorded", "EP-2", "x" * 40000)
        self.published(d)
        [text] = d.comments("EP-2")
        self.assertLessEqual(len(text), 32767)
        self.assertGreater(len(text), 32000)
        self.assertIn(f"journal entry {entry['id']}", text[-400:])

    def test_a_rejected_entry_gets_one_failed_line_and_holds_nothing_after_it(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        d.ticket("EP-3")
        rejected = d.entry("candidate.stopped", "EP-2")
        later = d.entry("candidate.handed", "EP-3")
        d.fail("comment", 400, ticket="EP-2", times=-1)
        for _ in range(3):
            self.published(d)
        self.assertEqual(d.outcomes(rejected["id"]), ["failed"])
        self.assertEqual(d.outcomes(later["id"]), ["comment"])
        self.assertEqual(len(d.journal), 3, "the publisher journals nothing")
        items = d.needs_you()
        self.assertEqual([item["holding"] for item in items], [False], items)
        self.assertIn(f"journal entry {rejected['id']}", items[0]["text"])

    def test_a_follow_up_filed_before_a_crash_is_found_by_its_label(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        d.ticket("EP-3")
        first = d.entry("decision.recorded", "EP-2", "Deferred F1")
        d.mark(first, {"story": "EP-2", "finding": "F1", "title": "Tidy the parser"})
        code, out = d.publish(env={"TRACKER_PUBLISHER_EXIT_AFTER": "create"})
        self.assertNotEqual(code, 0, out)
        second = d.entry("decision.recorded", "EP-3", "Deferred F1 too")
        d.mark(second, {"story": "EP-3", "finding": "F1", "title": "Tidy the lexer"})
        self.published(d)
        self.published(d)
        created = [call for call in d.calls() if call[0] == "create"]
        self.assertEqual(len(created), 2, "one follow-up per story, each once")
        labels = sorted(d.backend.read()["issues"][call[1]]["labels"][0] for call in created)
        self.assertEqual(labels, ["agent-followup-ep-2-f1", "agent-followup-ep-3-f1"])
        self.assertEqual(d.outcomes(first["id"]), ["create", "comment"])
        self.assertIn("Follow-up: EP-", d.comments("EP-2")[0])

    def test_a_409_and_a_site_wide_403_are_retried_and_a_one_ticket_403_is_not(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        d.ticket("EP-3")
        conflict = d.entry("lease.granted", "EP-2")
        d.fail("comment", 409, ticket="EP-2")
        code, out = d.publish()
        self.assertEqual((code, d.lines(conflict["id"])), (1, []), out)
        self.published(d)
        self.assertEqual(d.outcomes(conflict["id"]), ["comment"])

        site = d.entry("lease.released", "EP-2")
        d.fail("comment", 403, ticket="EP-2")
        d.fail("status", 403, ticket=EPIC)
        code, out = d.publish()
        self.assertEqual((code, d.lines(site["id"])), (1, []), out)
        self.published(d)
        self.assertEqual(d.outcomes(site["id"]), ["comment"])

        alone = d.entry("lease.granted", "EP-3")
        d.fail("comment", 403, ticket="EP-3", times=-1)
        self.published(d)
        self.assertEqual(d.outcomes(alone["id"]), ["failed"])

    def test_an_unreadable_epic_asks_the_project_whether_the_site_refuses(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        entry = d.entry("lease.granted", "EP-2")
        d.fail("comment", 403, ticket="EP-2")
        d.fail("status", 404, ticket=EPIC)
        d.fail("status", 403, ticket="EP")
        code, out = d.publish()
        self.assertEqual((code, d.lines(entry["id"])), (1, []), out)

    def test_an_entry_whose_command_post_failed_is_posted_once_by_the_next_run(self) -> None:
        # A lane command that couldn't post writes no line and doesn't fail; the publisher posts it.
        d = self.delivery()
        d.ticket("EP-2")
        entry = d.entry("candidate.handed", "EP-2")
        self.published(d)
        self.published(d)
        self.assertEqual(d.outcomes(entry["id"]), ["comment"])
        self.assertEqual(len(d.comments("EP-2")), 1)

    # Moves.

    def test_a_move_already_made_is_skipped_and_said_to_be_someone_elses(self) -> None:
        # The tracker lists only the moves out of Done, so the configured target says where "Complete" goes.
        d = self.delivery({"lead": {"candidate.merged": ["Complete"]}, "targets": {"Complete": "Done"}})
        d.ticket("EP-2", status="Done")
        entry = d.entry("candidate.merged", "EP-2")
        self.published(d)
        self.assertEqual(d.outcomes(entry["id"]), ["skip", "comment"])
        self.assertIn("already made by someone else", d.comments("EP-2")[0])
        self.assertEqual(d.status("EP-2"), "Done")
        self.assertFalse([call for call in d.calls() if call[0] == "transition"])

    def test_a_move_not_offered_is_handled_once_and_shown(self) -> None:
        d = self.delivery({"lead": {"candidate.merged": ["Complete"]}})
        d.ticket("EP-2", status="Backlog")
        entry = d.entry("candidate.merged", "EP-2")
        for _ in range(2):
            self.published(d)
        self.assertEqual(d.outcomes(entry["id"]), ["handled", "comment"])
        self.assertEqual(len(d.journal), 2)
        items = d.needs_you()
        self.assertEqual([item["holding"] for item in items], [False])
        self.assertIn("Complete", items[0]["text"])

    def test_a_lead_move_is_made_once(self) -> None:
        d = self.delivery({"lead": {"candidate.merged": ["Complete"], "delivery.closed": ["Close epic"]}})
        d.ticket("EP-2", status="In Review")
        d.entry("candidate.merged", "EP-2")
        d.entry("delivery.closed")
        self.published(d)
        self.published(d)
        self.assertEqual((d.status("EP-2"), d.status(EPIC)), ("Done", "Closed"))
        self.assertEqual(len([c for c in d.calls() if c[0] == "transition"]), 2)

    def test_with_lead_absent_no_later_move_is_made_and_the_update_says_so(self) -> None:
        d = self.delivery({"start": ["Start work"]})
        d.ticket("EP-2", status="In Review")
        d.entry("candidate.merged", "EP-2")
        self.published(d)
        self.assertEqual(d.status("EP-2"), "In Review")
        self.assertIn("belongs to people", d.comments("EP-2")[0])

    def test_with_start_set_the_start_move_and_update_wait_for_lane_start_and_happen_once(self) -> None:
        d = self.delivery({"start": ["Start work"]})
        d.ticket("EP-2")
        d.ticket("EP-3")
        two = d.entry("lane.assigned", "EP-2", lane="lane-1")
        three = d.entry("lane.assigned", "EP-3", lane="lane-1")
        later = d.entry("lease.granted", "EP-2")
        self.published(d)
        self.assertEqual((d.status("EP-2"), d.status("EP-3")), ("Backlog", "Backlog"))
        self.assertEqual((d.lines(two["id"]), d.lines(three["id"])), ([], []), "a waiting start writes nothing")
        self.assertEqual(d.outcomes(later["id"]), ["comment"], "later entries don't wait for it")
        self.assertEqual(d.needs_you(), [], "a start that waits isn't lag")
        self.assertIn("start never recorded", d.needs_you(closing=True)[0]["text"])
        d.lane_start("EP-2")
        self.published(d)
        self.assertEqual((d.status("EP-2"), d.status("EP-3")), ("In Progress", "Backlog"))
        d.lane_start("EP-3")
        self.published(d)
        self.published(d)
        self.assertEqual((d.status("EP-2"), d.status("EP-3")), ("In Progress", "In Progress"))
        self.assertEqual(d.outcomes(two["id"]), ["transition", "comment"])
        self.assertEqual(len([c for c in d.calls() if c[0] == "transition"]), 2)
        self.assertEqual(sum(text.startswith("Started") for text in d.comments("EP-2")), 1)

    def test_with_neither_set_no_move_is_made_and_the_update_says_so(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        d.entry("lane.assigned", "EP-2", lane="lane-1")
        d.lane_start("EP-2")
        self.published(d)
        self.assertEqual(d.status("EP-2"), "Backlog")
        self.assertIn("belongs to people", d.comments("EP-2")[0])

    def test_a_story_removed_before_its_start_never_moves(self) -> None:
        d = self.delivery({"start": ["Start work"]})
        d.ticket("EP-2")
        entry = d.entry("lane.assigned", "EP-2", lane="lane-1")
        self.published(d)
        d.planned = []
        d.entry("decision.recorded", summary="EP-2 removed from the plan")
        self.published(d)
        self.assertEqual(d.outcomes(entry["id"]), ["skip"])
        d.lane_start("EP-2")
        self.published(d)
        self.assertEqual(d.status("EP-2"), "Backlog")

    def test_a_story_reassigned_after_review_gets_no_second_start(self) -> None:
        d = self.delivery({"start": ["Start work"], "lead": {"candidate.handed": ["Submit for review"]}})
        d.ticket("EP-2")
        d.entry("lane.assigned", "EP-2", lane="lane-1")
        d.lane_start("EP-2")
        d.entry("candidate.handed", "EP-2")
        self.published(d)
        self.assertEqual(d.status("EP-2"), "In Review")
        again = d.entry("lane.assigned", "EP-2", lane="lane-2")
        d.lane_start("EP-2")
        self.published(d)
        self.assertEqual(d.outcomes(again["id"]), ["skip"])
        self.assertEqual(d.status("EP-2"), "In Review")
        self.assertEqual([c[2] for c in d.calls() if c[0] == "transition"], ["Start work", "Submit for review"])

    def test_a_story_moved_by_hand_and_merged_without_a_start_keeps_its_status(self) -> None:
        d = self.delivery({"start": ["Start work"]})
        d.ticket("EP-2", status="In Review")
        assigned = d.entry("lane.assigned", "EP-2", lane="lane-1")
        d.entry("candidate.handed", "EP-2")
        d.entry("candidate.merged", "EP-2")
        self.published(d)
        d.lane_start("EP-2")
        self.published(d)
        self.assertEqual(d.outcomes(assigned["id"]), ["skip"])
        self.assertIn("start never recorded", d.lines(assigned["id"])[0]["reason"])
        self.assertEqual(d.status("EP-2"), "In Review")
        self.assertEqual(d.needs_you(closing=True), [])

    def test_a_run_between_a_marked_journal_write_and_its_mark_still_posts_it_once(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        with tp.ledger_lock(d.state_dir, wait=True):
            entry = d.entry("decision.recorded", "EP-2", "Plan change c1: EP-3 added")
            self.published(d)  # Started between the two writes: it finds the lock held and leaves the entry.
            d.mark(entry)
        self.assertEqual(d.lines(entry["id"]), [])
        self.published(d)
        self.published(d)
        self.assertEqual(d.outcomes(entry["id"]), ["comment"])
        self.assertEqual(len(d.comments("EP-2")), 1)

    def test_an_unmarked_decision_is_skipped_and_a_marked_one_posted(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        accepted = d.entry("decision.recorded", "EP-2", "EP-2 accepted")
        change = d.entry("decision.recorded", "EP-2", "Plan change c1")
        d.mark(change)
        self.published(d)
        self.assertEqual((d.outcomes(accepted["id"]), d.outcomes(change["id"])), (["skip"], ["comment"]))

    def test_after_a_replace_a_reused_journal_id_is_posted_and_a_started_story_is_not_restarted(self) -> None:
        d = self.delivery({"start": ["Start work"], "lead": {"candidate.handed": ["Submit for review"]}})
        d.ticket("EP-2")
        d.ticket("EP-3")
        d.entry("lane.assigned", "EP-2", lane="lane-1")
        d.lane_start("EP-2")
        d.entry("candidate.handed", "EP-2")
        d.entry("lease.granted", "EP-3")
        self.published(d)
        self.assertEqual(d.status("EP-2"), "In Review")
        # --replace: a fresh journal from 1 and flow log, a later init time; the old ledger is left where it was.
        shutil.rmtree(d.flow_log)
        d.journal = []
        d.clock += 1000
        d.entry("decision.recorded", summary="Record starts at base def5678 on origin/main.")
        again = d.entry("lane.assigned", "EP-2", lane="lane-1")
        three = d.entry("lane.assigned", "EP-3", lane="lane-1")
        d.lane_start("EP-2")
        d.lane_start("EP-3")
        self.published(d)
        self.assertEqual(d.outcomes(again["id"]), ["skip"], "the earlier record's start marker is on the ticket")
        self.assertEqual(d.status("EP-2"), "In Review")
        self.assertEqual(d.outcomes(three["id"]), ["transition", "comment"],
                         "a story the old record only commented on still gets its first start")
        self.assertEqual(d.status("EP-3"), "In Progress")


    def test_a_move_whose_target_the_tracker_cant_say_is_handled_not_guessed(self) -> None:
        # Without a configured target, a move not listed from the ticket's status is the people's to judge.
        d = self.delivery({"lead": {"candidate.merged": ["Complete"]}})
        d.ticket("EP-2", status="Done")
        entry = d.entry("candidate.merged", "EP-2")
        self.published(d)
        self.assertEqual(d.outcomes(entry["id"]), ["handled", "comment"])

    def test_an_entry_left_part_done_is_finished_once_with_what_it_did(self) -> None:
        d = self.delivery({"lead": {"candidate.merged": ["Complete"]}})
        d.ticket("EP-2", status="In Review")
        entry = d.entry("candidate.merged", "EP-2")
        d.fail("comment", 503, ticket="EP-2")
        code, out = d.publish()
        self.assertEqual(code, 1, out)
        self.assertEqual(d.outcomes(entry["id"]), ["transition"], "the move landed before the comment failed")
        self.assertNotIn(entry["id"], tp.covered(d.ledger(), d.init()))
        self.published(d)
        self.published(d)
        self.assertEqual(d.outcomes(entry["id"]), ["transition", "comment"])
        [text] = d.comments("EP-2")
        self.assertIn('Moved with "Complete".', text)
        self.assertEqual(len([c for c in d.calls() if c[0] == "transition"]), 1)

    def test_a_skipped_move_left_part_done_is_neither_written_twice_nor_forgotten(self) -> None:
        d = self.delivery({"lead": {"candidate.merged": ["Complete"]}, "targets": {"Complete": "Done"}})
        d.ticket("EP-2", status="Done")
        entry = d.entry("candidate.merged", "EP-2")
        d.fail("comment", 503, ticket="EP-2", times=2)
        for _ in range(2):
            self.assertEqual(d.publish()[0], 1)
        self.published(d)
        self.assertEqual(d.outcomes(entry["id"]), ["skip", "comment"])
        self.assertIn("already made by someone else", d.comments("EP-2")[0])

    def test_a_story_reassigned_before_its_start_starts_once_for_its_last_lane(self) -> None:
        for moves in ({"start": ["Start work"]}, {}):
            for between in (False, True):
                with self.subTest(moves=moves, run_between=between):
                    d = self.delivery(moves)
                    d.ticket("EP-2")
                    first = d.entry("lane.assigned", "EP-2", lane="lane-1")
                    second = d.entry("lane.assigned", "EP-2", lane="lane-2")
                    if between:
                        self.published(d)
                    d.lane_start("EP-2")
                    self.published(d)
                    self.published(d)
                    self.assertEqual(d.outcomes(first["id"]), ["skip"])
                    self.assertEqual(d.outcomes(second["id"]), ["transition", "comment"] if moves else ["comment"])
                    started = [t for t in d.comments("EP-2") if t.startswith("Started")]
                    self.assertEqual(len(started), 1)
                    self.assertIn("(lane-2)", started[0])

    def test_a_crash_after_a_move_and_before_its_line_moves_nothing_again(self) -> None:
        for moves, kind, status in (({"start": ["Start work"]}, "lane.assigned", "Backlog"),
                                    ({"lead": {"candidate.merged": ["Complete"]}}, "candidate.merged", "In Review")):
            with self.subTest(kind=kind):
                d = self.delivery(moves)
                d.ticket("EP-2", status=status)
                entry = d.entry(kind, "EP-2", lane="lane-1")
                d.lane_start("EP-2")
                code, out = d.publish(env={"TRACKER_PUBLISHER_EXIT_AFTER": "transition"})
                self.assertNotEqual(code, 0, out)
                self.assertEqual(d.lines(entry["id"]), [])
                self.published(d)
                self.assertEqual(d.outcomes(entry["id"]), ["transition", "comment"])
                self.assertTrue(d.lines(entry["id"])[0].get("found"))
                self.assertEqual(len([c for c in d.calls() if c[0] == "transition"]), 1)
                [text] = d.comments("EP-2")
                self.assertNotIn("someone else", text)

    def test_every_transient_class_is_retried_and_posted_once(self) -> None:
        for status in (423, 429, 500, None):
            with self.subTest(status=status):
                d = self.delivery()
                d.ticket("EP-2")
                entry = d.entry("lease.granted", "EP-2")
                if status is None:
                    d.unreachable()
                else:
                    d.fail("comment", status, ticket="EP-2")
                code, out = d.publish()
                self.assertEqual((code, d.lines(entry["id"])), (1, []), out)
                self.assertIn("the next run retries from there", out)
                self.assertNotIn("Traceback", out)
                if status is None:
                    d.reachable()
                self.published(d)
                self.assertEqual(d.outcomes(entry["id"]), ["comment"])
                self.assertEqual(len(d.comments("EP-2")), 1)

    def test_a_start_after_the_review_move_or_before_the_assignment_is_not_taken(self) -> None:
        d = self.delivery({"start": ["Start work"]})
        d.ticket("EP-2")
        d.ticket("EP-3")
        d.lane_start("EP-3")  # Recorded before EP-3's assignment: not its start.
        three = d.entry("lane.assigned", "EP-3", lane="lane-1")
        two = d.entry("lane.assigned", "EP-2", lane="lane-1")
        opened = d.entry("lease.granted", "EP-2")
        d.command_line(opened, "transition", move="review", transition="Submit for review")
        d.lane_start("EP-2")
        self.published(d)
        self.assertEqual(d.lines(three["id"]), [], "a start recorded before the assignment leaves it waiting")
        self.assertEqual(d.outcomes(two["id"]), ["skip"])
        self.assertEqual(d.lines(two["id"])[0]["reason"], "the story is already in review")
        self.assertEqual((d.status("EP-2"), d.status("EP-3")), ("Backlog", "Backlog"))

    def test_a_ledger_left_beside_a_replaced_record_covers_none_of_its_entries(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        d.entry("lease.granted", "EP-2")
        self.published(d)
        d.journal, d.clock = [], d.clock + 1000
        d.entry("decision.recorded", summary="Record starts again.")
        again = d.entry("lease.granted", "EP-2", "The lease, under the new record")
        self.published(d)
        self.assertEqual(d.outcomes(again["id"]), ["comment"])
        self.assertEqual(len(d.comments("EP-2")), 2)

    def test_the_cut_is_said_and_counted_as_the_tracker_counts(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        entry = d.entry("blocker.recorded", "EP-2", "\U0001F600" * 20000)
        self.published(d)
        [text] = d.comments("EP-2")
        self.assertLessEqual(tp.units(text), 32767)
        self.assertIn("[Cut to the tracker's 32767-character limit; the rest is in the EP-1 delivery record, "
                      f"journal entry {entry['id']}.]", text)
        self.assertEqual(d.outcomes(entry["id"]), ["comment"])

    def test_a_torn_ledger_line_loses_only_itself(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        first = d.entry("lease.granted", "EP-2")
        second = d.entry("lease.released", "EP-2")
        (d.state_dir / tp.LEDGER).write_text('{"journal": 1, "outc')
        self.published(d)
        self.assertEqual((d.outcomes(1), d.outcomes(first["id"]), d.outcomes(second["id"])),
                         (["skip"], ["comment"], ["comment"]))

    def test_a_replaced_records_move_at_the_same_journal_id_is_still_made(self) -> None:
        d = self.delivery({"lead": {"candidate.merged": ["Complete"]}})
        d.ticket("EP-2", status="In Review")
        d.ticket("EP-3", status="In Review")
        d.entry("candidate.merged", "EP-2")
        self.published(d)
        d.journal, d.clock = [], d.clock + 1000
        d.entry("decision.recorded", summary="Record starts again.")
        again = d.entry("candidate.merged", "EP-3")
        failed = d.entry("candidate.stopped", "EP-3")
        self.published(d)
        self.assertEqual(d.outcomes(again["id"]), ["transition", "comment"])
        self.assertEqual((d.status("EP-2"), d.status("EP-3")), ("Done", "Done"))
        self.assertEqual(d.outcomes(failed["id"]), ["comment"])

    def test_needs_you_reads_only_the_current_records_lines(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        d.entry("lease.granted", "EP-2")
        d.fail("comment", 400, ticket="EP-2")
        self.published(d)
        self.assertEqual([item["holding"] for item in d.needs_you()], [False])
        d.journal, d.clock = [], d.clock + 1000
        d.entry("decision.recorded", summary="Record starts again.")
        self.published(d)
        self.assertEqual(d.needs_you(), [], "the replaced record's refusal isn't this record's")

    def test_a_handled_move_ends_its_sequence_on_a_retry_too(self) -> None:
        # "Start work" isn't offered from In Review, so "Complete" is never tried, on the retry either.
        d = self.delivery({"lead": {"candidate.merged": ["Start work", "Complete"]}})
        d.ticket("EP-2", status="In Review")
        entry = d.entry("candidate.merged", "EP-2")
        d.fail("comment", 503, ticket="EP-2")
        self.assertEqual(d.publish()[0], 1)
        self.published(d)
        self.assertEqual(d.outcomes(entry["id"]), ["handled", "comment"])
        self.assertEqual(d.status("EP-2"), "In Review")
        self.assertIn('The move "Start work" isn\'t offered from In Review', d.comments("EP-2")[0])

    def test_a_follow_up_filed_before_a_failed_comment_is_named_in_it(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        entry = d.entry("decision.recorded", "EP-2", "Deferred F3")
        d.mark(entry, {"story": "EP-2", "finding": "F3", "title": "Later"})
        d.fail("comment", 503, ticket="EP-2")
        self.assertEqual(d.publish()[0], 1)
        self.published(d)
        [created] = [call[1] for call in d.calls() if call[0] == "create"]
        self.assertIn(f"Follow-up: {created}.", d.comments("EP-2")[0])

    def test_a_reassignment_at_the_same_moment_as_the_start_takes_it(self) -> None:
        d = self.delivery({"start": ["Start work"]})
        d.ticket("EP-2")
        first = d.entry("lane.assigned", "EP-2", lane="lane-1")
        second = d.entry("lane.assigned", "EP-2", lane="lane-2")
        d.flow_log.mkdir(parents=True, exist_ok=True)
        with open(d.flow_log / f"{EPIC}.lane-starts.jsonl", "a") as log:
            log.write(json.dumps({"subject": "EP-2", "lane": "lane-2", "at": second["at"]}) + "\n")
        self.published(d)
        self.assertEqual((d.outcomes(first["id"]), d.outcomes(second["id"])), (["skip"], ["transition", "comment"]))

@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class JiraPublisherTests(Scenarios, unittest.TestCase):
    BACKEND = JiraBackend


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class ContractOnlyPublisherTests(Scenarios, unittest.TestCase):
    """The same scenarios against an adapter that implements only `tracker-adapter/1`, with no Jira code."""
    BACKEND = FileBackend


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class AdapterContractTests(unittest.TestCase):
    def run_story(self, backend) -> tuple:
        d = Delivery(self, backend, {"start": ["Start work"], "lead": {"candidate.merged": ["Submit for review"]},
                                     "targets": {"Submit for review": "In Review"}})
        d.ticket("EP-2")
        d.ticket("EP-3", status="In Review")
        d.entry("lane.assigned", "EP-2", lane="lane-1")
        d.lane_start("EP-2")
        d.entry("candidate.merged", "EP-2")
        d.entry("candidate.merged", "EP-3")
        deferral = d.entry("decision.recorded", "EP-2", "Deferred F2")
        d.mark(deferral, {"story": "EP-2", "finding": "F2", "title": "Later"})
        d.entry("blocker.recorded", "EP-3", "y" * 33000)
        d.fail("comment", 400, ticket="EP-3", times=1)
        for _ in range(2):
            self.assertEqual(d.publish()[0], 0)
        store = d.backend.read()
        tracker = {key: (issue["status"], [c["text"] for c in issue["comments"]], sorted(issue["labels"]))
                   for key, issue in store["issues"].items()}
        ledger = [(line["journal"], line["outcome"], line.get("transition")) for line in d.ledger()]
        return tracker, ledger, store["calls"]

    def test_the_publisher_posts_moves_and_files_the_same_through_any_adapter(self) -> None:
        jira = self.run_story(JiraBackend)
        other = self.run_story(FileBackend)
        self.assertEqual(jira, other)


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class HostOutboxTests(unittest.TestCase):
    def test_an_undrained_host_outbox_raises_needs_you_naming_the_updates(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="tracker-host-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        root, state = tmp / "repo", tmp / "repo/.orchestration/state"
        (root / ".agents").mkdir(parents=True)
        (root / ".agents/orchestration.json").write_text(json.dumps({"state": {
            "directory": ".orchestration/state", "renderers": [], "publishers": [{"id": "tracker", "kind": "host"}],
            "trackerSnapshot": {"argv": ["python3", "snapshot.py", "--kind", "linear", "--parent", "{parent}",
                                         "--token-env", "FX_TOKEN"]}}}))
        conf = tp.settings(root)
        self.assertEqual((conf["mode"], tp.refusal(conf)), ("host", None))
        tool = lambda *args: json.loads(subprocess.run([sys.executable, "-B", str(STATE_TOOL), "--dir", str(state), *args],
                                                       capture_output=True, text=True, check=True).stdout or "{}")
        tool("init", "--headline", "A delivery")
        tool("journal", "--kind", "decision.recorded", "--summary", "Record starts")
        tool("journal", "--kind", "candidate.handed", "--subject", "EP-2", "--summary", "Handed off")
        subprocess.run([sys.executable, "-B", str(RENDER), "--dir", str(state), "--binding",
                        str(root / ".agents/orchestration.json")], capture_output=True, check=True)
        doc = tool("show")
        [item] = tp.needs_you(doc, state, state.parent / "flow-log", "EP-1", conf)
        self.assertTrue(item["holding"])
        self.assertIn("2 candidate.handed", item["text"])
        version = doc["state"]["version"]
        tool("ack", "--version", str(version), "--through", "2")
        self.assertEqual(tp.needs_you(tool("show"), state, state.parent / "flow-log", "EP-1", conf), [])

    def test_settings_refuse_bad_targets_and_name_the_moves_without_one(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="tracker-targets-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        own = lambda targets: {"tracker": {"argv": ["adapter"], "transitions": {
            "start": ["Start work"], "lead": {"candidate.merged": ["Complete"], "delivery.closed": ["Close epic"]},
            "targets": targets}}}
        self.assertIn("tracker.transitions.targets", tp.refusal(tp.settings(tmp, own({"Complete": 7}), {})))
        self.assertEqual(tp.untargeted(tp.settings(tmp, own({"Complete": "Done"}), {})), ["Close epic", "Start work"])
        self.assertEqual(tp.untargeted(tp.settings(tmp, own({}), {})), ["Close epic", "Complete", "Start work"])
        for bad in ({"lead": ["Complete"]}, {"start": "Start work"}):
            conf = tp.settings(tmp, {"tracker": {"argv": ["adapter"], "transitions": bad}}, {})
            self.assertTrue(tp.refusal(conf))
            self.assertEqual(tp.untargeted(conf), [], "a malformed tracker is refused, never read for its moves")

    def test_a_tracker_with_no_known_adapter_needs_some_publisher_unless_manual(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="tracker-unknown-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for snapshot in (["tools/own_snapshot.py", "{parent}"], ["snapshot.py", "--kind", "other"]):
            with self.subTest(snapshot=snapshot):
                binding = {"state": {"publishers": [], "trackerSnapshot": {"argv": snapshot}}}
                conf = tp.settings(tmp, {}, binding)
                self.assertIsNone(conf["mode"])
                self.assertIn("the binding has no tracker publisher", tp.refusal(conf))
                binding["state"]["publishers"] = [{"id": "tracker", "kind": "host"}]
                self.assertIsNone(tp.refusal(tp.settings(tmp, {}, binding)))
                binding["state"]["publishers"] = []
                self.assertIsNone(tp.refusal(tp.settings(tmp, {"tracker": {"updates": "manual"}}, binding)))
        self.assertIsNone(tp.refusal(tp.settings(tmp, {}, {"state": {"publishers": []}})), "no tracker, no refusal")

    def test_a_host_tracker_without_a_host_publisher_is_refused_unless_manual(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="tracker-host-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        binding = {"state": {"publishers": [], "trackerSnapshot": {"argv": ["snapshot.py", "--kind", "linear"]}}}
        self.assertIn("host publisher", tp.refusal(tp.settings(tmp, {}, binding)))
        self.assertIsNone(tp.refusal(tp.settings(tmp, {"tracker": {"updates": "manual"}}, binding)))
        jira = {"state": {"publishers": [], "trackerSnapshot": {"argv": [
            "snapshot.py", "--kind", "jira", "--site", "https://t.test", "--token-env", "T", "--user-env", "U"]}}}
        self.assertIn("no tracker publisher", tp.refusal(tp.settings(tmp, {}, jira)))
        jira["state"]["publishers"] = [tp.publisher_entry(".agent-skills/current/skills/run-delivery/scripts/tracker_publisher.py")]
        self.assertIsNone(tp.refusal(tp.settings(tmp, {}, jira)))
        self.assertEqual(tp.settings(tmp, {}, jira)["adapter"],
                         {"name": "jira", "site": "https://t.test", "userEnv": "U", "tokenEnv": "T"})


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class RenderedPublisherTests(unittest.TestCase):
    def test_the_binding_runs_the_publisher_on_every_render_with_its_state_directory(self) -> None:
        d = Delivery(self, JiraBackend)
        d.ticket("EP-2")
        binding = {"state": {"directory": ".orchestration/state", "renderers": [],
                             "publishers": [tp.publisher_entry(str(PUBLISHER))]}}
        binding["state"]["publishers"][0]["argv"] += ["--repo", str(d.root)]
        (d.root / ".agents/orchestration.json").write_text(json.dumps(binding))
        state = d.state_dir
        tool = lambda *args: subprocess.run([sys.executable, "-B", str(STATE_TOOL), "--dir", str(state), *args],
                                            capture_output=True, text=True, check=True, env=d.env)
        tool("init", "--headline", "A delivery")
        version = json.loads(tool("show").stdout)["state"]["version"]
        tool("patch", "--writer", "lead", "--version", str(version), "--patch",
             json.dumps({"meta": {"trackerParent": EPIC}}))
        tool("journal", "--kind", "decision.recorded", "--summary", "Record starts")
        tool("journal", "--kind", "lease.granted", "--subject", "EP-2", "--summary", "The app lease")
        render = lambda: subprocess.Popen([sys.executable, "-B", str(RENDER), "--dir", str(state), "--binding",
                                           str(d.root / ".agents/orchestration.json")], cwd=d.root, env=d.env,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        runs = [render(), render()]
        for run in runs:
            out, err = run.communicate(timeout=120)
            self.assertEqual(run.returncode, 0, out + err)
        out, err = render().communicate(timeout=120)
        self.assertEqual(json.loads(out)["publishers"][0]["ok"], True, out + err)
        self.assertEqual(len(d.comments("EP-2")), 1)
        self.assertEqual(sorted(line["journal"] for line in d.ledger()), [1, 2])


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class RunDeliveryTrackerCommandTests(unittest.TestCase):
    """The commands' side: lead-setup binds the publisher, lead-init refuses a tracker whose updates nothing posts,
    lead-assign journals each story's own start, and lead-plan change marks its decision for the publisher."""

    @classmethod
    def setUpClass(cls) -> None:
        from tests import test_run_delivery as harness
        cls.harness = harness
        cached = harness.RELEASE_CACHE.get("release")
        if cached is not None and not cached.receipt.exists():
            # Built for test_run_delivery's own run and removed by its module cleanup: build one for this module.
            harness.RELEASE_CACHE.pop("release")
        for name, value in vars(harness.installed_release()).items():
            setattr(cls, name, value)

    def fixture(self, binding: bool = False):
        h = self.harness
        fx = h.Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        if binding:
            m = fx.main
            bound = h.doctor_binding()
            bound["state"]["publishers"] = []
            (m / ".agents/orchestration.json").write_text(json.dumps(bound, indent=2))
            conf = json.loads((m / ".agents/run-delivery.json").read_text())
            del conf["gates"]  # The binding's fallback checks are the gates.
            (m / ".agents/run-delivery.json").write_text(json.dumps(conf, indent=2))
            h.commit(m, "FX-1: bind the repository to a Jira tracker")
            h.sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        return fx

    def journal(self, fx) -> list[dict]:
        return json.loads(self.harness.sh(sys.executable, "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py",
                                          "--dir", fx.tmp / "record", "show").stdout)["journal"]

    def test_lead_setup_binds_the_tracker_publisher_and_lead_init_refuses_without_it(self) -> None:
        fx = self.fixture(binding=True)
        m = fx.main
        self.harness.setup_delivery(fx)
        path = m / ".agents/orchestration.json"
        publishers = json.loads(path.read_text())["state"]["publishers"]
        self.assertEqual(publishers, [tp.publisher_entry(".agent-skills/current/skills/run-delivery/scripts/tracker_publisher.py")])
        self.assertIsNone(tp.refusal(tp.settings(m)))
        bound = path.read_text()
        path.write_text(bound.replace('"publishers": [', '"publishers": [], "was": [', 1))
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 2, out)
        self.assertIn("the tracker has an adapter, but the binding has no tracker publisher", out)
        conf = json.loads((m / ".agents/run-delivery.json").read_text())
        (m / ".agents/run-delivery.json").write_text(json.dumps({**conf, "tracker": {**conf["tracker"], "updates": "manual"}}))
        path.write_text(bound.replace('"publishers": [', '"publishers": [], "was": [', 1).replace(', "was": [', ', "wasnt": [', 1))
        self.assertIsNone(tp.refusal(tp.settings(m)), "manual opts out")

    def test_lead_assign_journals_each_story_and_lead_plan_change_marks_its_decision(self) -> None:
        h = self.harness
        fx = self.fixture()
        m = fx.main
        path = h.plan_delivery(fx)
        d = json.loads(path.read_text())
        d["stories"]["FX-3"]["lane"] = "lane-1"
        d["lanes"]["lane-1"]["owns"] = ["src/one*", "src/two*"]
        d["lanes"]["lane-2"]["owns"] = ["docs/never*"]
        path.write_text(json.dumps(d, indent=2))
        h.commit(m, "FX-1: one lane")
        h.sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-assign", "FX-2", "FX-3", "--lane", "lane-1", "--title", "Lane 1", cwd=m)
        self.assertEqual(code, 0, out)
        assigned = [(e["subject"], e["lane"]) for e in self.journal(fx) if e["kind"] == "lane.assigned"]
        self.assertEqual(assigned, [("FX-2", "lane-1"), ("FX-3", "lane-1")])
        before = len(self.journal(fx))
        code, out = fx.flow("lead-plan", "change", "--kind", "relaxed", "--subjects", "FX-2", "--criterion", "one-fails",
                            "--text", "Thing one says it failed", "--why", "The operator eased it.", cwd=m)
        self.assertEqual(code, 0, out)
        written = [e for e in self.journal(fx)[before:] if e["kind"] == "decision.recorded"]
        self.assertEqual(len(written), 1, written)
        marks = tp.read_flow(fx.tmp / "flow-log", "FX-1", tp.MARKS)
        self.assertEqual(marks[-1]["journal"], written[0]["id"])
        self.assertEqual([mark["journal"] for mark in marks], [written[0]["id"]],
                         "only the lead's plan change is marked: lead-init's plan build isn't")


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class MarkedDecisionTests(unittest.TestCase):
    def test_a_marked_decision_holds_the_ledger_lock_across_its_journal_write_and_mark(self) -> None:
        import types
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location("run_delivery_under_test", SCRIPTS / "run_delivery.py")
            run_delivery = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(run_delivery)
        finally:
            sys.dont_write_bytecode = previous
        tmp = Path(tempfile.mkdtemp(prefix="tracker-mark-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        c = types.SimpleNamespace(state=tmp / "state", flow_log=tmp / "flow-log", delivery="EP-1")
        held_while_marking = []
        real_mark = run_delivery.tracker_publisher.mark

        def mark(*args, **kwargs):
            with run_delivery.tracker_publisher.ledger_lock(c.state, wait=False) as taken:
                held_while_marking.append(not taken)
            return real_mark(*args, **kwargs)

        self.addCleanup(setattr, run_delivery.tracker_publisher, "mark", real_mark)
        run_delivery.tracker_publisher.mark = mark
        with run_delivery.marked_decision(c) as written:
            with run_delivery.tracker_publisher.ledger_lock(c.state, wait=False) as taken:
                self.assertFalse(taken, "a publisher run inside the window must find the ledger held")
            written.append(7)
            self.assertEqual(tp.read_flow(c.flow_log, "EP-1", tp.MARKS), [], "the mark follows the journal write")
        self.assertEqual([m["journal"] for m in tp.read_flow(c.flow_log, "EP-1", tp.MARKS)], [7])
        self.assertEqual(held_while_marking, [True], "the mark is written while the ledger is still held")
        with run_delivery.tracker_publisher.ledger_lock(c.state, wait=False) as taken:
            self.assertTrue(taken, "released once marked")


if __name__ == "__main__":
    unittest.main()
