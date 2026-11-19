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
              digest: str | None = None, fingerprint: str | None = None, newer: bool = False,
              second: tuple | None = None, meta_changes: dict | None = None, current_headers: dict | None = None,
              padding: int = 0) -> None:
        """Publish ``value`` as the sibling's current cut. ``newer`` answers a second read of current with a later
        pointer, and ``second`` with that answer instead; ``meta_changes`` change the receipt current serves."""
        data = value if isinstance(value, bytes) else public.encode(value)
        type(self).sequence += 1
        now_ms = int(time.time() * 1000)
        meta = {"protocolVersion": "hosted-delivery-api/1", "rendererVersion": renderer or "delivery-line-map/2",
                "epoch": 1, "sequence": self.sequence, "digest": digest or hashlib.sha256(data).hexdigest(),
                "sourceFingerprint": fingerprint or (value["sourceFingerprint"] if isinstance(value, dict) else "f" * 64),
                "publishedAt": now_ms - 1000, "sourceConfirmedAt": now_ms - 1000, "serverNow": now_ms,
                "staleAfterMs": 120000, "stale": stale, **(meta_changes or {})}
        served = json.dumps(meta).encode() + b" " * padding
        answers = [(200, current_headers or {}, served, 0)]
        if newer:
            answers.append((200, {}, json.dumps({**meta, "sequence": meta["sequence"] + 1}).encode(), 0))
        if second:
            answers.append(second)
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
        stageless = copy.deepcopy(merged)
        del stageless["document"]["state"]["queue"][0]["stage"]
        cases = {
            "digest": dict(value=data, digest="d" * 64),
            "renderer": dict(value=merged, renderer="delivery-line-map/3"),
            "fingerprint": dict(value=merged, fingerprint="e" * 64),
            "schema": dict(value=refused_schema),
            "a row with no stage": dict(value=stageless),
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

    def test_a_read_too_old_to_take_is_stale_and_the_forge_snapshot_beside_it_still_applies(self) -> None:
        """Scenario 9(b), from scenario 2's record: a read the service calls fresh, older than the record takes."""
        self.first_read()
        self.serve(body("merged"))
        old = self.read()
        old["takenAt"] = old["siblings"][0]["readAt"] = at(2000)
        forge = {"takenAt": at(), "source": "forge",
                 "items": [{"subject": "OWN-1", "state": "open", "changeRequest": "https://forge.test/pr/1"}]}
        attempted = self.sibling()["lastAttemptAt"]
        result = self.reconcile(old, forge)
        self.assertTrue(result["ok"])
        own = next(item for item in state_tool.read_state(self.record)["planned"] if item["subject"] == "OWN-1")
        self.assertEqual(own.get("changeRequest"), "https://forge.test/pr/1")
        self.assertEqual((self.stored_stage(), self.sibling()["lastOutcome"]), ("building", "stale"))
        self.assertEqual(self.journal("sibling.landed"), [])
        # Refused, it never moves the latest attempt back to when it was read.
        self.assertEqual(self.sibling()["lastAttemptAt"], attempted)

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
        def changed(change):
            broken = copy.deepcopy(snapshot)
            change(broken["siblings"][0])
            return broken

        refused = {
            "an unknown outcome": [changed(lambda entry: entry.update(outcome="maybe"))],
            "stories on an unavailable read": [changed(lambda entry: entry.update(outcome="unavailable"))],
            "a read with no stories": [changed(lambda entry: entry.pop("stories"))],
            "a read taken in the future": [changed(lambda entry: entry.update(readAt=at(-3600)))],
            "one sibling in two snapshots": [snapshot, copy.deepcopy(snapshot)],
        }
        for name, snapshots in refused.items():
            with self.subTest(case=name):
                before = ((self.record / "state.json").read_bytes(), (self.record / "journal.jsonl").read_bytes())
                with self.assertRaises(state_tool.StateError):
                    self.reconcile(*snapshots)
                self.assertEqual(((self.record / "state.json").read_bytes(), (self.record / "journal.jsonl").read_bytes()),
                                 before)

    def test_reconcile_passes_over_an_earlier_read_a_relinked_sibling_and_a_read_of_another_epic(self) -> None:
        first = self.first_read()
        # An earlier read, applied after a later attempt, changes nothing.
        self.receiver.answer(f"/d/{self.public_id}/current", (500, {}, b"{}", 0))
        self.reconcile(self.read())
        kept = self.sibling()
        self.assertEqual(kept["lastOutcome"], "unavailable")
        # Read times are whole seconds: this one is a minute before the attempt the record holds.
        first["siblings"][0]["readAt"] = at(60)
        self.reconcile(first)
        self.assertEqual(self.sibling(), kept)
        # A read that says read under another parent counts as other-epic, whatever the command called it.
        self.serve(body("merged"))
        other = self.read()
        other["siblings"][0]["trackerParent"] = "EPIC-9"
        self.reconcile(other)
        self.assertEqual((self.sibling()["lastOutcome"], self.stored_stage()), ("other-epic", "building"))
        self.assertEqual(self.journal("sibling.landed"), [])
        self.assertEqual([entry["data"] for entry in self.journal("sibling.mismatched")],
                         [{"sibling": "mobile", "parent": "EPIC-9"}])
        # A read of a link the record no longer names for that sibling is passed over.
        fresh = self.read()
        state_tool.set_sibling(self.record, name="mobile", remove=True)
        state_tool.set_sibling(self.record, name="mobile", link=f"https://views.example.com/d/{'a' * 64}")
        self.reconcile(fresh)
        self.assertNotIn("lastOutcome", self.sibling())
        self.assertNotIn("stories", self.sibling())
        self.assertEqual(self.journal("sibling.landed"), [])

    def test_an_unchanged_read_writes_nothing_and_an_outcome_change_starts_its_time(self) -> None:
        first = self.first_read()["siblings"][0]
        before = ((self.record / "state.json").read_bytes(), (self.record / "journal.jsonl").read_bytes())
        # Read again, and nothing about the sibling changed: an idle refresh leaves the record as it was.
        again = self.read()
        again["siblings"][0]["readAt"] = again["takenAt"] = at()
        result = self.reconcile(again)
        self.assertEqual((result["drift"], result["changes"]), (False, []))
        self.assertEqual(((self.record / "state.json").read_bytes(), (self.record / "journal.jsonl").read_bytes()), before)
        self.assertEqual((self.sibling()["readAt"], self.sibling()["lastAttemptAt"]), (first["readAt"], first["readAt"]))
        # read -> unavailable is a change: lastAttemptAt is when the new outcome began, and a second unavailable read
        # leaves it there.
        self.receiver.answer(f"/d/{self.public_id}/current", (500, {}, b"{}", 0))
        down = self.read()
        began = down["siblings"][0]["readAt"]
        self.reconcile(down)
        self.assertEqual((self.sibling()["lastOutcome"], self.sibling()["lastAttemptAt"], self.sibling()["readAt"]),
                         ("unavailable", began, first["readAt"]))
        version = state_tool.read_state(self.record)["version"]
        later = self.read()
        later["siblings"][0]["readAt"] = later["takenAt"] = (datetime.datetime.now(datetime.timezone.utc)
                                                             + datetime.timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.reconcile(later)
        self.assertEqual((self.sibling()["lastOutcome"], self.sibling()["lastAttemptAt"]), ("unavailable", began))
        self.assertEqual(state_tool.read_state(self.record)["version"], version)

    def test_a_later_read_the_freshness_gate_refuses_leaves_a_stale_sibling_as_it_was(self) -> None:
        """Refused as too old, but later than when the sibling's stale outcome began: nothing changes but times."""
        self.serve(body("building"))
        snapshot = self.read()

        def taken(seconds_ago: float) -> dict:
            copied = copy.deepcopy(snapshot)
            copied["siblings"][0]["readAt"] = copied["takenAt"] = at(seconds_ago)
            return copied

        # One copy, read once: a second call could stamp the next second.
        stale = taken(30)
        self.reconcile(stale, max_age=5)
        began = self.sibling()["lastAttemptAt"]
        self.assertEqual((self.sibling()["lastOutcome"], began), ("stale", stale["siblings"][0]["readAt"]))
        held = ((self.record / "state.json").read_bytes(), (self.record / "journal.jsonl").read_bytes())
        result = self.reconcile(taken(15), max_age=5)
        self.assertEqual((result["drift"], result["changes"]), (False, []))
        self.assertEqual(((self.record / "state.json").read_bytes(), (self.record / "journal.jsonl").read_bytes()), held)

    def test_a_field_kept_from_an_earlier_read_clears_when_a_later_read_lacks_it(self) -> None:
        """Round 1's deferred finding: a closing the sibling no longer carries leaves the record."""
        self.first_read()
        closing = body("building")
        closing["document"]["state"]["meta"]["closing"] = {"started": {"entry": 1, "at": "2026-11-18T10:00:00Z"}}
        public.validate(closing)
        self.serve(closing)
        self.reconcile(self.read())
        self.assertEqual(self.sibling()["closing"], {"started": {"entry": 1, "at": "2026-11-18T10:00:00Z"}})
        self.serve(body("building"))
        self.reconcile(self.read())
        self.assertNotIn("closing", self.sibling())

    def test_a_settled_story_lands_and_a_queued_stage_wins_over_a_planned_one(self) -> None:
        self.first_read()
        self.serve(body("settled"))
        snapshot = self.read()
        self.reconcile(snapshot)
        [landed] = self.journal("sibling.landed")
        self.assertEqual(landed["data"], {"sibling": "mobile", "subject": "SIB-1",
                                          "firstSeenAt": snapshot["siblings"][0]["readAt"]})
        both = body("merged")
        both["document"]["state"]["planned"] = [{"subject": "SIB-1", "title": "The sibling's story", "lane": "lane-1",
                                                 "stage": "building"}]
        public.validate(both)
        self.serve(both)
        [entry] = self.read()["siblings"]
        self.assertEqual([story["stage"] for story in entry["stories"]], ["merged"])

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
        # A later read of a cut that isn't terminal says so.
        self.serve(body("merged"))
        self.reconcile(self.read())
        self.assertEqual((self.sibling()["lastOutcome"], self.sibling()["terminal"]), ("read", False))

    def test_a_terminal_cut_whose_current_cannot_be_read_again_is_stale(self) -> None:
        self.first_read()
        self.serve(body("merged", journal=closed_journal()), stale=True, second=(500, {}, b"{}", 0))
        [entry] = self.read()["siblings"]
        self.assertEqual((entry["outcome"], entry["terminal"]), ("stale", False))

    def test_the_reader_checks_currents_receipt_and_answer_as_the_viewer_does(self) -> None:
        self.first_read()
        cases = {
            "another protocol": (dict(meta_changes={"protocolVersion": "hosted-delivery-api/2"}), "incompatible"),
            "another stale window": (dict(meta_changes={"staleAfterMs": 60000}), "incompatible"),
            "not JSON by its type": (dict(current_headers={"Content-Type": "text/html"}), "incompatible"),
            "current over its bound": (dict(padding=5000), "incompatible"),
            "confirmed too long ago": (dict(meta_changes={"sourceConfirmedAt": int(time.time() * 1000) - 130000}), "stale"),
        }
        for name, (serving, outcome) in cases.items():
            with self.subTest(case=name):
                self.serve(body("merged"), **serving)
                [entry] = self.read()["siblings"]
                self.assertEqual(entry["outcome"], outcome)
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit) as stopped:
            hosted.main(["status", "--state-dir", str(self.record)])
        self.assertEqual(stopped.exception.code, 2)

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
        # A configuration with no delivery selected has no state directory: the check doesn't apply.
        doctor.c = SimpleNamespace()
        self.assertEqual(doctor.siblings()[0], "skipped-ok")
        # The check is part of the snapshots and publishers line.
        doctor.c = SimpleNamespace(state=self.record, name="run-delivery.json", flow_raw={})
        doctor.root = self.tmp
        settings = {"publisher": "linear", "mode": "none", "transitions": {}}
        with mock.patch.object(run_delivery, "publishing_problems", side_effect=lambda *_: []), \
                mock.patch.object(run_delivery.tracker_publisher, "settings", return_value=settings):
            doctor.binding = {"state": {}}
            status, detail = doctor.publishing()
            self.assertEqual(status, "wrong")
            self.assertIn("the record names sibling deliveries (mobile: read)", detail)
            doctor.binding = {"state": member}
            status, detail = doctor.publishing()
            self.assertEqual(status, "ok")
            self.assertIn("sibling deliveries (mobile: read)", detail)

    def test_a_sibling_command_that_prints_an_old_read_leaves_the_last_one_standing(self) -> None:
        self.first_read()
        old = {"schemaVersion": "sibling-snapshot/1", "takenAt": at(2000), "siblings": []}
        member = {"siblingSnapshot": {"argv": [sys.executable, "-c", f"print({json.dumps(json.dumps(old))})"]}}
        result = state_tool.refresh_sources(self.record, member)
        self.assertEqual(result["sources"]["siblings"]["status"], "stale", result)
        self.assertIn("too old", result["sources"]["siblings"]["reason"])
        self.assertEqual(self.sibling()["lastOutcome"], "read")

    # -- scenario 12 -----------------------------------------------------------------------------------------------

    def test_the_hosted_projection_of_a_record_with_siblings_carries_none(self) -> None:
        self.serve(body("merged"))
        self.reconcile(self.read())
        document = {"state": state_tool.read_state(self.record), "journal": state_tool.read_journal(self.record)}
        self.assertTrue(document["state"]["siblings"])
        # As the state wrapper hands it to every view and publisher: with the words the views give its siblings.
        render_state = sys.modules.get("render_state_for_siblings") or _load("render_state_for_siblings",
                                                                             STATE_SCRIPTS / "render_state.py")
        document = render_state.with_across(document)
        self.assertIn("SIB-1", document["across"]["held"])
        # A tracker that links every key, so a sibling's story would be linked if anything let it through.
        tracker = {"name": "Tracker", "issueUrl": "https://tracker.test/issue/{key}"}
        value = public.project(document, {}, config(tracker=tracker), source_fingerprint="1" * 64)
        public.validate(value)
        self.assertNotIn("across", value["document"])
        self.assertNotIn("SIB-1", value["links"]["issues"])
        self.assertIn(PARENT, value["links"]["issues"], "the tracker links what the hosted page does draw")
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
