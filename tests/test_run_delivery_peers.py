"""Deliveries on one base: the registry beside the git common directory, the peers `doctor --delivery` and the live
view list, the files a delivery claims, and the `peer.merged` entry a peer's merge journals (Decisions 27 to 30).

Each case is one throwaway repository with a local bare remote. Its first delivery, FX-1, is led from the main
checkout; a second, FX-9, on the same base, is led from a linked worktree of the same repository with its own record,
so both name one registry and one merge slot. A process a case starts, a supervisor or a slot holder, is stopped by
the pid it recorded.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

# The skill's modules another test module already loaded under their plain names, which its lazy imports read: this
# module's imports load them again, so they go back as they were once it has.
KEPT = {name: sys.modules[name] for name in ("delivery_state", "render_state", "run_delivery", "delivery_views_render")
        if name in sys.modules}
from tests.test_run_delivery import POSIX_ONLY, Fixture, commit, doctor_lines, installed_release, sh, stop_supervisors
from tests.test_run_delivery_supervisor import hand_off, running, supervised

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "run-delivery" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "orchestrate-work" / "scripts"))
sys.modules.update(KEPT)
import delivery_state  # noqa: E402
import run_delivery as flow  # noqa: E402
import tracker_publisher  # noqa: E402

# The fixture plan, with each story naming the file its lane changes.
NAMED = {"### S1 (FX-2). Thing one (AC1). Regex `/api/one$`\n": "\nIt changes `src/one.txt`.\n",
         "### S2 (FX-3). Thing two (AC2)\n": "\nIt changes `src/two.txt`.\n"}

# Holds a base's merge slot as a delivery's check would, until it is stopped by its pid.
SLOT_HOLDER = r'''
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import json, delivery_state
doing = json.loads(sys.argv[5]) if len(sys.argv) > 5 else None
with delivery_state.merge_slot(Path(sys.argv[2]), sys.argv[3], delivery=sys.argv[4], timeout=5, doing=doing):
    print("holding", flush=True)
    time.sleep(600)
'''

# One supervisor pass's peers step, run directly after the base's remote-tracking ref is set back to an earlier commit,
# as a long-running supervisor that no step of its pass fetched for would find it.
PEER_STEP = r'''
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import run_delivery as flow
flow.CFG = c = flow.Config(Path.cwd(), sys.argv[2])
subprocess.run(["git", "update-ref", f"refs/remotes/{c.remote}/{c.base_branch}", sys.argv[3]], check=True)
print(json.dumps(flow.Supervisor(c, True).peer_merges({})))
'''

# Appends one journal entry as the merge queue writes it.
RUNNER_ENTRY = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import delivery_state
delivery_state.append_journal(Path(sys.argv[2]), kind=sys.argv[3], data=json.loads(sys.argv[4]), lane=None,
                              subject=sys.argv[5], writer="runner")
'''


def sentence_entry(number: int, kind: str, words: str, subject: str) -> dict:
    """An entry as a writer journaled it before it wrote facts: only a sentence, which the journal now refuses."""
    at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return {"at": at, "id": number, "kind": kind, "lane": None, "subject": subject, "summary": words}


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_until(condition, what: str, seconds: float = 60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class PeerDeliveryTests(unittest.TestCase):
    """Two deliveries on one fixture base, each led from its own checkout of one repository."""

    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    # -- the fixture

    def fixture(self) -> Fixture:
        """FX-1 on feature/FX-1, led from the main checkout, with each story naming its file; not initialized."""
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        self.addCleanup(stop_supervisors, fx.tmp)
        plan = fx.main / "docs/plan.md"
        text = plan.read_text()
        for heading, files in NAMED.items():
            text = text.replace(heading, heading + files)
        plan.write_text(text)
        commit(fx.main, "FX-1: name each story's file")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        supervised(fx)
        return fx

    def peer(self, fx: Fixture, key: str = "FX-9", base: str = "feature/FX-1") -> Path:
        """A second delivery on `base`, its file on the base and its lead checkout a linked worktree of the
        repository, on lead/<key>, with a record of its own. Returns the checkout; not initialized."""
        m = fx.main
        sh("git", "pull", "-q", "--ff-only", "origin", "feature/FX-1", cwd=m)  # past any peer's merge
        d = json.loads((m / ".agents/deliveries/FX-1.json").read_text())
        stories = {f"{key}{k.split('-')[1]}": story for k, story in d["stories"].items()}
        # Its own plan: FX-1's, with each story's key its own.
        plan = (m / d["plan"]).read_text().replace("(FX-2)", f"({key}2)").replace("(FX-3)", f"({key}3)")
        (m / f"docs/plan-{key}.md").write_text(plan)
        d.update(delivery=key, stories=stories, plan=f"docs/plan-{key}.md", stateDirectory=str(fx.tmp / f"record-{key}"))
        if base != "feature/FX-1":
            # Another base the binding serves.
            d["baseBranch"] = base
            binding = json.loads((m / ".agents/orchestration.json").read_text())
            binding["bases"] = [*binding.get("bases", []), base]
            (m / ".agents/orchestration.json").write_text(json.dumps(binding, indent=2))
        (m / f".agents/deliveries/{key}.json").write_text(json.dumps(d, indent=2))
        commit(m, f"{key}: a second delivery")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        lead = fx.tmp / f"lead-{key}"
        sh("git", "worktree", "add", "-q", "-b", f"lead/{key}", lead, "origin/feature/FX-1", cwd=m)
        return lead

    def flow(self, fx: Fixture, key: str, *argv, cwd: Path, env: dict | None = None) -> str:
        code, out = fx.flow("--delivery", key, *argv, cwd=cwd, env=env)
        self.assertEqual(code, 0, out)
        return out

    def doctor(self, fx: Fixture, key: str, cwd: Path) -> dict[str, tuple[str, str]]:
        _, out = fx.flow("--delivery", key, "doctor", "--delivery", key, "--offline", cwd=cwd)
        return doctor_lines(out)

    def registry(self, fx: Fixture) -> Path:
        return (fx.origin.parent / "main/.git/agent-skills/deliveries").resolve()

    def entry(self, fx: Fixture, key: str) -> dict:
        return json.loads((self.registry(fx) / f"{key}.json").read_text())

    def record(self, fx: Fixture, folder: Path) -> dict:
        return json.loads(sh(sys.executable, "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py",
                             "--dir", folder, "show").stdout)

    def state(self, fx: Fixture, folder: Path, *argv) -> None:
        sh(sys.executable, "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py", "--dir", folder,
           *argv, cwd=fx.main)

    def patch(self, fx: Fixture, folder: Path, writer: str, *argv) -> None:
        """A state tool patch by `writer`, pinned to the record's current version."""
        version = self.record(fx, folder)["state"]["version"]
        self.state(fx, folder, "patch", "--writer", writer, "--version", str(version), *argv)

    def queued(self, fx: Fixture, folder: Path, key: str, lane: str, stage: str = "queued") -> None:
        """`key` in the record's queue, added as the lead adds a candidate and moved to `stage` as the runner moves it."""
        item = {"order": 1, "subject": key, "title": key, "lane": lane, "stage": "queued", "head": "a" * 40,
                "syncedHead": None, "leadInputs": [], "reportedRounds": None, "runId": None, "checks": []}
        state = self.record(fx, folder)["state"]
        queue, planned = state["queue"], [entry for entry in state["planned"] if entry["subject"] != key]
        self.patch(fx, folder, "lead", "--patch", json.dumps({"planned": planned,
                                                              "queue": [*queue, {**item, "order": len(queue) + 1}]}))
        if stage != "queued":
            self.patch(fx, folder, "runner", "--element", f"queue[subject={key}]", "--set", json.dumps({"/stage": stage}))

    def dead_pid(self) -> int:
        """The pid of a process this case started and stopped, and waited for."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        return proc.pid

    def hold_slot(self, fx: Fixture, delivery: str, doing: dict | None = None) -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-B", "-c", SLOT_HOLDER, str(fx.release / "skills/orchestrate-work/scripts"),
                                 str(fx.main), "feature/FX-1", delivery, *([json.dumps(doing)] if doing else [])],
                                stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait(), proc.stdout.close()))
        self.assertEqual(proc.stdout.readline().strip(), "holding")
        return proc

    def merged_on_base(self, fx: Fixture, peer: Path, key: str, story: str, path: str, text: str) -> str:
        """`story` of the peer `key` merged as its queue merges one: a commit on the base changing `path`, and the
        runner's candidate.merged entry in the peer's journal naming the head it merged at."""
        (peer / path).parent.mkdir(parents=True, exist_ok=True)
        sh("git", "fetch", "-q", "origin", cwd=peer)
        sh("git", "checkout", "-q", "--detach", "origin/feature/FX-1", cwd=peer)
        (peer / path).write_text(text)
        commit(peer, f"{story}: {path}")
        head = sh("git", "rev-parse", "HEAD", cwd=peer).stdout.strip()
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-1", cwd=peer)
        sh("git", "checkout", "-q", f"lead/{key}", cwd=peer)
        sh(sys.executable, "-B", "-c", RUNNER_ENTRY, fx.release / "skills/orchestrate-work/scripts",
           fx.tmp / f"record-{key}", "candidate.merged", json.dumps({"head": head}), story)
        return head

    def peer_merged(self, fx: Fixture) -> list[dict]:
        return [e for e in self.record(fx, fx.tmp / "record")["journal"] if e["kind"] == "peer.merged"]

    # -- the cases

    def test_deliveries_on_one_base_list_each_other_and_one_alone_lists_none(self) -> None:
        fx = self.fixture()
        m = fx.main
        self.flow(fx, "FX-1", "lead-init", "--no-supervisor", cwd=m)
        # Alone on its base: no peers, the slot free, and an assignment that warns of no other delivery.
        rows = self.doctor(fx, "FX-1", m)
        self.assertEqual(rows["deliveries on this base"], ("ok", "no other delivery is registered on feature/FX-1"))
        self.assertEqual(rows["merge slot"], ("ok", "feature/FX-1's merge slot is free"))
        out = self.flow(fx, "FX-1", "lead-assign", "FX-2", "--lane", "lane-1", "--title", "one", cwd=m)
        self.assertNotIn("another delivery", out)
        self.assertNotIn("claims", out)
        # A second delivery, from a linked worktree, with no supervisor.
        lead = self.peer(fx)
        self.flow(fx, "FX-9", "lead-init", "--no-supervisor", cwd=lead)
        self.assertEqual(self.entry(fx, "FX-9"), {"key": "FX-9", "base": "feature/FX-1", "lead": str(lead.resolve()),
                                                  "state": str((fx.tmp / "record-FX-9").resolve()), "pid": None})
        # Its queue checking one story with another queued, while its check holds the base's merge slot.
        self.patch(fx, fx.tmp / "record-FX-9", "runner", "--set", json.dumps({"/meta/checking/now": {
            "subject": "FX-92", "title": "Thing one", "startedAt": "2026-11-15T10:00:00Z", "steps": []}}))
        self.queued(fx, fx.tmp / "record-FX-9", "FX-93", "lane-2")
        holder = self.hold_slot(fx, "FX-9")
        rows = self.doctor(fx, "FX-1", m)
        status, detail = rows["deliveries on this base"]
        self.assertEqual(status, "ok", detail)
        self.assertEqual(detail, "FX-9 (no supervisor; led from " + str(lead.resolve()) + "): checking FX-92; "
                                 "queued FX-93")
        status, detail = rows["merge slot"]
        self.assertEqual(status, "ok", detail)
        self.assertRegex(detail, rf"^feature/FX-1's merge slot is held by FX-9 \(pid {holder.pid}, since 20\d\d-")
        # From a subfolder of the peer's worktree, which resolves the same registry, it lists FX-1 in turn.
        (lead / "src").mkdir(exist_ok=True)
        rows = self.doctor(fx, "FX-9", lead / "src")
        self.assertEqual(rows["deliveries on this base"][1],
                         f"FX-1 (no supervisor; led from {m.resolve()}): checking nothing; queued nothing")
        self.assertEqual(sorted(p.name for p in self.registry(fx).iterdir()), ["FX-1.json", "FX-9.json"])
        # The built-in live view lists the peer and the slot's holder too.
        page = self.live_page(fx, fx.tmp / "record", m)
        self.assertIn("Deliveries on feature/FX-1", page)
        self.assertIn("FX-9 (no supervisor", page)
        self.assertIn("checking FX-92; queued FX-93", page)
        self.assertIn(f"held by FX-9 (pid {holder.pid}, since", page)
        holder.kill()
        holder.wait()
        # A registry entry that can't be read is shown as unreadable and stops nothing; one on another base isn't
        # listed at all.
        (self.registry(fx) / "FX-5.json").write_text("{not json")
        (self.registry(fx) / "FX-7.json").write_text(json.dumps({"key": "FX-7", "base": "main", "lead": str(m),
                                                                 "state": str(fx.tmp / "record-FX-7"), "pid": None}))
        rows = self.doctor(fx, "FX-1", m)
        self.assertEqual(rows["deliveries on this base"][0], "ok")
        self.assertIn("FX-5.json: unreadable", rows["deliveries on this base"][1])
        self.assertNotIn("FX-7", rows["deliveries on this base"][1])
        self.assertEqual(rows["merge slot"], ("ok", "feature/FX-1's merge slot is free"))
        out = self.flow(fx, "FX-1", "lead-assign", "FX-3", "--lane", "lane-2", "--title", "two", cwd=m)
        self.assertIn("FX-5.json, a delivery registered beside this repository, is unreadable", out)

    def live_page(self, fx: Fixture, record: Path, lead: Path) -> str:
        script = fx.release / "skills/orchestrate-work/scripts/render_state.py"
        proc = subprocess.Popen([sys.executable, "-B", str(script), "live", "--dir", str(record), "--port", "0",
                                 "--repo", str(lead)], cwd=lead, stdout=subprocess.PIPE, text=True,
                                start_new_session=True)
        try:
            said = json.loads(proc.stdout.readline())
            self.assertTrue(said.get("ok"), said)
            with urllib.request.urlopen(f"http://{said['live']}/", timeout=10) as page:
                return page.read().decode("utf-8")
        finally:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

    def test_a_claim_warns_a_peers_assign_and_accept_of_a_claimed_file_and_refuses_nothing(self) -> None:
        fx = self.fixture()
        m = fx.main
        self.flow(fx, "FX-1", "lead-init", "--no-supervisor", cwd=m)
        lead = self.peer(fx)
        self.flow(fx, "FX-9", "lead-init", "--no-supervisor", cwd=lead)
        # Claims are of whole files, and the delivery's: a region, a line range, a lane, a path outside the repository
        # and a second claim of the same pattern are each refused, and nothing is recorded.
        whole = "a claim is of whole files"
        for argv, said in ((("--claim", "src/one.txt", "--start", "1", "--end", "3"), whole),
                           (("--claim", "src/one.txt:1-3"), whole), (("--claim", "src/one.txt#L1-L3"), whole),
                           (("--claim", "src/one*", "--lane", "lane-1"), "--claim takes neither --lane nor --from"),
                           (("--claim", "/abs/one.txt"), "name a repository-relative file or pattern"),
                           (("--claim", "../one.txt"), "name a repository-relative file or pattern")):
            code, out = fx.flow("--delivery", "FX-9", "lead-own", *argv, cwd=lead)
            self.assertEqual(code, 2, out)
            self.assertIn(said, out)
        self.assertEqual(self.record(fx, fx.tmp / "record-FX-9")["state"]["extensions"], {})
        out = self.flow(fx, "FX-9", "lead-own", "--claim", "src/one*", cwd=lead)
        self.assertIn("FX-9 claims src/one*", out)
        code, out = fx.flow("--delivery", "FX-9", "lead-own", "--claim", "src/one*", cwd=lead)
        self.assertEqual(code, 2, out)
        self.assertIn("FX-9 already claims src/one*", out)
        self.assertEqual(self.record(fx, fx.tmp / "record-FX-9")["state"]["extensions"]["claims"], ["src/one*"])
        [claimed] = [e for e in self.record(fx, fx.tmp / "record-FX-9")["journal"] if e["kind"] == "ownership.claimed"]
        self.assertEqual(claimed["data"], {"path": "src/one*", "base": {
            "ref": "origin/feature/FX-1", "head": sh("git", "rev-parse", "origin/feature/FX-1", cwd=lead).stdout.strip()}})
        self.assertNotIn("because", claimed)
        # FX-9's lane-1 holds src/one.txt through a region rather than a whole-file claim, and its FX-92 is queued.
        record9 = fx.tmp / "record-FX-9"
        self.patch(fx, record9, "lead", "--patch", json.dumps({"laneOwnership": {"lane-1": [], "lane-2": ["src/two*"]}}))
        self.patch(fx, record9, "lead", "--patch", json.dumps({"laneRegions": [
            {"lane": "lane-1", "path": "src/one.txt", "baseHead": "a" * 40, "start": 1, "end": 2}]}))
        self.queued(fx, record9, "FX-92", "lane-1")
        # FX-2 names the claimed file: its assignment warns, naming the claim, FX-9's stories on the file and their
        # stages, and still assigns.
        out = self.flow(fx, "FX-1", "lead-assign", "FX-2", "--lane", "lane-1", "--title", "one", cwd=m)
        self.assertIn("Files another delivery on feature/FX-1 claims (a warning; nothing is refused):", out)
        self.assertIn("src/one.txt is claimed by FX-9 (src/one*; FX-9 has no supervisor); its stories on it: "
                      "FX-92 (queued)", out)
        assigned = [e for e in self.record(fx, fx.tmp / "record")["journal"] if e["kind"] == "lane.assigned"]
        self.assertEqual([e["subject"] for e in assigned], ["FX-2"])
        # FX-3 names only src/two.txt, which nobody claims: no warning.
        out = self.flow(fx, "FX-1", "lead-assign", "FX-3", "--lane", "lane-2", "--title", "two", cwd=m)
        self.assertNotIn("claims", out)
        # The candidate changes the claimed file and another its story doesn't name: its acceptance warns of both, by
        # what it changes, and accepts.
        hand_off(fx, "FX-2", "lane-1", assign=False, files={"src/one-extra.txt": "extra\n"})
        code, out = fx.flow("--delivery", "FX-1", "lead-accept", "FX-2", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertIn("src/one.txt is claimed by FX-9 (src/one*; FX-9 has no supervisor); its stories on it: "
                      "FX-92 (queued)", out)
        self.assertIn("src/one-extra.txt is claimed by FX-9 (src/one*; FX-9 has no supervisor); its stories on it: "
                      "none", out)
        self.assertIn("accepted FX-2", out)
        # A peer whose supervisor is gone still warns, and says it is stale.
        entry = self.entry(fx, "FX-9")
        dead = self.dead_pid()
        (self.registry(fx) / "FX-9.json").write_text(json.dumps({**entry, "pid": dead}))
        out = self.flow(fx, "FX-1", "lead-assign", "FX-2", "--lane", "lane-1", "--title", "one", cwd=m)
        self.assertIn(f"src/one.txt is claimed by FX-9 (src/one*; FX-9 is stale: its supervisor, pid {dead}, isn't "
                      "running); its stories on it: FX-92 (queued)", out)

    def test_a_peers_supervisor_is_listed_live_then_stale_then_live_again_and_close_removes_its_entry(self) -> None:
        fx = self.fixture()
        m = fx.main
        # Initialized with no supervisor, it is listed as having none.
        self.flow(fx, "FX-1", "lead-init", "--no-supervisor", cwd=m)
        lead = self.peer(fx)
        self.flow(fx, "FX-9", "lead-init", "--no-supervisor", cwd=lead)
        self.assertIn("FX-1 (no supervisor;", self.doctor(fx, "FX-9", lead)["deliveries on this base"][1])
        # One initialized before the registry existed is not listed until its supervisor starts.
        (self.registry(fx) / "FX-1.json").unlink()
        self.assertEqual(self.doctor(fx, "FX-9", lead)["deliveries on this base"][1],
                         "no other delivery is registered on feature/FX-1")
        self.flow(fx, "FX-1", "lead-run", "--resume", cwd=m, env=running(fx))
        first = wait_until(lambda: self.entry(fx, "FX-1").get("pid"), "FX-1's supervisor to register")
        self.assertTrue(pid_alive(first))
        self.assertIn(f"FX-1 (live, pid {first}; led from", self.doctor(fx, "FX-9", lead)["deliveries on this base"][1])
        # The live view the supervisor started lists its peer, with nothing but what the supervisor gave it.
        address = json.loads((fx.tmp / "record/live.json").read_text())["address"]
        with urllib.request.urlopen(address, timeout=10) as served:
            page = served.read().decode("utf-8")
        self.assertIn("Deliveries on feature/FX-1", page)
        self.assertIn(f"FX-9 (no supervisor; led from {lead.resolve()}): checking nothing; queued nothing", page)
        # Stopped by its pid, it is stale.
        os.kill(first, signal.SIGTERM)
        wait_until(lambda: not pid_alive(first), "FX-1's supervisor to stop")
        self.assertIn(f"FX-1 (stale: its supervisor, pid {first}, isn't running; led from",
                      self.doctor(fx, "FX-9", lead)["deliveries on this base"][1])
        # Resumed, it is live again with its new pid.
        self.flow(fx, "FX-1", "lead-run", "--resume", cwd=m, env=running(fx))
        second = wait_until(lambda: (pid := self.entry(fx, "FX-1").get("pid")) != first and pid, "a new supervisor")
        self.assertTrue(pid_alive(second))
        self.assertIn(f"FX-1 (live, pid {second}; led from", self.doctor(fx, "FX-9", lead)["deliveries on this base"][1])
        # Its close removes its entry.
        self.state(fx, fx.tmp / "record", "journal", "--kind", "delivery.closed", "--data", "{}")
        fx.flow("--delivery", "FX-1", "lead-close", "--without-compounding", "the fixture closes early", cwd=m)
        self.assertFalse((self.registry(fx) / "FX-1.json").exists())
        self.assertEqual(self.doctor(fx, "FX-9", lead)["deliveries on this base"][1],
                         "no other delivery is registered on feature/FX-1")

    def test_a_peers_merge_of_an_open_storys_file_is_journalled_once_and_no_other_merge_is(self) -> None:
        fx = self.fixture()
        m = fx.main
        lead = self.peer(fx)
        self.flow(fx, "FX-9", "lead-init", "--no-supervisor", cwd=lead)
        # A merge of FX-2's file from before FX-1's record began is never FX-1's to hear of.
        self.merged_on_base(fx, lead, "FX-9", "FX-90", "src/one.txt", "one, before FX-1\n")
        self.flow(fx, "FX-1", "lead-init", "--no-supervisor", cwd=m)
        other = self.peer(fx, "FX-7", base="feature/FX-7")
        sh("git", "push", "-q", "origin", "origin/feature/FX-1:refs/heads/feature/FX-7", cwd=m)
        self.flow(fx, "FX-7", "lead-init", "--no-supervisor", cwd=other, env={**fx.env, "RUN_DELIVERY_BASE": "feature/FX-7"})
        # FX-3 is merged in FX-1's own record: a file only it names is no open story's.
        self.queued(fx, fx.tmp / "record", "FX-3", "lane-2", stage="merged")
        # FX-9 merges one story touching FX-2's file, one touching only FX-3's, and one touching no story's.
        stale = sh("git", "rev-parse", "origin/feature/FX-1", cwd=m).stdout.strip()
        touched = self.merged_on_base(fx, lead, "FX-9", "FX-92", "src/one.txt", "one, by FX-9\n")
        self.merged_on_base(fx, lead, "FX-9", "FX-93", "src/two.txt", "two, by FX-9\n")
        self.merged_on_base(fx, lead, "FX-9", "FX-94", "docs/notes.md", "notes\n")
        # One merged on the forge, recorded from a snapshot: its head isn't on the base, its summary goes on past it.
        sh("git", "checkout", "-q", "--detach", "origin/feature/FX-1~3", cwd=lead)
        (lead / "src/one.txt").write_text("one, on the forge\n")
        commit(lead, "FX-95: one")
        recovered = sh("git", "rev-parse", "HEAD", cwd=lead).stdout.strip()
        sh("git", "checkout", "-q", "lead/FX-9", cwd=lead)
        sh(sys.executable, "-B", "-c", RUNNER_ENTRY, fx.release / "skills/orchestrate-work/scripts",
           fx.tmp / "record-FX-9", "candidate.merged", json.dumps({"head": recovered}), "FX-95")
        # FX-7, on another base, merges a story touching FX-2's file there: not this delivery's peer.
        sh("git", "fetch", "-q", "origin", cwd=other)
        sh("git", "checkout", "-q", "--detach", "origin/feature/FX-7", cwd=other)
        (other / "src/one.txt").write_text("one, by FX-7\n")
        commit(other, "FX-72: one")
        seven = sh("git", "rev-parse", "HEAD", cwd=other).stdout.strip()
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-7", cwd=other)
        sh(sys.executable, "-B", "-c", RUNNER_ENTRY, fx.release / "skills/orchestrate-work/scripts",
           fx.tmp / "record-FX-7", "candidate.merged", json.dumps({"head": seven}), "FX-72")
        # A pass that finds the base's ref behind these merges fetches it before it reads one's files, so FX-93's merge
        # isn't read as changing FX-92's file too. It journals FX-92's and FX-95's merges once each; passes by a
        # supervisor started again journal nothing more.
        sh(sys.executable, "-B", "-c", PEER_STEP, fx.tool.parent, "FX-1", stale, cwd=m, env=fx.env)
        self.assertEqual(len(self.peer_merged(fx)), 2, self.peer_merged(fx))
        for _ in range(2):
            self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
            entries = self.peer_merged(fx)
            self.assertEqual(len(entries), 2, entries)
        self.assertEqual([(e["subject"], e["data"]) for e in entries], [
            ("FX-2", {"peer": "FX-9", "head": touched, "files": ["src/one.txt"]}),
            ("FX-2", {"peer": "FX-9", "head": recovered, "files": ["src/one.txt"]})])
        log = [json.loads(line) for line in (fx.tmp / "flow-log/FX-1.supervisor.jsonl").read_text().splitlines()]
        self.assertEqual(log[-1]["peerReads"]["FX-9"], max(e["id"] for e in self.record(fx, fx.tmp / "record-FX-9")["journal"]))
        self.assertNotIn("FX-7", log[-1]["peerReads"])
        reads = [json.loads(line) for line in (fx.tmp / "flow-log/FX-1.peers.jsonl").read_text().splitlines()]
        self.assertEqual({read["peer"] for read in reads}, {"FX-9"})
        # A merge journalled before its commit reaches the base, from a clone that shares nothing with this checkout,
        # holds the read there; once it is pushed, a later pass fetches it and journals it once.
        clone = fx.tmp / "clone"
        sh("git", "clone", "-q", "-b", "feature/FX-1", fx.origin, clone)
        sh("git", "config", "user.email", "fx@example.test", cwd=clone)
        sh("git", "config", "user.name", "fx", cwd=clone)
        (clone / "src/one.txt").write_text("one, from a clone\n")
        commit(clone, "FX-96: one")
        late = sh("git", "rev-parse", "HEAD", cwd=clone).stdout.strip()
        # A merge an older runner journaled as a sentence alone is refused at the journal; the merge is journaled as
        # its facts.
        errors = delivery_state.journal_errors([sentence_entry(1, "candidate.merged", f"Merged FX-96 at {late}", "FX-96")])
        self.assertTrue(errors and "in the older journal shape" in errors[0], errors)
        sh(sys.executable, "-B", "-c", RUNNER_ENTRY, fx.release / "skills/orchestrate-work/scripts",
           fx.tmp / "record-FX-9", "candidate.merged", json.dumps({"head": late}), "FX-96")
        held = max(e["id"] for e in self.record(fx, fx.tmp / "record-FX-9")["journal"])
        self.state(fx, fx.tmp / "record-FX-9", "journal", "--kind", "decision.recorded", "--text", "after FX-96",
                   "--why", "a later entry")
        self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
        self.assertEqual(len(self.peer_merged(fx)), 2)
        reads = [json.loads(line) for line in (fx.tmp / "flow-log/FX-1.peers.jsonl").read_text().splitlines()]
        self.assertLess(max(read["through"] for read in reads), held)
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-1", cwd=clone)
        self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
        self.assertEqual([e["data"] for e in self.peer_merged(fx)][2:],
                         [{"peer": "FX-9", "head": late, "files": ["src/one.txt"]}])
        # A merge entry that names no head, which only an older writer's sentence could be, is refused at the journal.
        errors = delivery_state.journal_errors([sentence_entry(1, "candidate.merged", "Merged FX-98", "FX-98")])
        self.assertTrue(errors and "in the older journal shape" in errors[0], errors)
        after = self.merged_on_base(fx, lead, "FX-9", "FX-99", "src/one.txt", "one, after a headless merge\n")
        self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
        self.assertEqual([e["data"] for e in self.peer_merged(fx)][3:],
                         [{"peer": "FX-9", "head": after, "files": ["src/one.txt"]}])
        # Once FX-2 is settled, a peer's merge of its file is no open story's.
        self.patch(fx, fx.tmp / "record", "lead", "--element", "planned[subject=FX-2]", "--set",
                   json.dumps({"/stage": "settled"}))
        self.merged_on_base(fx, lead, "FX-9", "FX-97", "src/one.txt", "one, after FX-2 settled\n")
        self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
        self.assertEqual(len(self.peer_merged(fx)), 4)
        # The tracker publisher posts nothing for it.
        self.assertIsNone(tracker_publisher.kind_updates()["peer.merged"])

    # -- what each delivery waits on in the other

    def peer_waits(self, fx: Fixture, folder: Path) -> dict:
        """The record's peer-waits.json as the last supervisor pass wrote it, its sentences by name."""
        written = json.loads((folder / "peer-waits.json").read_text())
        self.assertEqual(written["spec"], "delivery-peer-waits/1")
        self.assertGreater(written["staleAfter"], written["at"])
        return {name: [(wait["subject"], wait["words"]) for wait in written[name]] for name in ("waits", "waitedOn")}

    def files_named(self, fx: Fixture) -> None:
        """FX-1's plan sections name their files in a `Files:` sentence, which its planned entries carry."""
        plan = fx.main / "docs/plan.md"
        plan.write_text(plan.read_text().replace("It changes `src/one.txt`.", "Files: `src/one.txt`.")
                        .replace("It changes `src/two.txt`.", "Files: `src/two.txt`."))
        commit(fx.main, "FX-1: name each story's files")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)

    def test_a_claim_is_a_wait_in_the_delivery_that_needs_the_file_and_a_line_in_the_one_that_claims_it(self) -> None:
        fx = self.fixture()
        m = fx.main
        self.files_named(fx)
        self.flow(fx, "FX-1", "lead-init", "--no-supervisor", cwd=m)
        lead = self.peer(fx)
        self.flow(fx, "FX-9", "lead-init", "--no-supervisor", cwd=lead)
        record1, record9 = fx.tmp / "record", fx.tmp / "record-FX-9"
        self.patch(fx, record9, "lead", "--patch", json.dumps({"laneOwnership": {"lane-1": [], "lane-2": ["src/two*"]}}))
        self.flow(fx, "FX-9", "lead-own", "--claim", "src/one*", cwd=lead)
        # Each delivery's own pass derives its side from the same facts: FX-1's FX-2 names the file FX-9 claims.
        self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
        self.flow(fx, "FX-9", "lead-run", "--once", cwd=lead)
        # FX-3 names src/two.txt, which nobody claims: it waits on nothing.
        self.assertEqual(self.peer_waits(fx, record1), {"waitedOn": [], "waits": [
            ("FX-2", "Waits for delivery FX-9, which claims src/one.txt.")]})
        self.assertEqual(self.peer_waits(fx, record9), {"waits": [], "waitedOn": [
            ("lead", "Delivery FX-1's FX-2 waits on this delivery's claim on src/one.txt.")]})
        # It is derived and never stored: the record holds none of it, and the views add it while the file is fresh.
        shown = self.record(fx, record1)
        self.assertNotIn("Waits for delivery FX-9", json.dumps(shown))
        # Released, both sides clear on their next pass, with no action from either lead.
        self.patch(fx, record9, "lead", "--patch", json.dumps({"extensions": {"claims": []}}))
        self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
        self.flow(fx, "FX-9", "lead-run", "--once", cwd=lead)
        self.assertEqual(self.peer_waits(fx, record1), {"waits": [], "waitedOn": []})
        self.assertEqual(self.peer_waits(fx, record9), {"waits": [], "waitedOn": []})
        # Claimed again, then closed with its claim still listed: FX-1's next pass waits on nothing.
        self.flow(fx, "FX-9", "lead-own", "--claim", "src/one*", cwd=lead)
        self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
        self.assertTrue(self.peer_waits(fx, record1)["waits"])
        self.state(fx, record9, "journal", "--kind", "delivery.closed", "--data", "{}")
        fx.flow("--delivery", "FX-9", "lead-close", "--without-compounding", "the fixture closes early", cwd=lead)
        self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
        self.assertEqual(self.peer_waits(fx, record1), {"waits": [], "waitedOn": []})

    def waiting(self, fx: Fixture, *argv: str, cwd: Path) -> subprocess.Popen:
        """A lead command of FX-1 started while the slot is held, until its waiter file is written; stopped by its pid."""
        proc = subprocess.Popen([sys.executable, "-B", str(fx.tool), "--delivery", "FX-1", *argv], cwd=cwd, env=fx.env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait(), proc.stdout.close()))
        lock = delivery_state.merge_slot_path(fx.main, "feature/FX-1")
        wait_until(lambda: any(w["pid"] == proc.pid for w in delivery_state.slot_parties(lock)["waiters"])
                   or proc.poll() is not None, f"{argv[0]} to wait for the slot")
        if proc.poll() is not None:
            self.fail(f"{argv[0]} ended instead of waiting: {proc.stdout.read()}")
        return proc

    def test_a_lead_command_waiting_on_a_peers_hold_says_which_delivery_what_it_does_and_since_when(self) -> None:
        fx = self.fixture()
        m = fx.main
        # The writer upgrade waits for the slot within the binding's check timeout.
        binding = json.loads((m / ".agents/orchestration.json").read_text())
        (m / ".agents/orchestration.json").write_text(json.dumps({**binding, "checkTimeoutSeconds": 120}, indent=2))
        commit(m, "FX-1: a check timeout the upgrade waits within")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        self.flow(fx, "FX-1", "lead-init", "--no-supervisor", cwd=m)
        lead = self.peer(fx)
        self.flow(fx, "FX-9", "lead-init", "--no-supervisor", cwd=lead)
        record1, record9 = fx.tmp / "record", fx.tmp / "record-FX-9"
        lock = delivery_state.merge_slot_path(fx.main, "feature/FX-1")
        holder = self.hold_slot(fx, "FX-9", {"what": "check", "subject": "FX-92"})
        since = delivery_state.slot_parties(lock)["holder"]["since"]
        at = datetime.datetime.strptime(since[:19], "%Y-%m-%dT%H:%M:%S").strftime("%H:%M UTC")
        push = self.waiting(fx, "lead-push", "--wait-seconds", "120", cwd=m)
        [waiter] = delivery_state.slot_parties(lock)["waiters"]
        self.assertEqual((waiter["delivery"], waiter["doing"]), ("FX-1", {"what": "push"}))
        # Each delivery's pass: FX-1's lead waits on FX-9's check, and FX-9 says FX-1 waits on it.
        self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
        self.flow(fx, "FX-9", "lead-run", "--once", cwd=lead)
        self.assertEqual(self.peer_waits(fx, record1), {"waitedOn": [], "waits": [
            ("lead", f"The lead's push waits for delivery FX-9, which is checking FX-92, since {at}.")]})
        self.assertEqual(self.peer_waits(fx, record9), {"waits": [], "waitedOn": [
            ("FX-92", "Delivery FX-1 waits to start pushing.")]})
        push.kill()
        push.wait()
        # The writer upgrade says so in its waiter file too.
        upgrade = self.waiting(fx, "lead-upgrade-writer", "--expected", "0" * 64, "--reason", "test", "--no-supervisor",
                               cwd=m)
        [waiter] = delivery_state.slot_parties(lock)["waiters"]
        self.assertEqual((waiter["pid"], waiter["doing"]), (upgrade.pid, {"what": "upgrade"}))
        # Its own supervisor is parked while it waits, so it says its own wait, with no pass.
        wait_until(lambda: "writer upgrade" in json.dumps(self.peer_waits(fx, record1)), "the upgrade to say its wait")
        self.assertEqual(self.peer_waits(fx, record1)["waits"], [
            ("lead", f"The lead's writer upgrade waits for delivery FX-9, which is checking FX-92, since {at}.")])
        # Nothing rewrites it while it waits, so it stays fresh for as long as the upgrade may wait.
        written = json.loads((record1 / "peer-waits.json").read_text())
        lasts = (datetime.datetime.fromisoformat(written["staleAfter"][:-1]) - datetime.datetime.fromisoformat(written["at"][:-1]))
        self.assertGreaterEqual(lasts.total_seconds(), 120)
        upgrade.kill()
        upgrade.wait()
        # A push waiting when the holder lets go takes the slot and finishes, and both sides clear on their next pass.
        push = self.waiting(fx, "lead-push", "--wait-seconds", "120", cwd=m)
        holder.kill()
        holder.wait()
        self.assertEqual(push.wait(120), 0)
        self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
        self.flow(fx, "FX-9", "lead-run", "--once", cwd=lead)
        self.assertEqual(self.peer_waits(fx, record1), {"waits": [], "waitedOn": []})
        self.assertEqual(self.peer_waits(fx, record9), {"waits": [], "waitedOn": []})

class PeerWordingTests(unittest.TestCase):
    """The words that say how deliveries on one base see each other, where a lead reads them."""

    def text(self, relative: str) -> str:
        return " ".join((ROOT / relative).read_text(encoding="utf-8").split())

    def test_the_guide_says_where_deliveries_register_how_a_claim_warns_and_what_peer_merged_posts(self) -> None:
        guide = self.text("skills/orchestrate-work/references/orchestration-guide.md")
        for phrase in ("| `peer.merged` | None: the supervisor journals a merge by another delivery on the base that "
                       "changed a file one of this delivery's open stories names",
                       "run-delivery registers each delivery in `<git-common-dir>/agent-skills/deliveries/`, one JSON "
                       "file named for its key",
                       "`lead-own --claim PATTERN` claims whole files on the base, in the delivery's own record; a region "
                       "or a line range is refused.",
                       "and refuse nothing.",
                       "A delivery alone on its base, with the slot free, shows no section."):
            self.assertIn(phrase, guide)

    def test_the_command_table_names_the_claim_form_of_lead_own(self) -> None:
        self.assertIn("`--claim` claims whole files on the base for the delivery itself, in its own record, so another "
                      "delivery on the base warns, without refusing,", self.text("skills/run-delivery/SKILL.md"))



# -- the waits on other deliveries, derived in-process from records and slot files a case writes

WORDS = ROOT / "skills/orchestrate-work/references/wait-words.json"
CLOCK = "2026-11-17T14:10:02Z"


def ours() -> dict:
    """APP-1's record: APP-2 planned in idle lane 1, naming skills/render.py; APP-3 building in lane 2, which holds
    skills/views.py, which APP-1 claims; APP-5 queued in lane 2."""
    return {"state": {"lanes": [{"id": "lane-1", "state": "idle"}, {"id": "lane-2", "state": "working"}],
                      "queue": [{"subject": "APP-5", "lane": "lane-2", "stage": "queued"}],
                      "planned": [{"subject": "APP-2", "lane": "lane-1", "stage": "planned", "files": ["skills/render.py"]},
                                  {"subject": "APP-3", "lane": "lane-2", "stage": "building", "files": ["skills/views.py"]}],
                      "laneOwnership": {"lane-2": ["skills/views.py"]}, "extensions": {"claims": ["skills/views.py"]}},
            "journal": []}


def theirs() -> dict:
    """PEER-1's record: its lane 1 holds skills/*.py for PEER-2, which is building, and it claims skills/render.py;
    PEER-3, planned, names skills/views.py."""
    return {"key": "PEER-1", "state": {
        "lanes": [], "queue": [], "laneOwnership": {"lane-1": ["skills/*.py"]}, "extensions": {"claims": ["skills/render.py"]},
        "planned": [{"subject": "PEER-2", "lane": "lane-1", "stage": "building", "files": ["skills/render.py"]},
                    {"subject": "PEER-3", "lane": "lane-2", "stage": "planned", "files": ["skills/views.py"]}]}}


FREE = {"holder": None, "waiters": []}


def said(found: dict, name: str = "waits") -> list[tuple]:
    return [(wait["subject"], wait["words"]) for wait in found[name]]


class PeerWaitsTests(unittest.TestCase):
    """A claim or the merge slot gives a wait in the delivery that waits and a line in the one it waits on, from the
    same facts, and both go when the claim is released or the slot is."""

    def test_a_claimed_file_is_a_wait_on_the_peer_and_its_story_and_the_peer_says_who_waits_on_its_claim(self) -> None:
        found = delivery_state.peer_waits(ours(), "APP-1", [theirs()], FREE)
        self.assertEqual(said(found), [
            ("APP-2", "Waits for delivery PEER-1, whose PEER-2 claims skills/render.py."),
            ("lane-1", "Lane 1 is next on APP-2, which waits for delivery PEER-1, whose PEER-2 claims skills/render.py.")])
        # The idle lane's wait is its next story's: on the same delivery and story, freed the same way.
        for subject in ("APP-2", "lane-1"):
            [wait] = [wait for wait in found["waits"] if wait["subject"] == subject]
            self.assertEqual({k: wait[k] for k in ("on", "cause", "stage", "frees", "needsSomeone")},
                             {"on": {"delivery": "PEER-1", "subject": "PEER-2"}, "cause": "claim", "stage": "building",
                              "frees": "released", "needsSomeone": False})
        # PEER-3 waits on APP-1's claim: said under APP-3, the story on the file.
        self.assertEqual(said(found, "waitedOn"), [("APP-3", "Delivery PEER-1's PEER-3 waits on this delivery's claim on skills/views.py.")])
        self.assertEqual(found["waitedOn"][0]["by"], [{"delivery": "PEER-1", "subject": "PEER-3"}])
        # The peer derives the same from its side: its PEER-3 waits on APP-1, and it says APP-2 waits on its claim.
        peer = {"state": theirs()["state"], "journal": []}
        mirror = delivery_state.peer_waits(peer, "PEER-1", [{"key": "APP-1", "state": ours()["state"]}], FREE)
        self.assertEqual(said(mirror), [("PEER-3", "Waits for delivery APP-1, whose APP-3 claims skills/views.py.")])
        self.assertEqual(said(mirror, "waitedOn"), [("PEER-2", "Delivery APP-1's APP-2 waits on this delivery's claim on skills/render.py.")])

    def test_a_claim_no_story_holds_names_the_delivery_and_is_said_under_the_lead(self) -> None:
        peer = theirs()
        peer["state"]["laneOwnership"] = {}
        document = ours()
        document["state"]["laneOwnership"] = {}
        found = delivery_state.peer_waits(document, "APP-1", [peer], FREE)
        self.assertEqual(said(found)[0], ("APP-2", "Waits for delivery PEER-1, which claims skills/render.py."))
        self.assertEqual(found["waits"][0]["on"], {"delivery": "PEER-1"})
        self.assertEqual([subject for subject, _ in said(found, "waitedOn")], ["lead"])

    def test_a_released_claim_a_closed_peer_and_a_started_story_wait_on_nothing(self) -> None:
        peer = theirs()
        peer["state"]["extensions"] = {"claims": []}
        document = ours()
        document["state"]["extensions"] = {}
        self.assertEqual(delivery_state.peer_waits(document, "APP-1", [peer], FREE), {"waits": [], "waitedOn": []})
        # A closed peer is no longer registered, so it is not among the peers.
        self.assertEqual(delivery_state.peer_waits(ours(), "APP-1", [], FREE), {"waits": [], "waitedOn": []})
        started = ours()
        started["state"]["planned"][0]["stage"] = "building"
        self.assertEqual(said(delivery_state.peer_waits(started, "APP-1", [theirs()], FREE)), [])

    def test_a_story_naming_no_claimed_file_and_a_peers_started_story_wait_on_nothing(self) -> None:
        document = ours()
        document["state"]["planned"].append({"subject": "APP-4", "lane": "lane-1", "stage": "planned",
                                             "files": ["docs/unclaimed.md"]})
        peer = theirs()
        peer["state"]["planned"].append({"subject": "PEER-4", "lane": "lane-2", "stage": "building",
                                         "files": ["skills/views.py"]})
        found = delivery_state.peer_waits(document, "APP-1", [peer], FREE)
        self.assertEqual([subject for subject, _ in said(found)], ["APP-2", "lane-1"])
        self.assertEqual([wait["by"] for wait in found["waitedOn"]], [[{"delivery": "PEER-1", "subject": "PEER-3"}]])

    def test_only_an_idle_lane_says_a_claim_wait_and_only_its_next_planned_storys(self) -> None:
        document = ours()
        # Lane 1 is idle and next on APP-4, which names no claimed file; lane 2 is working: neither says APP-2's or
        # APP-6's wait.
        document["state"]["planned"] = [
            {"subject": "APP-4", "lane": "lane-1", "stage": "planned", "files": ["docs/unclaimed.md"]},
            {"subject": "APP-2", "lane": "lane-1", "stage": "planned", "files": ["skills/render.py"]},
            {"subject": "APP-6", "lane": "lane-2", "stage": "planned", "files": ["skills/render.py"]}]
        found = delivery_state.peer_waits(document, "APP-1", [theirs()], FREE)
        self.assertEqual([subject for subject, _ in said(found)], ["APP-2", "APP-6"])
        # A story lane 1 has already started is not what it is next on: its next planned story, APP-2, is.
        document["state"]["planned"] = [
            {"subject": "APP-7", "lane": "lane-1", "stage": "building", "files": ["docs/unclaimed.md"]},
            {"subject": "APP-2", "lane": "lane-1", "stage": "planned", "files": ["skills/render.py"]}]
        found = delivery_state.peer_waits(document, "APP-1", [theirs()], FREE)
        self.assertEqual(said(found)[1:], [
            ("lane-1", "Lane 1 is next on APP-2, which waits for delivery PEER-1, whose PEER-2 claims skills/render.py.")])

    def test_needs_you_never_calls_a_story_free_to_start_while_the_views_say_it_waits_on_a_peer(self) -> None:
        document = ours()
        document["state"]["planned"].append({"subject": "APP-4", "lane": "lane-1", "stage": "planned", "files": []})
        with tempfile.TemporaryDirectory() as folder:
            c = types.SimpleNamespace(release=ROOT, state=Path(folder))

            def free() -> list[str]:
                return [item["source"] for item in flow.waits_needing(c, document, []) if "free to start" in item["text"]]

            def write(seconds: int) -> None:
                at = datetime.datetime.now(datetime.timezone.utc)
                stale = (at + datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
                (Path(folder) / "peer-waits.json").write_text(json.dumps({
                    "spec": "delivery-peer-waits/1", "at": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "staleAfter": stale,
                    **delivery_state.peer_waits(document, "APP-1", [theirs()], FREE)}))

            self.assertEqual(free(), ["APP-2's waits", "APP-4's waits"])
            # While the views say APP-2 waits on PEER-1's claim, "Needs you" doesn't call it free; stale, they say
            # nothing of other deliveries, and neither does it.
            write(600)
            self.assertEqual(free(), ["APP-4's waits"])
            write(-60)
            self.assertEqual(free(), ["APP-2's waits", "APP-4's waits"])

    def test_a_peers_landed_story_is_not_named_and_one_its_region_covers_is(self) -> None:
        peer = theirs()
        peer["state"]["planned"][0]["stage"] = "settled"
        peer["state"]["queue"] = [{"subject": "PEER-6", "lane": "lane-1", "stage": "merged"}]
        self.assertEqual(said(delivery_state.peer_waits(ours(), "APP-1", [peer], FREE))[0],
                         ("APP-2", "Waits for delivery PEER-1, which claims skills/render.py."))
        peer["state"]["laneRegions"] = [{"lane": "lane-3", "path": "skills/render.py", "start": 1, "end": 2}]
        peer["state"]["planned"].append({"subject": "PEER-7", "lane": "lane-3", "stage": "building"})
        self.assertEqual(said(delivery_state.peer_waits(ours(), "APP-1", [peer], FREE))[0],
                         ("APP-2", "Waits for delivery PEER-1, whose PEER-7 claims skills/render.py."))

    def test_the_queue_named_by_its_headline_is_the_delivery_its_key_names(self) -> None:
        document = ours()
        document["state"].update(meta={"headline": "Show what everything waits on"}, extensions={})
        peer = theirs()
        peer["state"].update(meta={"headline": "The peer's own headline"}, extensions={}, planned=[])
        mine = {"delivery": "Show what everything waits on", "doing": {"what": "check", "subject": "APP-5"}}
        theirs_ = {"delivery": "The peer's own headline", "doing": {"what": "check", "subject": "PEER-2"}}
        # This delivery's queue holds the slot, and a peer's lead push waits: said under the piece, by the peer's key.
        found = delivery_state.peer_waits(document, "APP-1", [peer], self.slot(mine, {"delivery": "PEER-1", "doing": {"what": "push"}}))
        self.assertEqual(said(found, "waitedOn"), [("APP-5", "Delivery PEER-1 waits to start pushing.")])
        # A peer's queue holds it, and this delivery's queue's front piece waits: the wait names the peer by its key.
        found = delivery_state.peer_waits(document, "APP-1", [peer], self.slot(theirs_, {**mine, "doing": {"what": "check", "subject": "APP-5"}}))
        self.assertEqual(said(found), [("APP-5", "APP-5 waits for delivery PEER-1, which is checking PEER-2, since 14:10 UTC.")])
        self.assertEqual(found["waits"][0]["on"], {"delivery": "PEER-1", "subject": "PEER-2"})
        # A headline that is another delivery's key names that delivery, not this one.
        document["state"]["meta"]["headline"] = "PEER-1"
        found = delivery_state.peer_waits(document, "APP-1", [peer], self.slot({"delivery": "APP-1", "doing": {"what": "push"}},
                                                                             {"delivery": "PEER-1", "doing": {"what": "sync"}}))
        self.assertEqual(said(found, "waitedOn"), [("lead", "Delivery PEER-1 waits to start syncing.")])
        document["state"]["meta"]["headline"] = "Show what everything waits on"
        # This delivery's own lead waiting on its own queue waits on no other delivery.
        found = delivery_state.peer_waits(document, "APP-1", [peer], self.slot(mine, {"delivery": "APP-1", "doing": {"what": "push"}}))
        self.assertEqual(found, {"waits": [], "waitedOn": []})

    def test_a_merge_waiter_is_said_under_its_piece(self) -> None:
        slot = self.slot({"delivery": "PEER-1", "doing": {"what": "check", "subject": "PEER-2"}},
                         {"delivery": "APP-1", "doing": {"what": "merge", "subject": "APP-5"}})
        self.assertEqual([subject for subject, _ in said(delivery_state.peer_waits(ours(), "APP-1", [], slot))], ["APP-5"])

    def slot(self, holder: dict | None, *waiters: dict) -> dict:
        return {"holder": None if holder is None else {"pid": 1, "since": CLOCK, **holder},
                "waiters": [{"pid": 10 + index, "since": CLOCK, **waiter} for index, waiter in enumerate(waiters)]}

    def test_a_lead_command_and_the_queues_front_piece_wait_on_the_peers_hold_saying_what_it_does_and_since(self) -> None:
        for what, words in (("push", "push"), ("sync", "sync"), ("upgrade", "writer upgrade")):
            with self.subTest(what=what):
                slot = self.slot({"delivery": "PEER-1", "doing": {"what": "check", "subject": "PEER-2"}},
                                 {"delivery": "APP-1", "doing": {"what": what}})
                [wait] = delivery_state.peer_waits(ours(), "APP-1", [], slot)["waits"]
                self.assertEqual((wait["subject"], wait["words"]), (
                    "lead", f"The lead's {words} waits for delivery PEER-1, which is checking PEER-2, since 14:10 UTC."))
                self.assertEqual({k: wait[k] for k in ("on", "cause", "stage", "frees", "since", "needsSomeone")},
                                 {"on": {"delivery": "PEER-1", "subject": "PEER-2"}, "cause": "slot", "stage": "check",
                                  "frees": "released", "since": CLOCK, "needsSomeone": False})
        for held, doing in (({"what": "merge", "subject": "PEER-2"}, "merging PEER-2"), ({"what": "release"}, "releasing"),
                            ({"what": "push"}, "pushing")):
            with self.subTest(held=held):
                slot = self.slot({"delivery": "PEER-1", "doing": held}, {"delivery": "APP-1", "doing": {"what": "check", "subject": "APP-5"}})
                self.assertEqual(said(delivery_state.peer_waits(ours(), "APP-1", [], slot)),
                                 [("APP-5", f"APP-5 waits for delivery PEER-1, which is {doing}, since 14:10 UTC.")])

    def test_the_holder_names_each_waiter_and_what_it_waits_to_do(self) -> None:
        slot = self.slot({"delivery": "APP-1", "doing": {"what": "check", "subject": "APP-5"}},
                         {"delivery": "PEER-1", "doing": {"what": "push"}},
                         {"delivery": "PEER-1", "doing": {"what": "check", "subject": "PEER-2"}},
                         {"delivery": "APP-1", "doing": {"what": "push"}})
        found = delivery_state.peer_waits(ours(), "APP-1", [], slot)
        self.assertEqual(said(found, "waitedOn"), [("APP-5", "Delivery PEER-1 waits to start pushing."),
                                                   ("APP-5", "Delivery PEER-1 waits to start checking PEER-2.")])
        self.assertEqual(found["waitedOn"][1]["by"], [{"delivery": "PEER-1", "subject": "PEER-2"}])
        # This delivery's own waiter waits on itself, which is no wait on another delivery.
        self.assertEqual(found["waits"], [])
        # A lead's hold is said under the lead.
        slot["holder"]["doing"] = {"what": "sync"}
        self.assertEqual({subject for subject, _ in said(delivery_state.peer_waits(ours(), "APP-1", [], slot), "waitedOn")}, {"lead"})

    def test_every_caller_of_the_merge_slot_says_what_it_holds_it_for(self) -> None:
        import ast
        named = {"lead_sync": "sync", "lead_upgrade_writer": "upgrade", "lead_push": "push", "slot": None}
        seen, told = {}, {}
        for relative in ("skills/run-delivery/scripts/run_delivery.py", "skills/orchestrate-work/scripts/merge_queue.py"):
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            for function in ast.walk(tree):
                if not isinstance(function, ast.FunctionDef):
                    continue
                for call in ast.walk(function):
                    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                            and call.func.attr == "merge_slot"):
                        [doing] = [keyword.value for keyword in call.keywords if keyword.arg == "doing"]
                        seen[function.name] = ast.literal_eval(doing)["what"] if isinstance(doing, ast.Dict) else None
                        for keyword in call.keywords:
                            if keyword.arg == "on_wait":
                                told[function.name] = ast.unparse(keyword.value)
        self.assertEqual(seen, named)
        # The two that park the supervisor while they wait say their own wait, fresh for as long as each may wait, and
        # once they take the slot, fresh only until a pass is overdue.
        self.assertEqual(told, {
            "lead_sync": "lambda taken: say_peer_waits(c, 0 if taken else args.wait_seconds)",
            "lead_upgrade_writer": "lambda taken: say_peer_waits(c, 0 if taken else float(c.binding.get('checkTimeoutSeconds', 0)))"})

    def test_a_holder_or_waiter_file_without_doing_is_using_the_main_line_and_a_free_slot_waits_on_nothing(self) -> None:
        slot = self.slot({"delivery": "PEER-1"}, {"delivery": "APP-1"})
        self.assertEqual(said(delivery_state.peer_waits(ours(), "APP-1", [], slot)), [
            ("lead", "The lead's use of the main line waits for delivery PEER-1, which is using the main line, since 14:10 UTC.")])
        holding = self.slot({"delivery": "APP-1"}, {"delivery": "PEER-1"})
        self.assertEqual(said(delivery_state.peer_waits(ours(), "APP-1", [], holding), "waitedOn"),
                         [("lead", "Delivery PEER-1 waits to start using the main line.")])
        self.assertEqual(delivery_state.peer_waits(ours(), "APP-1", [], self.slot(None, {"delivery": "APP-1"})),
                         {"waits": [], "waitedOn": []})


def marked(table: dict) -> dict:
    """The table with every template carrying its own marker, ⟦group.name[.case]⟧, as tests.test_waits marks it."""
    copied = json.loads(json.dumps(table))
    for group, value in copied.items():
        if not isinstance(value, dict) or "templates" not in value:
            continue
        for name, template in value["templates"].items():
            if isinstance(template, dict):
                template["cases"] = {case: f"⟦{group}.{name}.{case}⟧ {words}" for case, words in template["cases"].items()}
                template["otherwise"] = f"⟦{group}.{name}⟧ {template['otherwise']}"
            else:
                value["templates"][name] = f"⟦{group}.{name}⟧ {template}"
    return copied


class PeerWaitViewTests(unittest.TestCase):
    """Each peer-wait sentence comes from its own template, and the Markdown view and the built-in page print it while
    peer-waits.json is fresh, and nothing of other deliveries when it is stale or absent."""

    def setUp(self) -> None:
        import render_state
        self.render_state = render_state
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.table = marked(json.loads(WORDS.read_text(encoding="utf-8")))
        slot = {"holder": {"pid": 1, "since": CLOCK, "delivery": "PEER-1", "doing": {"what": "check", "subject": "PEER-2"}},
                "waiters": [{"pid": 2, "since": CLOCK, "delivery": "APP-1", "doing": {"what": "push"}},
                            {"pid": 3, "since": CLOCK, "delivery": "APP-1", "doing": {"what": "check", "subject": "APP-5"}}]}
        claims = delivery_state.peer_waits(ours(), "APP-1", [theirs()], FREE, words=self.table)
        held = delivery_state.peer_waits(ours(), "APP-1", [], slot, words=self.table)
        holding = delivery_state.peer_waits(ours(), "APP-1", [], {
            "holder": {"pid": 1, "since": CLOCK, "delivery": "APP-1", "doing": {"what": "sync"}},
            "waiters": [{"pid": 2, "since": CLOCK, "delivery": "PEER-1", "doing": {"what": "merge", "subject": "PEER-2"}}]},
            words=self.table)
        self.found = {name: [*claims[name], *held[name], *holding[name]] for name in ("waits", "waitedOn")}

    def write(self, stale_after: str) -> None:
        (self.folder / "peer-waits.json").write_text(json.dumps({"spec": "delivery-peer-waits/1", "at": CLOCK,
                                                                 "staleAfter": stale_after, **self.found}))

    def record(self) -> dict:
        """The waits fixture's whole record, with APP-1's work in it."""
        document = json.loads((ROOT / "tests/fixtures/waits/record.json").read_text(encoding="utf-8"))
        document["journal"] = []
        mine = ours()["state"]
        template = next(iter(document["state"]["planned"]))
        document["state"]["planned"] = [{**template, **item, "title": item["subject"]} for item in mine["planned"]]
        rows = {row["id"]: row for row in document["state"]["lanes"]}
        document["state"]["lanes"] = [{**rows["lane-1"], **row} for row in mine["lanes"]]
        document["state"]["queue"] = [{"order": 1, "subject": "APP-5", "title": "Five", "lane": "lane-2", "stage": "queued",
                                       "head": "b" * 40, "syncedHead": None, "leadInputs": [], "reportedRounds": None,
                                       "runId": None, "checks": []}]
        document["state"].update(laneOwnership=mine["laneOwnership"], extensions=mine["extensions"])
        return document

    def test_every_template_the_cases_reach_carries_its_own_marker(self) -> None:
        reached = {re.search(r"⟦([a-zA-Z]+\.[a-zA-Z]+(?:\.[a-z0-9]+)?)⟧", wait["words"]).group(1)
                   for name in ("waits", "waitedOn") for wait in self.found[name]}
        self.assertEqual(reached, {"waits.claim.true", "lanes.claim.true", "holding.claim", "waits.slot.lead", "waits.slot",
                                   "holding.slot"})
        # The holder's doing is said from its own template inside the sentence.
        self.assertTrue(any("⟦doing.slot.check⟧" in wait["words"] for wait in self.found["waits"]))
        self.assertTrue(any("⟦doing.work.push⟧" in wait["words"] for wait in self.found["waits"]))

    def test_the_markdown_view_and_the_built_in_page_print_them_while_fresh(self) -> None:
        self.write("2999-01-01T00:00:00Z")
        shown = self.render_state.with_peer_waits(self.record(), self.folder)
        markdown = self.render_state.render_markdown(shown)
        page = self.render_state.render_static_page(shown)
        for wait in [*self.found["waits"], *self.found["waitedOn"]]:
            self.assertIn(self.render_state._markdown_text(wait["words"]), markdown)
        raw = re.search(r'<script type="application/json" id="delivery-state">(.*?)</script>', page, re.S).group(1)
        embedded = json.loads(raw)
        for name in ("waits", "waitedOn"):
            self.assertEqual([wait for wait in embedded[name] if wait in self.found[name]], self.found[name])
        # Under the story, the queued piece, the lane and the lead.
        lines = markdown.splitlines()
        story = lines.index(next(line for line in lines if line.startswith("- APP-2:")))
        self.assertIn("⟦waits.claim.true⟧", lines[story + 1])
        piece = lines.index(next(line for line in lines if line.startswith("- 1\\. APP-5:")))
        self.assertIn("⟦waits.slot⟧", "\n".join(lines[piece:piece + 3]))
        lead = [line for line in lines if "⟦waits.slot.lead⟧" in line or ("⟦holding.slot⟧" in line and line.startswith("- "))]
        self.assertEqual(len(lead), 2, markdown)
        # The page's own script prints what it is given: no peer-wait words of its own.
        script = page[page.index("<script>"):]
        for words in ("waits for delivery", "waits to start", "using the main line", "this delivery's claim"):
            self.assertNotIn(words, script.replace(raw, ""))

    def test_a_stale_file_or_none_says_nothing_of_other_deliveries(self) -> None:
        document = self.record()
        self.assertEqual(self.render_state.with_peer_waits(document, self.folder), document)
        self.write("2026-11-17T14:00:00Z")
        self.assertEqual(self.render_state.with_peer_waits(document, self.folder), document)
        (self.folder / "peer-waits.json").write_text("{not json")
        self.assertEqual(self.render_state.with_peer_waits(document, self.folder), document)
        self.write("2999-01-01T00:00:00Z")
        written = json.loads((self.folder / "peer-waits.json").read_text())
        (self.folder / "peer-waits.json").write_text(json.dumps({**written, "spec": "delivery-needs-you/1"}))
        self.assertEqual(self.render_state.with_peer_waits(document, self.folder), document)

    def test_the_record_every_view_and_the_live_stream_read_carries_them_while_fresh(self) -> None:
        record = self.folder / "record"
        delivery_state.init(record, "APP-1")
        self.folder = record
        claim = self.found["waits"][0]["words"]
        # As render() and the live view read the record: with its story, and its peer waits while they are fresh.
        self.assertNotIn(claim, json.dumps(self.render_state.with_story(self.render_state.show(record), record), ensure_ascii=False))
        view = self.render_state.LiveView(record, None)
        self.assertTrue(view.refresh())
        before = view.watch_key()
        self.write("2999-01-01T00:00:00Z")
        self.assertNotEqual(view.watch_key(), before)
        self.assertTrue(view.refresh())
        self.assertIn(claim, json.dumps(view.document, ensure_ascii=False))
        # Released, the next pass rewrites the file still fresh, with no wait in it: the key moves and the wait goes.
        released = view.watch_key()
        written = json.loads((record / "peer-waits.json").read_text())
        (record / "peer-waits.json").write_text(json.dumps({**written, "waits": [], "waitedOn": [], "at": "2026-11-17T14:20:00Z"}))
        self.assertNotEqual(view.watch_key(), released)
        self.assertTrue(view.refresh())
        self.assertNotIn(claim, json.dumps(view.document, ensure_ascii=False))
        self.write("2999-01-01T00:00:00Z")
        self.assertTrue(view.refresh())
        shown = self.render_state.with_story(self.render_state.show(record), record)
        self.assertEqual([wait["words"] for wait in shown["waits"]], [wait["words"] for wait in self.found["waits"]])
        # Gone stale with nothing else written, the key moves, and the next document carries none of it.
        fresh = view.watch_key()
        with mock.patch.object(self.render_state, "fresh_peer_waits", lambda directory, clock=None: None):
            self.assertNotEqual(view.watch_key(), fresh)
            self.assertTrue(view.refresh())
        self.assertNotIn(claim, json.dumps(view.document, ensure_ascii=False))
        (record / "peer-waits.json").unlink()
        self.assertNotIn(claim, json.dumps(self.render_state.with_story(self.render_state.show(record), record), ensure_ascii=False))


class LostSignalTests(unittest.TestCase):
    """The peer-waits step imports the state tool's modules in the supervisor's process on its first pass. A stop
    landing while Python compiles one runs the handler, and CPython before 3.14 then drops what it raised: the
    supervisor honours the signal it recorded at the pass's next step."""

    def supervisor(self, state: Path) -> flow.Supervisor:
        sup = object.__new__(flow.Supervisor)
        sup.c = types.SimpleNamespace(state=state, delivery="FX-1", load_delivery=lambda delivery: None)
        sup.once, sup.before_signals, sup.signalled = False, {signal.SIGTERM: flow._terminate}, None
        sup.lose = lambda: False
        return sup

    def swallowed(self, sup: flow.Supervisor) -> str:
        """The handler runs and its raise is lost, as inside compile()."""
        try:
            sup.on_signal(signal.SIGTERM, None)
        except SystemExit:
            pass
        return "reconciled"

    def test_a_signal_whose_raise_was_lost_stops_the_pass_at_its_next_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "state.json").write_text("{}\n")
            sup = self.supervisor(Path(tmp))
            sup.honour_signal()  # nothing recorded: nothing to honour
            later = mock.Mock(side_effect=AssertionError("a step ran after the signal"))
            with mock.patch.object(flow, "read_log", lambda *args: []), \
                    mock.patch.object(flow, "reconcile_once", lambda: self.swallowed(sup)), \
                    mock.patch.object(flow, "load_state", later), \
                    self.assertRaises(SystemExit) as stopped:
                sup.one_pass(1)
            self.assertEqual(stopped.exception.code, 128 + signal.SIGTERM)
            later.assert_not_called()
            status = json.loads((Path(tmp) / flow.SUPERVISOR_FILE).read_text())
            self.assertEqual((status["state"], status["why"], status["signal"]), ("stopped", "signal", "SIGTERM"))


if __name__ == "__main__":
    unittest.main()
