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
import http.client
import http.server
import importlib.util
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

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


class _Server(http.server.ThreadingHTTPServer):
    # HTTPServer.server_bind looks up the address's host name before it listens; where reverse lookups go unanswered
    # (macOS runners) that takes about 34 seconds per server. This one names its address without a lookup, as
    # `STATIC_SERVE` in tests/test_run_delivery.py does.
    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


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

        self.server = _Server(("127.0.0.1", 0), Handler)
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
        if rule["status"] == "nojson" and rule["op"] == args.op and rule["times"] != 0:
            rule["times"] -= 1
            save()
            print("not json")
            sys.exit(0)
        if (rule["op"] == args.op and rule.get("ticket") in (None, target) and rule.get("name") in (None, args.name)
                and rule["times"] != 0):
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


LINEAR_ADAPTER = ROOT / "skills/run-delivery/adapters/tracker_linear.py"
# The team's workflow: (name, type), in Linear's position order.
LINEAR_WORKFLOW = [("Triage", "triage"), ("Backlog", "backlog"), ("Todo", "unstarted"), ("In Progress", "started"),
                   ("In Review", "started"), ("Done", "completed"), ("Canceled", "canceled")]
# Set here from what Linear's API says when it refuses a longer body ("body must be shorter than or equal to 100000
# characters"), counted as JavaScript counts, not from the adapter's constant.
LINEAR_BODY_LIMIT = 100000
LINEAR_TOKEN = "lin_api_fixtureSecret0123456789"
# Each operation the adapter may send, with the variables it must carry (a dotted path reads into `input`).
LINEAR_OPERATIONS = {"Status": ["id"], "Team": ["key"], "Comments": ["id"], "Labelled": ["team", "label"],
                     "Label": ["name"], "CommentCreate": ["input.issueId", "input.body"],
                     "IssueUpdate": ["id", "input.stateId"], "IssueLabelCreate": ["input.name", "input.teamId"],
                     "IssueCreate": ["input.teamId", "input.title", "input.labelIds"]}
LINEAR_CARRIER = re.compile(r"<!-- run-delivery:(.+?) -->\s*\Z")


def linear_error(code: str, message: str | None = None) -> dict:
    """Linear's error shape, as it answers an invalid key."""
    return {"errors": [{"message": message or code.lower(), "extensions": {"code": code, "type": code.lower()}}]}


class FakeLinear:
    """Linear's GraphQL API, as much as the adapter uses, on port 0, keeping the same store as the fake Jira.

    It refuses an unknown operation or one missing a required variable, and an `Authorization` header that isn't the
    token exactly, with Linear's 401 shape. A failure rule {op, ticket, status, code, times} names a GraphQL operation:
    with `code` (or several `codes`) it answers `errors` at `status` (200 when left out); with `data`, that `data` at
    200; with neither, a bare HTTP `status`."""

    def __init__(self, token: str = LINEAR_TOKEN) -> None:
        self.store = blank_store()
        self.store["token"] = token
        self.store["labels"] = []
        self.store["requests"] = 0
        self.lock = threading.Lock()
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                document = json.loads(self.rfile.read(length) or b"null")
                with fake.lock:
                    status, answer = fake.answer(self.path, document, self.headers.get("Authorization", ""))
                body = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = _Server(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.site = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def state(self, name: str) -> dict:
        index = [n for n, _ in LINEAR_WORKFLOW].index(name)
        return {"id": f"state-{index}", "name": name, "type": LINEAR_WORKFLOW[index][1], "position": index}

    def answer(self, path: str, document: dict, auth: str):
        self.store["requests"] += 1
        if path != "/graphql":
            return 404, {}
        if auth != self.store["token"]:
            return 401, linear_error("AUTHENTICATION_ERROR", "Authentication required, not authenticated")
        operation, variables = document.get("operationName"), document.get("variables") or {}
        self.last_query = document.get("query", "")
        if operation not in LINEAR_OPERATIONS or f"query {operation}(" not in document.get("query", "") \
                and f"mutation {operation}(" not in document.get("query", ""):
            return 400, linear_error("GRAPHQL_VALIDATION_FAILED", f"unknown operation {operation}")
        for needed in LINEAR_OPERATIONS[operation]:
            value = variables
            for part in needed.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if value in (None, "", []):
                return 400, linear_error("GRAPHQL_VALIDATION_FAILED", f"{operation} needs {needed}")
        inputs = variables.get("input") or {}
        target = variables.get("id") or inputs.get("issueId") or variables.get("key") or variables.get("team")
        for rule in self.store["failures"]:
            if rule["op"] == operation and rule.get("ticket") in (None, target) and rule["times"] != 0:
                rule["times"] -= 1
                if rule.get("data") is not None:
                    return 200, {"data": rule["data"]}
                if rule.get("codes") or rule.get("code"):
                    errors = [linear_error(code, rule.get("message"))["errors"][0]
                              for code in rule.get("codes") or [rule["code"]]]
                    return rule.get("status") or 200, {"errors": errors}
                return rule["status"], {}
        issues = self.store["issues"]
        data = getattr(self, "op_" + operation)(variables, inputs, issues)
        if isinstance(data, tuple):
            return data
        return 200, {"data": data}

    def team_of(self, key: str) -> str:
        return key.rsplit("-", 1)[0]

    def op_Status(self, v, i, issues):
        issue = issues.get(v["id"])
        if issue is None:
            return 200, {"data": {"issue": None}, **linear_error("INPUT_ERROR", "Entity not found")}
        return {"issue": {"identifier": v["id"], "state": self.state(issue["status"]),
                          "team": {"states": {"nodes": [self.state(name) for name, _ in reversed(LINEAR_WORKFLOW)]}}}}

    def op_Team(self, v, i, issues):
        known = v["key"] in self.store["projects"]
        return {"teams": {"nodes": [{"id": f"team-{v['key']}", "key": v["key"]}] if known else []}}

    def op_Comments(self, v, i, issues):
        issue = issues.get(v["id"])
        if issue is None:
            return 200, linear_error("INPUT_ERROR", "Entity not found")
        size = int(re.search(r"comments\(first: (\d+)", self.last_query).group(1))
        start = int(v.get("after") or 0)
        page = issue["comments"][start:start + size]
        self.store["calls"].append(["comments-page", v["id"], start])
        return {"issue": {"comments": {"nodes": [{"body": c["text"]} for c in page],
                                       "pageInfo": {"hasNextPage": start + size < len(issue["comments"]),
                                                    "endCursor": str(start + size)}}}}

    def op_Labelled(self, v, i, issues):
        return {"issues": {"nodes": [{"identifier": k} for k, issue in issues.items()
                                     if v["label"] in issue["labels"] and self.team_of(k) == v["team"]][:10]}}

    def op_Label(self, v, i, issues):
        # A workspace label has no team.
        return {"issueLabels": {"nodes": [{"id": label["id"], "team": {"id": label["team"]} if label["team"] else None}
                                          for label in self.store["labels"] if label["name"] == v["name"]]}}

    def op_IssueLabelCreate(self, v, i, issues):
        label = {"id": f"label-{len(self.store['labels'])}", "name": i["name"], "team": i["teamId"]}
        self.store["labels"].append(label)
        return {"issueLabelCreate": {"success": True, "issueLabel": {"id": label["id"]}}}

    def op_CommentCreate(self, v, i, issues):
        if len(i["body"].encode("utf-16-le")) // 2 > LINEAR_BODY_LIMIT:
            return 200, linear_error("INVALID_INPUT", "body must be shorter than or equal to 100000 characters")
        issue = issues.get(i["issueId"])
        if issue is None:
            return 200, linear_error("INPUT_ERROR", "issue not found")
        found = LINEAR_CARRIER.search(i["body"])
        issue["comments"].append({"text": i["body"], "markers": [found.group(1)] if found else []})
        self.store["calls"].append(["comment", i["issueId"]])
        return {"commentCreate": {"success": True}}

    def op_IssueUpdate(self, v, i, issues):
        issue = issues.get(v["id"])
        state = next((name for name, _ in LINEAR_WORKFLOW if self.state(name)["id"] == i["stateId"]), None)
        if issue is None or state is None:
            return 200, linear_error("INPUT_ERROR", "Entity not found")
        issue["status"] = state
        self.store["calls"].append(["transition", v["id"], state])
        return {"issueUpdate": {"success": True}}

    def op_IssueCreate(self, v, i, issues):
        team = i["teamId"].removeprefix("team-")
        self.store["next"] += 1
        key = f"{team}-{self.store['next']}"
        names = [label["name"] for label in self.store["labels"] if label["id"] in i["labelIds"]]
        issues[key] = {"status": "Backlog", "comments": [], "markers": [], "labels": names,
                       "parent": i.get("parentId"), "summary": i["title"], "description": i.get("description")}
        self.store["calls"].append(["create", key])
        return {"issueCreate": {"success": True, "issue": {"identifier": key}}}


class LinearBackend:
    name = "linear"

    def __init__(self, folder: Path) -> None:
        self.linear = FakeLinear()
        self.store = self.linear.store

    def close(self) -> None:
        self.linear.close()

    def adapter(self) -> dict:
        return {"adapter": "linear", "site": self.linear.site, "tokenEnv": "FX_TRACKER_TOKEN"}

    def env(self) -> dict:
        return {"FX_TRACKER_TOKEN": LINEAR_TOKEN}

    def edit(self):
        return contextlib.nullcontext(self.store)

    def read(self) -> dict:
        with self.linear.lock:
            return json.loads(json.dumps(self.store))


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
        if self.backend.name in ("jira", "linear"):
            self.tracker["site"] = "http://127.0.0.1:9"
            self.write_config()
        else:
            self.fail("comment", None, times=-1)

    def reachable(self) -> None:
        if self.backend.name in ("jira", "linear"):
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


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class LaneCommandTests(RunDeliveryTrackerCommandTests):
    """The lane's tracker steps as commands (C5): lane-start records the start the publisher's start move waits for,
    the review move follows reviewWhen, and handoff posts its own update through the ledger, or leaves it to the
    publisher when the tracker refuses it."""

    def test_lead_assign_journals_each_story_and_lead_plan_change_marks_its_decision(self) -> None:
        pass  # The parent's case; not repeated here.

    def test_lead_setup_binds_the_tracker_publisher_and_lead_init_refuses_without_it(self) -> None:
        pass  # The parent's case; not repeated here.

    def lane(self, transitions: dict, review_pr: bool = False, **tracker):
        """FX-3 (lane-2) assigned, with a contract-only tracker; in-app checks off, so a hand-off needs only rounds."""
        h = self.harness
        fx = self.fixture()
        m = fx.main
        path = h.setup_delivery(fx)
        d = json.loads(path.read_text())
        d["stories"]["FX-3"]["inApp"] = "none"
        path.write_text(json.dumps(d, indent=2))
        h.commit(m, "FX-1: FX-3 needs no in-app checks")
        h.sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        backend = FileBackend(fx.tmp)
        with backend.edit() as store:
            for key, status in (("FX-1", "In Progress"), ("FX-2", "Backlog"), ("FX-3", "Backlog")):
                store["issues"][key] = {"status": status, "comments": [], "markers": [], "labels": [], "parent": None,
                                        "summary": key}
        conf = json.loads((m / ".agents/run-delivery.json").read_text())
        conf["tracker"] = {**conf["tracker"], **backend.adapter(), **tracker, "transitions": transitions}
        if review_pr:
            conf["review"]["pr"] = {"author": "Pipeline Bot", "waitSeconds": 1}
        own = fx.tmp / "run-delivery.json"
        own.write_text(json.dumps(conf, indent=2))
        fx.env.update({"RUN_DELIVERY_CONFIG": str(own), **backend.env()})
        code, out = fx.flow("lead-init", cwd=m)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertIn("lane-start FX-3 --base feature/FX-1", out)
        return fx, backend

    def publish(self, fx) -> None:
        doc = self.harness.sh(sys.executable, "-B", STATE_TOOL, "--dir", fx.tmp / "record", "show").stdout
        done = subprocess.run([sys.executable, "-B", str(PUBLISHER), "--state-dir", str(fx.tmp / "record"),
                               "--repo", str(fx.main), "--delivery", "FX-1"], input=doc, text=True,
                              capture_output=True, env=fx.env, cwd=fx.main)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def started(self, fx) -> Path:
        code, out = fx.flow("lane-start", "FX-3", "--base", "feature/FX-1", cwd=fx.main)
        self.assertEqual(code, 0, out)
        return fx.main / "lanes/FX-3"

    def opened(self, fx, wt: Path) -> str:
        (wt / "src/two.txt").write_text("two\n")
        self.harness.commit(wt, "FX-3: two")
        code, out = fx.flow("push", "FX-3", cwd=wt)
        self.assertEqual(code, 0, out)
        body = fx.tmp / "body.md"
        body.write_text("Summary\n")
        code, out = fx.flow("open-pr", "FX-3", "--title", "FX-3: two", "--body-file", body, cwd=wt)
        self.assertEqual(code, 0, out)
        return out

    def calls(self, backend, kind: str) -> list:
        return [call for call in backend.read()["calls"] if call[0] == kind]

    # Lane start.

    def test_lane_start_makes_the_checkout_and_records_the_start_the_publisher_moves_once(self) -> None:
        fx, backend = self.lane({"start": ["Start work"]})
        self.publish(fx)
        self.assertEqual(backend.read()["issues"]["FX-3"]["status"], "Backlog", "nothing moves before lane-start")
        wt = self.started(fx)
        self.assertEqual(self.harness.sh("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=wt).stdout.strip(), "feature/FX-3")
        self.assertEqual(backend.read()["calls"], [], "lane-start makes no tracker call of its own")
        record = json.loads(self.harness.sh(sys.executable, "-B", STATE_TOOL, "--dir", fx.tmp / "record", "show").stdout)
        self.assertEqual({l["id"]: l["focus"] for l in record["state"]["lanes"]}["lane-2"], "FX-3: started")
        self.publish(fx)
        self.publish(fx)
        issue = backend.read()["issues"]["FX-3"]
        self.assertEqual(issue["status"], "In Progress")
        self.assertEqual(len([c for c in issue["comments"] if c["text"].startswith("Started")]), 1)
        starts = fx.tmp / "flow-log/FX-1.lane-starts.jsonl"
        before = (starts.read_text(), (fx.tmp / "record/state.json").read_text())
        code, out = fx.flow("lane-start", "FX-3", "--base", "feature/FX-1", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("already started; nothing to do", out)
        self.assertEqual((starts.read_text(), (fx.tmp / "record/state.json").read_text()), before)

    def test_lane_start_refuses_a_base_the_binding_doesnt_serve(self) -> None:
        fx, _ = self.lane({"start": ["Start work"]})
        code, out = fx.flow("lane-start", "FX-3", "--base", "release/1", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("doesn't serve the base release/1", out)
        self.assertFalse((fx.main / "lanes/FX-3").exists())

    def test_with_a_host_only_tracker_lane_start_prints_the_move_and_post_and_records_them(self) -> None:
        fx, backend = self.lane({"start": ["Start work"]}, updates="host")
        code, out = fx.flow("lane-start", "FX-3", "--base", "feature/FX-1", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn('Tracker move (your story FX-3): "Start work", with your tracker tools.', out)
        self.assertIn("Tracker post (your story FX-3): Started (lane-2)", out)
        printed = tp.read_flow(fx.tmp / "flow-log", "FX-1", "lane-prints")
        self.assertEqual([(p["subject"], p["move"], p["transitions"]) for p in printed], [("FX-3", "start", ["Start work"])])
        self.assertEqual(backend.read()["calls"], [])

    # Review move.

    def test_with_no_review_when_open_pr_prints_the_move_and_nothing_moves(self) -> None:
        for review_pr in (False, True):
            with self.subTest(review_pr=review_pr):
                fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review"]}, review_pr=review_pr)
                wt = self.started(fx)
                out = self.opened(fx, wt)
                self.assertIn('Tracker move (your story FX-3): "Submit for review", with your tracker tools.', out)
                self.assertEqual(self.calls(backend, "transition"), [])

    def test_with_pr_open_open_pr_makes_the_review_move_once(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "pr-open"})
        wt = self.started(fx)
        self.publish(fx)
        out = self.opened(fx, wt)
        self.assertIn('Moved with "Submit for review".', out)
        self.assertEqual(backend.read()["issues"]["FX-3"]["status"], "In Review")
        body = fx.tmp / "body.md"
        code, out = fx.flow("open-pr", "FX-3", "--title", "FX-3: two", "--body-file", body, cwd=wt)
        self.assertIn("review move is already made", out)
        self.assertEqual([c[2] for c in self.calls(backend, "transition")], ["Start work", "Submit for review"])
        [line] = [l for l in tp.read_ledger(fx.tmp / "record") if l.get("move") == "review"]
        self.assertEqual((line["journal"], line["writer"], line["ticket"]), (tp.LANE_MOVE, "lane", "FX-3"))

    def test_with_gates_passed_open_pr_makes_the_move_once_the_gates_pass(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "gates-passed"})
        wt = self.started(fx)
        self.publish(fx)
        out = self.opened(fx, wt)
        self.assertIn("review move waits for the gates to pass", out)
        self.assertEqual(backend.read()["issues"]["FX-3"]["status"], "In Progress")
        code, out = fx.flow("gate", "FX-3", cwd=wt)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("open-pr", "FX-3", "--title", "FX-3: two", "--body-file", fx.tmp / "body.md", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(backend.read()["issues"]["FX-3"]["status"], "In Review")

    def test_with_pipeline_green_only_pr_review_makes_the_move(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "pipeline-green"},
                                review_pr=True)
        wt = self.started(fx)
        self.publish(fx)
        out = self.opened(fx, wt)
        self.assertNotIn("Tracker move", out)
        self.assertEqual(self.calls(backend, "transition"), [["transition", "FX-3", "Start work"]])
        reviews = fx.tmp / "reviews.json"
        fx.env["FAKE_FORGE_REVIEWS"] = str(reviews)
        reviews.write_text(json.dumps({"checks": [{"name": "pipe", "state": "pending", "at": "2026-01-01T00:00:10"}],
                                       "reviews": []}))
        code, out = fx.flow("pr-review", "FX-3", cwd=wt)
        self.assertEqual(code, 2, out)
        self.assertEqual(backend.read()["issues"]["FX-3"]["status"], "In Progress")
        reviews.write_text(json.dumps({"checks": [{"name": "pipe", "state": "passed", "at": "2026-01-01T00:00:10"}],
                                       "reviews": [{"author": "Pipeline Bot", "at": "2026-01-01T00:00:30", "body": "ok"}]}))
        code, out = fx.flow("pr-review", "FX-3", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(backend.read()["issues"]["FX-3"]["status"], "In Review")

    def test_pipeline_green_without_a_pr_review_is_refused(self) -> None:
        own = {"review": {}, "tracker": {"argv": ["adapter"], "transitions": {"review": ["R"], "reviewWhen": "pipeline-green"}}}
        self.assertIn("review.pr declares none", tp.refusal(tp.settings(Path(tempfile.gettempdir()), own, {})))
        own["review"]["pr"] = {"author": "Pipeline Bot"}
        self.assertEqual(tp.settings(Path(tempfile.gettempdir()), own, {})["problems"], [])
        own["tracker"]["transitions"]["reviewWhen"] = "soon"
        self.assertIn("must be one of gates-passed, pr-open, pipeline-green", tp.refusal(tp.settings(Path("."), own, {})))

    # Hand-off.

    def reviewed(self, fx, wt: Path) -> None:
        """Everything a hand-off needs short of the hand-off itself: the gate and one approved review round."""
        h = self.harness
        (wt / ".orchestration/review/FX-3").mkdir(parents=True, exist_ok=True)
        (wt / ".orchestration/review/FX-3/context.md").write_text("- evidence")
        for argv in (("gate", "FX-3"), ("review-prepare", "FX-3", "--round", "1")):
            code, out = fx.flow(*argv, cwd=wt)
            self.assertEqual(code, 0, out)
        h.write_results(wt / ".orchestration/review/FX-3", 1, "FX-3", fx.lenses(wt))
        code, out = fx.flow("review-reduce", "FX-3", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)

    def test_a_review_move_an_earlier_record_made_is_made_again_under_this_one(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "pr-open"})
        wt = self.started(fx)
        self.publish(fx)
        # The ledger beside this record holds a handled review move for FX-3 under an earlier record (another init).
        tp.append_ledger(fx.tmp / "record", {"journal": tp.LANE_MOVE, "init": "an-earlier-record", "outcome": "handled",
                                             "writer": "lane", "kind": "lane.review", "ticket": "FX-3", "move": "review",
                                             "transitions": ["Submit for review"]})
        out = self.opened(fx, wt)
        self.assertNotIn("review move is already made", out, "an earlier record's ledger line counted for this one")
        self.assertIn('Moved with "Submit for review".', out)
        self.assertEqual(backend.read()["issues"]["FX-3"]["status"], "In Review")

    def test_a_handoff_that_finds_its_post_already_on_the_ticket_posts_nothing_again(self) -> None:
        fx, backend = self.lane({"start": ["Start work"]})
        wt = self.started(fx)
        self.opened(fx, wt)
        self.reviewed(fx, wt)
        # The hand-off's own update is already on the ticket, as a post whose line was never written would leave it.
        journal = sorted(self.record(fx)["journal"], key=lambda e: e["id"])
        upcoming = journal[-1]["id"] + 1  # the entry the hand-off journals next
        stamp = tp.marker("FX-1", tp.init_id(journal), upcoming, "comment")
        with backend.edit() as store:
            store["issues"]["FX-3"]["markers"].append(stamp)
        code, out = fx.flow("handoff", "FX-3", "--pr", "https://forge.test/pr/1", cwd=wt)
        self.assertEqual(code, 0, out)
        [entry] = [e for e in self.record(fx)["journal"] if e["kind"] == "candidate.handed"]
        self.assertEqual(entry["id"], upcoming, "the marker wasn't the hand-off's own")
        self.assertEqual([c for c in self.calls(backend, "comment")], [], "the hand-off posted its update again")
        handed = [l for l in tp.read_ledger(fx.tmp / "record") if l.get("kind") == "candidate.handed"]
        self.assertEqual([(l["outcome"], l.get("found")) for l in handed], [("comment", True)])

    def handed(self, fx, wt: Path) -> tuple[int, str]:
        h = self.harness
        (wt / ".orchestration/review/FX-3").mkdir(parents=True, exist_ok=True)
        (wt / ".orchestration/review/FX-3/context.md").write_text("- evidence")
        code, out = fx.flow("gate", "FX-3", cwd=wt)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("review-prepare", "FX-3", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        h.write_results(wt / ".orchestration/review/FX-3", 1, "FX-3", fx.lenses(wt))
        code, out = fx.flow("review-reduce", "FX-3", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        return fx.flow("handoff", "FX-3", "--pr", "https://forge.test/pr/1", cwd=wt)

    def test_handoff_posts_its_own_update_once_under_its_journal_entry(self) -> None:
        fx, backend = self.lane({"start": ["Start work"]})
        wt = self.started(fx)
        self.opened(fx, wt)
        code, out = self.handed(fx, wt)
        self.assertEqual(code, 0, out)
        self.assertIn("Tracker post: posted the hand-off update on FX-3.", out)
        handed = [l for l in tp.read_ledger(fx.tmp / "record") if l.get("kind") == "candidate.handed"]
        self.assertEqual([(l["outcome"], l["writer"]) for l in handed], [("comment", "lane")])
        [entry] = [e for e in self.record(fx)["journal"] if e["kind"] == "candidate.handed"]
        self.assertEqual(handed[0]["journal"], entry["id"], "the line is under the entry the hand-off journalled")
        self.publish(fx)
        self.publish(fx)
        texts = [c["text"] for c in backend.read()["issues"]["FX-3"]["comments"]]
        self.assertEqual(sum(t.startswith("Handed off and queued") for t in texts), 1)
        self.assertIn("https://forge.test/pr/1", [t for t in texts if t.startswith("Handed off")][0])

    def test_a_handoff_whose_post_is_refused_still_hands_off_and_the_publisher_posts_it_once(self) -> None:
        fx, backend = self.lane({"start": ["Start work"]})
        wt = self.started(fx)
        self.opened(fx, wt)
        with backend.edit() as store:
            store["failures"].append({"op": "comment", "ticket": "FX-3", "status": 401, "times": 1})
        code, out = self.handed(fx, wt)
        self.assertEqual(code, 0, out)
        self.assertIn("not posted", out)
        self.assertIn("**Hand-off: FX-3**", out)
        record = json.loads(self.harness.sh(sys.executable, "-B", STATE_TOOL, "--dir", fx.tmp / "record", "show").stdout)
        [entry] = [e for e in record["journal"] if e["kind"] == "candidate.handed"]
        self.assertEqual([l for l in tp.read_ledger(fx.tmp / "record") if l["journal"] == entry["id"]], [])
        self.publish(fx)
        self.publish(fx)
        texts = [c["text"] for c in backend.read()["issues"]["FX-3"]["comments"]]
        self.assertEqual(sum(t.startswith("Handed off and queued") for t in texts), 1)

    def test_a_review_move_stopped_partway_is_resumed_by_the_next_run(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review", "Complete"],
                                 "reviewWhen": "pr-open"})
        wt = self.started(fx)
        self.publish(fx)
        with backend.edit() as store:
            store["failures"].append({"op": "transition", "ticket": "FX-3", "name": "Complete", "status": 503, "times": 1})
        out = self.opened(fx, wt)
        self.assertIn("review move wasn't made", out)
        self.assertEqual(backend.read()["issues"]["FX-3"]["status"], "In Review")
        code, out = fx.flow("open-pr", "FX-3", "--title", "FX-3: two", "--body-file", fx.tmp / "body.md", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertIn('Moved with "Complete".', out)
        self.assertEqual(backend.read()["issues"]["FX-3"]["status"], "Done")
        self.assertEqual([c[2] for c in self.calls(backend, "transition")], ["Start work", "Submit for review", "Complete"])

    def test_a_review_move_refused_at_handoff_is_left_for_the_lead(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "pr-open"})
        wt = self.started(fx)
        self.publish(fx)
        with backend.edit() as store:
            store["failures"].append({"op": "transition", "ticket": "FX-3", "status": 503, "times": -1})
        self.opened(fx, wt)
        code, out = self.handed(fx, wt)
        self.assertEqual(code, 0, out)
        self.assertIn("the lead's Needs you shows it", out)
        record = json.loads(self.harness.sh(sys.executable, "-B", STATE_TOOL, "--dir", fx.tmp / "record", "show").stdout)
        conf = tp.settings(fx.main, json.loads(Path(fx.env["RUN_DELIVERY_CONFIG"]).read_text()), {})
        items = tp.needs_you(record, fx.tmp / "record", fx.tmp / "flow-log", "FX-1", conf)
        self.assertTrue(any("FX-3: its review move (Submit for review) couldn't be made at hand-off" in i["text"]
                            for i in items), items)

    def test_a_hand_off_with_lead_moves_is_left_to_the_publisher_which_moves_and_posts_once(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "lead": {"candidate.handed": ["Submit for review"]}})
        wt = self.started(fx)
        self.publish(fx)
        self.opened(fx, wt)
        code, out = self.handed(fx, wt)
        self.assertEqual(code, 0, out)
        self.assertIn("the tracker publisher posts the hand-off update, with its moves", out)
        self.publish(fx)
        self.publish(fx)
        issue = backend.read()["issues"]["FX-3"]
        self.assertEqual(issue["status"], "In Review")
        handed = [c["text"] for c in issue["comments"] if c["text"].startswith("Handed off")]
        self.assertEqual(len(handed), 1)
        self.assertIn('Moved with "Submit for review".', handed[0])

    def test_running_lane_start_again_leaves_the_lanes_focus_alone(self) -> None:
        fx, _ = self.lane({"start": ["Start work"]})
        self.started(fx)
        self.harness.sh(sys.executable, "-B", STATE_TOOL, "--dir", fx.tmp / "record", "focus", "--lane", "lane-2",
                        "--text", "FX-3: review round 1", "--state", "reviewing")
        self.started(fx)
        record = json.loads(self.harness.sh(sys.executable, "-B", STATE_TOOL, "--dir", fx.tmp / "record", "show").stdout)
        lane = {l["id"]: l for l in record["state"]["lanes"]}["lane-2"]
        self.assertEqual((lane["focus"], lane["state"]), ("FX-3: review round 1", "reviewing"))

    def test_lead_setup_records_review_when_or_says_it_is_unset(self) -> None:
        h = self.harness
        fx = self.fixture()
        m = fx.main
        conf = json.loads((m / ".agents/run-delivery.json").read_text())
        conf["tracker"]["transitions"] = {"review": ["Submit for review"]}
        (m / ".agents/run-delivery.json").write_text(json.dumps(conf, indent=2))
        setup = ["lead-setup", "FX-1", "--plan", "docs/plan.md", "--base", "feature/FX-1", "--pattern", h.PATTERN,
                 "--no-authority"]
        code, out = fx.flow(*setup, cwd=m)
        self.assertEqual(code, 0, out)
        self.assertIn("tracker.transitions.reviewWhen is unset", out)
        code, out = fx.flow(*setup, "--force", "--review-when", "gates-passed", cwd=m)
        self.assertEqual(code, 0, out)
        written = json.loads((m / ".agents/run-delivery.json").read_text())
        self.assertEqual(written["tracker"]["transitions"], {"review": ["Submit for review"], "reviewWhen": "gates-passed"})
        self.assertNotIn("reviewWhen is unset", out)

    def record(self, fx) -> dict:
        return json.loads(self.harness.sh(sys.executable, "-B", STATE_TOOL, "--dir", fx.tmp / "record", "show").stdout)

    def test_with_a_host_only_tracker_open_pr_and_handoff_print_and_record(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "pr-open"},
                                updates="host")
        wt = self.started(fx)
        out = self.opened(fx, wt)
        self.assertIn('Tracker move (your story FX-3): "Submit for review", with your tracker tools.', out)
        code, out = self.handed(fx, wt)
        self.assertEqual(code, 0, out)
        self.assertIn("Tracker post (your story): hand-off: https://forge.test/pr/1", out)
        printed = [(p.get("move"), p.get("kind")) for p in tp.read_flow(fx.tmp / "flow-log", "FX-1", "lane-prints")]
        self.assertEqual(printed, [("start", None), ("review", None), (None, "candidate.handed")])
        self.assertEqual(backend.read()["calls"], [])

    def test_in_manual_mode_handoff_posts_nothing_itself(self) -> None:
        fx, backend = self.lane({"start": ["Start work"]}, updates="manual")
        wt = self.started(fx)
        self.opened(fx, wt)
        code, out = self.handed(fx, wt)
        self.assertEqual(code, 0, out)
        self.assertIn("Tracker post (your story): hand-off:", out)
        self.assertEqual(self.calls(backend, "comment"), [])

    def test_handoff_makes_a_review_move_open_pr_left_owed(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "pr-open"})
        wt = self.started(fx)
        self.publish(fx)
        with backend.edit() as store:
            store["failures"].append({"op": "transition", "ticket": "FX-3", "status": 503, "times": 1})
        out = self.opened(fx, wt)
        self.assertIn("review move wasn't made", out)
        code, out = self.handed(fx, wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(backend.read()["issues"]["FX-3"]["status"], "In Review")

    def test_handoff_waits_for_the_ledger_lock_before_its_journal_write(self) -> None:
        fx, backend = self.lane({"start": ["Start work"]})
        wt = self.started(fx)
        self.opened(fx, wt)
        (wt / ".orchestration/review/FX-3").mkdir(parents=True, exist_ok=True)
        (wt / ".orchestration/review/FX-3/context.md").write_text("- evidence")
        for argv in (["gate", "FX-3"], ["review-prepare", "FX-3", "--round", "1"]):
            self.assertEqual(fx.flow(*argv, cwd=wt)[0], 0)
        self.harness.write_results(wt / ".orchestration/review/FX-3", 1, "FX-3", fx.lenses(wt))
        self.assertEqual(fx.flow("review-reduce", "FX-3", "--round", "1", cwd=wt)[0], 0)
        with tp.ledger_lock(fx.tmp / "record", wait=True):
            process = subprocess.Popen([sys.executable, "-B", str(fx.tool), "handoff", "FX-3", "--pr",
                                        "https://forge.test/pr/1"], cwd=wt, env=fx.env, text=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not any(
                    e["kind"] == "review.evidence" for e in self.record(fx)["journal"]):
                time.sleep(0.2)
            time.sleep(2)
            self.assertIsNone(process.poll(), "handoff didn't wait for the ledger")
            self.assertFalse([e for e in self.record(fx)["journal"] if e["kind"] == "candidate.handed"],
                             "the hand-off was journalled outside the lock")
        out, _ = process.communicate(timeout=120)
        self.assertEqual(process.returncode, 0, out)
        self.assertEqual(sum(c["text"].startswith("Handed off") for c in backend.read()["issues"]["FX-3"]["comments"]), 1)

    def test_each_storys_review_move_is_its_own(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "pr-open"})
        d = json.loads((fx.main / ".agents/deliveries/FX-1.json").read_text())
        self.assertEqual(fx.flow("lead-assign", "FX-2", "--lane", "lane-1", "--title", "Lane 1", cwd=fx.main)[0], 0)
        wt3 = self.started(fx)
        self.assertEqual(fx.flow("lane-start", "FX-2", "--base", "feature/FX-1", cwd=fx.main)[0], 0)
        wt2 = fx.main / "lanes/FX-2"
        self.publish(fx)
        self.opened(fx, wt3)
        (wt2 / "src/one.txt").write_text("one\n")
        self.harness.commit(wt2, "FX-2: one")
        self.assertEqual(fx.flow("push", "FX-2", cwd=wt2)[0], 0)
        code, out = fx.flow("open-pr", "FX-2", "--title", "FX-2: one", "--body-file", fx.tmp / "body.md", cwd=wt2)
        self.assertEqual(code, 0, out)
        self.assertIn('Moved with "Submit for review".', out)
        issues = backend.read()["issues"]
        self.assertEqual((issues["FX-2"]["status"], issues["FX-3"]["status"]), ("In Review", "In Review"))
        self.assertTrue(d)

    def test_a_review_move_before_the_start_is_posted_waits_for_it(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "pr-open"})
        wt = self.started(fx)
        out = self.opened(fx, wt)
        self.assertIn("review move waits for its start to be posted", out)
        self.assertEqual([l for l in tp.read_ledger(fx.tmp / "record") if l.get("move") == "review"], [])
        self.publish(fx)
        code, out = fx.flow("open-pr", "FX-3", "--title", "FX-3: two", "--body-file", fx.tmp / "body.md", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(backend.read()["issues"]["FX-3"]["status"], "In Review")

    def test_a_review_move_owed_at_handoff_before_the_start_still_lets_the_start_be_made(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "pr-open"})
        wt = self.started(fx)
        self.opened(fx, wt)
        code, out = self.handed(fx, wt)
        self.assertEqual(code, 0, out)
        self.assertIn("waits for its start to be posted; the lead's Needs you shows it", out)
        self.publish(fx)
        issue = backend.read()["issues"]["FX-3"]
        self.assertEqual(issue["status"], "In Progress", "the owed review move didn't hold back the start")
        self.assertEqual(sum(c["text"].startswith("Started") for c in issue["comments"]), 1)
        conf = tp.settings(fx.main, json.loads(Path(fx.env["RUN_DELIVERY_CONFIG"]).read_text()), {})
        items = tp.needs_you(self.record(fx), fx.tmp / "record", fx.tmp / "flow-log", "FX-1", conf)
        self.assertTrue(any("FX-3: its review move (Submit for review) couldn't be made at hand-off" in i["text"]
                            for i in items), items)

    def test_a_review_move_made_or_not_offered_before_the_start_holds_the_start_back(self) -> None:
        for review, status, outcome in ((["Submit for review"], "In Review", "skip"), (["Complete"], "Backlog", "handled")):
            with self.subTest(outcome=outcome):
                fx, backend = self.lane({"start": ["Start work"], "review": review, "reviewWhen": "pr-open",
                                         "targets": {"Submit for review": "In Review"}})
                with backend.edit() as store:
                    store["issues"]["FX-3"]["status"] = status
                wt = self.started(fx)
                record = self.record(fx)
                [assigned] = [e for e in record["journal"] if e["kind"] == "lane.assigned"]
                # The start's entry already carries a line, so the review move isn't held back for it.
                with tp.ledger_lock(fx.tmp / "record", wait=True):
                    tp.append_ledger(fx.tmp / "record", {"journal": assigned["id"], "init": tp.init_id(record["journal"]),
                                                         "outcome": "skip", "writer": "publisher", "kind": "x"})
                self.opened(fx, wt)
                self.assertEqual([l["outcome"] for l in tp.read_ledger(fx.tmp / "record") if l.get("move") == "review"],
                                 [outcome])
                start = tp.Publisher(record, fx.tmp / "record", {"transitions": {}}, None, "FX-1", fx.tmp / "flow-log")
                self.assertEqual(start.start_decision(assigned), "the story is already in review")

    def test_this_records_own_review_marker_is_a_move_found_not_one_from_an_earlier_record(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "pr-open"})
        wt = self.started(fx)
        self.publish(fx)
        init = tp.init_id(self.record(fx)["journal"])
        with backend.edit() as store:
            store["issues"]["FX-3"]["status"] = "In Review"
            store["issues"]["FX-3"]["markers"].append(f"agent-delivery:FX-1:{init}:0:transition:Submit for review")
        out = self.opened(fx, wt)
        self.assertNotIn("earlier record", out)
        [line] = [l for l in tp.read_ledger(fx.tmp / "record") if l.get("move") == "review"]
        self.assertEqual((line["outcome"], line.get("found")), ("transition", True))

    def test_a_review_move_made_under_an_earlier_record_isnt_made_again(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "pr-open"})
        wt = self.started(fx)
        self.publish(fx)
        with backend.edit() as store:
            store["issues"]["FX-3"]["status"] = "In Review"
            store["issues"]["FX-3"]["markers"].append("agent-delivery:FX-1:20200101T000000000000Z:0:transition:Submit for review")
        out = self.opened(fx, wt)
        self.assertIn("made under an earlier record", out)
        self.assertEqual([c for c in self.calls(backend, "transition") if c[2] == "Submit for review"], [])
        self.assertEqual(tp.needs_you(self.record(fx), fx.tmp / "record", fx.tmp / "flow-log", "FX-1",
                                      tp.settings(fx.main, json.loads(Path(fx.env["RUN_DELIVERY_CONFIG"]).read_text()), {})),
                         [])

    def test_an_adapter_that_prints_no_json_never_fails_open_pr_or_handoff(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "pr-open"})
        wt = self.started(fx)
        self.publish(fx)
        with backend.edit() as store:
            store["failures"].append({"op": "markers", "ticket": None, "status": "nojson", "times": 3})
        out = self.opened(fx, wt)
        self.assertIn("review move wasn't made", out)
        code, out = self.handed(fx, wt)
        self.assertEqual(code, 0, out)
        self.assertIn("not posted", out)

    def test_lane_start_starts_again_after_a_reassignment(self) -> None:
        fx, _ = self.lane({"start": ["Start work"]})
        self.started(fx)
        self.assertEqual(fx.flow("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2", cwd=fx.main)[0], 0)
        self.started(fx)
        starts = tp.read_flow(fx.tmp / "flow-log", "FX-1", tp.LANE_STARTS)
        self.assertEqual([s["subject"] for s in starts], ["FX-3", "FX-3"])

    def test_lane_start_refuses_a_checkout_or_branch_it_didnt_make_for_the_story(self) -> None:
        fx, _ = self.lane({"start": ["Start work"]})
        wt = self.started(fx)
        self.harness.sh("git", "switch", "-q", "-c", "elsewhere", cwd=wt)
        code, out = fx.flow("lane-start", "FX-3", "--base", "feature/FX-1", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("exists on elsewhere, not feature/FX-3", out)
        fx2, _ = self.lane({"start": ["Start work"]})
        self.harness.sh("git", "switch", "-q", "--orphan", "feature/FX-3", cwd=fx2.main)
        (fx2.main / "x.txt").write_text("x\n")
        self.harness.commit(fx2.main, "unrelated")
        self.harness.sh("git", "switch", "-q", "feature/FX-1", cwd=fx2.main)
        code, out = fx2.flow("lane-start", "FX-3", "--base", "feature/FX-1", cwd=fx2.main)
        self.assertEqual(code, 2, out)
        self.assertIn("shares no history with origin/feature/FX-1", out)

    def test_lane_start_refuses_a_checkout_started_on_another_base(self) -> None:
        fx, _ = self.lane({"start": ["Start work"]})
        wt = self.started(fx)
        recorded = Path(self.harness.sh("git", "rev-parse", "--git-path", "run-delivery/lane.json", cwd=wt).stdout.strip())
        recorded = recorded if recorded.is_absolute() else wt / recorded
        recorded.write_text(json.dumps({"base": "integration-1"}))
        code, out = fx.flow("lane-start", "FX-3", "--base", "feature/FX-1", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("was started on integration-1, not feature/FX-1", out)

    def test_a_review_move_not_offered_is_handled_once(self) -> None:
        fx, backend = self.lane({"start": ["Start work"], "review": ["Complete"], "reviewWhen": "pr-open"})
        wt = self.started(fx)
        self.publish(fx)
        out = self.opened(fx, wt)
        self.assertIn("isn't offered from In Progress", out)
        code, out = fx.flow("open-pr", "FX-3", "--title", "FX-3: two", "--body-file", fx.tmp / "body.md", cwd=wt)
        self.assertIn("review move is already made", out)
        lines = [l["outcome"] for l in tp.read_ledger(fx.tmp / "record") if l.get("move") == "review"]
        self.assertEqual(lines, ["handled"])
        self.assertEqual([c for c in self.calls(backend, "transition") if c[2] == "Complete"], [])

    def test_a_failed_prepare_runs_again_on_the_next_lane_start(self) -> None:
        fx, _ = self.lane({"start": ["Start work"]})
        flag, count = fx.tmp / "fail-once", fx.tmp / "prepared"
        flag.write_text("x")
        own = Path(fx.env["RUN_DELIVERY_CONFIG"])
        conf = json.loads(own.read_text())
        conf["review"]["prepare"] = [["sh", "-c", f"if [ -f {flag} ]; then rm {flag}; exit 1; fi; echo run >> {count}"]]
        own.write_text(json.dumps(conf))
        code, out = fx.flow("lane-start", "FX-3", "--base", "feature/FX-1", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertEqual(tp.read_flow(fx.tmp / "flow-log", "FX-1", tp.LANE_STARTS), [], "no start before it's prepared")
        self.started(fx)
        self.started(fx)
        self.assertEqual(count.read_text(), "run\n")
        self.assertEqual(len(tp.read_flow(fx.tmp / "flow-log", "FX-1", tp.LANE_STARTS)), 1)

    # Brief.

    def test_the_brief_names_the_lane_commands_and_the_turn_rule(self) -> None:
        fx, _ = self.lane({"start": ["Start work"], "review": ["Submit for review"], "reviewWhen": "gates-passed"})
        code, out = fx.flow("lead-brief", cwd=fx.main)
        self.assertEqual(code, 0, out)
        text = (fx.main / "docs/lane-brief.md").read_text()
        self.assertIn("lane-start <key> --base feature/FX-1", text)
        self.assertNotIn("git worktree add", text)
        self.assertIn("End your turn only to hand off, to report yourself blocked, or to ask the lead.", text)
        self.assertIn("the tracker publisher makes it once `lane-start` records your start", text)
        self.assertIn("`open-pr` makes it once the PR is open and the gates pass at its head", text)
        self.assertNotIn("Post on your own story with your tracker tools: at start", text)
        own = Path(fx.env["RUN_DELIVERY_CONFIG"])
        own.write_text(json.dumps({**json.loads(own.read_text()), "tracker": {
            **json.loads(own.read_text())["tracker"], "updates": "host"}}))
        self.assertEqual(fx.flow("lead-brief", cwd=fx.main)[0], 0)
        self.assertIn("`lane-start` prints it; make it with your tracker tools.",
                      (fx.main / "docs/lane-brief.md").read_text())


class FakeJiraBindTests(unittest.TestCase):
    """The fake Jira listens without looking up its own host name. HTTPServer.server_bind asks socket.getfqdn for the
    address's name before it listens, and where reverse lookups of a loopback address go unanswered, as on a macOS
    runner, that holds every server about 34 seconds."""

    # The loopback names a stalled lookup holds, as `STALLED_LOOKUP` in tests/test_run_delivery.py lists them.
    LOOPBACK = ("", "localhost", "127.0.0.1", "::1", "0.0.0.0", "::")

    def test_the_fake_jira_listens_at_once_when_name_lookups_stall(self) -> None:
        released = threading.Event()  # set in cleanup, so a server that waits on the lookup isn't left waiting a minute
        stalled: list[str] = []
        real_getfqdn, real_gethostbyaddr = socket.getfqdn, socket.gethostbyaddr

        def getfqdn(name: str = "") -> str:
            if name in self.LOOPBACK:
                stalled.append(name)
                released.wait(60)
            return real_getfqdn(name)

        def gethostbyaddr(address: str):
            if address in self.LOOPBACK:
                stalled.append(address)
                released.wait(60)
            return real_gethostbyaddr(address)

        patch = mock.patch.multiple(socket, getfqdn=getfqdn, gethostbyaddr=gethostbyaddr)
        patch.start()
        made: list[FakeJira] = []
        built = threading.Thread(target=lambda: made.append(FakeJira()), daemon=True)

        def stop() -> None:  # in this order: release the lookup, let the constructor finish, then close what it made
            released.set()
            built.join(70)
            patch.stop()
            for jira in made:
                jira.close()

        self.addCleanup(stop)
        started = time.monotonic()
        built.start()
        built.join(5)
        self.assertFalse(built.is_alive(), f"FakeJira() waited on the stalled lookup of {stalled}")
        server = made[0].server
        port = server.server_address[1]
        # The name the stock bind would have looked up is the address itself.
        self.assertEqual((server.server_name, server.server_port), ("127.0.0.1", port))
        with socket.create_connection(("127.0.0.1", port), timeout=max(0.1, 5 - (time.monotonic() - started))):
            pass
        # And it serves: a request without credentials gets Jira's 401, within the same 5 s.
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=max(0.1, 5 - (time.monotonic() - started)))
        try:
            connection.request("GET", "/rest/api/3/myself")
            response = connection.getresponse()
            self.assertEqual((response.status, json.loads(response.read())), (401, {"errorMessages": ["unauthorized"]}))
        finally:
            connection.close()
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(stalled, [], "the bind looked up a loopback name")


# Each failure class, once: the GraphQL operation it hits, the adapter operation that sends it, the httpStatus
# tracker_linear.py reports, and the publisher's outcome. "unreachable" is the endpoint refusing connections.
LINEAR_FAILURES = [
    # (label, rule, adapter operation, httpStatus, publisher outcome)
    ("HTTP 429", dict(op="CommentCreate", status=429), "comment", 429, "transient"),
    ("HTTP 502", dict(op="CommentCreate", status=502), "comment", 502, "transient"),
    ("another 4xx: HTTP 404", dict(op="CommentCreate", status=404), "comment", 404, "failed"),
    ("another 4xx: HTTP 400", dict(op="CommentCreate", status=400), "comment", 400, "failed"),
    ("unreachable", None, "comment", None, "transient"),
    ("rate limit at 200", dict(op="CommentCreate", code="RATELIMITED"), "comment", 429, "transient"),
    ("rate limit at HTTP 400", dict(op="CommentCreate", status=400, code="RATELIMITED"), "comment", 429, "transient"),
    ("authentication at 200", dict(op="CommentCreate", code="AUTHENTICATION_ERROR"), "comment", 401, "transient"),
    ("forbidden at 200, one ticket", dict(op="CommentCreate", code="FORBIDDEN"), "comment", 403, "failed"),
    ("forbidden at 200, the whole site", dict(op="CommentCreate", code="FORBIDDEN"), "comment", 403, "transient"),
    ("another error at 200", dict(op="CommentCreate", code="INVALID_INPUT"), "comment", 400, "failed"),
    ("status with an error at 200", dict(op="Status", code="RATELIMITED"), "status", 429, "transient"),
    ("transition to an unknown state", dict(op="IssueUpdate", code="INVALID_INPUT"), "transition", 400, "failed"),
    ("a mutation answering no success", dict(op="CommentCreate", data={"commentCreate": {"success": False}}),
     "comment", 400, "failed"),
    ("a rate limit among other errors", dict(op="CommentCreate", codes=["INVALID_INPUT", "FORBIDDEN", "RATELIMITED"]),
     "comment", 429, "transient"),
    ("forbidden among other errors", dict(op="CommentCreate", codes=["INVALID_INPUT", "FORBIDDEN"]),
     "comment", 403, "failed"),
]


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class LinearAdapterContractTests(unittest.TestCase):
    """`tracker_linear.py` against the fake Linear: each operation, each failure class, and the token kept out."""

    def setUp(self) -> None:
        self.linear = FakeLinear()
        self.addCleanup(self.linear.close)
        self.store = self.linear.store
        self.tmp = Path(tempfile.mkdtemp(prefix="tracker-linear-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.outputs: list[str] = []
        for key, status in (("EP-2", "In Progress"), ("EP-3", "Done"), ("EP-4", "Backlog")):
            self.store["issues"][key] = {"status": status, "comments": [], "markers": [], "labels": [],
                                         "parent": EPIC, "summary": key}

    def adapter(self, *args: str, env: dict | None = None, token_env: str = "FX_LINEAR",
                site: str | None = None) -> tuple[int, dict]:
        environ = {key: value for key, value in os.environ.items() if not key.startswith(("FX_", "LINEAR_"))}
        environ.update({"FX_LINEAR": LINEAR_TOKEN} if env is None else env)
        done = subprocess.run([sys.executable, "-B", str(LINEAR_ADAPTER), "--site", site or self.linear.site,
                               "--token-env", token_env, *args], capture_output=True, text=True, env=environ, timeout=60)
        self.outputs.append(done.stdout + done.stderr)
        return done.returncode, json.loads(done.stdout)

    def body(self, text: str) -> str:
        path = self.tmp / f"body-{len(self.outputs)}.md"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def inject(self, op: str, status: int | None = None, ticket: str | None = None, times: int = 1, **rule) -> None:
        self.store["failures"].append({"op": op, "ticket": ticket, "status": status, "times": times, **rule})

    def test_status_offers_only_the_current_workflow_type_or_later_and_the_move_is_made(self) -> None:
        code, answer = self.adapter("status", "--ticket", "EP-2")
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer["status"], "In Progress")
        self.assertIsInstance(answer["commentLimit"], int)
        self.assertLess(answer["commentLimit"], LINEAR_BODY_LIMIT)
        offered = {t["name"]: t["offered"] for t in answer["transitions"]}
        self.assertEqual([t["name"] for t in answer["transitions"]], [name for name, _ in LINEAR_WORKFLOW],
                         "every state, in workflow order")
        self.assertTrue(all(t["to"] == t["name"] for t in answer["transitions"]))
        self.assertEqual(offered, {"Triage": False, "Backlog": False, "Todo": False, "In Progress": False,
                                   "In Review": True, "Done": True, "Canceled": True})
        # The allow side: In Progress to In Review is made, with one marker comment after the state.
        code, answer = self.adapter("transition", "--ticket", "EP-2", "--name", "In Review", "--marker", "m:review")
        self.assertEqual((code, answer), (0, {}))
        self.assertEqual(self.store["issues"]["EP-2"]["status"], "In Review")
        self.assertEqual([c for c in self.store["calls"] if c[0] != "comments-page"],
                         [["transition", "EP-2", "In Review"], ["comment", "EP-2"]])
        self.assertEqual(self.adapter("markers", "--ticket", "EP-2")[1], {"markers": ["m:review"]})
        code, answer = self.adapter("transition", "--ticket", "EP-2", "--name", "Done", "--marker", "m:done")
        self.assertEqual((code, self.store["issues"]["EP-2"]["status"]), (0, "Done"))
        # The refuse side: an earlier type is not offered, and a Done ticket offers nothing.
        code, answer = self.adapter("transition", "--ticket", "EP-4", "--name", "Triage", "--marker", "m:back")
        self.assertEqual((code, answer["httpStatus"]), (3, 400))
        self.assertEqual(self.store["issues"]["EP-4"]["status"], "Backlog")
        code, answer = self.adapter("status", "--ticket", "EP-3")
        self.assertEqual([t["name"] for t in answer["transitions"] if t["offered"]], [])
        before = list(self.store["calls"])
        code, answer = self.adapter("transition", "--ticket", "EP-3", "--name", "In Progress", "--marker", "m:start")
        self.assertEqual((code, answer["httpStatus"], self.store["calls"]), (3, 400, before),
                         "a replayed start never sends a Done ticket back")
        self.assertEqual(self.store["issues"]["EP-3"]["status"], "Done")

    def test_a_marker_on_the_second_page_of_comments_is_read_back(self) -> None:
        for n in range(150):
            self.store["issues"]["EP-2"]["comments"].append({"text": f"comment {n}", "markers": []})
        code, answer = self.adapter("comment", "--ticket", "EP-2", "--marker", "agent-delivery:EP-1:x:7:comment",
                                    "--body-file", self.body("Handed off.\nSecond line"))
        self.assertEqual((code, answer), (0, {}))
        [text] = [c["text"] for c in self.store["issues"]["EP-2"]["comments"][150:]]
        self.assertTrue(text.startswith("Handed off.\nSecond line"))
        self.assertTrue(text.endswith("<!-- run-delivery:agent-delivery:EP-1:x:7:comment -->"), text)
        code, answer = self.adapter("markers", "--ticket", "EP-2")
        self.assertEqual(answer, {"markers": ["agent-delivery:EP-1:x:7:comment"]})
        pages = [c[2] for c in self.store["calls"] if c[0] == "comments-page"]
        self.assertEqual(pages, [0, 100], "both pages, following pageInfo to the end")
        # Past MAX_PAGES pages, or with no cursor to go on from, markers refuses rather than answer in part.
        self.store["issues"]["EP-4"]["comments"] = [{"text": f"c{n}", "markers": []} for n in range(50 * 100 + 1)]
        code, answer = self.adapter("markers", "--ticket", "EP-4")
        self.assertEqual((code, "markers" in answer), (3, False), answer)
        self.assertIn("more than 50 pages", answer["error"])
        self.store["issues"]["EP-4"]["comments"] = []
        self.inject("Comments", ticket="EP-4", data={"issue": {"comments": {
            "nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": None}}}})
        code, answer = self.adapter("markers", "--ticket", "EP-4")
        self.assertEqual((code, "markers" in answer), (3, False), answer)
        self.assertIn("no next page", answer["error"])
        # A marker quoted in the middle of a comment isn't one the comment carries.
        self.store["issues"]["EP-4"]["comments"].append(
            {"text": "see <!-- run-delivery:quoted --> above", "markers": []})
        self.assertEqual(self.adapter("markers", "--ticket", "EP-4")[1], {"markers": []})

    def test_a_long_comment_is_cut_to_what_linear_takes_with_its_marker_kept(self) -> None:
        for text in ("x" * 150000, "😀" * 80000):
            with self.subTest(length=len(text)):
                self.store["issues"]["EP-2"]["comments"] = []
                code, answer = self.adapter("comment", "--ticket", "EP-2", "--marker", "m:long",
                                            "--body-file", self.body(text))
                self.assertEqual((code, answer), (0, {}), "the stub refuses a body over Linear's own limit")
                [stored] = [c["text"] for c in self.store["issues"]["EP-2"]["comments"]]
                self.assertLessEqual(len(stored.encode("utf-16-le")) // 2, LINEAR_BODY_LIMIT)
                self.assertGreater(len(stored.encode("utf-16-le")) // 2, LINEAR_BODY_LIMIT - 2000)
                self.assertEqual(self.adapter("markers", "--ticket", "EP-2")[1], {"markers": ["m:long"]})

    def test_each_failure_class_maps_to_its_http_status(self) -> None:
        args = {"comment": ("comment", "--ticket", "EP-2", "--marker", "m", "--body-file", self.body("An update")),
                "status": ("status", "--ticket", "EP-2"),
                "transition": ("transition", "--ticket", "EP-2", "--name", "In Review", "--marker", "m")}
        for label, rule, operation, status, _ in LINEAR_FAILURES:
            with self.subTest(label):
                self.store["failures"] = []
                self.store["issues"]["EP-2"]["status"] = "In Progress"
                if rule:
                    self.inject(**rule, ticket="EP-2")
                before = len(self.store["issues"]["EP-2"]["comments"])
                code, answer = self.adapter(*args[operation], site="http://127.0.0.1:9" if rule is None else None)
                self.assertEqual((code, answer["httpStatus"]), (3, status), answer)
                self.assertEqual(len(self.store["issues"]["EP-2"]["comments"]), before, "no success, nothing posted")
                self.assertEqual(self.store["issues"]["EP-2"]["status"], "In Progress")
        code, answer = self.adapter("transition", "--ticket", "EP-2", "--name", "No such state", "--marker", "m")
        self.assertEqual((code, answer["httpStatus"]), (3, 400), "a state the team doesn't have is refused unsent")

    def test_the_token_comes_only_from_its_variable_and_is_never_printed(self) -> None:
        requests = self.store["requests"]
        code, answer = self.adapter("status", "--ticket", "EP-2", env={})
        self.assertEqual((code, answer["httpStatus"]), (3, None))
        self.assertIn("FX_LINEAR is not set", answer["error"])
        self.assertEqual(self.store["requests"], requests, "a missing token fails before any request")
        code, answer = self.adapter("status", "--ticket", "EP-2", env={"OTHER_KEY": LINEAR_TOKEN}, token_env="OTHER_KEY")
        self.assertEqual((code, answer["status"]), (0, "In Progress"), "a different --token-env name")
        code, answer = self.adapter("status", "--ticket", "EP-2", env={"FX_LINEAR": "Bearer " + LINEAR_TOKEN})
        self.assertEqual((code, answer["httpStatus"]), (3, 401), "the key goes as it is, never as a bearer token")
        self.inject("CommentCreate", code="INVALID_INPUT", message=f"bad key {LINEAR_TOKEN} and lin_api_someOtherKey9")
        code, answer = self.adapter("comment", "--ticket", "EP-2", "--marker", "m", "--body-file", self.body("x"))
        self.assertEqual(answer["httpStatus"], 400)
        self.assertIn("[redacted]", answer["error"])
        self.assertNotIn("lin_api_someOtherKey9", answer["error"])
        # A key of another form, which the lin_api_ pattern wouldn't catch, is taken out as the key in use.
        self.store["token"] = "oauthFixture0123"
        self.inject("CommentCreate", code="INVALID_INPUT", message="refused oauthFixture0123 for this team")
        code, answer = self.adapter("comment", "--ticket", "EP-2", "--marker", "m", "--body-file", self.body("x"),
                                    env={"FX_LINEAR": "oauthFixture0123"})
        self.assertEqual(answer["httpStatus"], 400)
        self.assertIn("refused [redacted] for this team", answer["error"])
        self.assertFalse([out for out in self.outputs if "oauthFixture0123" in out])
        self.store["token"] = LINEAR_TOKEN
        for args in (("status", "--project", "EP"), ("markers", "--ticket", "EP-2"),
                     ("create", "--project", "EP", "--title", "A follow-up", "--body-file", self.body("Later"),
                      "--marker", "agent-followup-ep-2-f1", "--parent", "EP-2")):
            self.assertEqual(self.adapter(*args)[0], 0)
        self.assertFalse([out for out in self.outputs if LINEAR_TOKEN in out], "no output carries the token")

    def test_create_labels_the_item_and_markers_finds_it_by_its_label(self) -> None:
        body = self.body("Deferred F1")
        code, answer = self.adapter("create", "--project", "EP", "--title", "Tidy the parser", "--body-file", body,
                                    "--marker", "agent-followup-ep-2-f1", "--parent", "EP-2", "--type", "Bug")
        self.assertEqual(code, 0, answer)
        made = self.store["issues"][answer["key"]]
        self.assertEqual((made["labels"], made["parent"], made["summary"]),
                         (["agent-followup-ep-2-f1"], "EP-2", "Tidy the parser"))
        code, found = self.adapter("markers", "--project", "EP", "--marker", "agent-followup-ep-2-f1")
        self.assertEqual(found, {"items": [{"key": answer["key"]}]})
        # A second item with the same marker reuses the label rather than making another.
        self.adapter("create", "--project", "EP", "--title", "Again", "--body-file", body,
                     "--marker", "agent-followup-ep-2-f1")
        self.assertEqual(len(self.store["labels"]), 1)
        # A same-named label of another team isn't one this team can carry; a workspace label is.
        self.store["labels"] = [{"id": "label-other", "name": "agent-followup-x", "team": "team-OTHER"}]
        code, made = self.adapter("create", "--project", "EP", "--title", "t" * 300, "--body-file", body,
                                  "--marker", "agent-followup-x")
        self.assertEqual(code, 0, made)
        self.assertEqual([label["team"] for label in self.store["labels"]], ["team-OTHER", "team-EP"])
        self.assertEqual(len(self.store["issues"][made["key"]]["summary"]), 255, "the title is cut to 255")
        self.store["labels"] = [{"id": "label-workspace", "name": "agent-followup-y", "team": None}]
        self.adapter("create", "--project", "EP", "--title", "Workspace", "--body-file", body,
                     "--marker", "agent-followup-y")
        self.assertEqual([label["id"] for label in self.store["labels"]], ["label-workspace"])
        self.assertEqual(self.adapter("status", "--project", "NOPE")[1]["httpStatus"], 404)
        limit = self.adapter("status", "--ticket", "EP-2")[1]["commentLimit"]
        self.assertEqual(self.adapter("status", "--project", "EP")[1], {"commentLimit": limit})


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class LinearPublisherTests(unittest.TestCase):
    """The publisher through the shipped `tracker_linear.py`, as the Jira rows run through `tracker_jira.py`."""

    def delivery(self, transitions: dict | None = None, backend=LinearBackend) -> Delivery:
        return Delivery(self, backend, transitions)

    def published(self, d: Delivery) -> str:
        code, out = d.publish()
        self.assertEqual(code, 0, out)
        return out

    def inject(self, d: Delivery, op: str, status: int | None = None, ticket: str | None = None, times: int = 1,
               **rule) -> None:
        with d.backend.edit() as store:
            store["failures"].append({"op": op, "ticket": ticket, "status": status, "times": times, **rule})

    def marked(self, d: Delivery, key: str) -> list[str]:
        return [m for c in d.backend.read()["issues"][key]["comments"] for m in c["markers"]]

    def test_a_deliverys_entries_post_with_no_lead_action_each_in_the_ledger_once(self) -> None:
        d = self.delivery({"start": ["In Progress"], "lead": {"candidate.merged": ["Done"]}})
        d.ticket("EP-2")
        assigned = d.entry("lane.assigned", "EP-2", lane="lane-1")
        d.lane_start("EP-2")
        handed = d.entry("candidate.handed", "EP-2", "Handed off at abc")
        merged = d.entry("candidate.merged", "EP-2", "Merged at def")
        decision = d.entry("decision.recorded", "EP-2", "Plan change: EP-3 removed")
        d.mark(decision)
        self.published(d)
        self.published(d)
        self.assertEqual(d.outcomes(assigned["id"]), ["transition", "comment"])
        self.assertEqual(d.outcomes(handed["id"]), ["comment"])
        self.assertEqual(d.outcomes(merged["id"]), ["transition", "comment"])
        self.assertEqual(d.outcomes(decision["id"]), ["comment"])
        self.assertEqual(d.status("EP-2"), "Done")
        comments = d.comments("EP-2")
        for entry in (assigned, handed, merged, decision):
            self.assertEqual(sum(f"journal entry {entry['id']} " in text for text in comments), 1, entry)
        init = d.init()
        self.assertEqual(sorted(self.marked(d, "EP-2")), sorted([
            tp.marker(EPIC, init, assigned["id"], "transition:In Progress"), tp.marker(EPIC, init, assigned["id"], "comment"),
            tp.marker(EPIC, init, handed["id"], "comment"), tp.marker(EPIC, init, merged["id"], "transition:Done"),
            tp.marker(EPIC, init, merged["id"], "comment"), tp.marker(EPIC, init, decision["id"], "comment")]),
            "every action carries its marker, once")
        self.assertEqual(d.needs_you(), [])

    def test_each_failure_class_has_its_publisher_outcome(self) -> None:
        for label, rule, operation, status, outcome in LINEAR_FAILURES:
            with self.subTest(label):
                moves = {"lead": {"lease.granted": ["In Review"]}} if operation == "transition" else None
                d = self.delivery(moves)
                d.ticket("EP-2", status="In Progress")
                entry = d.entry("lease.granted", "EP-2", label)
                if rule is None:
                    d.unreachable()
                else:
                    self.inject(d, ticket="EP-2", times=1 if outcome == "transient" else -1, **rule)
                if label.endswith("the whole site"):
                    self.inject(d, "Status", code="FORBIDDEN", ticket=EPIC)
                code, out = d.publish()
                if outcome == "transient":
                    self.assertEqual(code, 1, out)
                    self.assertEqual(d.lines(entry["id"]), [], "no ledger line, success or otherwise")
                    self.assertTrue([item for item in d.needs_you() if item["holding"]])
                    d.reachable()
                    self.published(d)
                    self.assertEqual(d.outcomes(entry["id"])[-1], "comment", "the next run retries from there")
                    self.assertEqual(len(d.comments("EP-2")), 1)
                else:
                    self.assertEqual(code, 0, out)
                    self.assertEqual(d.outcomes(entry["id"])[-1], "failed")
                    self.published(d)
                    self.assertEqual(d.outcomes(entry["id"])[-1], "failed", "never retried")
                    shown = [item for item in d.needs_you()
                             if not item["holding"] and f"journal entry {entry['id']} " in item["text"]]
                    self.assertEqual(len(shown), 1, d.needs_you())
                    self.assertEqual(d.comments("EP-2"), [])

    def test_a_failure_between_a_moves_two_calls_is_rerun_to_a_ledger_that_matches_linear(self) -> None:
        for failing in ("IssueUpdate", "CommentCreate"):
            with self.subTest(failing=failing):
                d = self.delivery({"start": ["In Progress"]})
                d.ticket("EP-2")
                entry = d.entry("lane.assigned", "EP-2", lane="lane-1")
                d.lane_start("EP-2")
                # The first CommentCreate is the move's marker: the state update has already been made.
                self.inject(d, failing, status=503, ticket="EP-2")
                code, out = d.publish()
                self.assertEqual(code, 1, out)
                self.assertEqual(d.lines(entry["id"]), [])
                self.assertEqual(d.status("EP-2"), "Backlog" if failing == "IssueUpdate" else "In Progress")
                self.published(d)
                self.published(d)
                self.assertEqual(d.status("EP-2"), "In Progress")
                move = tp.marker(EPIC, d.init(), entry["id"], "transition:In Progress")
                comment = tp.marker(EPIC, d.init(), entry["id"], "comment")
                markers = self.marked(d, "EP-2")
                if failing == "IssueUpdate":
                    self.assertEqual(d.outcomes(entry["id"]), ["transition", "comment"])
                    self.assertEqual(sorted(markers), sorted([move, comment]))
                else:
                    # Moved and unmarked reads as made by someone else: never moved again, never claimed as ours.
                    self.assertEqual(d.outcomes(entry["id"]), ["skip", "comment"])
                    self.assertEqual(markers, [comment])
                self.assertEqual(markers.count(comment), 1, "one update comment")
                self.assertEqual([c for c in d.calls() if c[0] == "transition"], [["transition", "EP-2", "In Progress"]])

    def test_a_replayed_start_on_a_done_ticket_is_handled_and_never_moves_it(self) -> None:
        d = self.delivery({"start": ["In Progress"]})
        d.ticket("EP-2", status="Done")
        entry = d.entry("lane.assigned", "EP-2", lane="lane-1")
        d.lane_start("EP-2")
        self.published(d)
        self.published(d)
        self.assertEqual(d.outcomes(entry["id"]), ["handled", "comment"])
        self.assertEqual(d.status("EP-2"), "Done")
        self.assertFalse([c for c in d.calls() if c[0] == "transition"])
        [item] = d.needs_you()
        self.assertFalse(item["holding"])
        self.assertIn("EP-2: the move \"In Progress\" isn't offered from Done", item["text"])

    def test_the_first_run_over_a_host_mode_record_skips_through_journal_through(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        posted = d.entry("candidate.handed", "EP-2", "Posted by the host publisher")
        after = d.entry("candidate.merged", "EP-2", "Merged after the switch")
        (d.state_dir / "published.json").write_text(json.dumps(
            {"version": 7, "journalThrough": posted["id"], "at": stamp(d.clock + 5)}))
        self.published(d)
        self.published(d)
        [line] = d.lines(posted["id"])
        self.assertEqual((line["outcome"], line.get("final"), line["reason"], line["writer"], line["through"]),
                         ("skip", True, "published before the tracker publisher", "publisher", posted["id"]))
        self.assertEqual(d.outcomes(after["id"]), ["comment"])
        [text] = d.comments("EP-2")
        self.assertIn(f"journal entry {after['id']} ", text)
        self.assertEqual(self.marked(d, "EP-2"), [tp.marker(EPIC, d.init(), after["id"], "comment")])
        self.assertEqual(d.lines(1)[0]["outcome"], "skip", "the record's own start is skipped as published too")

    def test_an_earlier_records_or_unreadable_published_file_is_never_taken_for_this_ones(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        entry = d.entry("candidate.handed", "EP-2")
        path = d.state_dir / "published.json"
        path.write_text("{not json")
        code, out = d.publish()
        self.assertEqual(code, 1, out)
        self.assertIn("published.json is malformed", out)
        self.assertEqual(d.ledger(), [], "nothing is seeded or posted from a file that can't be read")
        path.write_text(json.dumps({"version": 9, "journalThrough": 50, "at": stamp(-100)}))
        self.published(d)
        self.assertEqual(d.outcomes(entry["id"]), ["comment"], "acknowledged before this record began")

    def test_an_ack_at_the_records_first_moment_counts_and_a_malformed_through_stops_the_run(self) -> None:
        d = self.delivery()
        d.ticket("EP-2")
        posted = d.entry("candidate.handed", "EP-2")
        path = d.state_dir / "published.json"
        for through in (True, -1, "2"):
            with self.subTest(through=through):
                path.write_text(json.dumps({"version": 1, "journalThrough": through, "at": stamp(d.clock)}))
                code, out = d.publish()
                self.assertEqual(code, 1, out)
                self.assertIn("published.json is malformed", out)
                self.assertEqual(d.ledger(), [])
        path.write_text(json.dumps({"version": 1, "journalThrough": posted["id"], "at": d.journal[0]["at"]}))
        self.published(d)
        self.assertEqual(d.lines(posted["id"])[0]["reason"], "published before the tracker publisher")

    def test_only_the_first_run_seeds_and_a_later_ack_covers_nothing(self) -> None:
        d = self.delivery({"start": ["In Progress"]})
        d.ticket("EP-2")
        posted = d.entry("candidate.handed", "EP-2", "Posted by the host publisher")
        path = d.state_dir / "published.json"
        path.write_text(json.dumps({"version": 3, "journalThrough": posted["id"], "at": stamp(d.clock + 1)}))
        self.published(d)
        self.assertEqual(d.lines(posted["id"])[0]["reason"], "published before the tracker publisher")
        # A host publisher beside it acknowledges past a story still waiting for its start, and past an entry a
        # transient failure held back: neither is taken for one it posted.
        assigned = d.entry("lane.assigned", "EP-2", lane="lane-1")
        held = d.entry("lease.granted", "EP-2", "Held back")
        self.inject(d, "Comments", status=503, ticket="EP-2")
        self.assertEqual(d.publish()[0], 1)
        path.write_text(json.dumps({"version": 9, "journalThrough": held["id"], "at": stamp(d.clock + 1)}))
        self.published(d)
        d.lane_start("EP-2")
        self.published(d)
        self.assertEqual(d.outcomes(assigned["id"]), ["transition", "comment"])
        self.assertEqual(d.outcomes(held["id"]), ["comment"])
        self.assertEqual(d.status("EP-2"), "In Progress")
        self.assertFalse([line for line in d.ledger() if line["journal"] in (assigned["id"], held["id"])
                          and line.get("reason") == "published before the tracker publisher"])

    def test_a_record_the_publisher_began_is_never_seeded_by_a_later_ack(self) -> None:
        d = self.delivery({"start": ["In Progress"]})
        d.ticket("EP-2")
        handed = d.entry("candidate.handed", "EP-2", "Posted by the tracker publisher")
        self.published(d)
        self.assertEqual(d.outcomes(handed["id"]), ["comment"])
        assigned = d.entry("lane.assigned", "EP-2", lane="lane-1")
        held = d.entry("lease.granted", "EP-2", "Held back by a transient failure")
        self.inject(d, "Comments", status=503, ticket="EP-2")
        self.assertEqual(d.publish()[0], 1)
        self.assertEqual((d.lines(assigned["id"]), d.lines(held["id"])), ([], []), "one waits, one is held back")
        (d.state_dir / "published.json").write_text(json.dumps(
            {"version": 6, "journalThrough": held["id"], "at": stamp(d.clock + 1)}))
        self.published(d)
        d.lane_start("EP-2")
        self.published(d)
        self.assertEqual(d.outcomes(held["id"]), ["comment"])
        self.assertEqual(d.outcomes(assigned["id"]), ["transition", "comment"])
        self.assertEqual(d.status("EP-2"), "In Progress")
        self.assertFalse([line for line in d.ledger() if line.get("reason") == "published before the tracker publisher"])

    def test_a_command_line_before_the_first_run_doesnt_end_the_seeding_and_a_cut_seeding_finishes(self) -> None:
        for case in ("a lane command's line first", "a seeding cut short"):
            with self.subTest(case):
                d = self.delivery()
                d.ticket("EP-2")
                first = d.entry("candidate.handed", "EP-2", "Posted by the host publisher")
                second = d.entry("candidate.stopped", "EP-2", "Posted by the host publisher too")
                after = d.entry("candidate.merged", "EP-2", "After the switch")
                (d.state_dir / "published.json").write_text(json.dumps(
                    {"version": 4, "journalThrough": second["id"], "at": stamp(d.clock + 1)}))
                if case == "a seeding cut short":
                    # A real seeding run, cut back to its first line, as a run killed after that line leaves it.
                    self.published(d)
                    ledger = d.state_dir / tp.LEDGER
                    ledger.write_text(ledger.read_text().splitlines(keepends=True)[0])
                    self.assertEqual([(line["journal"], line["writer"], line["through"]) for line in d.ledger()],
                                     [(1, "publisher", second["id"])])
                    with d.backend.edit() as store:
                        store["issues"]["EP-2"]["comments"] = []
                    # The ack the cut run read is the one it finishes with, whatever the file says now.
                    (d.state_dir / "published.json").write_text(json.dumps(
                        {"version": 5, "journalThrough": after["id"], "at": stamp(d.clock + 1)}))
                else:
                    lane = d.entry("candidate.handed", "EP-2", "Handed off by its lane")
                    d.command_line(lane, action="comment")
                self.published(d)
                for entry in (first, second):
                    self.assertEqual([(line["outcome"], line.get("reason")) for line in d.lines(entry["id"])],
                                     [("skip", "published before the tracker publisher")], entry)
                self.assertEqual(d.outcomes(after["id"]), ["comment"])
                self.assertEqual(len(d.comments("EP-2")), 1, "only the entry after journalThrough is posted")

    def test_no_output_log_or_ledger_line_carries_the_key(self) -> None:
        d = self.delivery({"start": ["In Progress"]})
        d.ticket("EP-2")
        d.entry("lane.assigned", "EP-2", lane="lane-1")
        d.lane_start("EP-2")
        d.entry("candidate.handed", "EP-2")
        self.inject(d, "CommentCreate", code="RATELIMITED", ticket="EP-2")
        outputs = [d.publish()[1], d.publish()[1]]
        d.entry("candidate.stopped", "EP-2")
        with d.backend.edit() as store:
            store["token"] = "lin_api_rotatedKey"
        outputs.append(d.publish()[1])
        files = [path.read_text(errors="replace") for path in d.root.rglob("*") if path.is_file()]
        self.assertTrue(any("tracker-published" in str(p) for p in d.root.rglob("*")))
        self.assertFalse([text for text in outputs + files if LINEAR_TOKEN in text])
        self.assertFalse([item for item in d.needs_you() if LINEAR_TOKEN in item["text"]])

    def test_a_move_already_made_is_skipped_with_no_needs_you_item_for_both_adapters(self) -> None:
        rows = [(JiraBackend, {"lead": {"candidate.merged": ["Complete"]}, "targets": {"Complete": "Done"}}),
                (LinearBackend, {"lead": {"candidate.merged": ["Done"]}})]
        for backend, transitions in rows:
            with self.subTest(backend=backend.name):
                d = self.delivery(transitions, backend)
                d.ticket("EP-2", status="Done")
                entry = d.entry("candidate.merged", "EP-2", "Merged; the tracker's integration closed it")
                self.published(d)
                self.published(d)
                lines = d.lines(entry["id"])
                self.assertEqual([line["outcome"] for line in lines], ["skip", "comment"])
                self.assertEqual((lines[0]["status"], lines[0]["reason"]), ("Done", "already Done: made by someone else"))
                [text] = d.comments("EP-2")
                self.assertIn("already made by someone else", text)
                self.assertEqual(d.status("EP-2"), "Done")
                self.assertFalse([call for call in d.calls() if call[0] == "transition"])
                self.assertEqual(d.needs_you(), [], "a move someone else made is nothing to do")


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class LinearSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tracker-linear-settings-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def binding(self, *flags: str, publishers: list | None = None) -> dict:
        return {"state": {"publishers": publishers or [], "trackerSnapshot": {"argv": [
            "python3", "snapshot.py", "--kind", "linear", "--parent", "{parent}", *flags]}}}

    def test_the_linear_adapter_gives_the_publisher_with_the_snapshots_token(self) -> None:
        publisher = [tp.publisher_entry(".agent-skills/current/skills/run-delivery/scripts/tracker_publisher.py")]
        conf = tp.settings(self.tmp, {"tracker": {"adapter": "linear"}},
                           self.binding("--token-env", "FX_LINEAR", publishers=publisher))
        self.assertEqual((conf["mode"], conf["adapter"], conf["problems"]),
                         ("publisher", {"name": "linear", "site": None, "tokenEnv": "FX_LINEAR"}, []))
        self.assertIsNone(tp.refusal(conf))
        self.assertIn("no tracker publisher", tp.refusal(tp.settings(
            self.tmp, {"tracker": {"adapter": "linear"}}, self.binding("--token-env", "FX_LINEAR"))))
        bare = tp.settings(self.tmp, {"tracker": {"adapter": "linear"}}, {"state": {"publishers": publisher}})
        self.assertEqual(bare["adapter"]["tokenEnv"], "LINEAR_API_KEY")
        jira = {"state": {"publishers": publisher, "trackerSnapshot": {"argv": [
            "snapshot.py", "--kind", "jira", "--site", "https://t.test", "--token-env", "JIRA_X"]}}}
        self.assertEqual(tp.settings(self.tmp, {"tracker": {"adapter": "linear"}}, jira)["adapter"]["tokenEnv"],
                         "LINEAR_API_KEY", "a Jira snapshot's token never goes to Linear")
        named = tp.settings(self.tmp, {"tracker": {"adapter": "linear", "tokenEnv": "OWN"}}, self.binding("--token-env", "X"))
        self.assertEqual(named["adapter"]["tokenEnv"], "OWN")
        adapter = tp.Adapter(conf, self.tmp)
        self.assertEqual(adapter.prefix[2:], [str(LINEAR_ADAPTER), "--token-env", "FX_LINEAR"])

    def test_a_linear_snapshot_with_no_adapter_named_keeps_the_host_publisher(self) -> None:
        conf = tp.settings(self.tmp, {}, self.binding("--token-env", "FX_LINEAR",
                                                     publishers=[{"id": "tracker", "kind": "host"}]))
        self.assertEqual((conf["mode"], conf["adapter"], conf["host"]), ("host", None, "tracker"))
        self.assertIsNone(tp.refusal(conf))
        self.assertIn("tracker.adapter", tp.refusal(tp.settings(self.tmp, {}, self.binding("--token-env", "FX_LINEAR"))))
        self.assertIn("isn't one run-delivery ships (jira, linear)",
                      tp.refusal(tp.settings(self.tmp, {"tracker": {"adapter": "trello"}}, {})))


class LinearWordingTests(unittest.TestCase):
    """Decision 1: the claim that Linear is reachable only through the host is withdrawn from every file the
    run-delivery and linear profiles ship, and linear-tracker-adapter's own MCP-only rule stays word for word."""

    WITHDRAWN = ("reachable only through the host", "only through the host's connector", "host-only tracker",
                 "by this corpus's rule", "by this corpus's own rule", "linear needs a host publisher",
                 "linear is reachable only", "linear, by this corpus")

    def shipped(self) -> dict[str, str]:
        files = {}
        for profile in ("run-delivery", "linear"):
            for rel in json.loads((ROOT / f"profiles/{profile}.json").read_text(encoding="utf-8"))["expectedFiles"]:
                path = ROOT / rel
                if path.suffix in (".md", ".py", ".json", ".yaml", ".html", ".js"):
                    files[rel] = " ".join(path.read_text(encoding="utf-8").split()).casefold()
        files["catalog.json"] = " ".join((ROOT / "catalog.json").read_text(encoding="utf-8").split()).casefold()
        return files

    def test_the_withdrawn_claim_is_absent_from_every_shipped_file(self) -> None:
        files = self.shipped()
        self.assertIn("skills/run-delivery/adapters/tracker_linear.py", files)
        found = {rel: [phrase for phrase in self.WITHDRAWN if phrase in text] for rel, text in files.items()}
        self.assertEqual({rel: hits for rel, hits in found.items() if hits}, {})

    def test_the_linear_skills_mcp_only_rule_stays(self) -> None:
        skill = " ".join((ROOT / "skills/linear-tracker-adapter/SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("Use only the host's native Linear MCP capability. The adapter contract's `transportBoundary` is "
                      "exhaustive; no other provider access path is allowed.", skill)
        contract = json.loads((ROOT / "skills/linear-tracker-adapter/references/adapter-contract.json").read_text())
        self.assertIn("transportBoundary", contract)
        self.assertEqual(set(contract["transportBoundary"]["forbiddenActionMarkers"]),
                         {"direct-linear-api", "http-client-or-sdk", "command-downloader", "subprocess-or-shell-bridge",
                          "oauth-flow", "credential-token-env", "fallback-transport"})


if __name__ == "__main__":
    unittest.main()
