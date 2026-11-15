"""Deliveries on one base: the registry beside the git common directory, the peers `doctor --delivery` and the live
view list, the files a delivery claims, and the `peer.merged` entry a peer's merge journals (Decisions 27 to 30).

Each case is one throwaway repository with a local bare remote. Its first delivery, FX-1, is led from the main
checkout; a second, FX-9, on the same base, is led from a linked worktree of the same repository with its own record,
so both name one registry and one merge slot. A process a case starts, a supervisor or a slot holder, is stopped by
the pid it recorded.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

from tests.test_run_delivery import POSIX_ONLY, Fixture, commit, doctor_lines, installed_release, sh, stop_supervisors
from tests.test_run_delivery_supervisor import hand_off, running, supervised

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "run-delivery" / "scripts"))
import tracker_publisher  # noqa: E402

# The fixture plan, with each story naming the file its lane changes.
NAMED = {"### S1 (FX-2). Thing one (AC1). Regex `/api/one$`\n": "\nIt changes `src/one.txt`.\n",
         "### S2 (FX-3). Thing two (AC2)\n": "\nIt changes `src/two.txt`.\n"}

# Holds a base's merge slot as a delivery's check would, until it is stopped by its pid.
SLOT_HOLDER = r'''
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import delivery_state
with delivery_state.merge_slot(Path(sys.argv[2]), sys.argv[3], delivery=sys.argv[4], timeout=5):
    print("holding", flush=True)
    time.sleep(120)
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
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import delivery_state
delivery_state.append_journal(Path(sys.argv[2]), kind=sys.argv[3], summary=sys.argv[4], lane=None, subject=sys.argv[5],
                              writer="runner")
'''


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

    def hold_slot(self, fx: Fixture, delivery: str) -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-B", "-c", SLOT_HOLDER, str(fx.release / "skills/orchestrate-work/scripts"),
                                 str(fx.main), "feature/FX-1", delivery], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
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
           fx.tmp / f"record-{key}", "candidate.merged", f"Merged {story} at {head}", story)
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
        self.state(fx, fx.tmp / "record", "journal", "--kind", "delivery.closed", "--summary", "closed out")
        fx.flow("--delivery", "FX-1", "lead-close", cwd=m)
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
           fx.tmp / "record-FX-9", "candidate.merged",
           f"Recorded from a forge snapshot: merged as change request https://forge.test/pr/5 at {recovered}, "
           "not recorded by the runner", "FX-95")
        # FX-7, on another base, merges a story touching FX-2's file there: not this delivery's peer.
        sh("git", "fetch", "-q", "origin", cwd=other)
        sh("git", "checkout", "-q", "--detach", "origin/feature/FX-7", cwd=other)
        (other / "src/one.txt").write_text("one, by FX-7\n")
        commit(other, "FX-72: one")
        seven = sh("git", "rev-parse", "HEAD", cwd=other).stdout.strip()
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-7", cwd=other)
        sh(sys.executable, "-B", "-c", RUNNER_ENTRY, fx.release / "skills/orchestrate-work/scripts",
           fx.tmp / "record-FX-7", "candidate.merged", f"Merged FX-72 at {seven}", "FX-72")
        # A pass that finds the base's ref behind these merges fetches it before it reads one's files, so FX-93's merge
        # isn't read as changing FX-92's file too. It journals FX-92's and FX-95's merges once each; passes by a
        # supervisor started again journal nothing more.
        sh(sys.executable, "-B", "-c", PEER_STEP, fx.tool.parent, "FX-1", stale, cwd=m, env=fx.env)
        self.assertEqual(len(self.peer_merged(fx)), 2, self.peer_merged(fx))
        for _ in range(2):
            self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
            entries = self.peer_merged(fx)
            self.assertEqual(len(entries), 2, entries)
        self.assertEqual([e["subject"] for e in entries], ["FX-2", "FX-2"])
        self.assertEqual([e["summary"] for e in entries], [
            f"FX-9, a delivery on feature/FX-1, merged FX-92 at {touched[:10]}, changing src/one.txt, which FX-2 names",
            f"FX-9, a delivery on feature/FX-1, merged FX-95 at {recovered[:10]}, changing src/one.txt, which FX-2 names"])
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
        sh(sys.executable, "-B", "-c", RUNNER_ENTRY, fx.release / "skills/orchestrate-work/scripts",
           fx.tmp / "record-FX-9", "candidate.merged", f"Merged FX-96 at {late}", "FX-96")
        held = max(e["id"] for e in self.record(fx, fx.tmp / "record-FX-9")["journal"])
        self.state(fx, fx.tmp / "record-FX-9", "journal", "--kind", "decision.recorded", "--summary", "after FX-96")
        self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
        self.assertEqual(len(self.peer_merged(fx)), 2)
        reads = [json.loads(line) for line in (fx.tmp / "flow-log/FX-1.peers.jsonl").read_text().splitlines()]
        self.assertLess(max(read["through"] for read in reads), held)
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-1", cwd=clone)
        self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
        self.assertEqual([e["summary"] for e in self.peer_merged(fx)][2:],
                         [f"FX-9, a delivery on feature/FX-1, merged FX-96 at {late[:10]}, changing src/one.txt, "
                          "which FX-2 names"])
        # A merge entry that names no head is read past: it holds back no later merge.
        sh(sys.executable, "-B", "-c", RUNNER_ENTRY, fx.release / "skills/orchestrate-work/scripts",
           fx.tmp / "record-FX-9", "candidate.merged", "Merged FX-98", "FX-98")
        after = self.merged_on_base(fx, lead, "FX-9", "FX-99", "src/one.txt", "one, after a headless merge\n")
        self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
        self.assertEqual([e["summary"] for e in self.peer_merged(fx)][3:],
                         [f"FX-9, a delivery on feature/FX-1, merged FX-99 at {after[:10]}, changing src/one.txt, "
                          "which FX-2 names"])
        # Once FX-2 is settled, a peer's merge of its file is no open story's.
        self.patch(fx, fx.tmp / "record", "lead", "--element", "planned[subject=FX-2]", "--set",
                   json.dumps({"/stage": "settled"}))
        self.merged_on_base(fx, lead, "FX-9", "FX-97", "src/one.txt", "one, after FX-2 settled\n")
        self.flow(fx, "FX-1", "lead-run", "--once", cwd=m)
        self.assertEqual(len(self.peer_merged(fx)), 4)
        # The tracker publisher posts nothing for it.
        self.assertIsNone(tracker_publisher.UPDATES["peer.merged"])


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


if __name__ == "__main__":
    unittest.main()
