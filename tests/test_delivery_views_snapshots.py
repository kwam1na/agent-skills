"""The delivery-views Jira and Bitbucket snapshot kinds, against recorded responses.

Each response comes from a fixture under tests/fixtures/delivery-views-snapshots,
whose provenance sidecar says how it was made. A scripted transport stands in
for the network and records every request the adapter makes.
"""

from __future__ import annotations

import base64
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

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
JIRA_ARGS = ["--kind", "jira", "--site", "https://eshyft.atlassian.net", "--parent", "SHFT-10",
             "--user-env", "JIRA_EMAIL", "--token-env", "JIRA_API_TOKEN", "--now", NOW]
SEARCH = "https://eshyft.atlassian.net/rest/api/3/search/jql?"
API = "https://api.bitbucket.org/2.0/repositories/eshyft/eshyftnew/"


def body(value: object) -> bytes:
    return value if isinstance(value, bytes) else json.dumps(value).encode()


class Transport:
    """Answers each request with the next scripted response, and records the request."""

    def __init__(self, *script: tuple) -> None:
        self.script = list(script)
        self.requests: list[tuple[str, dict]] = []
        self.bodies: list[bytes | None] = []
        self.mismatches: list[str] = []
        self.clock = 0.0

    def __call__(self, method: str, url: str, headers: dict, timeout: float, sent: bytes | None = None):
        self.requests.append((url, dict(headers)))
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
        return Transport((SEARCH, 200, JIRA["page-1"]), (SEARCH, 200, JIRA["page-2"]))

    def test_children_page_by_token_and_map_to_tracker_items(self) -> None:
        transport = self.pages()
        code, out, _ = self.run_kind([*JIRA_ARGS, "--canceled-statuses", "Won't Do,Duplicate"], transport, JIRA_ENV)
        self.assertEqual(code, 0)
        first, second = (urllib.parse.parse_qs(urllib.parse.urlsplit(url).query) for url, _ in transport.requests)
        self.assertEqual(first["jql"], ["parent = SHFT-10"])
        self.assertNotIn("nextPageToken", first)
        self.assertEqual(second["nextPageToken"], ["token-page-2"])
        self.assertEqual(transport.requests[0][1]["Authorization"],
                         "Basic " + base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode())
        document = self.checked(out, parent="SHFT-10")
        self.assertEqual(document["source"], "tracker")
        items = {item["subject"]: item for item in document["items"]}
        self.assertEqual(list(items), ["SHFT-11", "SHFT-12", "SHFT-13", "SHFT-14"])
        self.assertEqual(items["SHFT-11"], {
            "subject": "SHFT-11", "title": "Rails for the views", "state": "backlog",
            "summary": "Shared config and link helpers.\nfirst point\nsecond\nline\n@Ama owns it.",
        })
        # Only the inward Blocks link to another child: not the outward one, the
        # Relates link, or a blocker outside the epic.
        self.assertEqual(items["SHFT-12"], {"subject": "SHFT-12", "title": "Line map renderer", "state": "started",
                                            "blockedBy": ["SHFT-11"]})
        self.assertEqual(items["SHFT-13"]["state"], "completed")
        summary = items["SHFT-13"]["summary"]
        self.assertLessEqual(len(summary), 1000)
        self.assertTrue(summary.endswith("…"))
        self.assertEqual(items["SHFT-14"], {"subject": "SHFT-14", "title": "Dropped idea", "state": "canceled"})

    def test_a_done_status_not_listed_as_canceled_is_completed(self) -> None:
        code, out, _ = self.run_kind(JIRA_ARGS, self.pages(), JIRA_ENV)
        self.assertEqual(code, 0)
        items = {item["subject"]: item for item in json.loads(out)["items"]}
        self.assertEqual(items["SHFT-14"]["state"], "completed")

    def test_a_named_blocks_link_type(self) -> None:
        pages = copy.deepcopy(JIRA)
        for link in pages["page-1"]["issues"][1]["fields"]["issuelinks"]:
            if link["type"]["name"] == "Relates":
                link["type"]["name"] = "Depends"
        transport = Transport((SEARCH, 200, pages["page-1"]), (SEARCH, 200, pages["page-2"]))
        code, out, _ = self.run_kind([*JIRA_ARGS, "--blocks-link", "Depends"], transport, JIRA_ENV)
        self.assertEqual(code, 0)
        items = {item["subject"]: item for item in json.loads(out)["items"]}
        self.assertEqual(items["SHFT-12"]["blockedBy"], ["SHFT-14"])

    def test_unknown_or_missing_mapping_fields_fail_closed(self) -> None:
        no_links = copy.deepcopy(JIRA["page-2"])
        del no_links["issues"][0]["fields"]["issuelinks"]
        for name, page, needle in (("unknown-category", JIRA["unknown-category"], "undefined"),
                                   ("missing-status", JIRA["missing-status"], "SHFT-16"),
                                   ("missing issue links", no_links, "issue links")):
            with self.subTest(name):
                code, _, err = self.run_kind(JIRA_ARGS, Transport((SEARCH, 200, page)), JIRA_ENV)
                self.assertNotEqual(code, 0)
                self.failed(err, needle)

    def test_self_and_repeated_blocks_links_are_kept_once(self) -> None:
        pages = copy.deepcopy(JIRA)
        links = pages["page-1"]["issues"][1]["fields"]["issuelinks"]
        blocks = links[0]["type"]
        links += [{"id": "8", "type": blocks, "inwardIssue": {"key": "SHFT-11"}},
                  {"id": "9", "type": blocks, "inwardIssue": {"key": "SHFT-12"}}]
        transport = Transport((SEARCH, 200, pages["page-1"]), (SEARCH, 200, pages["page-2"]))
        code, out, _ = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertEqual(code, 0)
        items = {item["subject"]: item for item in json.loads(out)["items"]}
        self.assertEqual(items["SHFT-12"]["blockedBy"], ["SHFT-11"])

    def test_an_issue_listed_twice_fails(self) -> None:
        repeated = {**JIRA["page-2"], "issues": [JIRA["page-1"]["issues"][0]]}
        transport = Transport((SEARCH, 200, JIRA["page-1"]), (SEARCH, 200, repeated))
        code, _, err = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "twice")

    def test_provider_text_that_is_not_unicode_fails_on_one_line(self) -> None:
        page = copy.deepcopy(JIRA["page-2"])
        page["issues"][0]["fields"]["summary"] = "x\ud800"
        code, _, err = self.run_kind(JIRA_ARGS, Transport((SEARCH, 200, page)), JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "Unicode")

    def test_provider_values_in_a_refusal_stay_on_one_line_and_never_hold_a_credential(self) -> None:
        for value in ("weird\nline two", f"echo {TOKEN}", f"echo {SECRETS[1]}"):
            with self.subTest(value=value):
                page = copy.deepcopy(JIRA["unknown-category"])
                page["issues"][0]["fields"]["status"]["statusCategory"]["key"] = value
                code, _, err = self.run_kind(JIRA_ARGS, Transport((SEARCH, 200, page)), JIRA_ENV)
                self.assertNotEqual(code, 0)
        # A long reflected token is redacted before the line is capped.
        long_token = "L" * 192
        page = copy.deepcopy(JIRA["unknown-category"])
        page["issues"][0]["fields"]["status"]["statusCategory"]["key"] = "x" * 40 + long_token
        code, _, err = self.run_kind(JIRA_ARGS, Transport((SEARCH, 200, page)), {**JIRA_ENV, "JIRA_API_TOKEN": long_token})
        self.assertNotEqual(code, 0)
        self.assertNotIn("L" * 20, err)
        # The user's email is not a credential: a snapshot that names it still prints.
        page = copy.deepcopy(JIRA["page-2"])
        page["issues"][0]["fields"]["summary"] = f"Ask {EMAIL}"
        code, out, _ = self.run_kind(JIRA_ARGS, Transport((SEARCH, 200, page)), JIRA_ENV)
        self.assertEqual(code, 0)
        self.assertIn(EMAIL, out)
        page = copy.deepcopy(JIRA["page-2"])
        page["issues"][0]["fields"]["summary"] = f"title {TOKEN}"
        code, _, err = self.run_kind(JIRA_ARGS, Transport((SEARCH, 200, page)), JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "credential")

    def test_a_parent_that_is_not_an_issue_key_is_refused(self) -> None:
        argv = [*JIRA_ARGS]
        argv[argv.index("SHFT-10")] = "SHFT-10 OR project = HR"
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
                transport = Transport((SEARCH, status, JIRA["unauthorized"]))
                code, _, err = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
                self.assertNotEqual(code, 0)
                self.failed(err, str(status))
                self.assertEqual(len(transport.requests), 1)

    def test_rate_limited_then_success(self) -> None:
        transport = Transport((SEARCH, 429, b"", {"Retry-After": "2"}), (SEARCH, 200, JIRA["page-1"]),
                              (SEARCH, 200, JIRA["page-2"]))
        code, _, _ = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertEqual(code, 0)
        self.assertEqual(self.slept, [2.0])

    def test_server_errors_retry_twice_then_fail(self) -> None:
        transport = Transport(*[(SEARCH, 503, b"busy")] * 3)
        code, _, err = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "503")
        self.assertEqual(len(transport.requests), 3)

    def test_every_server_error_is_retried(self) -> None:
        transport = Transport((SEARCH, 520, b""), (SEARCH, 200, JIRA["page-2"]))
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
        transport = Transport((SEARCH, 200, JIRA["page-1"]), *[(SEARCH, 500, b"oops")] * 3)
        code, out, _ = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")

    def test_the_item_cap_is_a_failure(self) -> None:
        issue = JIRA["page-1"]["issues"][0]
        page = {"issues": [{**issue, "key": f"SHFT-{index}", "fields": {**issue["fields"], "issuelinks": []}}
                           for index in range(1000, 1500)], "isLast": True}
        code, _, err = self.run_kind(JIRA_ARGS, Transport((SEARCH, 200, page)), JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "500")
        page["issues"].pop()
        code, _, _ = self.run_kind(JIRA_ARGS, Transport((SEARCH, 200, page)), JIRA_ENV)
        self.assertEqual(code, 0)

    def test_the_deadline_bounds_the_whole_run(self) -> None:
        # The first page takes 30 seconds of a 20-second deadline.
        transport = Transport((SEARCH, 200, JIRA["page-1"], {}, 30), (SEARCH, 200, JIRA["page-2"]))
        code, _, err = self.run_kind([*JIRA_ARGS, "--timeout-seconds", "20"], transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "deadline")
        self.assertEqual(len(transport.requests), 1)
        # A slow last page still misses the deadline.
        transport = Transport((SEARCH, 200, JIRA["page-1"]), (SEARCH, 200, JIRA["page-2"], {}, 30))
        code, _, err = self.run_kind([*JIRA_ARGS, "--timeout-seconds", "20"], transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "deadline")
        # A Retry-After past the deadline is not waited for.
        transport = Transport((SEARCH, 429, b"", {"Retry-After": "60"}))
        code, _, err = self.run_kind([*JIRA_ARGS, "--timeout-seconds", "20"], transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.slept, [])

    def test_a_redirect_to_another_origin_is_refused(self) -> None:
        transport = Transport((SEARCH, 302, b"", {"Location": "https://evil.example/rest/api/3/search/jql"}))
        code, _, err = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "redirect")
        self.assertEqual(len(transport.requests), 1)
        for location in ("http://eshyft.atlassian.net/rest/api/3/search/jql",
                         "https://eshyft.atlassian.net:8443/rest/api/3/search/jql"):
            with self.subTest(location=location):
                transport = Transport((SEARCH, 307, b"", {"Location": location}))
                code, _, err = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
                self.assertNotEqual(code, 0)
                self.assertEqual(len(transport.requests), 1)
        transport = Transport(*[(SEARCH, 302, b"", {"Location": SEARCH + "jql=again"})] * 7)
        code, _, err = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertNotEqual(code, 0)
        self.failed(err, "redirects")
        # A redirect on the same origin is followed, with the same authorization.
        transport = Transport((SEARCH, 301, b"", {"Location": SEARCH + "jql=moved"}), (SEARCH, 200, JIRA["page-2"]))
        code, _, _ = self.run_kind(JIRA_ARGS, transport, JIRA_ENV)
        self.assertEqual(code, 0)

    def test_malformed_json_fails(self) -> None:
        for payload in (b"{not json", b"[]", b"\xff\xfe"):
            with self.subTest(payload=payload):
                code, _, err = self.run_kind(JIRA_ARGS, Transport((SEARCH, 200, payload)), JIRA_ENV)
                self.assertNotEqual(code, 0)

    def test_an_unexpected_exception_is_one_line_with_no_secret(self) -> None:
        transport = Transport((SEARCH, 200, RuntimeError(f"boom {TOKEN} {SECRETS[1]}")))
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
            "queue": [{"subject": "SHFT-11"}, {"subject": "SHFT-12"}],
            "planned": [{"subject": "SHFT-13"}],
            "tickets": {"EDD-7": {}, "SHFT-14": {}},
            "followups": [],
        }), encoding="utf-8")
        (self.state / "journal.jsonl").write_text(
            json.dumps({"at": "2026-10-20T00:00:00Z", "id": 1}) + "\n"
            + json.dumps({"at": "2026-10-21T00:00:00Z", "id": 2}) + "\n", encoding="utf-8")
        self.environ = {"BITBUCKET_TOKEN": TOKEN, "BITBUCKET_EMAIL": EMAIL}

    def args(self, *extra: str) -> list[str]:
        return ["--kind", "bitbucket", "--workspace", "eshyft", "--repo", "eshyftnew", "--binding",
                ".agents/orchestration.json", "--subject-pattern", "(SHFT|EDD)-[0-9]+", "--token-env",
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
            {"subject": "EDD-7", "title": "EDD-7: draft work", "state": "draft",
             "changeRequest": "https://bitbucket.org/eshyft/eshyftnew/pull-requests/36"},
            # No open request: the most recently updated merged one wins over a later declined one.
            {"subject": "SHFT-11", "title": "SHFT-11: shared rails", "state": "merged",
             "changeRequest": "https://bitbucket.org/eshyft/eshyftnew/pull-requests/31", "head": "a" * 40},
            # Matched by its branch; the most recently updated open one wins.
            {"subject": "SHFT-12", "title": "Line map renderer", "state": "open",
             "changeRequest": "https://bitbucket.org/eshyft/eshyftnew/pull-requests/34",
             "head": "c" * 24 + "0123456789abcdef"},
            # Superseded is closed; a link off bitbucket.org is dropped.
            {"subject": "SHFT-13", "title": "SHFT-13: superseded", "state": "closed", "head": "f" * 40},
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
        self.assertEqual([item["subject"] for item in json.loads(out)["items"]], ["SHFT-14"])

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

    def test_a_next_page_on_another_origin_is_refused(self) -> None:
        for next_link in ("https://evil.example/2.0/repositories/eshyft/eshyftnew/pullrequests",
                          "http://api.bitbucket.org/2.0/repositories/eshyft/eshyftnew/pullrequests?page=2"):
            with self.subTest(next_link=next_link):
                page = {**BITBUCKET["page-1"], "next": next_link}
                transport = Transport((API + "pullrequests?", 200, page))
                code, _, err = self.run_kind(self.args(), transport, self.environ, cwd=self.root)
                self.assertNotEqual(code, 0)
                self.assertEqual(len(transport.requests), 1)

    def test_the_title_match_comes_before_the_branch(self) -> None:
        pull = copy.deepcopy(BITBUCKET["before-since"]["values"][0])
        pull.update(title="SHFT-11: from the title", created_on="2026-10-20T12:00:00.000000+00:00")
        pull["source"]["branch"]["name"] = "SHFT-12-branch"
        transport = Transport((API + "pullrequests?", 200, {"values": [pull]}),
                              (API + "commit/121212121212", 200, BITBUCKET["commit-1"]))
        code, out, _ = self.run_kind(self.args(), transport, self.environ, cwd=self.root)
        self.assertEqual(code, 0)
        self.assertEqual([item["subject"] for item in json.loads(out)["items"]], ["SHFT-11"])

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

    def test_refusals(self) -> None:
        for label, script, needle in (
            ("unknown state", [(GRAPHQL, 200, LINEAR["unknown-state"])], "limbo"),
            ("no issue", [(GRAPHQL, 200, LINEAR["no-issue"])], "ENG-100"),
            ("refused query", [(GRAPHQL, 200, LINEAR["refused"])], "Entity not found"),
            ("no next page", [(GRAPHQL, 200, LINEAR["page-1"]), (GRAPHQL, 200, LINEAR["repeated-cursor"])], "next page"),
            ("not JSON", [(GRAPHQL, 200, b"<html>")], "JSON"),
        ):
            with self.subTest(label):
                code, _, err = self.run_kind(LINEAR_ARGS, Transport(*script), LINEAR_ENV)
                self.assertNotEqual(code, 0)
                self.failed(err, needle)

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
        # The captured command lists its items in the tracker's order; the port in the record's.
        by_subject = lambda items: sorted(items, key=lambda item: item["subject"])  # noqa: E731
        self.assertEqual({**document, "items": by_subject(document["items"])},
                         {**CAPTURED["forge"], "items": by_subject(CAPTURED["forge"]["items"])})
        listing = urllib.parse.parse_qs(urllib.parse.urlsplit(transport.requests[0][0]).query)
        self.assertEqual(listing, {"state": ["all"], "sort": ["created"], "direction": ["desc"], "per_page": ["100"]})
        # The second page reaches a pull request made before the since time, so the third is never read.
        self.assertEqual(len(transport.requests), 2)
        self.assertTrue(all(headers["Authorization"] == f"Bearer {TOKEN}" for _, headers in transport.requests))

    def test_since_includes_an_older_pull_request(self) -> None:
        transport = Transport((PULLS, 200, GITHUB["page-1"], {"Link": f'<{PULLS}page=2>; rel="next"'}),
                              (PULLS + "page=2", 200, GITHUB["page-2"]))
        code, out, _ = self.run_kind(self.args("--since", "2026-10-19T00:00:00Z"), transport, self.environ,
                                     cwd=self.root)
        self.assertEqual(code, 0)
        items = {item["subject"]: item for item in json.loads(out)["items"]}
        # An open request made before the epic now wins for its subject.
        self.assertEqual(items["ENG-101"]["changeRequest"], "https://github.com/example-org/example-repo/pull/10")

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


if __name__ == "__main__":
    unittest.main()
