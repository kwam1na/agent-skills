"""The planning record: `plan-open` opens a delivery's shared record from its plan's first draft, `lead-setup` and
`lead-init` take it over in place, the plan's sections are checked against the seat that changes them, and the plan is
approved jointly, by every approver seat endorsing one commit.

The commands run through run_delivery.py's own command line in the run-delivery fixture repository, whose first
checkout is seat a. A second clone of its remote is seat b, which joins the record the way the sync loop would carry
it: a's partition pushed, b's fetched and ingested. A peer's record is read from its files, never asked to ingest.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.collab_remotes import assert_fast_forwards_only
from tests.quiet_git import quieten
from tests.test_run_delivery import PATTERN, POSIX_ONLY, ROOT, Fixture, commit, installed_release, plan_delivery, \
    setup_delivery, sh
from tests.test_run_delivery_plan_review import OC, results, slug_store, store, with_slug

RUN_QUIETLY = True  # every case runs the tool's command line and real git, several times

SCRIPTS = ROOT / "skills/orchestrate-work/scripts"
sys.path.insert(0, str(SCRIPTS))
import delivery_state as state_tool  # noqa: E402  (the corpus's own state tool, as the tests of the record use it)

collab = state_tool._collab()
from tests.test_plan_sync import PLAN_SYNC as plan_sync  # noqa: E402  (loaded without writing bytecode)
KEY = "FX-1"
QUEUE_GIT = re.compile(r"^GIT_(AUTHOR|COMMITTER)_(NAME|EMAIL|DATE)$|^GIT_CONFIG_(COUNT|PARAMETERS|KEY_\d+|VALUE_\d+)$")


def own_git(env: dict[str, str]) -> dict[str, str]:
    """The environment with nothing the merge queue passes its checks' git: each clone's own identity and settings."""
    return {name: value for name, value in env.items() if not QUEUE_GIT.match(name)}


def files(folder: Path) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


def journal(folder: Path) -> list[dict]:
    return [json.loads(line) for line in (folder / "journal.jsonl").read_text().splitlines()]


def record(folder: Path) -> dict:
    return json.loads((folder / "state.json").read_text())


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class PlanningRecordCase(unittest.TestCase):
    """The installed release and a fixture repository per case, with git reading only each clone's own settings."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)
        patcher = mock.patch.dict(os.environ, own_git(dict(os.environ)), clear=True)
        patcher.start()
        cls.addClassCleanup(patcher.stop)

    def fixture(self, slug: str = "fixture-plan") -> Fixture:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        fx.env = own_git(fx.env)
        # As every test remote is (D18): each ref update recorded, and a forced one allowed to land.
        sh("git", "config", "core.logAllRefUpdates", "always", cwd=fx.origin)
        sh("git", "config", "receive.denyNonFastForwards", "false", cwd=fx.origin)
        with_slug(fx, "docs/plan.md", slug)
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        return fx

    def flow(self, fx: Fixture, *argv, cwd: Path | None = None) -> tuple[int, str]:
        return fx.flow(*argv, cwd=cwd or fx.main)

    def opened(self, fx: Fixture, key: str = KEY, plan: str = "docs/plan.md") -> Path:
        code, out = self.flow(fx, "plan-open", "--plan", plan, "--key", key)
        self.assertEqual(code, 0, out)
        folder = Path(re.search(r"planning record of \S+ at (\S+):", out).group(1))
        self.assertTrue((folder / "state.json").is_file(), out)
        return folder

    def review(self, fx: Fixture) -> str:
        """One aligned round of the plan's own review: the commit it aligned on."""
        code, out = fx.flow("plan-review", "prepare", "--plan", "docs/plan.md", "--lenses", OC, cwd=fx.main)
        self.assertEqual(code, 0, out)
        source = slug_store(fx, "fixture-plan")
        results(fx, 1, {}, source, "fixture-plan.plan")
        code, out = fx.flow("plan-review", "reduce", "--plan", "docs/plan.md", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.kept_review(fx)
        return json.loads((source / "round-1.json").read_text())["candidate"]

    def kept_review(self, fx: Fixture) -> None:
        """The review's record beside the plan, committed and pushed as a lead keeps it."""
        commit(fx.main, "the plan review's record")
        branch = sh("git", "branch", "--show-current", cwd=fx.main).stdout.strip()
        sh("git", "push", "-q", "origin", branch, cwd=fx.main)

    def delivered(self, fx: Fixture) -> Path:
        """lead-setup's delivery file, completed as the fixture completes it, with its record left where it is."""
        path = plan_delivery(fx)
        d = json.loads(path.read_text())
        del d["stateDirectory"]
        path.write_text(json.dumps(d, indent=2))
        commit(fx.main, "FX-1: the record stays the planning record")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        return path


class PlanOpenTests(PlanningRecordCase):
    """plan-open's preconditions, each refused with the record left as it was, and the record it opens."""

    def test_an_uncommitted_plan_one_without_a_review_slug_and_an_existing_record_are_refused(self) -> None:
        fx = self.fixture()
        plan = fx.main / "docs/plan.md"
        plan.write_text(plan.read_text() + "\nAn edit not committed yet.\n")
        code, out = self.flow(fx, "plan-open", "--plan", "docs/plan.md", "--key", KEY)
        self.assertEqual(code, 2, out)
        self.assertIn("plan.uncommitted", out)
        sh("git", "checkout", "-q", "--", "docs/plan.md", cwd=fx.main)
        (fx.main / "docs/bare.md").write_text("# A plan with no front matter\n")
        commit(fx.main, "a bare plan")
        code, out = self.flow(fx, "plan-open", "--plan", "docs/bare.md", "--key", KEY)
        self.assertEqual(code, 2, out)
        self.assertIn("plan.no_review_slug", out)
        self.assertFalse((fx.main / ".orchestration/deliveries").exists())
        # An unshared record already kept for the key stays exactly as it is.
        folder = self.opened(fx)
        unshared = Path(str(folder).replace(f"/{KEY}/", "/FX-9/"))
        unshared.mkdir(parents=True)
        state_tool.init(unshared, "An unshared delivery")
        before = files(unshared)
        with_slug(fx, "docs/other.md", "other-plan", "# Other\n")
        code, out = self.flow(fx, "plan-open", "--plan", "docs/other.md", "--key", "FX-9")
        self.assertEqual(code, 2, out)
        self.assertIn("collab.record_exists", out)
        self.assertEqual(files(unshared), before)
        # The shared one too: a plan has one planning record, and a key one record.
        shared = files(folder)
        for argv in (("--plan", "docs/plan.md", "--key", "FX-8"), ("--plan", "docs/other.md", "--key", KEY)):
            code, out = self.flow(fx, "plan-open", *argv)
            self.assertEqual(code, 2, out)
            self.assertIn("collab.record_exists", out)
        self.assertEqual(files(folder), shared)

    def test_plan_open_makes_exactly_one_admitted_approver_seat_holding_every_scope(self) -> None:
        fx = self.fixture()
        folder = self.opened(fx)
        state, entries = record(folder), journal(folder)
        seat = state_tool.local_seat(folder, KEY)
        self.assertEqual(state["meta"]["phase"], "planning")
        self.assertEqual(state["meta"]["planFile"], "docs/plan.md")
        self.assertEqual(list(state["meta"]["collab"]["seats"]), [seat])
        about = state["meta"]["collab"]["seats"][seat]
        self.assertEqual((about["admitted"], about["approver"]), (True, True))
        self.assertEqual((entries[0]["id"], entries[0]["kind"]), (1, "record.started"))
        self.assertEqual(state.get("planned", []), [])
        for scope in ("delivery", "queue", "lane:lane-1", "plan:## Decisions", "plan:(preamble)"):
            self.assertEqual(state_tool.holder(entries, scope), seat, scope)



class PlanningSupervisorTests(PlanningRecordCase):
    """plan-open starts the planning record's supervisor: its live view and the sync loop, and no queue. Hosted
    publishing is its own owner process, which the supervisor starts as it starts for any record; the pass's publish
    step, the binding's publishers with the tracker's among them, is left out."""

    def test_the_planning_supervisor_runs_the_view_and_the_sync_loop_and_passes_over_the_views_only(self) -> None:
        fx = self.fixture()
        env = {**fx.env, "RUN_DELIVERY_SUPERVISOR": "on"}
        code, out = fx.flow("plan-open", "--plan", "docs/plan.md", "--key", KEY, cwd=fx.main, env=env)
        self.assertEqual(code, 0, out)
        self.assertIn("supervisor: running; live view http://", out)
        folder = Path(re.search(r"planning record of \S+ at (\S+):", out).group(1))
        lock = folder.parent / "flow-log" / f"{KEY}.supervisor.lock"
        children = {child["name"] for child in json.loads(lock.read_text())["children"]}
        self.assertEqual(children, {"live", "collab"})
        # The sync loop pushes the first seat's partition, as nothing else does.
        ref = f"refs/agent-skills/{KEY}/{state_tool.local_seat(folder, KEY)}"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not sh("git", "ls-remote", fx.origin, ref, cwd=fx.tmp).stdout:
            time.sleep(0.5)
        self.assertTrue(sh("git", "ls-remote", fx.origin, ref, cwd=fx.tmp).stdout, "the sync loop pushed nothing")
        passes = []
        while time.monotonic() < deadline and not passes:
            log = folder.parent / "flow-log" / f"{KEY}.supervisor.jsonl"
            passes = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            time.sleep(0.5)
        self.assertTrue(passes, "no pass was logged")
        steps = set(passes[0]) - {"pass", "startedAt", "finishedAt", "at", "delivery", "laneWords"}
        self.assertEqual(steps, {"needs", "render", "latest", "close"}, passes[0])
        self.assertEqual(passes[0]["close"], "planning: nothing to close")


class AdoptionTests(PlanningRecordCase):
    """lead-setup and lead-init take the planning record over in place: one record from the first draft on."""

    def refused(self, fx: Fixture, folder: Path, code: str, *argv: str) -> None:
        """The command exits 2 with `code`, and the planning record's files are as they were."""
        kept = files(folder)
        done, out = self.flow(fx, *argv)
        self.assertEqual((done, code in out), (2, True), out)
        self.assertEqual(files(folder), kept)

    def test_lead_setup_and_lead_init_adopt_the_planning_record_keeping_its_journal_partitions_and_hosted_identity(
            self) -> None:
        fx = self.fixture()
        folder = self.opened(fx)
        self.review(fx)
        code, out = self.flow(fx, "lead-plan", "endorse", "--plan", "docs/plan.md")
        self.assertEqual(code, 0, out)
        collab.push(folder, remote="origin")
        seat = state_tool.local_seat(folder, KEY)
        ref = f"refs/agent-skills/{KEY}/{seat}"
        partition = sh("git", "rev-parse", ref, cwd=fx.origin).stdout.strip()
        (folder / "hosted-identity.json").write_text('{"publicId": "a fixture\'s"}\n')
        # A delivery file keeping its record elsewhere would start a second record: refused.
        path = plan_delivery(fx)
        self.refused(fx, folder, "collab.record_elsewhere", "lead-init")
        d = json.loads(path.read_text())
        del d["stateDirectory"]
        path.write_text(json.dumps(d, indent=2))
        commit(fx.main, "FX-1: the record stays the planning record")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        # --replace would archive the shared record; and the plan's record keeps its key through approval.
        self.refused(fx, folder, "collab.record_shared", "lead-init", "--replace")
        setup = ("--plan", "docs/plan.md", "--base", "feature/FX-1", "--pattern", PATTERN, "--no-authority")
        self.refused(fx, folder, "collab.key_differs", "lead-setup", "FX-7", *setup)
        self.assertFalse((fx.main / ".agents/deliveries/FX-7.json").exists())
        # lead-setup says it adopts the record; drafted again here, then put back as the fixture completed it.
        code, out = self.flow(fx, "lead-setup", KEY, *setup, "--force")
        self.assertEqual(code, 0, out)
        self.assertIn(f"adopting the planning record of {KEY} at {folder} in place", out)
        sh("git", "checkout", "-q", "--", str(path.relative_to(fx.main)), cwd=fx.main)
        # A plan edited since its review aligned isn't approved, however freshly endorsed; put back, it is.
        plan = fx.main / "docs/plan.md"
        plan.write_text(plan.read_text() + "\nAn edit no review saw.\n")
        commit(fx.main, "an unreviewed edit")
        self.assertEqual(self.flow(fx, "lead-plan", "endorse", "--plan", "docs/plan.md")[0], 0)
        self.refused(fx, folder, "plan.not_aligned", "lead-init")
        sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "revert", "--no-edit", "HEAD", cwd=fx.main)
        self.assertEqual(self.flow(fx, "lead-plan", "endorse", "--plan", "docs/plan.md")[0], 0)
        before, planning = journal(folder), (folder / "journal.jsonl").read_bytes()
        code, out = self.flow(fx, "lead-init")
        self.assertEqual(code, 0, out)
        state, entries = record(folder), journal(folder)
        # One record: the planning entries as they were, then lead-init's own, continuing the same ids.
        self.assertTrue((folder / "journal.jsonl").read_bytes().startswith(planning))
        self.assertEqual(entries[:len(before)], before)
        self.assertEqual([entry["id"] for entry in entries], list(range(1, len(entries) + 1)))
        self.assertGreater(len(entries), len(before))
        self.assertEqual([entry["kind"] for entry in entries].count("record.started"), 1)
        self.assertEqual(state["meta"]["phase"], "delivery")
        self.assertEqual(state["meta"]["collab"]["key"], KEY)
        self.assertEqual([item["subject"] for item in state["planned"]], ["FX-2", "FX-3"])
        self.assertEqual((folder / "hosted-identity.json").read_text(), '{"publicId": "a fixture\'s"}\n')
        self.assertEqual(sh("git", "rev-parse", ref, cwd=fx.origin).stdout.strip(), partition)
        # meta.phase moves to delivery once: a second lead-init finds a delivery's record, and leaves it.
        kept = files(folder)
        code, out = self.flow(fx, "lead-init")
        self.assertEqual(code, 2, out)
        self.assertIn("already holds a record", out)
        self.assertEqual(files(folder), kept)



def generation_tools(generation: Path):
    """The installed generation's own state tool and collab module, in process: what its commands write with."""
    script = generation / "skills/orchestrate-work/scripts/delivery_state.py"
    spec = importlib.util.spec_from_file_location("delivery_state_of_the_generation", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module, module._collab()


# b's section, which names the placeholder key plan-rekey replaces.
B_SECTION = "### S2 (FX-3). Thing two (AC2)"


class TwoSeatTests(PlanningRecordCase):
    """a opens the record and holds every scope but b's section; b is a second clone of the same remote."""

    def two_seats(self) -> tuple[Fixture, Path, Path, Path, str]:
        fx = self.fixture()
        tool, link = generation_tools(self.generation)
        plan = fx.main / "docs/plan.md"
        plan.write_text(plan.read_text() + "\nIt comes after COLLAB-1.\n")
        commit(fx.main, "the plan names a placeholder key")
        self.delivered(fx)
        sh("git", "switch", "-q", "-c", "lead/FX-1", cwd=fx.main)
        sh("git", "push", "-q", "-u", "origin", "lead/FX-1", cwd=fx.main)
        a = self.opened(fx)
        aligned = self.review(fx)
        b_repo = fx.tmp / "b"
        sh("git", "clone", "-q", "-b", "lead/FX-1", fx.origin, b_repo)
        quieten(b_repo)
        sh("git", "config", "user.name", "Ben", cwd=b_repo)
        b = b_repo / a.relative_to(fx.main.resolve())
        link.push(a, remote="origin")
        link.fetch(b, remote="origin", key=KEY)
        link.ingest(b, key=KEY)
        link.request_seat(b, label="Ben")
        link.push(b, remote="origin")
        for there, then in ((a, lambda: link.admit_seat(a, tool.local_seat(b, KEY))),
                            (a, lambda: link.give_scope(a, f"plan:{B_SECTION}", tool.local_seat(b, KEY))),
                            (b, None)):
            link.fetch(there, remote="origin", key=KEY)
            link.ingest(there, key=KEY)
            if then:
                then()
                link.push(there, remote="origin")
        self.exchange = lambda *folders: [step for folder in folders for step in (
            link.push(folder, remote="origin"), link.fetch(folder, remote="origin", key=KEY),
            link.ingest(folder, key=KEY))]
        return fx, a, b_repo, b, aligned

    def test_sections_rekeying_and_joint_approval_across_two_seats(self) -> None:
        fx, a, b_repo, b, aligned = self.two_seats()
        seat_b = state_tool.local_seat(b, KEY)
        self.assertEqual(state_tool.holder(journal(a), f"plan:{B_SECTION}"), seat_b)
        self.assertEqual(state_tool.holder(journal(b), f"plan:{B_SECTION}"), seat_b)
        surfaces = lambda: (sh("git", "rev-parse", "HEAD", cwd=b_repo).stdout, (b_repo / "docs/plan.md").read_text(),
                            sh("git", "ls-remote", fx.origin, "refs/heads/lead/FX-1", cwd=fx.tmp).stdout)
        # b edits a section it doesn't hold: refused before any commit or push, naming the request command.
        text = (b_repo / "docs/plan.md").read_text()
        (b_repo / "docs/plan.md").write_text(text.replace('| Copy | "Couldn\'t be loaded." |', "| Copy | b's |"))
        before = surfaces()
        code, out = self.flow(fx, "plan-sync", "--plan", "docs/plan.md", cwd=b_repo)
        self.assertEqual(code, 2, out)
        self.assertIn("plan.section_not_held", out)
        self.assertIn("## Decisions", out)
        self.assertIn("scope request --scope 'plan:## Decisions'", out)
        self.assertEqual(surfaces(), before)
        # The allowed case beside it: an edit inside b's own section lands.
        (b_repo / "docs/plan.md").write_text(text.replace("- Scenarios: it renders.", "- Scenarios: it renders, b says."))
        code, out = self.flow(fx, "plan-sync", "--plan", "docs/plan.md", cwd=b_repo)
        self.assertEqual(code, 0, out)
        self.assertIn("it renders, b says.", sh("git", "show", "lead/FX-1:docs/plan.md", cwd=fx.origin).stdout)
        sh("git", "pull", "-q", "--ff-only", cwd=fx.main)
        # b's edit is a change since the review aligned: another round aligns on it.
        code, out = fx.flow("plan-review", "prepare", "--plan", "docs/plan.md", cwd=fx.main)
        self.assertEqual(code, 0, out)
        results(fx, 2, {}, slug_store(fx, "fixture-plan"), "fixture-plan.plan")
        code, out = fx.flow("plan-review", "reduce", "--plan", "docs/plan.md", cwd=fx.main)
        self.assertEqual(code, 0, out)
        aligned = json.loads((slug_store(fx, "fixture-plan") / "round-2.json").read_text())["candidate"]
        self.kept_review(fx)
        # Re-keying is the delivery holder's: b's is refused, a's lands, re-keying b's section.
        code, out = self.flow(fx, "plan-rekey", "--plan", "docs/plan.md", "--map", "COLLAB-1=FX-2", cwd=b_repo)
        self.assertEqual(code, 2, out)
        self.assertIn("scope.not_held", out)
        code, out = self.flow(fx, "plan-rekey", "--plan", "docs/plan.md", "--map", "COLLAB-1=FX-2")
        self.assertEqual(code, 0, out)
        rekey = next(entry for entry in journal(a) if entry["kind"] == "plan.rekeyed")
        landed = sh("git", "rev-parse", "lead/FX-1", cwd=fx.origin).stdout.strip()
        self.assertEqual(rekey["data"]["to"], landed)
        self.assertIn("It comes after FX-2.", sh("git", "show", "lead/FX-1:docs/plan.md", cwd=fx.origin).stdout)
        # An ordinary commit to the section the re-keying touched is still checked: a doesn't hold it.
        plan = fx.main / "docs/plan.md"
        plan.write_text(plan.read_text().replace("It comes after FX-2.", "It comes after FX-2, a says."))
        code, out = self.flow(fx, "plan-sync", "--plan", "docs/plan.md")
        self.assertEqual(code, 2, out)
        self.assertIn(f"plan.section_not_held", out)
        self.assertIn(B_SECTION, out)
        sh("git", "checkout", "-q", "--", "docs/plan.md", cwd=fx.main)
        assert_fast_forwards_only(fx.origin, "refs/heads/lead/FX-1")
        # Joint approval: b endorsed the aligned commit before the re-keying, a the re-keyed one.
        code, out = self.flow(fx, "lead-plan", "endorse", "--plan", "docs/plan.md")
        self.assertEqual(code, 0, out)
        code, out = self.flow(fx, "lead-init")
        self.assertEqual(code, 2, out)
        self.assertIn("plan.endorsement_missing", out)
        self.assertIn("Ben", out)
        sh("git", "pull", "-q", "--ff-only", cwd=b_repo)
        sh("git", "checkout", "-q", aligned, cwd=b_repo)
        code, out = self.flow(fx, "lead-plan", "endorse", "--plan", "docs/plan.md", cwd=b_repo)
        self.assertEqual(code, 0, out)
        sh("git", "switch", "-q", "lead/FX-1", cwd=b_repo)
        self.exchange(b, a)
        kept = files(a)
        code, out = self.flow(fx, "lead-init")
        self.assertEqual(code, 2, out)
        self.assertIn("plan.endorsement_stale", out)
        self.assertEqual(files(a), kept)
        code, out = self.flow(fx, "lead-plan", "endorse", "--plan", "docs/plan.md", cwd=b_repo)
        self.assertEqual(code, 0, out)
        self.exchange(b, a, b)
        # Approved, but b doesn't hold delivery: b's lead-init is refused before it stops anything or writes.
        kept = files(b)
        code, out = self.flow(fx, "lead-init", cwd=b_repo)
        self.assertEqual(code, 2, out)
        self.assertIn("scope.not_held", out)
        self.assertEqual(files(b), kept)
        code, out = self.flow(fx, "lead-init")
        self.assertEqual(code, 0, out)
        self.assertEqual(record(a)["meta"]["phase"], "delivery")



A, B, C = "s-aaaaaaaaaaaa", "s-bbbbbbbbbbbb", "s-cccccccccccc"
X, Y, Z = "1" * 40, "2" * 40, "3" * 40
ALIGNED = "# Plan\n\n## Stories\n\n### COLLAB-1. One\n\nAfter COLLAB-2.\n"
TEXTS = {X: ALIGNED, Y: plan_sync.rekeyed(ALIGNED, {"COLLAB-1": "FX-2", "COLLAB-2": "FX-3"}),
         Z: plan_sync.rekeyed(ALIGNED, {"COLLAB-1": "FX-2", "COLLAB-2": "FX-3"}) + "A hand edit.\n"}


class JointApprovalTests(unittest.TestCase):
    """D11's four rules over a record's own entries, in their order: the first a record fails is its refusal."""

    def setUp(self) -> None:
        self.entries: list[dict] = []
        self.seats = {A: {"label": "Ann", "admitted": True, "approver": True},
                      B: {"label": "Ben", "admitted": True, "approver": True},
                      C: {"label": "Cy", "admitted": True, "approver": False},
                      # Asked to join as an approver, not admitted yet: it holds nothing up either.
                      "s-dddddddddddd": {"label": "Di", "admitted": False, "approver": True}}

    def add(self, seat: str, kind: str, data: dict, **links) -> dict:
        entry = {"id": len(self.entries) + 1, "kind": kind, "data": data,
                 "origin": {"seat": seat, "seq": len(self.entries) + 1, "seen": {}}, **links}
        self.entries.append(entry)
        return entry

    def rekey(self, to: str = Y) -> None:
        self.add(A, "plan.rekeyed", {"from": X, "to": to, "map": {"COLLAB-1": "FX-2", "COLLAB-2": "FX-3"}})

    def endorse(self, *pairs: tuple[str, str]) -> None:
        for seat, commit in pairs:
            self.add(seat, "plan.endorsed", {"commit": commit})

    def refused(self, current: str = Y, aligned: str | None = X) -> str | None:
        refusal = plan_sync.approval_refusal(self.entries, self.seats, current, aligned, TEXTS.get)
        return refusal.code if refusal else None

    def test_aligned_on_x_rekeyed_at_y_and_endorsed_at_y_by_both_approvers_passes(self) -> None:
        self.rekey()
        self.endorse((A, Y), (B, Y))
        # Cy writes but doesn't approve: no endorsement of Cy's holds it.
        self.assertIsNone(self.refused())
        # Aligned on Y itself passes too, with no re-keying to follow.
        self.assertIsNone(self.refused(aligned=Y))

    def test_each_rule_refuses_by_its_own_code(self) -> None:
        self.rekey()
        self.endorse((A, Y))
        self.assertEqual(self.refused(), "plan.endorsement_missing")
        self.endorse((B, X))
        self.assertEqual(self.refused(), "plan.endorsement_stale")
        self.endorse((B, Y))
        self.assertIsNone(self.refused())
        raised = self.add(B, "feedback.raised", {"anchor": "### COLLAB-1. One", "text": "Not yet.", "blocking": True})
        self.add(A, "feedback.raised", {"anchor": "## Stories", "text": "A thought.", "blocking": False})
        self.assertEqual(self.refused(), "plan.feedback_blocking")
        self.add(A, "feedback.resolved", {"outcome": "accepted", "reason": "Done."}, answers=[raised["id"]])
        self.assertIsNone(self.refused())
        self.assertEqual(self.refused(aligned=None), "plan.not_aligned")

    def test_an_unrecorded_text_edit_at_the_endorsed_commit_is_not_aligned(self) -> None:
        self.rekey()
        self.endorse((A, Z), (B, Z))
        self.assertEqual(self.refused(current=Z), "plan.not_aligned")
        # A re-keying entry naming the edited commit exempts nothing: its text isn't the map applied.
        self.rekey(to=Z)
        self.assertEqual(self.refused(current=Z), "plan.not_aligned")

    def test_a_record_failing_two_adjacent_rules_gets_the_earlier_code(self) -> None:
        self.rekey()
        # Missing and stale: Ben has none, Ann's is of X.
        self.endorse((A, X))
        self.assertEqual(self.refused(), "plan.endorsement_missing")
        # Stale and not aligned: at Z, Ann's is of Z and Ben's of Y.
        self.endorse((A, Z), (B, Y))
        self.assertEqual(self.refused(current=Z), "plan.endorsement_stale")
        # Not aligned and blocking feedback: both endorse Z, and feedback that stops approval is open.
        self.endorse((B, Z))
        self.add(B, "feedback.raised", {"anchor": "## Stories", "text": "Not yet.", "blocking": True})
        self.assertEqual(self.refused(current=Z), "plan.not_aligned")
        self.assertEqual(self.refused(current=Y), "plan.endorsement_stale")


if __name__ == "__main__":
    unittest.main()
