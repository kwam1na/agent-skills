"""Sibling deliveries: naming one, reading its published snapshot, and what the record keeps of the read.

Every reader case runs against loopback receivers this class starts once, on port 0, which record each request's
path and headers. The bodies are real ``hosted-delivery-snapshot/2`` projections of small records, served at the
digest of their bytes, as the hosted service serves them.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import http.server
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import agent_skills
from tests.delivery_views_fixtures import config


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = Path(agent_skills.__file__).resolve().parents[1]
STATE_SCRIPTS = PRODUCT_ROOT / "skills/orchestrate-work/scripts"
VIEWS_SCRIPTS = PRODUCT_ROOT / "skills/delivery-views/scripts"
HOSTED = VIEWS_SCRIPTS / "hosted.py"
TOKEN = "t" * 48
PARENT = "EPIC-1"


def _load(name: str, path: Path):
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(path.parent))
        sys.dont_write_bytecode = previous


state_tool = _load("delivery_state", STATE_SCRIPTS / "delivery_state.py")
hosted = _load("delivery_views_hosted_for_siblings", HOSTED)
public = hosted.public
run_delivery = _load("run_delivery_for_siblings", PRODUCT_ROOT / "skills/run-delivery/scripts/run_delivery.py")


def at(seconds_ago: float = 0) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def body(stage: str = "building", *, parent: str | None = PARENT, journal: list | None = None,
         fingerprint: str = "f" * 64) -> dict:
    """A sibling's published body: one story, SIB-1, at ``stage``, under ``parent``."""
    landed = stage in ("merged", "queued")
    item = {"subject": "SIB-1", "title": "The sibling's story", "lane": "lane-1", "stage": stage}
    state = {"planned": [] if landed else [item],
             "queue": [{**item, "head": "a" * 40, "changeRequest": "https://forge.test/pr/7"}] if landed else []}
    state["meta"] = {"headline": "Sibling", "updatedAt": "2026-11-18T10:00:00Z",
                     **({"trackerParent": parent} if parent else {})}
    document = {"state": state, "journal": journal or []}
    return public.project(document, {}, config(), source_fingerprint=fingerprint)


def closed_journal(*, reopened: bool = False) -> list:
    kinds = ["lane.assigned", "candidate.handed", "check.started", "delivery.closed"]
    if reopened:
        kinds.append("lane.assigned")
    return [{"id": index, "at": "2026-11-18T10:00:00Z", "kind": kind, "lane": None, "subject": None, "data": {}}
            for index, kind in enumerate(kinds, 1)]


class Receiver(http.server.ThreadingHTTPServer):
    """Serves fixed answers by path and records every request's path and headers."""
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.routes: dict[str, list] = {}
        self.seen: list[tuple[str, dict]] = []
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def reset(self) -> None:
        with self.lock:
            self.routes, self.seen = {}, []

    def answer(self, path: str, *answers) -> None:
        """Answer ``path`` with each of ``answers`` in turn, the last one for good: (status, headers, bytes, delay)."""
        with self.lock:
            self.routes[path] = list(answers)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        server: Receiver = self.server  # type: ignore[assignment]
        with server.lock:
            server.seen.append((self.path, {key.lower(): value for key, value in self.headers.items()}))
            answers = server.routes.get(self.path)
            answer = (answers.pop(0) if len(answers) > 1 else answers[0]) if answers else (404, {}, b"{}", 0)
        status, headers, data, delay = answer
        if delay:
            time.sleep(delay)
        try:
            self.send_response(status)
            for key, value in {"Content-Type": "application/json", **headers}.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass


class SiblingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.receiver = Receiver()
        cls.elsewhere = Receiver()
        for server in (cls.receiver, cls.elsewhere):
            cls.addClassCleanup(server.server_close)
            cls.addClassCleanup(server.shutdown)
        cls.public_id = "c" * 64
        cls.link = f"{cls.receiver.origin}/d/{cls.public_id}"
        cls.sequence = 0

    def setUp(self) -> None:
        self.receiver.reset()
        self.elsewhere.reset()
        folder = tempfile.TemporaryDirectory(prefix="siblings-")
        self.addCleanup(folder.cleanup)
        self.tmp = Path(folder.name)
        self.record = self.tmp / "record"
        state_tool.init(self.record, "Delivery", seed={"meta": {"trackerParent": PARENT},
                        "planned": [{"subject": "OWN-1", "title": "Our own story", "lane": None, "stage": "planned"}]})
        state_tool.set_sibling(self.record, name="mobile", link=self.link)

    # -- helpers ---------------------------------------------------------------------------------------------------

    def serve(self, value: dict | bytes, *, stale: bool = False, renderer: str | None = None,
              digest: str | None = None, fingerprint: str | None = None, newer: bool = False) -> None:
        """Publish ``value`` as the sibling's current cut. ``newer`` answers a second read of current with a later
        pointer."""
        data = value if isinstance(value, bytes) else public.encode(value)
        type(self).sequence += 1
        now_ms = int(time.time() * 1000)
        meta = {"protocolVersion": "hosted-delivery-api/1", "rendererVersion": renderer or "delivery-line-map/2",
                "epoch": 1, "sequence": self.sequence, "digest": digest or hashlib.sha256(data).hexdigest(),
                "sourceFingerprint": fingerprint or (value["sourceFingerprint"] if isinstance(value, dict) else "f" * 64),
                "publishedAt": now_ms - 1000, "sourceConfirmedAt": now_ms - 1000, "serverNow": now_ms,
                "staleAfterMs": 120000, "stale": stale}
        answers = [(200, {}, json.dumps(meta).encode(), 0)]
        if newer:
            answers.append((200, {}, json.dumps({**meta, "sequence": meta["sequence"] + 1}).encode(), 0))
        self.receiver.answer(f"/d/{self.public_id}/current", *answers)
        self.receiver.answer(f"/d/{self.public_id}/snapshots/{meta['digest']}", (200, {}, data, 0))

    def read(self, **options) -> dict:
        """One read of every sibling, in-process, as ``hosted.py siblings`` prints it."""
        return hosted.read_siblings(self.record, **options)

    def snapshot_file(self, snapshot: dict, name: str = "siblings.json") -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(snapshot))
        return path

    def reconcile(self, *snapshots: dict, max_age: float = 900) -> dict:
        return state_tool.reconcile(self.record, [self.snapshot_file(s, f"s{i}.json") for i, s in enumerate(snapshots)],
                                    max_age=max_age)

    def sibling(self) -> dict:
        return next(row for row in state_tool.read_state(self.record)["siblings"] if row["name"] == "mobile")

    def journal(self, kind: str) -> list[dict]:
        return [entry for entry in state_tool.read_journal(self.record) if entry["kind"] == kind]

    def stored_stage(self) -> str | None:
        return next((story["stage"] for story in self.sibling().get("stories", []) if story["subject"] == "SIB-1"), None)

    def first_read(self) -> dict:
        """Scenario 2's read: the story at building, applied."""
        self.serve(body("building"))
        snapshot = self.read()
        self.reconcile(snapshot)
        return snapshot

    # -- scenario 1 ------------------------------------------------------------------------------------------------

    def test_lead_names_and_removes_a_sibling_and_each_change_is_journaled(self) -> None:
        [added] = self.journal("sibling.added")
        self.assertEqual(added["data"], {"sibling": "mobile", "link": self.link})
        self.assertEqual([(row["name"], row["link"]) for row in state_tool.read_state(self.record)["siblings"]],
                         [("mobile", self.link)])
        state_tool.set_sibling(self.record, name="mobile", remove=True)
        [removed] = self.journal("sibling.removed")
        self.assertEqual(removed["data"], {"sibling": "mobile"})
        self.assertEqual(state_tool.read_state(self.record).get("siblings", []), [])

    def test_a_sibling_is_refused_without_a_tracker_parent_or_an_https_link_and_nothing_is_written(self) -> None:
        bare = self.tmp / "bare"
        state_tool.init(bare, "Bare")
        refusals = [(bare, "web", f"https://views.example.com/d/{'a' * 64}"),
                    (self.record, "web", f"https://views.example.com/d/{'a' * 63}"),
                    (self.record, "web", f"https://views.example.com/x/{'a' * 64}"),
                    (self.record, "web", f"views.example.com/d/{'a' * 64}"),
                    (self.record, "web", f"https://user@views.example.com/d/{'a' * 64}"),
                    (self.record, "web", f"https://views.example.com/d/{'a' * 64}?x=1"),
                    (self.record, "mobile", f"https://views.example.com/d/{'a' * 64}"),
                    (self.record, "web", self.link)]
        for directory, name, link in refusals:
            with self.subTest(link=link, name=name):
                before = ((directory / "state.json").read_bytes(), (directory / "journal.jsonl").read_bytes())
                with self.assertRaises(state_tool.StateError):
                    state_tool.set_sibling(directory, name=name, link=link)
                self.assertEqual(((directory / "state.json").read_bytes(), (directory / "journal.jsonl").read_bytes()),
                                 before)
        with self.assertRaises(state_tool.StateError):
            state_tool.set_sibling(self.record, name="web", remove=True)
        state_tool.set_sibling(self.record, name="web", link=f"https://views.example.com/d/{'a' * 64}")
        self.assertEqual(len(self.journal("sibling.added")), 2)

    def test_lead_sibling_is_the_lead_command_that_names_and_removes_one(self) -> None:
        with mock.patch.object(run_delivery, "lead_sibling") as command, \
                mock.patch.object(run_delivery, "receive_hosted_credentials"), \
                mock.patch.object(run_delivery, "quarantine_hosted_checkout"), \
                mock.patch.object(run_delivery, "cfg", side_effect=run_delivery.Stop("no configuration here")):
            # The status lines a lead command prints after it read the configuration, which this case has none of.
            run_delivery.main(["lead-sibling", "add", "--link", self.link, "--name", "web"])
            run_delivery.main(["lead-sibling", "remove", "--name", "web"])
        self.assertEqual([(call.args[0].action, call.args[0].name, call.args[0].link) for call in command.call_args_list],
                         [("add", "web", self.link), ("remove", "web", None)])
        fake = SimpleNamespace(release=PRODUCT_ROOT, state=self.record)
        with mock.patch.object(run_delivery, "cfg", return_value=fake), \
                mock.patch("builtins.print") as printed:
            run_delivery.lead_sibling(SimpleNamespace(action="add", link=f"https://views.example.com/d/{'b' * 64}",
                                                      name="web"))
            self.assertIn("web", " ".join(str(call.args[0]) for call in printed.call_args_list))
            # Plain http is refused but from a loopback receiver, before the record is read.
            for link in ("http://views.example.com/d/" + "b" * 64, "http://localhost:8080/d/" + "b" * 64,
                         "ftp://views.example.com/d/" + "b" * 64):
                with self.subTest(link=link), self.assertRaises(run_delivery.Stop):
                    run_delivery.lead_sibling(SimpleNamespace(action="add", link=link, name="api"))
            with self.assertRaises(run_delivery.Stop):
                run_delivery.lead_sibling(SimpleNamespace(action="add", link=self.link, name="mobile"))
            run_delivery.lead_sibling(SimpleNamespace(action="remove", link=None, name="web"))
        self.assertEqual([row["name"] for row in state_tool.read_state(self.record)["siblings"]], ["mobile"])

    # -- scenarios 2 and 3 ----------------------------------------------------------------------------------------

    def test_a_valid_read_is_applied_and_its_requests_carry_no_credential(self) -> None:
        self.serve(body("building"))
        environment = {**os.environ, "FIXTURE_PUBLISH_TOKEN": TOKEN, "HTTP_COOKIE": "session=1",
                       "PYTHONDONTWRITEBYTECODE": "1"}
        done = subprocess.run([sys.executable, "-B", str(HOSTED), "siblings", "--state-dir", str(self.record)],
                              env=environment, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        snapshot = json.loads(done.stdout)
        self.assertEqual(snapshot["schemaVersion"], "sibling-snapshot/1")
        [entry] = snapshot["siblings"]
        self.assertEqual((entry["name"], entry["link"], entry["outcome"], entry["trackerParent"]),
                         ("mobile", self.link, "read", PARENT))
        self.assertEqual(entry["stories"], [{"subject": "SIB-1", "title": "The sibling's story", "stage": "building"}])
        self.assertEqual([path for path, _ in self.receiver.seen],
                         [f"/d/{self.public_id}/current", f"/d/{self.public_id}/snapshots/" +
                          hashlib.sha256(public.encode(body("building"))).hexdigest()])
        for path, headers in self.receiver.seen:
            self.assertNotIn("authorization", headers, path)
            self.assertNotIn("cookie", headers, path)
            self.assertNotIn(TOKEN, json.dumps(headers), path)
        result = self.reconcile(snapshot)
        self.assertTrue(result["ok"])
        sibling = self.sibling()
        self.assertEqual((self.stored_stage(), sibling["readAt"], sibling["lastOutcome"], sibling["lastAttemptAt"]),
                         ("building", entry["readAt"], "read", entry["readAt"]))
        self.assertEqual(self.journal("sibling.landed"), [])

    # -- scenario 4 ------------------------------------------------------------------------------------------------

    def test_a_redirect_reads_as_unavailable_and_is_never_followed(self) -> None:
        self.receiver.answer(f"/d/{self.public_id}/current",
                             (302, {"Location": f"{self.elsewhere.origin}/d/{self.public_id}/current"}, b"{}", 0))
        [entry] = self.read()["siblings"]
        self.assertEqual(entry["outcome"], "unavailable")
        self.assertNotIn("stories", entry)
        self.assertEqual(self.elsewhere.seen, [])

    # -- scenarios 5 and 6 -----------------------------------------------------------------------------------------

    def test_a_body_that_fails_any_check_is_incompatible_and_changes_no_story(self) -> None:
        self.first_read()
        merged = body("merged")
        data = public.encode(merged)
        refused_schema = copy.deepcopy(merged)
        refused_schema["unexpected"] = True
        cases = {
            "digest": dict(value=data, digest="d" * 64),
            "renderer": dict(value=merged, renderer="delivery-line-map/3"),
            "fingerprint": dict(value=merged, fingerprint="e" * 64),
            "schema": dict(value=refused_schema),
        }
        for name, case in cases.items():
            with self.subTest(case=name):
                if name == "digest":
                    self.serve(case["value"], digest=case["digest"])
                    self.receiver.answer(f"/d/{self.public_id}/snapshots/{'d' * 64}", (200, {}, data, 0))
                else:
                    self.serve(case["value"], **{k: v for k, v in case.items() if k != "value"})
                snapshot = self.read()
                [entry] = snapshot["siblings"]
                self.assertEqual(entry["outcome"], "incompatible")
                self.assertNotIn("stories", entry)
                self.reconcile(snapshot)
                self.assertEqual(self.stored_stage(), "building")
                self.assertEqual(self.sibling()["lastOutcome"], "incompatible")
                self.assertEqual(self.journal("sibling.landed"), [])

    # -- scenario 7 ------------------------------------------------------------------------------------------------

    def test_a_timeout_or_a_server_error_reads_as_unavailable_and_keeps_the_last_read(self) -> None:
        first = self.first_read()["siblings"][0]
        for name, answer in {"500": (500, {}, b'{"error":"temporarily_unavailable"}', 0),
                             "timeout": (200, {}, b"{}", 1.5)}.items():
            with self.subTest(case=name):
                self.receiver.answer(f"/d/{self.public_id}/current", answer)
                snapshot = self.read(timeout=0.5)
                self.assertEqual(snapshot["siblings"][0]["outcome"], "unavailable")
                self.reconcile(snapshot)
                sibling = self.sibling()
                self.assertEqual((self.stored_stage(), sibling["lastOutcome"], sibling["readAt"]),
                                 ("building", "unavailable", first["readAt"]))

    # -- scenario 8 ------------------------------------------------------------------------------------------------

    def test_a_sibling_of_another_epic_contributes_nothing_and_is_journaled_once_per_parent(self) -> None:
        self.serve(body("merged", parent="EPIC-9"))
        for _ in range(2):
            snapshot = self.read()
            self.assertEqual(snapshot["siblings"][0]["outcome"], "other-epic")
            self.assertNotIn("stories", snapshot["siblings"][0])
            self.reconcile(snapshot)
        self.assertNotIn("stories", self.sibling())
        self.assertEqual(self.sibling()["lastOutcome"], "other-epic")
        self.assertEqual(self.journal("sibling.landed"), [])
        self.assertEqual([entry["data"] for entry in self.journal("sibling.mismatched")],
                         [{"sibling": "mobile", "parent": "EPIC-9"}])
        self.serve(body("merged", parent="EPIC-8"))
        self.reconcile(self.read())
        self.reconcile(self.read())
        self.assertEqual([entry["data"]["parent"] for entry in self.journal("sibling.mismatched")], ["EPIC-9", "EPIC-8"])

    # -- scenario 9 ------------------------------------------------------------------------------------------------

    def test_a_stale_read_frees_nothing_either_way_and_a_fresh_one_lands_the_story_once(self) -> None:
        self.first_read()
        # (a) The service says stale, and the body is not terminal.
        self.serve(body("merged"), stale=True)
        snapshot = self.read()
        self.assertEqual((snapshot["siblings"][0]["outcome"], snapshot["siblings"][0].get("terminal")), ("stale", False))
        self.reconcile(snapshot)
        self.assertEqual((self.stored_stage(), self.sibling()["lastOutcome"]), ("building", "stale"))
        self.assertEqual(self.journal("sibling.landed"), [])
        # (b) A read the service calls fresh, but older than the record takes, beside a fresh forge snapshot.
        self.serve(body("merged"))
        old = self.read()
        old["takenAt"] = old["siblings"][0]["readAt"] = at(2000)
        forge = {"takenAt": at(), "source": "forge",
                 "items": [{"subject": "OWN-1", "state": "open", "changeRequest": "https://forge.test/pr/1"}]}
        result = self.reconcile(old, forge)
        self.assertTrue(result["ok"])
        own = next(item for item in state_tool.read_state(self.record)["planned"] if item["subject"] == "OWN-1")
        self.assertEqual(own.get("changeRequest"), "https://forge.test/pr/1")
        self.assertEqual((self.stored_stage(), self.sibling()["lastOutcome"]), ("building", "stale"))
        self.assertEqual(self.journal("sibling.landed"), [])
        # A fresh read lands it, once.
        fresh = self.read()
        self.reconcile(fresh)
        [landed] = self.journal("sibling.landed")
        self.assertEqual(landed["data"], {"sibling": "mobile", "subject": "SIB-1",
                                          "firstSeenAt": fresh["siblings"][0]["readAt"]})
        self.assertEqual((self.stored_stage(), self.sibling()["lastOutcome"]), ("merged", "read"))
        self.reconcile(self.read())
        self.assertEqual(len(self.journal("sibling.landed")), 1)

    def test_a_malformed_sibling_snapshot_is_refused_whole(self) -> None:
        snapshot = self.first_read()
        broken = copy.deepcopy(snapshot)
        broken["siblings"][0]["outcome"] = "maybe"
        before = (self.record / "state.json").read_bytes()
        with self.assertRaises(state_tool.StateError):
            self.reconcile(broken)
        self.assertEqual((self.record / "state.json").read_bytes(), before)

    # -- scenario 10 -----------------------------------------------------------------------------------------------

    def test_a_terminal_cut_read_stale_is_read_unless_it_reopened_or_current_moved_on(self) -> None:
        self.first_read()
        cases = [("closed", dict(journal=closed_journal()), {}, ("read", True)),
                 ("reopened", dict(journal=closed_journal(reopened=True)), {}, ("stale", False)),
                 ("newer", dict(journal=closed_journal()), {"newer": True}, ("stale", False))]
        for name, shape, serving, expected in cases:
            with self.subTest(case=name):
                self.serve(body("merged", **shape), stale=True, **serving)
                [entry] = self.read()["siblings"]
                self.assertEqual((entry["outcome"], entry["terminal"]), expected)
        self.serve(body("merged", journal=closed_journal()), stale=True)
        snapshot = self.read()
        self.reconcile(snapshot)
        self.assertEqual((self.stored_stage(), self.sibling()["lastOutcome"], self.sibling()["terminal"]),
                         ("merged", "read", True))
        self.assertEqual(len(self.journal("sibling.landed")), 1)

    # -- scenario 11 -----------------------------------------------------------------------------------------------

    def test_serves_refresh_runs_the_sibling_command_and_doctor_names_one_missing(self) -> None:
        self.serve(body("merged"))
        member = {"siblingSnapshot": {"argv": [sys.executable, "-B", str(HOSTED), "siblings",
                                               "--state-dir", "{stateDirectory}"], "timeoutSeconds": 60}}
        result = state_tool.refresh_sources(self.record, member)
        self.assertEqual(result["sources"]["siblings"]["status"], "observed", result)
        self.assertEqual((self.stored_stage(), self.sibling()["lastOutcome"]), ("merged", "read"))
        self.assertEqual(len(self.journal("sibling.landed")), 1)
        self.assertEqual(state_tool.read_state(self.record)["meta"]["sources"]["siblings"]["status"], "observed")

        doctor = run_delivery.Doctor.__new__(run_delivery.Doctor)
        doctor.c = SimpleNamespace(state=self.record)
        doctor.binding = {"state": {}}
        status, detail = doctor.siblings()
        self.assertEqual(status, "wrong")
        self.assertIn("siblingSnapshot", detail)
        self.assertIn("mobile", detail)
        doctor.binding = {"state": member}
        status, detail = doctor.siblings()
        self.assertEqual(status, "ok")
        self.assertIn("mobile: read", detail)

    # -- scenario 12 -----------------------------------------------------------------------------------------------

    def test_the_hosted_projection_of_a_record_with_siblings_carries_none(self) -> None:
        self.serve(body("merged"))
        self.reconcile(self.read())
        document = {"state": state_tool.read_state(self.record), "journal": state_tool.read_journal(self.record)}
        self.assertTrue(document["state"]["siblings"])
        value = public.project(document, {}, config(), source_fingerprint="1" * 64)
        public.validate(value)
        self.assertEqual(value["schemaVersion"], "hosted-delivery-snapshot/2")
        self.assertNotIn("siblings", value["document"]["state"])
        text = json.dumps(value)
        for private in (self.link, self.public_id, "SIB-1"):
            self.assertNotIn(private, text)
        kinds = [entry["kind"] for entry in value["document"]["journal"]]
        self.assertIn("sibling.landed", kinds)
        for entry in value["document"]["journal"]:
            self.assertNotIn("data", entry)


if __name__ == "__main__":
    unittest.main()
