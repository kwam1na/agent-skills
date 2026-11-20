"""The delivery-views Jira and Bitbucket snapshot kinds, against recorded responses.

Each response comes from a fixture under tests/fixtures/delivery-views-snapshots,
whose provenance sidecar says how it was made. A scripted transport stands in
for the network and records every request the adapter makes.
"""

from __future__ import annotations

import base64
import copy
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import agent_skills


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = Path(agent_skills.__file__).resolve().parents[1]
SCRIPTS = PRODUCT_ROOT / "skills/delivery-views/scripts"
FIXTURES = ROOT / "tests/fixtures/delivery-views-snapshots"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(SCRIPTS))
snapshot = _load("snapshot", SCRIPTS / "snapshot.py")
delivery_state = _load("delivery_state", PRODUCT_ROOT / "skills/orchestrate-work/scripts/delivery_state.py")

JIRA = json.loads((FIXTURES / "jira.json").read_text(encoding="utf-8"))
LINEAR = json.loads((FIXTURES / "linear.json").read_text(encoding="utf-8"))
GITHUB = json.loads((FIXTURES / "github.json").read_text(encoding="utf-8"))
# What the existing TypeScript commands printed for the same issues and pull requests.
CAPTURED = json.loads((FIXTURES / "athena-snapshots.json").read_text(encoding="utf-8"))
BITBUCKET = json.loads((FIXTURES / "bitbucket.json").read_text(encoding="utf-8"))
NOW = "2026-10-22T12:00:00Z"
EMAIL = "lead@example.com"
TOKEN = "sentinel-token-7f3a9c"
SECRETS = (
    TOKEN,
    base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode(),
    base64.b64encode(TOKEN.encode()).decode(),
)
JIRA_ENV = {"JIRA_EMAIL": EMAIL, "JIRA_API_TOKEN": TOKEN}
JIRA_ARGS = ["--kind", "jira", "--site", "https://example.atlassian.net", "--parent", "APP-10",
             "--user-env", "JIRA_EMAIL", "--token-env", "JIRA_API_TOKEN", "--now", NOW]
SEARCH = "https://example.atlassian.net/rest/api/3/search/jql?"
API = "https://api.bitbucket.org/2.0/repositories/example/app/"


ISSUE = "https://example.atlassian.net/rest/api/3/issue/APP-10?fields=status"


def jira_parent(category: str = "indeterminate", name: str = "In Progress", status: int = 200) -> tuple:
    """The answer for the epic's own issue, which the Jira kind reads once after its children."""
    return (ISSUE, status, {"key": "APP-10", "fields": {"status": {"name": name, "statusCategory": {"key": category}}}})


def jira(*script: tuple) -> "Transport":
    """A Jira run's answers: the search pages given, then the parent's own issue, started."""
    return Transport(*script, jira_parent())


def body(value: object) -> bytes:
    return value if isinstance(value, bytes) else json.dumps(value).encode()


class Transport:
    """Answers each request with the next scripted response, and records the request."""

    def __init__(self, *script: tuple) -> None:
        self.script = list(script)
        self.requests: list[tuple[str, dict]] = []
        self.bodies: list[bytes | None] = []
        self.methods: list[str] = []
        self.mismatches: list[str] = []
        self.clock = 0.0

    def __call__(self, method: str, url: str, headers: dict, timeout: float, sent: bytes | None = None):
        self.requests.append((url, dict(headers)))
        self.methods.append(method)
        self.bodies.append(sent)
        if not self.script:
            self.mismatches.append(f"unscripted request {url}")
            raise OSError("connection refused")
        prefix, status, payload, *rest = self.script.pop(0)
        if not url.startswith(prefix):
            self.mismatches.append(f"expected {prefix}, got {url}")
        response_headers = rest[0] if rest else {}
        if len(rest) > 1:
            self.clock += rest[1]
        if isinstance(payload, Exception):
            raise payload
        return snapshot.Response(status, {key.lower(): value for key, value in response_headers.items()}, body(payload))

    def now(self) -> float:
        return self.clock


class SnapshotCase(unittest.TestCase):
    def run_kind(self, argv: list[str], transport: Transport, environ: dict, cwd: Path | None = None):
        slept: list[float] = []

        def sleep(seconds: float) -> None:
            slept.append(seconds)
            transport.clock += seconds

        code, out, err = snapshot.run(argv, environ=environ, transport=transport, clock=transport.now, sleep=sleep,
                                      cwd=cwd)
        for secret in SECRETS:
            self.assertNotIn(secret, out)
            self.assertNotIn(secret, err)
        self.assertEqual(transport.mismatches, [])
        if code == 0:
            self.assertEqual(err, "")
            self.assertTrue(out.endswith("\n") and out.count("\n") == 1, out)
        else:
            self.assertEqual(out, "")
            self.assertEqual(err.count("\n"), 1, err)
            self.assertTrue(err.startswith("snapshot: "), err)
            self.assertNotIn("Traceback", err)
        self.slept = slept
        return code, out, err

    def checked(self, out: str, parent: str | None = None) -> dict:
        document = json.loads(out)
        delivery_state.check_snapshot(document, "snapshot", 1e10, parent=parent)
        self.assertEqual(document["takenAt"], NOW)
        return document

    def failed(self, err: str, *needles: str) -> None:
        for needle in needles:
            self.assertIn(needle, err)


class JiraTests(SnapshotCase):
    def pages(self, *extra: str) -> Transport:
        return jira((SEARCH, 200, JIRA["page-1"]), (SEARCH, 200, JIRA["page-2"]))

    def test_children_page_by_token_and_map_to_tracker_items(self) -> None:
        transport = self.pages()
        code, out, _ = self.run_kind([*JIRA_ARGS, "--canceled-statuses", "Won't Do,Duplicate"], transport, JIRA_ENV)
        self.assertEqual(code, 0)
        first, second = (urllib.parse.parse_qs(urllib.parse.urlsplit(url).query) for url, _ in transport.requests[:2])
        # Then the parent's own issue, once: the search lists only its children.
        self.assertEqual([url for url, _ in transport.requests[2:]], [ISSUE])
        self.assertEqual(first["jql"], ["parent = APP-10"])
        self.assertNotIn("nextPageToken", first)
        self.assertEqual(second["nextPageToken"], ["token-page-2"])
        self.assertEqual(transport.requests[0][1]["Authorization"],
                         "Basic " + base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode())
        document = self.checked(out, parent="APP-10")
        self.assertEqual(document["source"], "tracker")
        items = {item["subject"]: item for item in document["items"]}
        self.assertEqual(list(items), ["APP-11", "APP-12", "APP-13", "APP-14"])
        self.assertEqual(items["APP-11"], {
            "subject": "APP-11", "title": "Rails for the views", "state": "backlog",
            "summary": "Shared config and link helpers.\nfirst point\nsecond\nline\n@Ama owns it.",
        })
        # Only the inward Blocks link to another child: not the outward one, the
        # Relates link, or a blocker outside the epic.
        self.assertEqual(items["APP-12"], {"subject": "APP-12", "title": "Line map renderer", "state": "started",
                                            "blockedBy": ["APP-11"]})
        self.assertEqual(items["APP-13"]["state"], "completed")
        summary = items["APP-13"]["summary"]
        self.assertLessEqual(len(summary), 1000)
        self.assertTrue(summary.endswith("…"))
        self.assertEqual(items["APP-14"], {"subject": "APP-14", "title": "Dropped idea", "state": "canceled"})

    def test_the_search_expands_transitions_and_an_expanded_issue_names_its_status_and_offered_moves(self) -> None:
        pages = copy.deepcopy(JIRA)
        first = pages["page-1"]["issues"][0]
        first["transitions"] = [{"id": "11", "name": "Refined", "to": {"name": "Ready for Dev"}},
                                {"id": "12", "name": "Won't do"}, {"id": "13", "name": "Refined"}, {"id": "14"},
                                {"id": "15", "name": ""}, "junk"]
        transport = jira((SEARCH, 200, pages["page-1"]), (SEARCH, 200, pages["page-2"]))
        code, out, _ = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertEqual(code, 0)
        for url, _ in transport.requests[:-1]:
            self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["expand"], ["transitions"])
        self.assertEqual(transport.requests[-1][0], ISSUE)
        items = {item["subject"]: item for item in self.checked(out, parent="APP-10")["items"]}
        self.assertEqual((items[first["key"]]["status"], items[first["key"]]["offeredTransitions"]),
                         (first["fields"]["status"]["name"], ["Refined", "Won't do"]))
        # An issue the answer gives no transitions for carries neither field.
        self.assertNotIn("status", items["APP-12"])
        self.assertNotIn("offeredTransitions", items["APP-12"])

    def test_an_issue_s_acceptance_criteria_section_is_its_criteria(self) -> None:
        pages = copy.deepcopy(JIRA)
        paragraph = lambda text: {"type": "paragraph", "content": [{"type": "text", "text": text}]}
        heading = lambda text: {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": text}]}
        item = lambda *parts: {"type": "listItem", "content": [paragraph(part) for part in parts]}
        pages["page-1"]["issues"][0]["fields"]["description"] = {"type": "doc", "version": 1, "content": [
            heading("Summary"), paragraph("Shared config."),
            heading("Acceptance Criteria"),
            {"type": "orderedList", "content": [item("Set up from a plan, every story has a lane."),
                                                item("next orders a blocked story", "after its dependencies.")]},
            heading("Execution Posture"), paragraph("test-first")]}
        # An issue whose description has no such section carries no criteria.
        pages["page-1"]["issues"][1]["fields"]["description"] = {"type": "doc", "version": 1, "content": [
            heading("Summary"), paragraph("Only a summary.")]}
        # One whose section holds only paragraphs yields them, up to the next heading.
        pages["page-2"]["issues"][0]["fields"]["description"] = {"type": "doc", "version": 1, "content": [
            heading("Acceptance Criteria"), paragraph("The view loads."), paragraph("It says when it fails."),
            heading("Notes"), paragraph("Not a criterion.")]}
        transport = jira((SEARCH, 200, pages["page-1"]), (SEARCH, 200, pages["page-2"]))
        code, out, _ = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertEqual(code, 0)
        items = {entry["subject"]: entry for entry in self.checked(out, parent="APP-10")["items"]}
        self.assertEqual(items["APP-11"]["criteria"], ["Set up from a plan, every story has a lane.",
                                                       "next orders a blocked story after its dependencies."])
        self.assertNotIn("criteria", items["APP-12"])
        self.assertEqual(items["APP-13"]["criteria"], ["The view loads.", "It says when it fails."])

    def test_the_parent_s_own_state_is_emitted_completed_while_its_children_are_started(self) -> None:
        pages = copy.deepcopy(JIRA)
        for page in (pages["page-1"], pages["page-2"]):
            for issue in page["issues"]:
                issue["fields"]["status"] = {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}
        transport = Transport((SEARCH, 200, pages["page-1"]), (SEARCH, 200, pages["page-2"]),
                              jira_parent("done", "Done"))
        code, out, _ = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertEqual(code, 0)
        document = self.checked(out, parent="APP-10")
        self.assertEqual({item["state"] for item in document["items"]}, {"started"})
        self.assertEqual(document["parent"], {"subject": "APP-10", "state": "completed"})
        self.assertEqual(transport.requests[-1][0], ISSUE)
        # A status the lead lists as canceled is canceled for the parent too; one the snapshot doesn't know fails.
        for script, expected in (((jira_parent("done", "Won't Do"),), "canceled"), ((jira_parent("new", "To Do"),), "backlog")):
            with self.subTest(expected=expected):
                code, out, _ = self.run_kind([*JIRA_ARGS, "--canceled-statuses", "Won't Do"],
                                             Transport((SEARCH, 200, JIRA["page-2"]), *script), JIRA_ENV)
                self.assertEqual((code, self.checked(out, parent="APP-10")["parent"]["state"]), (0, expected))
        code, _, err = self.run_kind(JIRA_ARGS, Transport((SEARCH, 200, JIRA["page-2"]), jira_parent("limbo")), JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "APP-10", "limbo")
        # A parent the tracker no longer has leaves the member out: nothing says it closed.
        code, out, _ = self.run_kind(JIRA_ARGS, Transport((SEARCH, 200, JIRA["page-2"]), (ISSUE, 404, b"{}")), JIRA_ENV)
        self.assertEqual(code, 0)
        self.assertNotIn("parent", self.checked(out, parent="APP-10"))
        # A parent read that fails leaves its state unknown and still prints the children's snapshot, so a lead's
        # record keeps its tracker items, and the delivery stays not known to be closed.
        for name, answer in (("unreachable", (ISSUE, 0, snapshot.Failure("could not reach example.atlassian.net: URLError"))),
                             ("refused", (ISSUE, 403, {"errorMessages": ["no permission"]})),
                             ("unauthorized", (ISSUE, 401, {})),
                             ("not JSON", (ISSUE, 200, b"<html>a sign-in page</html>"))):
            with self.subTest(parent_read=name):
                transport = Transport((SEARCH, 200, JIRA["page-2"]), answer)
                code, out, err = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
                self.assertEqual(code, 0, err)
                document = self.checked(out, parent="APP-10")
                self.assertNotIn("parent", document)
                self.assertEqual([item["subject"] for item in document["items"]],
                                 [issue["key"] for issue in JIRA["page-2"]["issues"]])
                self.assertEqual(transport.requests[-1][0], ISSUE)
        # An answer for the parent that holds no fields object says so, rather than failing some other way.
        for answer in ({"key": "APP-10"}, {"key": "APP-10", "fields": []}, ["APP-10"]):
            with self.subTest(answer=answer):
                code, _, err = self.run_kind(JIRA_ARGS, Transport((SEARCH, 200, JIRA["page-2"]), (ISSUE, 200, answer)),
                                             JIRA_ENV)
                self.assertNotEqual(code, 0)
                self.failed(err, "APP-10", "has no fields")

    def test_a_done_status_not_listed_as_canceled_is_completed(self) -> None:
        code, out, _ = self.run_kind(JIRA_ARGS, self.pages(), JIRA_ENV)
        self.assertEqual(code, 0)
        items = {item["subject"]: item for item in json.loads(out)["items"]}
        self.assertEqual(items["APP-14"]["state"], "completed")

    def test_a_named_blocks_link_type(self) -> None:
        pages = copy.deepcopy(JIRA)
        for link in pages["page-1"]["issues"][1]["fields"]["issuelinks"]:
            if link["type"]["name"] == "Relates":
                link["type"]["name"] = "Depends"
        transport = jira((SEARCH, 200, pages["page-1"]), (SEARCH, 200, pages["page-2"]))
        code, out, _ = self.run_kind([*JIRA_ARGS, "--blocks-link", "Depends"], transport, JIRA_ENV)
        self.assertEqual(code, 0)
        items = {item["subject"]: item for item in json.loads(out)["items"]}
        self.assertEqual(items["APP-12"]["blockedBy"], ["APP-14"])

    def test_unknown_or_missing_mapping_fields_fail_closed(self) -> None:
        no_links = copy.deepcopy(JIRA["page-2"])
        del no_links["issues"][0]["fields"]["issuelinks"]
        for name, page, needle in (("unknown-category", JIRA["unknown-category"], "undefined"),
                                   ("missing-status", JIRA["missing-status"], "APP-16"),
                                   ("missing issue links", no_links, "issue links")):
            with self.subTest(name):
                code, _, err = self.run_kind(JIRA_ARGS, jira((SEARCH, 200, page)), JIRA_ENV)
                self.assertNotEqual(code, 0)
                self.failed(err, needle)

    def test_self_and_repeated_blocks_links_are_kept_once(self) -> None:
        pages = copy.deepcopy(JIRA)
        links = pages["page-1"]["issues"][1]["fields"]["issuelinks"]
        blocks = links[0]["type"]
        links += [{"id": "8", "type": blocks, "inwardIssue": {"key": "APP-11"}},
                  {"id": "9", "type": blocks, "inwardIssue": {"key": "APP-12"}}]
        transport = jira((SEARCH, 200, pages["page-1"]), (SEARCH, 200, pages["page-2"]))
        code, out, _ = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertEqual(code, 0)
        items = {item["subject"]: item for item in json.loads(out)["items"]}
        self.assertEqual(items["APP-12"]["blockedBy"], ["APP-11"])

    def test_an_issue_listed_twice_fails(self) -> None:
        repeated = {**JIRA["page-2"], "issues": [JIRA["page-1"]["issues"][0]]}
        transport = jira((SEARCH, 200, JIRA["page-1"]), (SEARCH, 200, repeated))
        code, _, err = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "twice")

    def test_provider_text_that_is_not_unicode_fails_on_one_line(self) -> None:
        page = copy.deepcopy(JIRA["page-2"])
        page["issues"][0]["fields"]["summary"] = "x\ud800"
        code, _, err = self.run_kind(JIRA_ARGS, jira((SEARCH, 200, page)), JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "Unicode")

    def test_provider_values_in_a_refusal_stay_on_one_line_and_never_hold_a_credential(self) -> None:
        for value in ("weird\nline two", f"echo {TOKEN}", f"echo {SECRETS[1]}"):
            with self.subTest(value=value):
                page = copy.deepcopy(JIRA["unknown-category"])
                page["issues"][0]["fields"]["status"]["statusCategory"]["key"] = value
                code, _, err = self.run_kind(JIRA_ARGS, jira((SEARCH, 200, page)), JIRA_ENV)
                self.assertNotEqual(code, 0)
        # A long reflected token is redacted before the line is capped.
        long_token = "L" * 192
        page = copy.deepcopy(JIRA["unknown-category"])
        page["issues"][0]["fields"]["status"]["statusCategory"]["key"] = "x" * 40 + long_token
        code, _, err = self.run_kind(JIRA_ARGS, jira((SEARCH, 200, page)), {**JIRA_ENV, "JIRA_API_TOKEN": long_token})
        self.assertNotEqual(code, 0)
        self.assertNotIn("L" * 20, err)
        # The user's email is not a credential: a snapshot that names it still prints.
        page = copy.deepcopy(JIRA["page-2"])
        page["issues"][0]["fields"]["summary"] = f"Ask {EMAIL}"
        code, out, _ = self.run_kind(JIRA_ARGS, jira((SEARCH, 200, page)), JIRA_ENV)
        self.assertEqual(code, 0)
        self.assertIn(EMAIL, out)
        page = copy.deepcopy(JIRA["page-2"])
        page["issues"][0]["fields"]["summary"] = f"title {TOKEN}"
        code, _, err = self.run_kind(JIRA_ARGS, jira((SEARCH, 200, page)), JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "credential")

    def test_a_parent_that_is_not_an_issue_key_is_refused(self) -> None:
        argv = [*JIRA_ARGS]
        argv[argv.index("APP-10")] = "APP-10 OR project = HR"
        transport = Transport()
        code, _, err = self.run_kind(argv, transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "--parent")
        self.assertEqual(transport.requests, [])


class FailureTests(SnapshotCase):
    def test_a_missing_variable_is_named(self) -> None:
        for missing in ("JIRA_API_TOKEN", "JIRA_EMAIL"):
            with self.subTest(missing):
                environ = {key: value for key, value in JIRA_ENV.items() if key != missing}
                transport = Transport()
                code, _, err = self.run_kind(JIRA_ARGS, transport, environ)
                self.assertNotEqual(code, 0)
                self.failed(err, missing)
                self.assertEqual(transport.requests, [])

    def test_the_command_line_fails_on_one_line(self) -> None:
        environment = {key: value for key, value in os.environ.items() if key not in JIRA_ENV}
        environment["JIRA_EMAIL"] = EMAIL
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "snapshot.py"), *JIRA_ARGS],
                                capture_output=True, text=True, env=environment, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.count("\n"), 1, result.stderr)
        self.assertIn("JIRA_API_TOKEN", result.stderr)
        usage = subprocess.run([sys.executable, "-B", str(SCRIPTS / "snapshot.py"), "--kind", "jira"],
                               capture_output=True, text=True, timeout=60)
        self.assertNotEqual(usage.returncode, 0)
        self.assertEqual(usage.stdout, "")
        self.assertEqual(usage.stderr.count("\n"), 1, usage.stderr)

    def test_unauthorized_fails_at_once(self) -> None:
        for status in (401, 403):
            with self.subTest(status):
                transport = jira((SEARCH, status, JIRA["unauthorized"]))
                code, _, err = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
                self.assertNotEqual(code, 0)
                self.failed(err, str(status))
                self.assertEqual(len(transport.requests), 1)

    def test_rate_limited_then_success(self) -> None:
        transport = jira((SEARCH, 429, b"", {"Retry-After": "2"}), (SEARCH, 200, JIRA["page-1"]),
                              (SEARCH, 200, JIRA["page-2"]))
        code, _, _ = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertEqual(code, 0)
        self.assertEqual(self.slept, [2.0])

    def test_server_errors_retry_twice_then_fail(self) -> None:
        transport = jira(*[(SEARCH, 503, b"busy")] * 3)
        code, _, err = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "503")
        self.assertEqual(len(transport.requests), 3)

    def test_every_server_error_is_retried(self) -> None:
        transport = jira((SEARCH, 520, b""), (SEARCH, 200, JIRA["page-2"]))
        code, _, _ = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertEqual(code, 0)

    def test_retry_after_as_a_date(self) -> None:
        self.assertEqual(snapshot._retry_after("Wed, 21 Oct 2015 07:28:00 GMT", 0), 0.0)
        later = snapshot.email.utils.format_datetime(
            snapshot.datetime.datetime.now(snapshot.datetime.timezone.utc) + snapshot.datetime.timedelta(seconds=90),
            usegmt=True)
        self.assertGreater(snapshot._retry_after(later, 0), 60)
        self.assertEqual(snapshot._retry_after("nan", 0), 0.0)
        self.assertEqual(snapshot._retry_after("-5", 0), 0.0)

    def test_a_page_two_failure_leaves_stdout_empty(self) -> None:
        transport = jira((SEARCH, 200, JIRA["page-1"]), *[(SEARCH, 500, b"oops")] * 3)
        code, out, _ = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")

    def test_the_item_cap_is_a_failure(self) -> None:
        issue = JIRA["page-1"]["issues"][0]
        page = {"issues": [{**issue, "key": f"APP-{index}", "fields": {**issue["fields"], "issuelinks": []}}
                           for index in range(1000, 1500)], "isLast": True}
        code, _, err = self.run_kind(JIRA_ARGS, jira((SEARCH, 200, page)), JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "500")
        page["issues"].pop()
        code, _, _ = self.run_kind(JIRA_ARGS, jira((SEARCH, 200, page)), JIRA_ENV)
        self.assertEqual(code, 0)

    def test_the_deadline_bounds_the_whole_run(self) -> None:
        # The first page takes 30 seconds of a 20-second deadline.
        transport = jira((SEARCH, 200, JIRA["page-1"], {}, 30), (SEARCH, 200, JIRA["page-2"]))
        code, _, err = self.run_kind([*JIRA_ARGS, "--timeout-seconds", "20"], transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "deadline")
        self.assertEqual(len(transport.requests), 1)
        # A slow last page still misses the deadline.
        transport = jira((SEARCH, 200, JIRA["page-1"]), (SEARCH, 200, JIRA["page-2"], {}, 30))
        code, _, err = self.run_kind([*JIRA_ARGS, "--timeout-seconds", "20"], transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "deadline")
        # A Retry-After past the deadline is not waited for.
        transport = jira((SEARCH, 429, b"", {"Retry-After": "60"}))
        code, _, err = self.run_kind([*JIRA_ARGS, "--timeout-seconds", "20"], transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.slept, [])

    def test_a_redirect_to_another_origin_is_refused(self) -> None:
        transport = jira((SEARCH, 302, b"", {"Location": "https://evil.example/rest/api/3/search/jql"}))
        code, _, err = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "redirect")
        self.assertEqual(len(transport.requests), 1)
        for location in ("http://example.atlassian.net/rest/api/3/search/jql",
                         "https://example.atlassian.net:8443/rest/api/3/search/jql"):
            with self.subTest(location=location):
                transport = jira((SEARCH, 307, b"", {"Location": location}))
                code, _, err = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
                self.assertNotEqual(code, 0)
                self.assertEqual(len(transport.requests), 1)
        transport = jira(*[(SEARCH, 302, b"", {"Location": SEARCH + "jql=again"})] * 7)
        code, _, err = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "redirects")
        # A redirect on the same origin is followed, with the same authorization.
        transport = jira((SEARCH, 301, b"", {"Location": SEARCH + "jql=moved"}), (SEARCH, 200, JIRA["page-2"]))
        code, _, _ = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertEqual(code, 0)

    def test_malformed_json_fails(self) -> None:
        for payload in (b"{not json", b"[]", b"\xff\xfe"):
            with self.subTest(payload=payload):
                code, _, err = self.run_kind(JIRA_ARGS, jira((SEARCH, 200, payload)), JIRA_ENV)
                self.assertNotEqual(code, 0)

    def test_an_unexpected_exception_is_one_line_with_no_secret(self) -> None:
        transport = jira((SEARCH, 200, RuntimeError(f"boom {TOKEN} {SECRETS[1]}")))
        code, _, err = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "unexpected")


class BitbucketTests(SnapshotCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".agents").mkdir()
        (self.root / ".agents/orchestration.json").write_text(json.dumps(
            {"state": {"directory": "delivery", "renderers": [], "publishers": []}}), encoding="utf-8")
        self.state = self.root / "delivery"
        self.state.mkdir()
        (self.state / "state.json").write_text(json.dumps({
            "queue": [{"subject": "APP-11"}, {"subject": "APP-12"}],
            "planned": [{"subject": "APP-13"}],
            "tickets": {"ABC-7": {}, "APP-14": {}},
            "followups": [],
        }), encoding="utf-8")
        (self.state / "journal.jsonl").write_text(
            json.dumps({"at": "2026-10-20T00:00:00Z", "id": 1}) + "\n"
            + json.dumps({"at": "2026-10-21T00:00:00Z", "id": 2}) + "\n", encoding="utf-8")
        self.environ = {"BITBUCKET_TOKEN": TOKEN, "BITBUCKET_EMAIL": EMAIL}

    def args(self, *extra: str) -> list[str]:
        return ["--kind", "bitbucket", "--workspace", "example", "--repo", "app", "--binding",
                ".agents/orchestration.json", "--subject-pattern", "(APP|ABC)-[0-9]+", "--token-env",
                "BITBUCKET_TOKEN", "--now", NOW, *extra]

    def full(self) -> Transport:
        return Transport(
            (API + "pullrequests?", 200, BITBUCKET["page-1"]),
            (API + "pullrequests?page=2", 200, BITBUCKET["page-2"]),
            (API + "commit/eeeeeeeeeeee", 404, BITBUCKET["not-found"]),
            (API + "commit/aaaaaaaaaaaa", 200, BITBUCKET["commit-a"]),
            (API + "commit/cccccccccccc", 200, BITBUCKET["commit-c"]),
            (API + "commit/ffffffffffff", 200, BITBUCKET["commit-f"]),
        )

    def test_a_state_dir_reads_that_record_and_never_the_bindings(self) -> None:
        self.state.rename(self.root / "served")
        code, out, _ = self.run_kind(self.args("--state-dir", "served"), self.full(), self.environ, cwd=self.root)
        self.assertEqual(code, 0)
        self.assertEqual([item["subject"] for item in json.loads(out)["items"]], ["ABC-7", "APP-11", "APP-12", "APP-13"])
        code, _, err = self.run_kind(self.args("--state-dir", "gone"), self.full(), self.environ, cwd=self.root)
        self.assertNotEqual(code, 0)
        self.failed(err, "the record could not be read through --state-dir gone")

    def test_pull_requests_map_to_the_record_subjects(self) -> None:
        transport = self.full()
        code, out, _ = self.run_kind(self.args(), transport, self.environ, cwd=self.root / ".agents")
        self.assertEqual(code, 0)
        listing = urllib.parse.parse_qs(urllib.parse.urlsplit(transport.requests[0][0]).query)
        self.assertEqual(listing["q"], ['created_on >= 2026-10-20T00:00:00+00:00'])
        self.assertEqual(sorted(listing["state"]), ["DECLINED", "MERGED", "OPEN", "SUPERSEDED"])
        self.assertEqual(transport.requests[0][1]["Authorization"], f"Bearer {TOKEN}")
        document = self.checked(out)
        self.assertEqual(document["source"], "forge")
        self.assertEqual(document["items"], [
            # A draft whose short hash cannot be resolved keeps no head.
            {"subject": "ABC-7", "title": "ABC-7: draft work", "state": "draft",
             "changeRequest": "https://bitbucket.org/example/app/pull-requests/36"},
            # No open request: the most recently updated merged one wins over a later declined one.
            {"subject": "APP-11", "title": "APP-11: shared rails", "state": "merged",
             "changeRequest": "https://bitbucket.org/example/app/pull-requests/31", "head": "a" * 40},
            # Matched by its branch; the most recently updated open one wins.
            {"subject": "APP-12", "title": "Line map renderer", "state": "open",
             "changeRequest": "https://bitbucket.org/example/app/pull-requests/34",
             "head": "c" * 24 + "0123456789abcdef"},
            # Superseded is closed; a link off bitbucket.org is dropped.
            {"subject": "APP-13", "title": "APP-13: superseded", "state": "closed", "head": "f" * 40},
        ])

    def test_basic_authorization_with_a_user(self) -> None:
        transport = self.full()
        code, _, _ = self.run_kind(self.args("--user-env", "BITBUCKET_EMAIL"), transport, self.environ, cwd=self.root)
        self.assertEqual(code, 0)
        expected = "Basic " + base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()
        self.assertTrue(all(headers["Authorization"] == expected for _, headers in transport.requests))

    def test_a_pull_request_made_before_the_since_time_is_excluded(self) -> None:
        transport = Transport((API + "pullrequests?", 200, BITBUCKET["before-since"]))
        code, out, _ = self.run_kind(self.args(), transport, self.environ, cwd=self.root)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["items"], [])
        transport = Transport((API + "pullrequests?", 200, BITBUCKET["before-since"]),
                              (API + "commit/121212121212", 200, BITBUCKET["commit-1"]))
        code, out, _ = self.run_kind(self.args("--since", "2026-10-19T00:00:00Z"), transport, self.environ,
                                     cwd=self.root)
        self.assertEqual(code, 0)
        listing = urllib.parse.parse_qs(urllib.parse.urlsplit(transport.requests[0][0]).query)
        self.assertEqual(listing["q"], ['created_on >= 2026-10-19T00:00:00+00:00'])
        self.assertEqual([item["subject"] for item in json.loads(out)["items"]], ["APP-14"])

    def test_an_unknown_state_fails(self) -> None:
        code, _, err = self.run_kind(self.args(), Transport((API + "pullrequests?", 200, BITBUCKET["unknown-state"])),
                                     self.environ, cwd=self.root)
        self.assertNotEqual(code, 0)
        self.failed(err, "LIMBO")

    def test_a_missing_or_unreadable_record_fails(self) -> None:
        for label, damage in (("missing", lambda: (self.state / "state.json").unlink()),
                              ("unreadable", lambda: (self.state / "state.json").write_text("{", encoding="utf-8")),
                              ("no journal", lambda: (self.state / "journal.jsonl").unlink())):
            with self.subTest(label):
                self.setUp()
                damage()
                transport = Transport()
                code, _, err = self.run_kind(self.args(), transport, self.environ, cwd=self.root)
                self.assertNotEqual(code, 0)
                self.failed(err, "record")
                self.assertEqual(transport.requests, [])

    def test_the_since_time_of_a_new_record(self) -> None:
        # A new record's journal is empty: the since time must then be given, and once given the journal is not read.
        (self.state / "journal.jsonl").write_text("", encoding="utf-8")
        transport = Transport()
        code, _, err = self.run_kind(self.args(), transport, self.environ, cwd=self.root)
        self.assertNotEqual(code, 0)
        self.failed(err, "--since")
        self.assertEqual(transport.requests, [])
        # A damaged first line is an unreadable record, not an empty one.
        for line in ("\n", "[]\n", "{}\n", "{\n"):
            with self.subTest(first_line=line):
                (self.state / "journal.jsonl").write_text(line, encoding="utf-8")
                transport = Transport()
                code, _, err = self.run_kind(self.args(), transport, self.environ, cwd=self.root)
                self.assertNotEqual(code, 0)
                self.failed(err, "could not be read")
                self.assertEqual(transport.requests, [])
        (self.state / "journal.jsonl").unlink()
        transport = Transport((API + "pullrequests?", 200, BITBUCKET["before-since"]),
                              (API + "commit/121212121212", 200, BITBUCKET["commit-1"]))
        code, out, _ = self.run_kind(self.args("--since", "2026-10-19T00:00:00Z"), transport, self.environ,
                                     cwd=self.root)
        self.assertEqual(code, 0)
        self.assertEqual([item["subject"] for item in json.loads(out)["items"]], ["APP-14"])

    def test_a_next_page_on_another_origin_is_refused(self) -> None:
        for next_link in ("https://evil.example/2.0/repositories/example/app/pullrequests",
                          "http://api.bitbucket.org/2.0/repositories/example/app/pullrequests?page=2"):
            with self.subTest(next_link=next_link):
                page = {**BITBUCKET["page-1"], "next": next_link}
                transport = Transport((API + "pullrequests?", 200, page))
                code, _, err = self.run_kind(self.args(), transport, self.environ, cwd=self.root)
                self.assertNotEqual(code, 0)
                self.assertEqual(len(transport.requests), 1)

    def test_the_title_match_comes_before_the_branch(self) -> None:
        pull = copy.deepcopy(BITBUCKET["before-since"]["values"][0])
        pull.update(title="APP-11: from the title", created_on="2026-10-20T12:00:00.000000+00:00")
        pull["source"]["branch"]["name"] = "APP-12-branch"
        transport = Transport((API + "pullrequests?", 200, {"values": [pull]}),
                              (API + "commit/121212121212", 200, BITBUCKET["commit-1"]))
        code, out, _ = self.run_kind(self.args(), transport, self.environ, cwd=self.root)
        self.assertEqual(code, 0)
        self.assertEqual([item["subject"] for item in json.loads(out)["items"]], ["APP-11"])

    def test_a_resolved_hash_that_is_not_the_short_one_is_left_out(self) -> None:
        pull = copy.deepcopy(BITBUCKET["before-since"]["values"][0])
        pull["created_on"] = "2026-10-20T12:00:00.000000+00:00"
        transport = Transport((API + "pullrequests?", 200, {"values": [pull]}),
                              (API + "commit/121212121212", 200, BITBUCKET["commit-a"]))
        code, out, _ = self.run_kind(self.args(), transport, self.environ, cwd=self.root)
        self.assertEqual(code, 0)
        self.assertNotIn("head", json.loads(out)["items"][0])


GRAPHQL = "https://api.linear.app/graphql"
LINEAR_ENV = {"LINEAR_API_KEY": "lin_api_" + TOKEN.replace("-", "")}
LINEAR_ARGS = ["--kind", "linear", "--parent", "ENG-100", "--token-env", "LINEAR_API_KEY", "--now", NOW]
PULLS = "https://api.github.com/repos/example-org/example-repo/pulls?"


class LinearTests(SnapshotCase):
    def test_site_names_the_graphql_origin_the_linear_adapter_posts_to(self) -> None:
        site = "https://linear.example.test"
        transport = Transport((site + "/graphql", 200, LINEAR["page-1"]), (site + "/graphql", 200, LINEAR["page-2"]))
        code, out, _ = self.run_kind([*LINEAR_ARGS, "--site", site + "/"], transport, LINEAR_ENV)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), CAPTURED["tracker"])
        self.assertEqual([url for url, _ in transport.requests], [site + "/graphql"] * 2)
        # The key goes to whatever --site names, so only an https origin or this machine's loopback is taken.
        for bad in ("http://linear.example.test", "https://linear.example.test/api", "ftp://linear.example.test",
                    "https://linear.example.test?x=1", "https://linear.example.test#x"):
            with self.subTest(site=bad):
                code, _, err = self.run_kind([*LINEAR_ARGS, "--site", bad], Transport(), LINEAR_ENV)
                self.assertNotEqual(code, 0)
                self.assertIn("--site must be an https origin", err)
        for loopback in ("http://127.0.0.1:9", "http://localhost:9", "http://[::1]:9"):
            with self.subTest(site=loopback):
                code, _, _ = self.run_kind([*LINEAR_ARGS, "--site", loopback], Transport(
                    (loopback + "/graphql", 200, LINEAR["page-1"]), (loopback + "/graphql", 200, LINEAR["page-2"])),
                    LINEAR_ENV)
                self.assertEqual(code, 0)

    def test_the_tracker_snapshot_equals_the_captured_command_output(self) -> None:
        transport = Transport((GRAPHQL, 200, LINEAR["page-1"]), (GRAPHQL, 200, LINEAR["page-2"]))
        code, out, _ = self.run_kind(LINEAR_ARGS, transport, LINEAR_ENV)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), CAPTURED["tracker"])
        self.checked(out, parent="ENG-100")
        variables = [json.loads(body)["variables"] for body in transport.bodies]
        self.assertEqual(variables, [{"parent": "ENG-100", "after": None}, {"parent": "ENG-100", "after": "cursor-1"}])
        self.assertTrue(all(headers["Authorization"] == LINEAR_ENV["LINEAR_API_KEY"]
                            for _, headers in transport.requests))
        self.assertEqual(transport.methods, ["POST", "POST"])
        self.assertTrue(all(headers["Content-Type"] == "application/json" for _, headers in transport.requests))

    def test_each_item_s_status_is_its_state_s_own_name_and_no_move_is_listed(self) -> None:
        pages = copy.deepcopy(LINEAR)
        names = {}
        for name in ("page-1", "page-2"):
            for number, node in enumerate(pages[name]["data"]["issue"]["children"]["nodes"]):
                node["state"]["name"] = f"Column {name[-1]}.{number}"
                names.setdefault(node["identifier"], node["state"]["name"])  # a child listed twice is kept as first listed
        transport = Transport((GRAPHQL, 200, pages["page-1"]), (GRAPHQL, 200, pages["page-2"]))
        code, out, _ = self.run_kind(LINEAR_ARGS, transport, LINEAR_ENV)
        self.assertEqual(code, 0)
        document = self.checked(out, parent="ENG-100")
        self.assertEqual({item["subject"]: item["status"] for item in document["items"]}, names)
        self.assertFalse(any("offeredTransitions" in item for item in document["items"]))
        # Only the status is new: every other member is what the recorded answer gives.
        self.assertEqual([{k: v for k, v in item.items() if k != "status"} for item in document["items"]],
                         CAPTURED["tracker"]["items"])
        self.assertIn("state { type name }", json.loads(transport.bodies[0])["query"].split("children")[1])
        for bad in ("", 7):
            with self.subTest(name=bad):
                pages["page-1"]["data"]["issue"]["children"]["nodes"][0]["state"]["name"] = bad
                code, _, err = self.run_kind(LINEAR_ARGS, Transport((GRAPHQL, 200, pages["page-1"])), LINEAR_ENV)
                self.assertNotEqual(code, 0)
                self.failed(err, "ENG-101", "state name")

    def test_the_parent_s_own_state_is_emitted_completed_while_its_children_are_started(self) -> None:
        pages = copy.deepcopy(LINEAR)
        pages["page-1"]["data"]["issue"]["state"] = {"type": "completed"}
        for name in ("page-1", "page-2"):
            for node in pages[name]["data"]["issue"]["children"]["nodes"]:
                node["state"] = {"type": "started"}
        transport = Transport((GRAPHQL, 200, pages["page-1"]), (GRAPHQL, 200, pages["page-2"]))
        code, out, _ = self.run_kind(LINEAR_ARGS, transport, LINEAR_ENV)
        self.assertEqual(code, 0)
        document = self.checked(out, parent="ENG-100")
        self.assertEqual({item["state"] for item in document["items"]}, {"started"})
        self.assertEqual(document["parent"], {"subject": "ENG-100", "state": "completed"})
        # The query it sent asks for the parent's own state, beside its children's.
        self.assertIn("state { type }", json.loads(transport.bodies[0])["query"].split("children")[0])
        # An answer that gives no parent state leaves the member out, as the recorded answer does.
        self.assertNotIn("parent", json.loads(self.run_kind(
            LINEAR_ARGS, Transport((GRAPHQL, 200, LINEAR["page-1"]), (GRAPHQL, 200, LINEAR["page-2"])), LINEAR_ENV)[1]))
        for kind, expected in (("canceled", "canceled"), ("started", "started"), ("triage", "backlog")):
            with self.subTest(kind=kind):
                pages["page-1"]["data"]["issue"]["state"] = {"type": kind}
                code, out, _ = self.run_kind(LINEAR_ARGS, Transport((GRAPHQL, 200, pages["page-1"]),
                                                                    (GRAPHQL, 200, pages["page-2"])), LINEAR_ENV)
                self.assertEqual((code, self.checked(out, parent="ENG-100")["parent"]["state"]), (0, expected))
        pages["page-1"]["data"]["issue"]["state"] = {"type": "limbo"}
        code, _, err = self.run_kind(LINEAR_ARGS, Transport((GRAPHQL, 200, pages["page-1"])), LINEAR_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "ENG-100", "limbo")

    def test_refusals(self) -> None:
        for label, script, needle in (
            ("unknown state", [(GRAPHQL, 200, LINEAR["unknown-state"])], "limbo"),
            ("no issue", [(GRAPHQL, 200, LINEAR["no-issue"])], "ENG-100"),
            ("refused query", [(GRAPHQL, 200, LINEAR["refused"])], "Entity not found"),
            ("no next page", [(GRAPHQL, 200, LINEAR["page-1"]), (GRAPHQL, 200, LINEAR["repeated-cursor"])], "next page"),
            ("not JSON", [(GRAPHQL, 200, b"<html>")], "JSON"),
            ("next page as text", [(GRAPHQL, 200, self.changed(lambda info: info.update(hasNextPage="true")))], "shape"),
            ("blocker without an identifier", [(GRAPHQL, 200, self.blocked_by({"identifier": 5}))], "blocker"),
        ):
            with self.subTest(label):
                code, _, err = self.run_kind(LINEAR_ARGS, Transport(*script), LINEAR_ENV)
                self.assertNotEqual(code, 0)
                self.failed(err, needle)

    def changed(self, edit) -> dict:
        page = copy.deepcopy(LINEAR["page-1"])
        edit(page["data"]["issue"]["children"]["pageInfo"])
        return page

    def blocked_by(self, issue: object) -> dict:
        page = copy.deepcopy(LINEAR["unknown-state"])
        node = page["data"]["issue"]["children"]["nodes"][0]
        node["state"]["type"] = "started"
        node["inverseRelations"]["nodes"] = [{"type": "blocks", "issue": issue}]
        return page

    def test_a_child_blocked_by_the_parent_keeps_no_such_blocker(self) -> None:
        code, out, _ = self.run_kind(LINEAR_ARGS, Transport((GRAPHQL, 200, self.blocked_by({"identifier": "ENG-100"}))),
                                     LINEAR_ENV)
        self.assertEqual(code, 0)
        self.assertNotIn("blockedBy", json.loads(out)["items"][0])
        self.checked(out, parent="ENG-100")

    def test_a_hostile_summary_stays_inside_the_deadline(self) -> None:
        page = self.blocked_by({"identifier": "ENG-1"})
        page["data"]["issue"]["children"]["nodes"][0]["description"] = "## Summary\n" + "[a[b]c" * 40000
        started = time.monotonic()
        code, _, _ = self.run_kind(LINEAR_ARGS, Transport((GRAPHQL, 200, page)), LINEAR_ENV)
        self.assertEqual(code, 0)
        self.assertLess(time.monotonic() - started, 5)

    def test_summaries_read_text_as_the_ported_command_did(self) -> None:
        cases = (
            # JavaScript's \b is ASCII: the opener is still stripped before a letter it does not know.
            ("## Summary\nDeferred from\u00e9 review. Real text.", "Real text."),
            ("## Summary\n\u0661. item", "\u0661. item"),
            ("## Summary\n\ufeffword\ufeff", "word"),
            ("## \u017fummary\nNot a heading.", None),
            ("## Summary\n\x1cword", "\x1cword"),
        )
        for description, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(snapshot.summary_of(description), expected)
        # The sentence cut is measured in UTF-16 units: 60 emoji are 120 of them.
        long = "\U0001F600" * 60 + ". " + "y" * 1100
        self.assertEqual(snapshot.summary_of("## Summary\n" + long), "\U0001F600" * 60 + ".")

    def test_a_child_s_acceptance_criteria_section_is_its_criteria(self) -> None:
        pages = copy.deepcopy(LINEAR)
        nodes = pages["page-1"]["data"]["issue"]["children"]["nodes"]
        nodes[0]["description"] = ("## Summary\n\nShared config.\n\n## Acceptance Criteria\n\n"
                                   "1. Set up from a plan, every story has a lane.\n"
                                   "2. `next` orders a blocked story\n   after its dependencies.\n"
                                   "3. One command adds a story, which `lead-assign` then accepts.\n\n"
                                   "## Execution Posture\n\ntest-first\n")
        nodes[1]["description"] = "## Summary\n\nNo criteria here.\n"
        transport = Transport((GRAPHQL, 200, pages["page-1"]), (GRAPHQL, 200, pages["page-2"]))
        code, out, _ = self.run_kind(LINEAR_ARGS, transport, LINEAR_ENV)
        self.assertEqual(code, 0)
        items = {entry["subject"]: entry for entry in self.checked(out, parent="ENG-100")["items"]}
        self.assertEqual(items[nodes[0]["identifier"]]["criteria"], [
            "Set up from a plan, every story has a lane.", "next orders a blocked story after its dependencies.",
            "One command adds a story, which lead-assign then accepts."])
        self.assertNotIn("criteria", items[nodes[1]["identifier"]])

    def test_criteria_read_the_section_s_list_or_else_its_paragraphs_within_their_bounds(self) -> None:
        cases = (
            ("## Acceptance Criteria\n- one\n- two\n  continued\n\n- three", ["one", "two continued", "three"]),
            ("### Acceptance criteria:\n1) [linked](https://x.test) **one**", ["linked one"]),
            ("## Acceptance Criteria\nFirst paragraph\nwraps.\n\nSecond.", ["First paragraph wraps.", "Second."]),
            ("## Acceptance Criteria\n\n## Next\n- not these", None),
            ("```\n## Acceptance Criteria\n- fenced\n```", None),
            ("## Summary\n- no section", None),
            (None, None),
        )
        for description, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(snapshot.criteria_of(description), expected)
        many = snapshot.criteria_of("## Acceptance Criteria\n" + "".join(f"- c{n}\n" for n in range(60)))
        self.assertEqual(many, [f"c{n}" for n in range(50)])
        long = snapshot.criteria_of("## Acceptance Criteria\n- " + "x" * 700)
        self.assertEqual((len(long[0]), long[0][-1]), (600, "…"))
        # The snapshot's shape holds them, and refuses one over its bound.
        document = {"takenAt": NOW, "source": "tracker",
                    "items": [{"subject": "ENG-1", "state": "backlog", "criteria": ["x" * 601]}]}
        with self.assertRaises(delivery_state.StateError):
            delivery_state.check_snapshot(document, "snapshot", 1e10)

    def test_a_reflected_key_is_redacted_in_any_form(self) -> None:
        reflected = {"errors": [{"message": f"bad key {LINEAR_ENV['LINEAR_API_KEY']} or lin_api_someotherkey9"}]}
        code, _, err = self.run_kind(LINEAR_ARGS, Transport((GRAPHQL, 200, reflected)), LINEAR_ENV)
        self.assertNotEqual(code, 0)
        self.assertNotIn("lin_api_", err)
        self.assertIn("[redacted]", err)


class GithubTests(SnapshotCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".agents").mkdir()
        (self.root / ".agents/orchestration.json").write_text(json.dumps(
            {"state": {"directory": "delivery", "renderers": [], "publishers": []}}), encoding="utf-8")
        state = self.root / "delivery"
        state.mkdir()
        (state / "state.json").write_text(json.dumps({
            "queue": [{"subject": "ENG-101"}, {"subject": "ENG-102"}],
            "planned": [{"subject": "ENG-103"}],
            "tickets": {"ENG-104": {}, "ENG-105": {}, "ENG-106": {}},
            "followups": [],
        }), encoding="utf-8")
        (state / "journal.jsonl").write_text(json.dumps({"at": "2026-10-20T08:00:00Z", "id": 1}) + "\n",
                                             encoding="utf-8")
        self.environ = {"GITHUB_TOKEN": TOKEN}

    def args(self, *extra: str) -> list[str]:
        return ["--kind", "github", "--owner", "example-org", "--repo", "example-repo", "--binding",
                ".agents/orchestration.json", "--subject-pattern", "^ENG-[0-9]+$", "--token-env", "GITHUB_TOKEN",
                "--now", NOW, *extra]

    def pages(self) -> Transport:
        return Transport((PULLS, 200, GITHUB["page-1"], {"Link": f'<{PULLS}page=2>; rel="next", <{PULLS}page=2>; rel="last"'}),
                         (PULLS + "page=2", 200, GITHUB["page-2"], {"Link": f'<{PULLS}page=3>; rel="next"'}))

    def test_the_forge_snapshot_equals_the_captured_command_output(self) -> None:
        transport = self.pages()
        code, out, _ = self.run_kind(self.args(), transport, self.environ, cwd=self.root)
        self.assertEqual(code, 0)
        document = self.checked(out)
        # The captured command lists its items in the tracker's order; the port sorts them by subject.
        by_subject = lambda items: sorted(items, key=lambda item: item["subject"])  # noqa: E731
        legacy_items = [{key: value for key, value in item.items() if key != "mergedAt"} for item in document["items"]]
        self.assertEqual({**document, "items": by_subject(legacy_items)},
                         {**CAPTURED["forge"], "items": by_subject(CAPTURED["forge"]["items"])})
        for item in document["items"]:
            if item["state"] == "merged":
                source = next(pull for page in GITHUB.values() for pull in page
                              if pull.get("html_url") == item["changeRequest"])
                self.assertEqual(snapshot._moment(item["mergedAt"], "snapshot"),
                                 snapshot._moment(source["merged_at"], "source"))
            else:
                self.assertNotIn("mergedAt", item)
        listing = urllib.parse.parse_qs(urllib.parse.urlsplit(transport.requests[0][0]).query)
        self.assertEqual(listing, {"state": ["all"], "sort": ["created"], "direction": ["desc"], "per_page": ["100"]})
        # The second page reaches a pull request made before the since time, so the third is never read.
        self.assertEqual(len(transport.requests), 2)
        self.assertTrue(all(headers["Authorization"] == f"Bearer {TOKEN}" for _, headers in transport.requests))
        self.assertTrue(all(headers["Accept"] == "application/vnd.github+json"
                            and headers["X-GitHub-Api-Version"] == "2022-11-28" for _, headers in transport.requests))
        self.assertEqual(transport.methods, ["GET", "GET"])

    def test_a_state_dir_reads_that_record_and_never_the_bindings(self) -> None:
        (self.root / "delivery").rename(self.root / "served")
        code, out, _ = self.run_kind(self.args("--state-dir", "served"), self.pages(), self.environ, cwd=self.root)
        self.assertEqual(code, 0)
        self.assertEqual({item["subject"] for item in json.loads(out)["items"]},
                         {item["subject"] for item in CAPTURED["forge"]["items"]})
        # The binding's directory is gone, so without --state-dir there is no record to read.
        code, _, err = self.run_kind(self.args(), self.pages(), self.environ, cwd=self.root)
        self.assertNotEqual(code, 0)
        self.failed(err, "the record could not be read")

    def test_a_new_record_needs_since(self) -> None:
        (self.root / "delivery/journal.jsonl").write_text("", encoding="utf-8")
        transport = Transport()
        code, _, err = self.run_kind(self.args(), transport, self.environ, cwd=self.root)
        self.assertNotEqual(code, 0)
        self.failed(err, "--since")
        self.assertEqual(transport.requests, [])

    def test_since_includes_an_older_pull_request(self) -> None:
        transport = Transport((PULLS, 200, GITHUB["page-1"], {"Link": f'<{PULLS}page=2>; rel="next"'}),
                              (PULLS + "page=2", 200, GITHUB["page-2"]))
        code, out, _ = self.run_kind(self.args("--since", "2026-10-19T00:00:00Z"), transport, self.environ,
                                     cwd=self.root)
        self.assertEqual(code, 0)
        items = {item["subject"]: item for item in json.loads(out)["items"]}
        # An open request made before the epic now wins for its subject.
        self.assertEqual(items["ENG-101"]["changeRequest"], "https://github.com/example-org/example-repo/pull/10")

    def test_a_title_opening_with_its_subject_counts_as_a_bracketed_one_does(self) -> None:
        pull = {**GITHUB["page-1"][0], "title": "ENG-101: the title run-delivery's open-pr requires"}
        code, out, _ = self.run_kind(self.args(), Transport((PULLS, 200, [pull])), self.environ, cwd=self.root)
        self.assertEqual(code, 0)
        self.assertEqual([item["subject"] for item in json.loads(out)["items"]], ["ENG-101"])
        for title in ("fix: nothing the record holds", "Mentions ENG-101: in passing", "ENG-101 without a colon"):
            with self.subTest(title):
                code, out, _ = self.run_kind(self.args(), Transport((PULLS, 200, [{**pull, "title": title}])), self.environ, cwd=self.root)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)["items"], [])

    def test_the_cap_counts_only_pull_requests_in_the_window(self) -> None:
        recent = [{**GITHUB["page-1"][0], "number": index, "title": f"[ENG-{index}]: x"} for index in range(450)]
        older = [{**pull, "number": 1000 + index, "created_at": "2026-10-01T00:00:00Z"}
                 for index, pull in enumerate(recent[:100])]
        code, _, _ = self.run_kind(self.args(), Transport((PULLS, 200, recent + older)), self.environ, cwd=self.root)
        self.assertEqual(code, 0)

    def test_paging_refusals(self) -> None:
        for label, link in (("unreadable next link", f'<{PULLS}page=2>; type="x"; rel="next"'),
                            ("a page that names itself", f'<{PULLS}state=all&sort=created&direction=desc&per_page=100>; rel="next"')):
            with self.subTest(label):
                transport = Transport((PULLS, 200, GITHUB["page-1"][:1], {"Link": link}),
                                      (PULLS, 200, GITHUB["page-1"][:1], {"Link": link}))
                code, _, err = self.run_kind(self.args(), transport, self.environ, cwd=self.root)
                self.assertNotEqual(code, 0)
                self.assertLessEqual(len(transport.requests), 1)

    def test_a_link_off_the_host_and_the_merge_time_tie_break(self) -> None:
        merged_later = {**GITHUB["page-1"][-1], "number": 41, "title": "[ENG-106]: merged later",
                        "created_at": "2026-10-20T20:00:00Z", "merged_at": "2026-10-21T09:00:00Z",
                        "closed_at": "2026-10-21T09:00:00Z", "state": "closed",
                        "html_url": "https://evil.example/example-org/example-repo/pull/41"}
        merged_first = {**merged_later, "number": 40, "title": "[ENG-106]: merged first",
                        "created_at": "2026-10-20T21:00:00Z", "merged_at": "2026-10-21T08:00:00Z",
                        "closed_at": "2026-10-21T08:00:00Z", "html_url": "https://github.com/example-org/example-repo/pull/40"}
        code, out, _ = self.run_kind(self.args(), Transport((PULLS, 200, [merged_first, merged_later])), self.environ,
                                     cwd=self.root)
        self.assertEqual(code, 0)
        item = json.loads(out)["items"][0]
        # The later merge wins although it was created earlier, and its link off github.com is dropped.
        self.assertEqual(item["title"], "[ENG-106]: merged later")
        self.assertNotIn("changeRequest", item)

    def test_refusals(self) -> None:
        unknown = copy.deepcopy(GITHUB["page-1"])
        unknown[0]["state"] = "limbo"
        foreign = {"Link": '<https://evil.example/repos/example-org/example-repo/pulls?page=2>; rel="next"'}
        for label, script, needle in (
            ("unknown state", [(PULLS, 200, unknown)], "limbo"),
            ("not a list", [(PULLS, 200, {"message": "x"})], "list"),
            ("next page on another origin", [(PULLS, 200, GITHUB["page-1"], foreign)], "origin"),
        ):
            with self.subTest(label):
                code, _, err = self.run_kind(self.args(), Transport(*script), self.environ, cwd=self.root)
                self.assertNotEqual(code, 0)
                self.failed(err, needle)


class SharedRuleTests(SnapshotCase):
    """The failure, bounds and credential rules, for each kind added after the first two."""

    def kinds(self):
        github = GithubTests("test_refusals")
        github.setUp()
        self.addCleanup(github.temporary.cleanup)
        return (("linear", LINEAR_ARGS, GRAPHQL, LINEAR_ENV, None),
                ("github", github.args(), PULLS, github.environ, github.root))

    def test_shared_failures(self) -> None:
        for kind, argv, url, environ, cwd in self.kinds():
            for label, script, needle in (
                ("unauthorized", [(url, 401, b"{}")], "401"),
                ("server errors", [(url, 503, b"")] * 3, "503"),
                ("malformed JSON", [(url, 200, b"{")], "JSON"),
                ("cross-origin redirect", [(url, 302, b"", {"Location": "https://evil.example/x"})], "redirect"),
                ("unexpected", [(url, 200, RuntimeError(f"boom {TOKEN}"))], "unexpected"),
            ):
                with self.subTest(kind=kind, case=label):
                    code, _, err = self.run_kind(argv, Transport(*script), environ, cwd=cwd)
                    self.assertNotEqual(code, 0)
                    self.failed(err, needle)
            with self.subTest(kind=kind, case="rate limited then success"):
                first = LINEAR["page-1"] if kind == "linear" else GITHUB["page-1"]
                second = LINEAR["page-2"] if kind == "linear" else GITHUB["page-2"]
                script = [(url, 429, b"", {"Retry-After": "1"}), (url, 200, first,
                          {} if kind == "linear" else {"Link": f'<{PULLS}page=2>; rel="next"'}), (url, 200, second)]
                code, _, _ = self.run_kind(argv, Transport(*script), environ, cwd=cwd)
                self.assertEqual(code, 0)
            with self.subTest(kind=kind, case="missing variable"):
                code, _, err = self.run_kind(argv, Transport(), {}, cwd=cwd)
                self.assertNotEqual(code, 0)
                self.failed(err, next(iter(environ)))
            with self.subTest(kind=kind, case="deadline"):
                code, _, err = self.run_kind([*argv, "--timeout-seconds", "5"], Transport((url, 200, b"{}", {}, 30)),
                                             environ, cwd=cwd)
                self.assertNotEqual(code, 0)
                self.failed(err, "deadline")

    def test_the_item_cap(self) -> None:
        node = LINEAR["page-2"]["data"]["issue"]["children"]["nodes"][1]
        nodes = [{**node, "identifier": f"ENG-{index}"} for index in range(1000, 1500)]
        page = {"data": {"issue": {"createdAt": "2026-10-20T08:00:00.000Z",
                                   "children": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}}}
        code, _, err = self.run_kind(LINEAR_ARGS, Transport((GRAPHQL, 200, page)), LINEAR_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "500")
        pull = GITHUB["page-1"][0]
        pulls = [{**pull, "number": index} for index in range(500)]
        github = GithubTests("test_refusals")
        github.setUp()
        self.addCleanup(github.temporary.cleanup)
        code, _, err = self.run_kind(github.args(), Transport((PULLS, 200, pulls)), github.environ, cwd=github.root)
        self.assertNotEqual(code, 0)
        self.failed(err, "500")


SCOPE = json.loads((FIXTURES / "jira-scope/jira-scope.json").read_text(encoding="utf-8"))
COMPONENTS = "https://example.atlassian.net/rest/api/3/project/APP/components"
LABELS = "https://example.atlassian.net/rest/api/3/label?"
STATUSES = "https://example.atlassian.net/rest/api/3/project/APP/statuses"
QA_MAP = ("--status-state", "Ready for QA=completed", "--status-state", "In QA=completed",
          "--status-state", "Ready for Release=completed", "--status-state", "QA Return=started")


class JiraScopeTests(SnapshotCase):
    """--scope splits a parent shared across repositories, and --status-state reads a status by its name."""

    def take(self, *extra: str, cwd: Path | None = None, search: dict | None = None) -> tuple[dict, Transport]:
        transport = jira((SEARCH, 200, search or SCOPE["search"]))
        code, out, err = self.run_kind([*JIRA_ARGS, *extra], transport, JIRA_ENV, cwd=cwd)
        self.assertEqual(code, 0, err)
        return self.checked(out), transport

    def split(self, document: dict) -> tuple[list[str], list[str], list[str]]:
        return ([item["subject"] for item in document["items"]], [item["subject"] for item in document["outside"]],
                [item["subject"] for item in document["items"] if item.get("unmarked")])

    def test_a_component_scope_leaves_another_repository_s_children_outside_with_their_status_and_component(self) -> None:
        document, transport = self.take("--scope", "component=Mobile App")
        self.assertEqual(self.split(document), (["APP-21", "APP-24", "APP-25", "APP-27"],
                                                ["APP-22", "APP-23", "APP-26"], ["APP-24"]))
        self.assertEqual(document["outside"][0], {"subject": "APP-22", "title": "[Backend] Login API",
                                                  "state": "started", "status": "Ready for QA",
                                                  "marks": {"component": ["Backend"]}})
        # The search asks for what the scope reads; a blocker outside still blocks.
        fields = urllib.parse.parse_qs(urllib.parse.urlsplit(transport.requests[0][0]).query)["fields"][0]
        self.assertEqual(fields.split(","), ["summary", "description", "status", "issuelinks", "components", "labels"])
        self.assertEqual(document["items"][0]["blockedBy"], ["APP-22"])
        self.assertNotIn("statusStates", document)
        # An outside child keeps its summary and blockers, as an item would.
        search = copy.deepcopy(SCOPE["search"])
        search["issues"][2]["fields"]["description"] = {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Refresh before expiry."}]}]}
        search["issues"][2]["fields"]["issuelinks"] = [{"type": {"name": "Blocks"}, "inwardIssue": {"key": "APP-22"}}]
        document, _ = self.take("--scope", "component=Mobile App", search=search)
        self.assertEqual(document["outside"][1], {"subject": "APP-23", "title": "Backend: token refresh",
                                                  "state": "started", "summary": "Refresh before expiry.",
                                                  "blockedBy": ["APP-22"], "status": "In QA",
                                                  "marks": {"component": ["Backend"]}})

    def test_labels_prefixes_and_several_scopes_split_the_same_way(self) -> None:
        for extra, inside, outside in (
            (("--scope", "label=mobile"), ["APP-21", "APP-24", "APP-25", "APP-27"], ["APP-22", "APP-23", "APP-26"]),
            # A prefix ignores case; "Backend:" is a marker too, so that child is outside rather than unmarked.
            (("--scope", "prefix=[Mobile]"), ["APP-21", "APP-24", "APP-25", "APP-27"], ["APP-22", "APP-23", "APP-26"]),
            (("--scope", "prefix=backend:"), ["APP-23", "APP-24"], ["APP-21", "APP-22", "APP-25", "APP-26", "APP-27"]),
            # Several values: a child matching any of them is in scope.
            (("--scope", "component=Nowhere", "--scope", "label=mobile", "--scope", "prefix=[Backend]"),
             ["APP-21", "APP-22", "APP-24", "APP-25", "APP-26", "APP-27"], ["APP-23"]),
        ):
            with self.subTest(extra=extra):
                document, _ = self.take(*extra)
                self.assertEqual(self.split(document)[:2], (inside, outside))
                self.assertEqual(self.split(document)[2], ["APP-24"])
        document, _ = self.take("--scope", "prefix=[Mobile]")
        self.assertEqual(document["outside"][1]["marks"], {"prefix": ["Backend:"]})

    def test_a_child_with_no_value_of_the_scoped_kind_stays_in_scope_flagged(self) -> None:
        search = copy.deepcopy(SCOPE["search"])
        search["issues"][1]["fields"]["components"] = []  # APP-22 loses its component but keeps its label
        document, _ = self.take("--scope", "component=Mobile App", search=search)
        self.assertEqual(self.split(document), (["APP-21", "APP-22", "APP-24", "APP-25", "APP-27"],
                                                ["APP-23", "APP-26"], ["APP-22", "APP-24"]))
        # Marked by another scoped kind it isn't unmarked: with the label scoped too, it is simply outside.
        document, _ = self.take("--scope", "component=Mobile App", "--scope", "label=mobile", search=search)
        self.assertEqual(self.split(document), (["APP-21", "APP-24", "APP-25", "APP-27"],
                                                ["APP-22", "APP-23", "APP-26"], ["APP-24"]))

    def test_the_delivery_s_own_plan_wins_over_the_tracker(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repo"
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "state").mkdir()
        (root / "state/plan.json").write_text(json.dumps({"units": [{"subject": "APP-26"}, {"subject": "APP-21"}]}),
                                              encoding="utf-8")
        document, _ = self.take("--scope", "component=Mobile App", "--state-dir", "state", cwd=root)
        self.assertEqual(self.split(document)[:2], (["APP-21", "APP-24", "APP-25", "APP-26", "APP-27"],
                                                    ["APP-22", "APP-23"]))
        # The absolute path a binding's {stateDirectory} fills in reads the same plan; a record with no plan keeps none.
        absolute, _ = self.take("--scope", "component=Mobile App", "--state-dir", str(root / "state"), cwd=root)
        self.assertEqual(absolute, document)
        (root / "state/plan.json").unlink()
        self.assertEqual(self.split(self.take("--scope", "component=Mobile App", "--state-dir", "state", cwd=root)[0])[1],
                         ["APP-22", "APP-23", "APP-26"])
        (root / "state/plan.json").write_text("{", encoding="utf-8")
        code, _, err = self.run_kind([*JIRA_ARGS, "--scope", "component=Mobile App", "--state-dir", "state"],
                                     jira((SEARCH, 200, SCOPE["search"])), JIRA_ENV, cwd=root)
        self.assertNotEqual(code, 0)
        self.failed(err, "the plan could not be read through --state-dir state")

    def test_a_mapped_status_reads_as_its_state_whatever_its_category(self) -> None:
        document, _ = self.take(*QA_MAP, "--canceled-statuses", "Done", "--status-state", "In QA=completed")
        states = {item["subject"]: item["state"] for item in document["items"]}
        self.assertEqual(states, {"APP-21": "started", "APP-22": "completed", "APP-23": "completed", "APP-24": "backlog",
                                  "APP-25": "started", "APP-26": "completed", "APP-27": "canceled"})
        self.assertEqual(document["statusStates"], {"Ready for QA": "completed", "In QA": "completed",
                                                    "Ready for Release": "completed", "QA Return": "started"})
        self.assertNotIn("outside", document)
        # The parent's own status is read by the same map.
        transport = Transport((SEARCH, 200, SCOPE["search"]), jira_parent("indeterminate", "Ready for Release"))
        code, out, _ = self.run_kind([*JIRA_ARGS, *QA_MAP], transport, JIRA_ENV)
        self.assertEqual(self.checked(out)["parent"], {"subject": "APP-10", "state": "completed"})

    def test_without_either_argument_the_snapshot_is_as_it_was(self) -> None:
        document, transport = self.take()
        fields = urllib.parse.parse_qs(urllib.parse.urlsplit(transport.requests[0][0]).query)["fields"][0]
        self.assertEqual(fields, "summary,description,status,issuelinks")
        self.assertEqual(sorted(document), ["items", "parent", "source", "takenAt"])
        self.assertEqual([item["subject"] for item in document["items"]], [f"APP-{n}" for n in range(21, 28)])
        self.assertFalse(any("unmarked" in item for item in document["items"]))
        self.assertEqual(document["items"][1]["state"], "started")

    def test_usage_refusals(self) -> None:
        for extra, needle in (
            (("--scope", "team=Mobile"), "--scope takes <kind>=<value>"),
            (("--scope", "component="), "--scope takes"),
            (("--status-state", "In QA=deployed"), "--status-state takes"),
            (("--status-state", "In QA"), "--status-state takes"),
            (("--status-state", "In QA=completed", "--status-state", "In QA=started"), "maps 'In QA' twice"),
            (("--canceled-statuses", "In QA", "--status-state", "In QA=completed"), "is in --canceled-statuses"),
        ):
            with self.subTest(extra=extra):
                code, _, err = self.run_kind([*JIRA_ARGS, *extra], Transport(), JIRA_ENV)
                self.assertNotEqual(code, 0)
                self.failed(err, needle)
        code, _, err = self.run_kind([*LINEAR_ARGS, "--scope", "label=x"], Transport(), LINEAR_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "--scope is for --kind jira only")

    def test_check_names_unknown_values_unmapped_statuses_and_unmarked_children(self) -> None:
        transport = jira((SEARCH, 200, SCOPE["search"]))
        transport.script += [(COMPONENTS, 200, SCOPE["components"]), (LABELS, 200, SCOPE["labels-1"]),
                             (LABELS, 200, SCOPE["labels-2"]), (STATUSES, 200, SCOPE["statuses"])]
        code, out, err = self.run_kind([*JIRA_ARGS, "--scope", "component=Mobile App", "--scope", "component=Frontend",
                                        "--scope", "label=web", "--scope", "label=ios", "--status-state",
                                        "Ready for QA=completed", "--status-state", "Deployed=completed", "--check"],
                                       transport, JIRA_ENV)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"check": "tracker-scope/1",
                                           "unknown": ["component Frontend", "label ios", "status Deployed"],
                                           "unmapped": ["In Progress", "In QA", "In Review", "QA Return",
                                                        "Ready for Release"],
                                           "unmarked": ["APP-24"]})
        self.assertIn("startAt=2", transport.requests[4][0])
        # Nothing to check against beyond the search: no lists are read.
        transport = jira((SEARCH, 200, SCOPE["search"]))
        code, out, _ = self.run_kind([*JIRA_ARGS, "--scope", "prefix=[Mobile]", "--check"], transport, JIRA_ENV)
        self.assertEqual(json.loads(out), {"check": "tracker-scope/1", "unknown": [], "unmapped": [],
                                           "unmarked": ["APP-24"]})
        self.assertEqual(len(transport.requests), 2)


def tracker_snapshot(items: list[dict], **members: object) -> dict:
    return {"takenAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "tracker", "items": items, **members}


def queued(subject: str, **fields: object) -> dict:
    return {"order": 1, "subject": subject, "title": f"Work {subject}", "lane": "lane-1", "stage": "merged",
            "head": "a" * 40, "syncedHead": None, "leadInputs": [], "reportedRounds": 1, "runId": None, "checks": [],
            **fields}


OUTSIDE = [{"subject": "APP-22", "title": "[Backend] Login API", "state": "started", "status": "Ready for QA",
            "marks": {"component": ["Backend"]}},
           {"subject": "APP-23", "title": "Backend: token refresh", "state": "backlog", "status": "To Do",
            "marks": {"component": ["Backend"]}}]


class ScopedSweepTests(unittest.TestCase):
    """reconcile reads a scoped tracker snapshot: outside work is never taken in, and what was is cleared once."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "state"
        delivery_state.init(self.directory, "Mobile part")
        self.count = 0

    def reconcile(self, snapshot: dict) -> dict:
        self.count += 1
        path = self.root / f"tracker-{self.count}.json"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        return delivery_state.reconcile(self.directory, [path])

    def state(self) -> dict:
        return delivery_state.show(self.directory)["state"]

    def lead(self, changes: dict) -> None:
        queue = changes.pop("queue", None)
        delivery_state.patch(self.directory, writer="lead", version=self.state()["version"], changes=changes)
        if queue is not None:
            # Merged work reaches the queue through hand-offs and the runner; the record is written as they leave it.
            path = self.directory / delivery_state.STATE_FILE
            state = json.loads(path.read_text(encoding="utf-8"))
            state["queue"] = queue
            path.write_text(json.dumps(state), encoding="utf-8")
            self.assertEqual(delivery_state.state_errors(state), [])

    def cleared(self) -> list[dict]:
        return [(entry["subject"], entry["data"]) for entry in delivery_state.read_journal(self.directory)
                if entry["kind"] == "work.cleared"]

    def test_outside_children_are_kept_for_the_views_and_never_added_or_reported(self) -> None:
        result = self.reconcile(tracker_snapshot([{"subject": "APP-21", "title": "Login", "state": "started"}],
                                                 outside=OUTSIDE))
        state = self.state()
        self.assertEqual([entry["subject"] for entry in state["planned"]], ["APP-21"])
        self.assertEqual(state["followups"], [])
        self.assertEqual(state["outside"], OUTSIDE)
        self.assertFalse([entry for entry in [*result["changes"], *result["reports"]]
                          if entry["subject"] in ("APP-22", "APP-23")])
        self.assertEqual(self.cleared(), [])
        # Read again, nothing drifts; an outside list that empties takes the member away.
        again = self.reconcile(tracker_snapshot([{"subject": "APP-21", "title": "Login", "state": "started"}],
                                                outside=OUTSIDE))
        self.assertEqual((again["drift"], again["reports"]), (False, []))
        self.reconcile(tracker_snapshot([{"subject": "APP-21", "title": "Login", "state": "started"}], outside=[]))
        self.assertNotIn("outside", self.state())

    def test_a_record_already_holding_outside_work_drops_it_once_and_keeps_its_own_stories(self) -> None:
        own_planned = [
            # Stories of this delivery that the tracker marks outside, each kept by one sign of the plan or a lane.
            {"subject": "APP-30", "title": "Own, with a lane", "lane": "lane-1", "stage": "building", "trackerStarted": True},
            {"subject": "APP-31", "title": "Own, assigned", "lane": "lane-1", "stage": "planned"},
            {"subject": "APP-33", "title": "Own, not yet started", "lane": None, "stage": "planned"},
            {"subject": "APP-34", "title": "Own, ordered after", "lane": None, "stage": "building", "trackerStarted": True,
             "after": ["APP-30"]},
            {"subject": "APP-35", "title": "Own, with a change request", "lane": None, "stage": "building",
             "trackerStarted": True, "changeRequest": "https://example.test/pull/35"},
            {"subject": "APP-36", "title": "Own, with a run", "lane": None, "stage": "building", "trackerStarted": True,
             "runId": "run-36"},
            {"subject": "APP-37", "title": "Own, building by its change request alone", "lane": None, "stage": "building"},
            {"subject": "APP-38", "title": "Own, in review", "lane": None, "stage": "review", "trackerStarted": True},
            {"subject": "APP-39", "title": "Own, placed by its files", "lane": None, "stage": "building",
             "trackerStarted": True, "files": ["app/settings.py"]},
        ]
        self.lead({
            # What the sweep added before the snapshot was scoped: lane-less building work.
            "planned": [{"subject": "APP-22", "title": "[Backend] Login API", "lane": None, "stage": "building",
                         "trackerStarted": True}, *own_planned],
            # Follow-ups the tracker added, one with no reference; one the lead filed by hand under an outside key; and
            # one the lead filed for a finding.
            "followups": [{"id": "APP-23", "title": "Backend: token refresh", "reference": "APP-23"},
                          {"id": "APP-26", "title": "Push service", "reference": None},
                          {"id": "APP-27", "title": "Check the backend's logout", "reference": "https://example.test/APP-27"},
                          {"id": "F-3", "title": "Measure it", "reference": "https://example.test/F-3"}],
            "queue": [queued("APP-32")],
            "tickets": {"APP-22": {"title": "[Backend] Login API", "summary": "The API."}},
        })
        states = {"APP-30": "completed"}
        own = [{"subject": entry["subject"], "title": entry["title"], "state": states.get(entry["subject"], "started"),
                "status": "In Progress", "marks": {"component": ["Backend"]}} for entry in [*own_planned, queued("APP-32")]]
        elsewhere = [*OUTSIDE,
                     {"subject": "APP-26", "title": "Push service", "state": "started", "marks": {"component": ["Backend"]}},
                     {"subject": "APP-27", "title": "Logout API", "state": "backlog", "marks": {"component": ["Backend"]}}]
        snapshot = tracker_snapshot([], outside=[*reversed(elsewhere), *own])
        result = self.reconcile(snapshot)
        state = self.state()
        self.assertEqual([entry["subject"] for entry in state["planned"]], [entry["subject"] for entry in own_planned])
        self.assertEqual([entry["id"] for entry in state["followups"]], ["APP-27", "F-3"])
        self.assertEqual([entry["subject"] for entry in state["queue"]], ["APP-32"])
        self.assertNotIn("APP-22", state.get("tickets", {}))
        self.assertEqual(state["outside"], sorted(elsewhere, key=lambda item: item["subject"]),
                         "the delivery's own stories are not elsewhere, and the list is in subject order")
        why = "outside this delivery's scope"
        self.assertEqual(self.cleared(), [("APP-22", {"from": "planned", "why": why}),
                                          ("APP-23", {"from": "followups", "why": why}),
                                          ("APP-26", {"from": "followups", "why": why})])
        self.assertTrue(all("cleared" not in change for change in result["changes"]))
        # The delivery's own stories still follow the tracker, outside or not: a started ticket moves planned work on,
        # and a completed one is reported against work still building.
        planned = {entry["subject"]: entry for entry in state["planned"]}
        self.assertEqual(planned["APP-31"]["stage"], "building")
        self.assertIn({"subject": "APP-31", "change": "started in the tracker: now building", "recovery": False},
                      result["changes"])
        # Lane-less work of this delivery keeps its shape, so no later sweep can take it for work the sweep added.
        self.assertEqual((planned["APP-33"]["stage"], "trackerStarted" in planned["APP-33"]), ("planned", False))
        self.assertNotIn("trackerStarted", planned["APP-37"])
        self.assertFalse([change for change in result["changes"] if change["subject"] in ("APP-33", "APP-37")])
        self.assertIn({"subject": "APP-30", "note": "completed in the tracker but still building in the record: check the forge"},
                      result["reports"])
        # The next sweep, and the one after, find nothing more to clear.
        for _ in range(2):
            self.reconcile(snapshot)
        self.assertEqual(len(self.cleared()), 3)
        self.assertEqual([entry["subject"] for entry in self.state()["planned"]], [entry["subject"] for entry in own_planned])

    def test_a_tracker_snapshot_no_longer_scoped_empties_the_outside_member(self) -> None:
        self.reconcile(tracker_snapshot([], outside=OUTSIDE))
        self.assertEqual(self.state()["outside"], OUTSIDE)
        self.reconcile({**tracker_snapshot([]), "source": "forge"})
        self.assertEqual(self.state()["outside"], OUTSIDE, "a forge snapshot says nothing of the tracker's scope")
        self.reconcile(tracker_snapshot([]))
        self.assertNotIn("outside", self.state())

    def test_a_story_of_the_delivery_journaled_as_added_is_never_cleared(self) -> None:
        self.lead({"planned": [{"subject": "APP-40", "title": "Added", "lane": None, "stage": "building",
                                "trackerStarted": True}]})
        delivery_state.append_journal(self.directory, kind="story.added", subject="APP-40",
                                      data={"title": "Added", "why": "found during review"})
        self.reconcile(tracker_snapshot([], outside=[{"subject": "APP-40", "state": "started",
                                                      "marks": {"label": ["backend"]}}]))
        self.assertEqual([entry["subject"] for entry in self.state()["planned"]], ["APP-40"])
        self.assertEqual(self.cleared(), [])

    def test_a_merged_story_whose_ticket_moves_to_a_status_mapped_to_started_is_reported(self) -> None:
        self.lead({"queue": [queued("APP-25"), queued("APP-21"), queued("APP-28", stage="queued")]})
        mapped = {"QA Return": "started", "Ready for QA": "completed"}
        result = self.reconcile(tracker_snapshot([
            {"subject": "APP-25", "title": "Profile", "state": "started", "status": "QA Return"},
            {"subject": "APP-21", "title": "Login", "state": "completed", "status": "Ready for QA"},
            # Handed and not yet merged, its ticket sent back: not returned after a merge that hasn't happened.
            {"subject": "APP-28", "title": "Logout", "state": "started", "status": "QA Return"},
        ], statusStates=mapped))
        self.assertEqual(result["reports"], [{"subject": "APP-25",
                                              "note": "returned after merge (QA Return): assign a fix"}])
        # Started by its category alone, with no map, a merged story's ticket is the usual post-merge state.
        result = self.reconcile(tracker_snapshot([
            {"subject": "APP-25", "title": "Profile", "state": "started", "status": "In QA"}], statusStates=mapped))
        self.assertEqual(result["reports"], [])
        result = self.reconcile(tracker_snapshot([
            {"subject": "APP-25", "title": "Profile", "state": "started", "status": "QA Return"}]))
        self.assertEqual(result["reports"], [])

    def test_next_reads_an_outside_blocker_s_state_so_deployed_work_elsewhere_unblocks(self) -> None:
        self.lead({"planned": [{"subject": "APP-21", "title": "Login", "lane": None, "stage": "planned"}],
                   "lanes": [{"id": "lane-1", "title": "Mobile", "state": "idle", "since": "2026-11-18T00:00:00Z",
                              "focus": "", "waitingOn": None}]})
        item = {"subject": "APP-21", "title": "Login", "state": "backlog", "blockedBy": ["APP-22"]}
        for state, blocked in (("started", True), ("completed", False)):
            with self.subTest(state=state):
                snapshot = tracker_snapshot([item], outside=[{**OUTSIDE[0], "state": state}])
                ranked = delivery_state.next_for_lane(self.state(), "lane-1", snapshot, None)
                self.assertEqual([(entry["subject"], entry["blocked"]) for entry in ranked], [("APP-21", blocked)])
                self.assertNotIn("not in the snapshot", " ".join(entry["reason"] for entry in ranked))

    def test_an_unscoped_snapshot_writes_no_outside_member(self) -> None:
        self.reconcile(tracker_snapshot([{"subject": "APP-22", "title": "Login API", "state": "started"}]))
        state = self.state()
        self.assertNotIn("outside", state)
        self.assertEqual(state["planned"], [{"subject": "APP-22", "title": "Login API", "lane": None,
                                             "stage": "building", "trackerStarted": True}])

    def test_the_snapshot_check_refuses_outside_lists_that_cannot_be(self) -> None:
        for snapshot, needle in (
            (tracker_snapshot([{"subject": "APP-22", "state": "started"}], outside=OUTSIDE[:1]), "in items too"),
            (tracker_snapshot([], outside=[OUTSIDE[0], OUTSIDE[0]]), "each subject appears once"),
            ({**tracker_snapshot([], outside=[]), "source": "forge"}, "/outside: only a tracker snapshot"),
            ({**tracker_snapshot([], statusStates={"QA": "started"}), "source": "forge"}, "/statusStates: only"),
            (tracker_snapshot([], statusStates={"QA": "deployed"}), "statusStates"),
            (tracker_snapshot([{"subject": "APP-21", "state": "started", "unmarked": False}]), "unmarked"),
            ({**tracker_snapshot([{"subject": "APP-21", "state": "open", "unmarked": True}]), "source": "forge"},
             "/items/0/unmarked: only a tracker snapshot carries it"),
        ):
            with self.subTest(needle=needle):
                with self.assertRaises(delivery_state.StateError) as raised:
                    delivery_state.check_snapshot(snapshot, "tracker.json", 1e10)
                self.assertIn(needle, str(raised.exception))



def sibling_row(name: str, stories: list[dict], outcome: str = "read", **fields: object) -> dict:
    """A sibling as reconcile leaves it: its last kept read's stories, and its latest read's outcome."""
    at = "2026-11-18T10:00:00Z"
    return {"name": name, "link": "https://example.test/d/" + "a" * 64, "addedAt": at, "lastOutcome": outcome,
            "lastAttemptAt": at, "readAt": at, "trackerParent": "APP-1", "stories": stories, **fields}


class SiblingSweepTests(unittest.TestCase):
    """The tracker sweep leaves a child a sibling delivery holds to it, with or without a scope."""

    # The scoped sweep's fixture, without its cases.
    setUp, reconcile, state, lead, cleared = (ScopedSweepTests.setUp, ScopedSweepTests.reconcile, ScopedSweepTests.state,
                                              ScopedSweepTests.lead, ScopedSweepTests.cleared)

    def siblings(self, *rows: dict) -> None:
        path = self.directory / delivery_state.STATE_FILE
        state = json.loads(path.read_text(encoding="utf-8"))
        state["meta"]["trackerParent"] = "APP-1"
        state["siblings"] = list(rows)
        path.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(delivery_state.state_errors(state), [])

    def test_a_child_a_sibling_holds_is_never_taken_in_and_a_copy_the_sweep_added_is_cleared_once(self) -> None:
        backend = [{"subject": "APP-40", "title": "Login API", "stage": "building"},
                   {"subject": "APP-41", "title": "Token refresh", "stage": "planned"}]
        self.lead({
            # A copy the sweep took in before the sibling was read, and a follow-up it filed the same way.
            "planned": [{"subject": "APP-40", "title": "Login API", "lane": None, "stage": "building", "trackerStarted": True}],
            "followups": [{"id": "APP-41", "title": "Token refresh", "reference": "APP-41"}],
            "tickets": {"APP-40": {"title": "Login API", "summary": "The API."}},
        })
        self.siblings(sibling_row("backend", backend))
        children = [{"subject": "APP-40", "title": "Login API", "state": "started"},
                    {"subject": "APP-41", "title": "Token refresh", "state": "backlog"},
                    {"subject": "APP-42", "title": "Login screen", "state": "started"}]
        # No --scope: the tracker lists every child of the parent as its own.
        result = self.reconcile(tracker_snapshot(children))
        state = self.state()
        self.assertEqual([entry["subject"] for entry in state["planned"]], ["APP-42"])
        self.assertEqual(state["followups"], [])
        self.assertNotIn("APP-40", state.get("tickets", {}))
        self.assertNotIn("outside", state, "a child a sibling holds is drawn in its group, not elsewhere")
        why = "held by sibling backend"
        self.assertEqual(self.cleared(), [("APP-40", {"from": "planned", "why": why}),
                                          ("APP-41", {"from": "followups", "why": why})])
        self.assertFalse([entry for entry in result["reports"] if entry["subject"] in ("APP-40", "APP-41")])
        # A second sweep finds nothing more to clear, and takes nothing in.
        again = self.reconcile(tracker_snapshot(children))
        self.assertEqual(len(self.cleared()), 2)
        self.assertEqual((again["drift"], again["reports"]), (False, []))
        # A sibling that can't be read now still holds what its last kept read listed: nothing comes back.
        self.siblings(sibling_row("backend", backend, outcome="unavailable"))
        self.reconcile(tracker_snapshot(children))
        self.assertEqual([entry["subject"] for entry in self.state()["planned"]], ["APP-42"])
        self.assertEqual(self.state()["followups"], [])
        self.assertEqual(len(self.cleared()), 2)

    def test_a_story_of_this_delivery_with_a_subject_a_sibling_lists_is_never_cleared(self) -> None:
        own = [{"subject": "APP-50", "title": "Own, with a lane", "lane": "lane-1", "stage": "building", "trackerStarted": True},
               {"subject": "APP-51", "title": "Own, not yet started", "lane": None, "stage": "planned"},
               {"subject": "APP-52", "title": "Own, ordered after", "lane": None, "stage": "building",
                "trackerStarted": True, "after": ["APP-50"]}]
        self.lead({"planned": own, "queue": [queued("APP-53")]})
        listed = [{"subject": subject, "title": "Theirs", "stage": "building"} for subject in ("APP-50", "APP-51", "APP-52", "APP-53")]
        self.siblings(sibling_row("backend", listed))
        snapshot = tracker_snapshot([{"subject": subject, "title": "Ours", "state": "started"}
                                     for subject in ("APP-50", "APP-51", "APP-52", "APP-53")])
        for _ in range(2):
            self.reconcile(snapshot)
        state = self.state()
        self.assertEqual([entry["subject"] for entry in state["planned"]], ["APP-50", "APP-51", "APP-52"])
        self.assertEqual([entry["subject"] for entry in state["queue"]], ["APP-53"])
        self.assertEqual(self.cleared(), [])
        # Lane-less work of this delivery keeps its shape, so no later sweep takes it for work the sweep added.
        planned = {entry["subject"]: entry for entry in state["planned"]}
        self.assertEqual((planned["APP-51"]["stage"], "trackerStarted" in planned["APP-51"]), ("planned", False))

    def test_a_story_journaled_as_added_is_never_cleared_though_it_looks_tracker_added(self) -> None:
        self.lead({"planned": [{"subject": "APP-60", "title": "Added by the lead", "lane": None, "stage": "building",
                                "trackerStarted": True}]})
        delivery_state.append_journal(self.directory, kind="story.added", subject="APP-60",
                                      data={"title": "Added by the lead", "why": "found during review"})
        self.siblings(sibling_row("backend", [{"subject": "APP-60", "title": "Theirs", "stage": "building"}]))
        self.reconcile(tracker_snapshot([{"subject": "APP-60", "title": "Ours", "state": "started"}]))
        self.assertEqual([entry["subject"] for entry in self.state()["planned"]], ["APP-60"])
        self.assertEqual(self.cleared(), [])

    def test_with_a_scope_a_child_a_sibling_holds_is_cleared_for_the_sibling_and_left_out_of_elsewhere(self) -> None:
        self.lead({"planned": [{"subject": "APP-22", "title": "[Backend] Login API", "lane": None, "stage": "building",
                                "trackerStarted": True}]})
        self.siblings(sibling_row("backend", [{"subject": "APP-22", "title": "Login API", "stage": "review"}]))
        self.reconcile(tracker_snapshot([], outside=OUTSIDE))
        self.assertEqual(self.cleared(), [("APP-22", {"from": "planned", "why": "held by sibling backend"})])
        # The scoped snapshot's outside list is still kept whole: the views decide which group draws each child.
        self.assertEqual(self.state()["outside"], OUTSIDE)

    def test_the_tracker_still_reports_on_a_story_of_this_delivery_a_sibling_lists(self) -> None:
        self.lead({"planned": [{"subject": "APP-70", "title": "Own", "lane": "lane-1", "stage": "building",
                                "trackerStarted": True}]})
        self.siblings(sibling_row("backend", [{"subject": "APP-70", "title": "Theirs", "stage": "building"}]))
        result = self.reconcile(tracker_snapshot([{"subject": "APP-70", "title": "Own", "state": "canceled"}]))
        self.assertIn({"subject": "APP-70", "note": "canceled in the tracker but still building in the record"},
                      result["reports"])

    def test_a_subject_two_siblings_hold_is_cleared_for_the_first_in_name_order(self) -> None:
        self.lead({"planned": [{"subject": "APP-80", "title": "Shared", "lane": None, "stage": "building",
                                "trackerStarted": True}]})
        listed = [{"subject": "APP-80", "title": "Shared", "stage": "building"}]
        self.siblings(sibling_row("web", listed), sibling_row("api", listed))
        self.reconcile(tracker_snapshot([{"subject": "APP-80", "title": "Shared", "state": "started"}]))
        self.assertEqual(self.cleared(), [("APP-80", {"from": "planned", "why": "held by sibling api"})])

    def test_a_sibling_now_of_another_epic_holds_nothing_it_listed_before(self) -> None:
        listed = [{"subject": "APP-90", "title": "Theirs once", "stage": "building"}]
        self.siblings(sibling_row("backend", listed, outcome="other-epic"))
        result = self.reconcile(tracker_snapshot([{"subject": "APP-90", "title": "Ours now", "state": "started"}]))
        # The views draw such a sibling with no stories, so the sweep takes the child in as it would any other.
        self.assertEqual([entry["subject"] for entry in self.state()["planned"]], ["APP-90"])
        self.assertIn("APP-90", [change["subject"] for change in result["changes"]])
        self.assertEqual(self.cleared(), [])

    def test_a_forge_snapshot_alone_clears_nothing(self) -> None:
        self.lead({"planned": [{"subject": "APP-40", "title": "Login API", "lane": None, "stage": "building",
                                "trackerStarted": True}]})
        self.siblings(sibling_row("backend", [{"subject": "APP-40", "title": "Login API", "stage": "building"}]))
        self.count += 1
        path = self.root / f"forge-{self.count}.json"
        path.write_text(json.dumps({**tracker_snapshot([]), "source": "forge"}), encoding="utf-8")
        delivery_state.reconcile(self.directory, [path])
        self.assertEqual(self.cleared(), [])


class TrackerScopeDoctorTests(unittest.TestCase):
    """doctor's tracker snapshot scope row runs the binding's snapshot with --check and reads what it names."""

    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("run_delivery_scope_doctor",
                                                      PRODUCT_ROOT / "skills/run-delivery/scripts/run_delivery.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"
        self.state.mkdir()
        (self.state / "state.json").write_text(json.dumps({"meta": {"trackerParent": "APP-10"}}), encoding="utf-8")
        self.ran: list[list[str]] = []
        self.answer = subprocess.CompletedProcess([], 0, json.dumps(
            {"check": "tracker-scope/1", "unknown": [], "unmapped": [], "unmarked": []}), "")

    def row(self, *extra: str, token: tuple[str, str] = ("ok", "JIRA_API_TOKEN set")) -> tuple[str, str]:
        doctor = object.__new__(self.module.Doctor)
        doctor.tracker_token = lambda: token
        doctor.root = self.state.parent
        doctor.c = SimpleNamespace(state=self.state, delivery="DELIVERY-1")
        doctor.binding = {"state": {"trackerSnapshot": {"argv": [
            "python3", "snapshot.py", "--kind", "jira", "--parent", "{parent}", "--state-dir", "{stateDirectory}",
            *extra], "timeoutSeconds": 30}}}

        def run(argv, cwd=None, check=True, **kw):
            self.ran.append(list(argv))
            return self.answer

        with mock.patch.object(self.module, "run", run):
            return doctor.tracker_scope()

    def found(self, **found: list[str]) -> None:
        self.answer = subprocess.CompletedProcess([], 0, json.dumps(
            {"check": "tracker-scope/1", "unknown": [], "unmapped": [], "unmarked": [], **found}), "")

    def test_without_scope_or_status_map_the_row_does_not_apply_and_runs_nothing(self) -> None:
        self.assertEqual(self.row("--canceled-statuses", "Won't Do"),
                         ("skipped-ok", "(the tracker snapshot sets no --scope or --status-state)"))
        self.assertEqual(self.ran, [])

    def test_the_check_runs_the_binding_s_own_snapshot_filled_in(self) -> None:
        self.assertEqual(self.row("--scope", "component=Mobile App"),
                         ("ok", "every scope value and mapped status is known"))
        # The parent is the record's tracker parent, never the delivery's own key; with no record, the key.
        (self.state / "state.json").unlink()
        self.row("--scope", "component=Mobile App")
        self.assertEqual(self.ran[-1][5], "DELIVERY-1")
        del self.ran[-1]
        self.assertEqual(self.ran, [["python3", "snapshot.py", "--kind", "jira", "--parent", "APP-10", "--state-dir",
                                     str(self.state), "--scope", "component=Mobile App", "--check"]])

    def test_unknown_values_are_wrong_and_unmapped_or_unmarked_ones_warn(self) -> None:
        self.found(unknown=["component Frontend", "status Deployed"], unmapped=["In QA"], unmarked=["APP-24"])
        status, detail = self.row("--scope", "component=Frontend", "--status-state", "Deployed=completed")
        self.assertEqual(status, "wrong")
        for needle in ("the project has no component Frontend, status Deployed", "In QA", "APP-24"):
            self.assertIn(needle, detail)
        self.found(unmapped=["In QA", "QA Return"])
        self.assertEqual(self.row("--status-state", "Ready for QA=completed"),
                         ("warn", "In Progress statuses with no --status-state: In QA, QA Return; map each, or leave "
                                  "it to its category"))
        self.found(unmarked=["APP-24"])
        self.assertEqual(self.row("--scope", "label=mobile"),
                         ("warn", "in scope with no scoped value: APP-24; tag each in the tracker"))

    def test_a_scope_without_the_state_directory_warns_that_the_delivery_file_is_not_kept(self) -> None:
        doctor_argv = ["python3", "snapshot.py", "--kind", "jira", "--parent", "{parent}", "--scope", "label=mobile"]
        doctor = object.__new__(self.module.Doctor)
        doctor.root, doctor.tracker_token = self.state.parent, lambda: ("ok", "set")
        doctor.c = SimpleNamespace(state=self.state, delivery="DELIVERY-1")
        doctor.binding = {"state": {"trackerSnapshot": {"argv": doctor_argv}}}
        with mock.patch.object(self.module, "run", lambda argv, **kw: self.answer):
            status, detail = doctor.tracker_scope()
        self.assertEqual(status, "warn")
        self.assertIn("it sets --scope without --state-dir {stateDirectory}", detail)
        # A status map alone needs no state directory.
        doctor.binding = {"state": {"trackerSnapshot": {"argv": [
            "python3", "snapshot.py", "--kind", "jira", "--parent", "{parent}", "--status-state", "In QA=completed"]}}}
        with mock.patch.object(self.module, "run", lambda argv, **kw: self.answer):
            self.assertEqual(doctor.tracker_scope(), ("ok", "every scope value and mapped status is known"))

    def test_with_the_token_unset_the_row_is_skipped_and_runs_nothing(self) -> None:
        self.assertEqual(self.row("--scope", "label=mobile", token=("missing", "JIRA_API_TOKEN is unset")),
                         ("skipped", "(needs tracker-token)"))
        self.assertEqual(self.ran, [])

    def test_a_failed_check_is_wrong_and_names_why(self) -> None:
        self.answer = subprocess.CompletedProcess([], 1, "", "snapshot: the provider refused the credentials: HTTP 401\n")
        self.assertEqual(self.row("--scope", "label=mobile"),
                         ("wrong", "the tracker snapshot's --check failed: snapshot: the provider refused the "
                                   "credentials: HTTP 401"))


if __name__ == "__main__":
    unittest.main()
