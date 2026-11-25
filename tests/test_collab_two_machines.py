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
import time
import unittest
from pathlib import Path

from tests.collab_remotes import assert_fast_forwards_only
from tests.quiet_git import quieten
from tests.test_planning_record import B_SECTION, KEY, PlanningRecordCase, journal, record
from tests.test_run_delivery import POSIX_ONLY, commit, sh
from tests.test_run_delivery_plan_review import OC, results, slug_store

RUN_QUIETLY = True  # two supervisors, each with its live view and sync loop, and real git throughout

# A write on one machine shows on the other within this many seconds: two ticks of the sync loop and one push.
REACH_SECONDS = 10
# The fields a record keeps for its own machine only, which the two machines never compare (D6's `local` rows).
LOCAL_META = ("updatedAt", "writerGeneration", "closing", "collab")


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
    """The finish line's two-machine delivery, step by step, on one pair of clones."""

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
        self.delivered(fx)
        sh("git", "switch", "-q", "-c", "lead/FX-1", cwd=fx.main)
        sh("git", "push", "-q", "-u", "origin", "lead/FX-1", cwd=fx.main)
        self.env = {**fx.env, "RUN_DELIVERY_SUPERVISOR": "on"}
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
        # The plan's branch and both seats' chains only ever moved forward on the remote.
        for ref in ("refs/heads/lead/FX-1", f"refs/agent-skills/{KEY}/{seat_a}", f"refs/agent-skills/{KEY}/{seat_b}"):
            assert_fast_forwards_only(fx.origin, ref)

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
