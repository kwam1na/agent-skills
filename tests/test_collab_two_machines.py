"""Two machines, end to end: one shared delivery written by two clones of one bare remote, from its plan's first draft
to its adoption, driven only through the commands an engineer runs and the supervisors those commands start.

Seat a is the run-delivery fixture's first checkout and seat b a second clone of its remote, each with its own state
directory. Nothing here pushes, fetches or ingests a partition: every write travels through the running supervisors'
sync loops, with `collab.pollSeconds` at 2, and reaches the other machine's record within 10 seconds. After each step
both records compose to the same state and hold the same journal origins in an order consistent with causality.
A peer's record is read from its files, never asked to ingest.
"""

from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
import unittest
from pathlib import Path

from tests import test_delivery_views_hosted as publisher_fixtures
from tests.collab_remotes import assert_fast_forwards_only
from tests.quiet_git import quieten
from tests.test_planning_record import B_SECTION, KEY, PlanningRecordCase, journal, record
from tests.test_run_delivery import POSIX_ONLY, commit, plan_delivery, sh, write_results
from tests.test_run_delivery_supervisor import hand_off, supervised
from tests.test_run_delivery_plan_review import OC, results, slug_store
from tests.test_run_delivery_tracker import FAKE_ADAPTER, WORKFLOW, blank_store

# The hosted publisher reaches the fake origin on loopback in place of the configured one, on both machines.
SHIM = """import http.client
Real = http.client.HTTPConnection
def fixture(host, port=None, timeout=None, context=None):
    assert host == "deliveries.example.com"
    return Real("127.0.0.1", {port}, timeout=timeout)
http.client.HTTPSConnection = fixture
"""

RUN_QUIETLY = True  # two supervisors, each with its live view and sync loop, and real git throughout

# A write on one machine shows on the other within this many seconds: two ticks of the sync loop and one push.
REACH_SECONDS = 10
# The fields a record keeps for its own machine only, which the two machines never compare (D6's `local` rows).
LOCAL_META = ("updatedAt", "writerGeneration", "closing", "collab")


def tracker_publisher_entry() -> dict:
    """The binding's tracker publisher, from the release the fixture runs, as lead-setup adds it."""
    return {"id": "tracker", "argv": [sys.executable, "-B", ".agent-skills/current/skills/run-delivery/scripts/"
                                      "tracker_publisher.py", "--state-dir", "{stateDirectory}"], "timeoutSeconds": 600}


def origins(folder: Path) -> list[tuple[str, int]]:
    return [(entry["origin"]["seat"], entry["origin"]["seq"]) for entry in journal(folder) if entry.get("origin")]


def shared_state(folder: Path) -> dict:
    """The record's state as both machines must agree on it: everything but the machine-local fields."""
    state = record(folder)
    state.pop("version", None)
    state["meta"] = {name: value for name, value in state["meta"].items() if name not in LOCAL_META}
    return state


def causal(folder: Path) -> bool:
    """Whether every entry follows each entry its `seen` names, and its own seat's earlier ones."""
    at: dict[tuple[str, int], int] = {}
    for index, entry in enumerate(journal(folder)):
        origin = entry.get("origin")
        if not origin:
            continue
        if origin["seq"] > 1 and at.get((origin["seat"], origin["seq"] - 1), index) >= index:
            return False
        if any(at.get((seat, seq), index) >= index for seat, seq in (origin.get("seen") or {}).items() if seq):
            return False
        at[(origin["seat"], origin["seq"])] = index
    return True


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class TwoMachinesTests(PlanningRecordCase):
    """The finish line's two-machine delivery, step by step, on one pair of clones: a TestCase on the planning
    record's fixture, which installs the release once per class and keeps git to each clone's own settings."""

    def setUp(self) -> None:
        fx = self.fx = self.fixture()
        binding = fx.main / ".agents/run-delivery.json"
        raw = json.loads(binding.read_text())
        raw["collab"] = {"pollSeconds": 2, "autoGrant": True}
        binding.write_text(json.dumps(raw, indent=2) + "\n")
        commit(fx.main, "two seats, a short poll")
        plan = fx.main / "docs/plan.md"
        plan.write_text(plan.read_text() + "\nIt comes after COLLAB-1.\n")
        commit(fx.main, "the plan names a placeholder key")
        # The fixture delivery with a binding whose queue merges for real, its record left the planning record's.
        path = plan_delivery(fx, supervised(fx))
        d = json.loads(path.read_text())
        del d["stateDirectory"]
        path.write_text(json.dumps(d, indent=2))
        # One tracker both machines post to, each through its own ledger, and one hosted origin both publish to.
        (fx.main / "tools/fake_adapter.py").write_text(FAKE_ADAPTER)
        self.tracker = fx.tmp / "tracker.json"
        store = blank_store()
        store["issues"] = {key: {"status": "Backlog", "comments": [], "markers": [], "labels": [], "parent": None,
                                 "summary": key} for key in ("FX-1", "FX-2", "FX-3")}
        self.tracker.write_text(json.dumps(store))
        conf = json.loads(binding.read_text())
        conf["tracker"] = {**conf.get("tracker", {}), "argv": [sys.executable, "-B", "tools/fake_adapter.py"]}
        binding.write_text(json.dumps(conf, indent=2) + "\n")
        bound = fx.main / ".agents/orchestration.json"
        flow = json.loads(bound.read_text())
        flow["state"]["publishers"] = [tracker_publisher_entry()]
        bound.write_text(json.dumps(flow, indent=2) + "\n")
        (fx.main / ".agents/delivery-views.json").write_text(json.dumps(publisher_fixtures.CFG, indent=2) + "\n")
        commit(fx.main, "FX-1: the record stays the planning record, with its tracker and hosted page")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        sh("git", "switch", "-q", "-c", "lead/FX-1", cwd=fx.main)
        sh("git", "push", "-q", "-u", "origin", "lead/FX-1", cwd=fx.main)
        self.origin = publisher_fixtures.ReceiverTests("test_actual_lost_response_reconciles_and_confirmation_is_fresh_"
                                                       "collection_only")
        self.origin.setUp()
        self.addCleanup(self.origin.doCleanups)
        shim = fx.tmp / "shim"
        shim.mkdir()
        (shim / "sitecustomize.py").write_text(SHIM.format(port=self.origin.server.server_port))
        self.env = {**fx.env, "RUN_DELIVERY_SUPERVISOR": "on", "FIXTURE_PUBLISH_TOKEN": publisher_fixtures.TOKEN,
                    "PYTHONPATH": str(shim), "FAKE_TRACKER_STORE": str(self.tracker), "FAKE_TRACKER_TOKEN": "good",
                    "FAKE_TRACKER_WORKFLOW": json.dumps(WORKFLOW)}
        fx.env = {**self.env, "RUN_DELIVERY_SUPERVISOR": "off"}
        self.b_repo = fx.tmp / "b"
        sh("git", "clone", "-q", "-b", "lead/FX-1", fx.origin, self.b_repo)
        quieten(self.b_repo)
        sh("git", "config", "user.name", "Ben", cwd=self.b_repo)

    # -- driving the two machines ---------------------------------------------------------------------------------

    def run_at(self, where: Path, *argv, code: int = 0) -> str:
        done, out = self.fx.flow(*argv, cwd=where, env=self.env)
        self.assertEqual(done, code, out)
        return out

    def stop_supervisors(self, *folders: Path) -> None:
        """Each supervisor the commands started, and its hosted owner, stopped by the pid its lock records."""
        for folder in folders:
            for lock in folder.parent.glob("flow-log/*.supervisor.lock"):
                with_pid = json.loads(lock.read_text() or "{}").get("pid") if lock.is_file() else None
                if isinstance(with_pid, int) and with_pid > 1:
                    try:
                        os.killpg(with_pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        pass
            owner = folder / "hosted-owner.json"
            pid = json.loads(owner.read_text()).get("pid") if owner.is_file() else None
            if isinstance(pid, int) and pid > 1:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def converged(self, what: str) -> None:
        """Within the reach bound, both records hold the same origins, causally ordered, and the same shared state."""
        deadline = time.monotonic() + REACH_SECONDS
        while True:
            same = sorted(origins(self.a)) == sorted(origins(self.b)) and shared_state(self.a) == shared_state(self.b)
            if same and causal(self.a) and causal(self.b):
                return
            if time.monotonic() >= deadline:
                self.fail(f"{what}: the two records didn't converge within {REACH_SECONDS}s: "
                          f"a {sorted(set(origins(self.a)) ^ set(origins(self.b)))} differ, states equal: "
                          f"{shared_state(self.a) == shared_state(self.b)}")
            time.sleep(0.25)

    def reaches(self, folder: Path, kind: str, what: str, **data) -> dict:
        """The entry of `kind` (matching `data`) that reaches `folder` within the reach bound."""
        deadline = time.monotonic() + REACH_SECONDS
        while True:
            found = [entry for entry in journal(folder) if entry["kind"] == kind
                     and all(entry["data"].get(name) == value for name, value in data.items())]
            if found:
                return found[-1]
            if time.monotonic() >= deadline:
                self.fail(f"{what}: no {kind} reached {folder} within {REACH_SECONDS}s")
            time.sleep(0.25)

    def edit(self, where: Path, old: str, new: str) -> None:
        path = where / "docs/plan.md"
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new))

    # -- the delivery ---------------------------------------------------------------------------------------------

    def test_a_delivery_two_machines_share_from_its_first_draft(self) -> None:
        fx, b_repo = self.fx, self.b_repo
        # plan-open on the first clone makes seat a, holding every scope, and starts its supervisor.
        out = self.run_at(fx.main, "plan-open", "--plan", "docs/plan.md", "--key", KEY)
        self.assertIn("supervisor: running", out)
        self.a = Path(re.search(r"planning record of \S+ at (\S+):", out).group(1))
        self.b = b_repo / self.a.relative_to(fx.main.resolve())
        self.addCleanup(self.stop_supervisors, self.a, self.b)
        seat_a = self.tool_seat(self.a)
        # collab join on the second builds its record from a's partition, starts its supervisor, and asks for a seat.
        out = self.run_at(b_repo, "collab", "join", "--key", KEY)
        self.assertIn("supervisor: running", out)
        seat_b = self.tool_seat(self.b)
        self.assertNotEqual(seat_a, seat_b)
        self.assertEqual(journal(self.b)[0]["origin"]["seat"], seat_a)
        self.reaches(self.a, "seat.requested", "b's request reaches a", seat=seat_b)
        self.run_at(fx.main, "collab", "admit", "--seat", seat_b)
        self.reaches(self.b, "seat.admitted", "a's admission reaches b", seat=seat_b)
        self.converged("after the admission")
        # a passes b a section; each seat edits a section it holds, concurrently, and both edits compose.
        self.run_at(fx.main, "collab", "give", "--scope", f"plan:{B_SECTION}", "--to", seat_b)
        self.reaches(self.b, "scope.transferred", "the section reaches b", scope=f"plan:{B_SECTION}")
        self.converged("after the section's transfer")
        self.edit(fx.main, '| Copy | "Couldn\'t be loaded." |', "| Copy | a's copy |")
        self.edit(b_repo, "- Scenarios: it renders.", "- Scenarios: it renders, b says.")
        self.run_at(fx.main, "plan-sync", "--plan", "docs/plan.md", "--message", "a's decision")
        self.run_at(b_repo, "plan-sync", "--plan", "docs/plan.md", "--message", "b's scenario")
        self.run_at(fx.main, "plan-sync", "--plan", "docs/plan.md")
        for where in (fx.main, b_repo):
            text = (where / "docs/plan.md").read_text()
            self.assertIn("| Copy | a's copy |", text)
            self.assertIn("- Scenarios: it renders, b says.", text)
        # An edit to a section b doesn't hold is refused before anything is written, naming the section.
        self.edit(b_repo, "| Copy | a's copy |", "| Copy | b's copy |")
        out = self.run_at(b_repo, "plan-sync", "--plan", "docs/plan.md", code=2)
        self.assertIn("plan.section_not_held", out)
        self.assertIn("## Decisions", out)
        sh("git", "checkout", "-q", "--", "docs/plan.md", cwd=b_repo)
        # Feedback raised by b on a's section reaches a, and a, its holder, settles it.
        self.run_at(b_repo, "collab", "feedback", "add", "--anchor", "## Decisions", "--text", "Say why the copy changed.",
                    "--blocking")
        raised = self.reaches(self.a, "feedback.raised", "b's feedback reaches a")
        self.run_at(fx.main, "collab", "feedback", "resolve", "--id", raised["id"], "--outcome", "answered", "--reason",
                    "The old copy blamed the shopper.")
        self.reaches(self.b, "feedback.resolved", "a's settlement reaches b")
        self.converged("after the feedback")
        # Plan review aligns on the plan as it stands, and both approver seats endorse that commit.
        self.review(fx)
        sh("git", "pull", "-q", "--ff-only", cwd=b_repo)
        current = sh("git", "rev-list", "-1", "HEAD", "--", "docs/plan.md", cwd=fx.main).stdout.strip()
        # b endorses first; a, endorsing once b's has reached it, rebuilds the plan's view with both.
        self.run_at(b_repo, "lead-plan", "endorse", "--plan", "docs/plan.md")
        self.reaches(self.a, "plan.endorsed", "b's endorsement reaches a", commit=current)
        self.run_at(fx.main, "lead-plan", "endorse", "--plan", "docs/plan.md")
        self.converged("after both endorsements")
        plan = json.loads((self.a / "plan.json").read_text())["preview"]
        self.assertEqual(plan["approval"], "endorsed")
        # lead-init adopts the planning record in place: one record, one journal, the same seats.
        hosted = (self.a / "hosted-identity.json").read_bytes() if (self.a / "hosted-identity.json").exists() else None
        self.run_at(fx.main, "lead-init")
        self.assertEqual(record(self.a)["meta"]["phase"], "delivery")
        self.assertEqual((self.a / "hosted-identity.json").read_bytes() if hosted is not None else None, hosted)
        # lead-init writes the delivery's seed over the record in place; b's record shows it, as a delivery.
        self.converged("after lead-init")
        self.assertEqual(record(self.b)["meta"]["phase"], "delivery")
        # The two lanes split between the seats: a, holding delivery, assigns both and passes lane-2 to b.
        for key, lane in (("FX-2", "lane-1"), ("FX-3", "lane-2")):
            self.run_at(fx.main, "lead-assign", key, "--lane", lane, "--title", lane)
        self.run_at(fx.main, "collab", "give", "--scope", "lane:lane-2", "--to", seat_b)
        self.reaches(self.b, "scope.transferred", "lane-2 reaches b", scope="lane:lane-2")
        self.converged("after the lanes split")
        # b's lane works in b's clone, and b, holding its lane, accepts its hand-off; a's queue merges it.
        self.lane_on(b_repo, "FX-3", "lane-2")
        self.run_at(b_repo, "lead-accept", "FX-3")
        self.assertEqual(self.merged("FX-3"), seat_a, "a's queue merges b's candidate")
        self.converged("after a's queue merged b's candidate")
        # The queue moves to b, whose supervisor starts serving it; a's lane hands off, a accepts, and b's queue merges.
        self.run_at(fx.main, "collab", "give", "--scope", "queue", "--to", seat_b, "--wait", "60")
        self.reaches(self.b, "scope.transferred", "the queue reaches b", scope="queue")
        hand_off(fx, "FX-2", "lane-1", assign=False)
        self.run_at(fx.main, "lead-accept", "FX-2")
        self.assertEqual(self.merged("FX-2"), seat_b, "b's queue merges a's candidate")
        self.converged("after b's queue merged a's candidate")
        # delivery moves to b, whose supervisor takes the hosted identity a carried and publishes as it: the public
        # link is unchanged, and no tracker update is posted twice.
        identity = json.loads((self.a / "hosted-identity.json").read_text())
        self.run_at(fx.main, "collab", "give", "--scope", "delivery", "--to", seat_b, "--wait", "60")
        self.reaches(self.b, "scope.transferred", "delivery reaches b", scope="delivery")
        self.converged("after delivery moved to b")
        self.taken(identity)
        self.posted_once()
        for scope in ("delivery", "queue", "lane:lane-2"):
            self.run_at(b_repo, "collab", "give", "--scope", scope, "--to", seat_a, "--wait", "60")
            self.reaches(self.a, "scope.transferred", f"{scope} comes back to a", scope=scope, to=seat_a)
        self.converged("after the hand-back")
        # The plan's branch and both seats' chains only ever moved forward on the remote.
        for ref in ("refs/heads/lead/FX-1", f"refs/agent-skills/{KEY}/{seat_a}", f"refs/agent-skills/{KEY}/{seat_b}"):
            assert_fast_forwards_only(fx.origin, ref)

    def taken(self, identity: dict) -> None:
        """b's supervisor writes the identity a carried as its own and publishes under it, within its next passes."""
        deadline = time.monotonic() + 60
        path = self.b / "hosted-identity.json"
        while not (path.is_file() and json.loads(path.read_text()).get("publicId") == identity["publicId"]):
            if time.monotonic() >= deadline:
                self.fail(f"b didn't take a's hosted identity within 60s: {path.read_text() if path.is_file() else None}")
            time.sleep(0.5)

    def posted_once(self) -> None:
        """Every tracker marker, a comment's or a transition's, was posted once, by whichever seat held delivery."""
        store = json.loads(self.tracker.read_text())
        posted = [marker for issue in store["issues"].values()
                  for marker in [*issue["markers"], *(m for comment in issue["comments"] for m in comment["markers"])]]
        self.assertTrue(posted, "the tracker publisher posted nothing")
        self.assertEqual(sorted(posted), sorted(set(posted)), "a tracker update was posted twice")

    def merged(self, key: str) -> str:
        """`key`'s candidate merges on the base and its merge reaches both machines, within the queue's own time: the
        seat whose queue merged it."""
        deadline = time.monotonic() + 120
        while True:
            found = [[entry for entry in journal(folder) if entry["kind"] == "candidate.merged" and entry.get("subject") == key]
                     for folder in (self.a, self.b)]
            if all(found):
                self.assertEqual(found[0][0]["origin"], found[1][0]["origin"])
                return found[0][0]["origin"]["seat"]
            if time.monotonic() >= deadline:
                self.fail(f"{key} didn't merge and reach both machines within 120s")
            time.sleep(0.5)

    def lane_on(self, root: Path, key: str, lane: str) -> Path:
        """A lane's whole flow for `key` in the clone at `root`: its checkout, its change, its gate, one approved
        review round, its draft pull request and its hand-off, as the fixture's lanes run it on the first clone."""
        fx = self.fx
        sh("git", "fetch", "-q", "origin", cwd=root)
        wt = root / "lanes" / key
        sh("git", "worktree", "add", "-q", "--no-track", "-b", f"feature/{key}", wt, "origin/feature/FX-1", cwd=root)
        name = {"lane-1": "one", "lane-2": "two"}[lane]
        (wt / f"src/{name}.txt").write_text(f"{name}\n")
        commit(wt, f"{key}: {name}")
        review = wt / ".orchestration/review" / key
        review.mkdir(parents=True, exist_ok=True)
        (review / "context.md").write_text("- fixture evidence\n")
        for argv in (("gate", key), ("review-prepare", key, "--round", "1")):
            code, out = fx.flow(*argv, cwd=wt)
            self.assertEqual(code, 0, out)
        meta = json.loads((review / "round-1.meta.json").read_text())
        write_results(review, 1, key, [entry for entry in fx.lenses(wt) if entry[0] in meta["lenses"]])
        code, out = fx.flow("review-reduce", key, "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        sh("git", "push", "-q", "-u", "origin", f"HEAD:feature/{key}", cwd=wt)
        body = fx.tmp / f"{key}.md"
        body.write_text(f"{key}\n")
        code, out = fx.flow("open-pr", key, "--title", f"{key}: {name}", "--body-file", body, cwd=wt)
        self.assertEqual(code, 0, out)
        url = next(u for u, pr in json.loads((fx.tmp / "forge.json").read_text()).items() if pr["source"] == f"feature/{key}")
        code, out = fx.flow("handoff", key, "--pr", url, cwd=wt)
        self.assertEqual(code, 0, out)
        return wt

    def review(self, fx) -> str:
        """One aligned round of the plan's own review, kept beside the plan and pushed: the commit it aligned on."""
        code, out = fx.flow("plan-review", "prepare", "--plan", "docs/plan.md", "--lenses", OC,
                            cwd=fx.main, env=self.env)
        self.assertEqual(code, 0, out)
        source = slug_store(fx, "fixture-plan")
        results(fx, 1, {}, source, "fixture-plan.plan")
        code, out = fx.flow("plan-review", "reduce", "--plan", "docs/plan.md", cwd=fx.main, env=self.env)
        self.assertEqual(code, 0, out)
        self.kept_review(fx)
        return json.loads((source / "round-1.json").read_text())["candidate"]

    def tool_seat(self, folder: Path) -> str:
        found = sh("git", "config", "--local", "--get", f"agent-skills.seat.{KEY}", cwd=folder).stdout.strip()
        self.assertRegex(found, r"^s-[0-9a-f]{12}$")
        return found


if __name__ == "__main__":
    unittest.main()
