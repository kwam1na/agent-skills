"""run-delivery's plan review: `plan-review prepare|reduce|end`, its durable round store, and the plan it writes.

A plan review runs the way a story's review does, in a fixture repository: one clean checkout and one brief per lens,
carried findings, and a reduction per round. It is unbounded, records the commit that closed each finding, and says
when it stops converging. `build_plan` reads its store, so every plan write keeps it.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

from tests.test_run_delivery import POSIX_ONLY, Fixture, commit, installed_release, plan_delivery, sh

RUN_SERIALLY = True

OC, AT = "lens.outcome-correctness", "lens.adversarial-testing"
SHORT = {OC: "outcome-correctness", AT: "adversarial-testing"}


def store(fx: Fixture) -> Path:
    return fx.main.resolve() / ".orchestration/review/FX-1/plan"


def review_delivery(fx: Fixture, lenses=(OC, AT)) -> Path:
    """The fixture delivery with criteria and the plan review's lenses declared in its delivery file."""
    path = plan_delivery(fx)
    d = json.loads(path.read_text())
    d["planReview"] = {"lenses": list(lenses)}
    path.write_text(json.dumps(d, indent=2))
    commit(fx.main, "FX-1: the plan review's lenses")
    return path


def revise(fx: Fixture, text: str) -> str:
    """A plan revision: one more line in the plan, committed. Returns its commit."""
    plan = fx.main / "docs/plan.md"
    plan.write_text(plan.read_text() + f"\n{text}\n")
    commit(fx.main, f"FX-1: plan revision: {text}")
    return sh("git", "rev-parse", "HEAD", cwd=fx.main).stdout.strip()


def finding(lens: str, n: int, k: int, summary: str = "The plan leaves a case out.", severity: str = "P1") -> dict:
    return {"id": f"{SHORT[lens]}-r{n}-{k}", "severity": severity, "summary": summary, "location": "docs/plan.md:1",
            "failureScenario": "s", "proposedChange": "c"}


def results(fx: Fixture, n: int, by_lens: dict) -> None:
    """Each lens's result for round n, as its reviewer returns it: aligned unless given findings or an outcome."""
    meta = json.loads((store(fx) / f"round-{n}.meta.json").read_text())
    for lens in meta["lenses"]:
        given = by_lens.get(lens, {})
        result = {"schemaVersion": "review-lens-result/1", "lensId": lens, "subjectRef": "FX-1.plan",
                  "candidateRef": meta["candidate"], "round": n,
                  "outcome": "changes-requested" if given.get("findings") else "aligned",
                  "findings": given.get("findings", []), "evidence": [f"tree {meta['tree']} checked at start and end"]}
        if n > 1:
            result["resolvedFindings"] = given.get("resolved", [])
        result.update(given.get("override", {}))
        (store(fx) / f"result-r{n}-{SHORT[lens]}.json").write_text(json.dumps(result))


class PlanReviewCase(unittest.TestCase):
    """A fixture repository and the installed release, shared by the cases below."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def fixture(self) -> Fixture:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        return fx

    def flow(self, fx: Fixture, *argv) -> tuple[int, str]:
        return fx.flow("plan-review", *argv, cwd=fx.main)

    def three_rounds(self, fx: Fixture) -> list[str]:
        """Round 1 raises four findings, round 2 fixes them and raises one, round 3 fixes it: the candidates."""
        candidates = []
        code, out = self.flow(fx, "prepare")
        self.assertEqual(code, 0, out)
        candidates.append(json.loads((store(fx) / "round-1.meta.json").read_text())["candidate"])
        results(fx, 1, {OC: {"findings": [finding(OC, 1, k) for k in (1, 2, 3)]},
                        AT: {"findings": [finding(AT, 1, 1, "A review past the caps blocks the write.")]}})
        code, out = self.flow(fx, "reduce")
        self.assertEqual(code, 0, out)
        candidates.append(revise(fx, "Round 1's four findings are answered."))
        code, out = self.flow(fx, "prepare")
        self.assertEqual(code, 0, out)
        closed = lambda lens, ks: [{"id": f"{SHORT[lens]}-r1-{k}", "state": "fixed", "resolution": "Answered in C2."}
                                   for k in ks]
        results(fx, 2, {OC: {"findings": [finding(OC, 2, 1, "Scenario 6 reads the wrong rounds.")],
                             "resolved": closed(OC, (1, 2, 3))},
                        AT: {"resolved": closed(AT, (1,))}})
        code, out = self.flow(fx, "reduce")
        self.assertEqual(code, 0, out)
        candidates.append(revise(fx, "Scenario 6 reads rounds 2 to 10."))
        code, out = self.flow(fx, "prepare")
        self.assertEqual(code, 0, out)
        results(fx, 3, {OC: {"resolved": [{"id": "outcome-correctness-r2-1", "state": "closed",
                                           "resolution": "The off-by-one is corrected."}]}})
        code, out = self.flow(fx, "reduce")
        self.assertEqual(code, 0, out)
        self.assertIn("aligned", out)
        return candidates


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class PlanReviewCommandTests(PlanReviewCase):

    def test_three_rounds_to_alignment_record_each_fix_commit_and_leave_no_checkout(self) -> None:
        fx = self.fixture()
        review_delivery(fx)
        code, out = self.flow(fx, "prepare")
        self.assertEqual(code, 0, out)
        meta = json.loads((store(fx) / "round-1.meta.json").read_text())
        self.assertEqual(meta["lenses"], [OC, AT])
        self.assertEqual(meta["candidate"], sh("git", "rev-parse", "HEAD", cwd=fx.main).stdout.strip())
        for lens in (OC, AT):
            checkout = store(fx) / "checkouts" / lens
            self.assertEqual(sh("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip(), meta["candidate"])
            brief = (store(fx) / f"brief-r1-{SHORT[lens]}.md").read_text()
            self.assertIn("`unbounded`: unbounded round 1, with no bound supplied or invented.", brief)
            self.assertIn(f"Your private checkout: `{checkout}`", brief)
            self.assertIn("`subjectRef`: `FX-1.plan`", brief)
            self.assertIn("P2 and P3 notes belong in `evidence`", brief)
            self.assertNotIn("file each with `\"state\": \"deferred\"`", brief)
            self.assertIn(str(store(fx) / f"brief-r1-{SHORT[lens]}.md"), out)
        self.assertEqual(meta["startChecks"], {lens: "passed" for lens in (OC, AT)})
        # Round 1 is prepared: the rest of the three rounds, then what the store holds.
        sh("git", "worktree", "list", cwd=fx.main)
        results(fx, 1, {OC: {"findings": [finding(OC, 1, k) for k in (1, 2, 3)]},
                        AT: {"findings": [finding(AT, 1, 1)]}})
        code, out = self.flow(fx, "reduce")
        self.assertEqual(code, 0, out)
        self.assertIn("plan review round 1: changes requested; 4 open findings", out)
        self.assertFalse((store(fx) / "checkouts" / OC).exists())
        self.assertNotIn(str(store(fx) / "checkouts"), sh("git", "worktree", "list", cwd=fx.main).stdout)
        second = revise(fx, "Round 1's four findings are answered.")
        self.flow(fx, "prepare")
        closed = lambda lens, ks: [{"id": f"{SHORT[lens]}-r1-{k}", "state": "fixed", "resolution": "Answered."} for k in ks]
        results(fx, 2, {OC: {"findings": [finding(OC, 2, 1)], "resolved": closed(OC, (1, 2, 3))},
                        AT: {"resolved": closed(AT, (1,))}})
        code, out = self.flow(fx, "reduce")
        self.assertEqual(code, 0, out)
        self.assertIn("plan review round 2: changes requested; 1 open finding", out)
        self.assertNotIn("isn't converging", out)
        third = revise(fx, "Scenario 6 reads rounds 2 to 10.")
        self.flow(fx, "prepare")
        results(fx, 3, {OC: {"resolved": [{"id": "outcome-correctness-r2-1", "state": "closed", "resolution": "Done."}]}})
        code, out = self.flow(fx, "reduce")
        self.assertEqual(code, 0, out)
        self.assertIn("plan review round 3: every lens aligned", out)
        rounds = [json.loads((store(fx) / f"round-{n}.json").read_text()) for n in (1, 2, 3)]
        self.assertEqual([r["candidate"] for r in rounds], [meta["candidate"], second, third])
        self.assertEqual([r["outcome"] for r in rounds], ["unresolved", "unresolved", "aligned"])
        # The projection gives each fixed closure the commit of the round that reports it closed.
        review = fx.call("plan_review_of", cwd=fx.main, delivery="FX-1")
        self.assertEqual(review["state"], "aligned")
        self.assertEqual([entry["round"] for entry in review["rounds"]], [1, 2, 3])
        self.assertEqual([(note["id"], note["fixCommit"]) for note in review["rounds"][1]["settled"]],
                         [("outcome-correctness-r1-1", second), ("outcome-correctness-r1-2", second),
                          ("outcome-correctness-r1-3", second), ("adversarial-testing-r1-1", second)])
        self.assertEqual([(note["id"], note["fixCommit"]) for note in review["rounds"][2]["settled"]],
                         [("outcome-correctness-r2-1", third)])
        self.assertEqual(review["rounds"][0]["findings"], {"P0": 0, "P1": 4, "P2": 0, "P3": 0})
        self.assertFalse(list((store(fx) / "checkouts").iterdir()) if (store(fx) / "checkouts").exists() else [])

    def test_a_different_lens_set_after_round_one_is_refused(self) -> None:
        fx = self.fixture()
        path = review_delivery(fx)
        self.flow(fx, "prepare")
        results(fx, 1, {OC: {"findings": [finding(OC, 1, 1)]}})
        self.assertEqual(self.flow(fx, "reduce")[0], 0)
        d = json.loads(path.read_text())
        d["planReview"]["lenses"] = [OC]
        path.write_text(json.dumps(d, indent=2))
        commit(fx.main, "FX-1: fewer plan lenses")
        code, out = self.flow(fx, "prepare")
        self.assertEqual(code, 2, out)
        self.assertIn("the plan review's lenses were fixed at round 1", out)
        self.assertFalse((store(fx) / "round-2.meta.json").exists())

    def test_lenses_the_repository_doesnt_declare_are_refused(self) -> None:
        fx = self.fixture()
        review_delivery(fx, lenses=(OC, "lens.unknown"))
        code, out = self.flow(fx, "prepare")
        self.assertEqual(code, 2, out)
        self.assertIn("lens.unknown", out)

    def test_an_unchanged_candidate_is_refused(self) -> None:
        fx = self.fixture()
        review_delivery(fx)
        self.flow(fx, "prepare")
        results(fx, 1, {OC: {"findings": [finding(OC, 1, 1)]}})
        self.flow(fx, "reduce")
        code, out = self.flow(fx, "prepare")
        self.assertEqual(code, 2, out)
        self.assertIn("is the candidate of round 1; commit the plan's revision first", out)
        # An uncommitted revision is refused too: the candidate is a commit.
        (fx.main / "docs/plan.md").write_text("changed\n")
        code, out = self.flow(fx, "prepare")
        self.assertEqual(code, 2, out)
        self.assertIn("uncommitted", out)

    def test_a_lens_checkout_that_moved_fails_the_round_and_keeps_the_checkouts(self) -> None:
        fx = self.fixture()
        review_delivery(fx)
        self.flow(fx, "prepare")
        results(fx, 1, {})
        moved = store(fx) / "checkouts" / AT
        (moved / "docs/plan.md").write_text("a lens edited its checkout\n")
        code, out = self.flow(fx, "reduce")
        self.assertEqual(code, 2, out)
        self.assertIn(f"{AT}: its checkout {moved} failed the end identity check", out)
        self.assertFalse((store(fx) / "round-1.json").exists())
        self.assertTrue(moved.exists())
        self.assertTrue((store(fx) / "checkouts" / OC).exists())

    def test_results_that_dont_match_a_plan_review_are_refused(self) -> None:
        fx = self.fixture()
        review_delivery(fx)
        self.flow(fx, "prepare")
        deferred = {**finding(OC, 1, 1, severity="P2"), "state": "deferred", "deferredIssue": "later"}
        results(fx, 1, {OC: {"findings": [deferred]},
                        AT: {"findings": [finding(AT, 1, 1)], "override": {"outcome": "aligned"}}})
        code, out = self.flow(fx, "reduce")
        self.assertEqual(code, 2, out)
        self.assertIn(f"{OC}: finding outcome-correctness-r1-1 is P2: a plan review files P2 and P3 notes in evidence", out)
        self.assertIn(f"{AT}: outcome is aligned but it raises an open P0/P1 finding", out)
        self.assertFalse((store(fx) / "round-1.json").exists())

    def test_a_round_that_raises_a_new_finding_and_leaves_no_fewer_open_says_it_isnt_converging(self) -> None:
        fx = self.fixture()
        review_delivery(fx)
        self.flow(fx, "prepare")
        results(fx, 1, {OC: {"findings": [finding(OC, 1, 1), finding(OC, 1, 2)]}})
        self.flow(fx, "reduce")
        revise(fx, "An answer that doesn't hold.")
        self.flow(fx, "prepare")
        # Two closed and one new: one open, fewer than round 1's two, so it still converges.
        results(fx, 2, {OC: {"findings": [finding(OC, 2, 1)], "resolved": [
            {"id": f"outcome-correctness-r1-{k}", "state": "fixed", "resolution": "Answered."} for k in (1, 2)]}})
        code, out = self.flow(fx, "reduce")
        self.assertEqual(code, 0, out)
        self.assertNotIn("isn't converging", out)
        revise(fx, "Another answer that doesn't hold.")
        self.flow(fx, "prepare")
        results(fx, 3, {OC: {"findings": [finding(OC, 3, 1)], "resolved": [
            {"id": "outcome-correctness-r2-1", "state": "open", "resolution": "Still open."}]}})
        code, out = self.flow(fx, "reduce")
        self.assertEqual(code, 0, out)
        self.assertIn("The plan review isn't converging: round 3 raised a new finding and leaves 2 open, no fewer "
                      "than round 2's 1. The operator may end it:", out)
        self.assertIn("plan-review end --reason", out)
        self.assertFalse((store(fx) / "ended.json").exists(), "reduce ended the review itself")
        self.assertEqual(len(list(store(fx).glob("round-*.json"))) - len(list(store(fx).glob("round-*.meta.json"))), 3)

    def test_end_records_a_reason_at_its_cap_and_refuses_one_over_it_or_a_review_already_aligned(self) -> None:
        fx = self.fixture()
        review_delivery(fx)
        code, out = self.flow(fx, "end", "--reason", "Stopped.")
        self.assertEqual(code, 2, out)
        self.assertIn("no reduced round to end", out)
        self.flow(fx, "prepare")
        results(fx, 1, {OC: {"findings": [finding(OC, 1, 1)]}})
        self.flow(fx, "reduce")
        code, out = self.flow(fx, "end", "--reason", "x" * 601)
        self.assertEqual(code, 2, out)
        self.assertIn("600 characters", out)
        reason = "r" * 600
        code, out = self.flow(fx, "end", "--reason", reason)
        self.assertEqual(code, 0, out)
        ended = json.loads((store(fx) / "ended.json").read_text())
        self.assertEqual((ended["reason"], ended["round"]), (reason, 1))
        self.assertEqual(fx.call("plan_review_of", cwd=fx.main, delivery="FX-1")["endedReason"], reason)
        revise(fx, "After the end.")
        code, out = self.flow(fx, "prepare")
        self.assertEqual(code, 2, out)
        self.assertIn("the plan review was ended", out)
        aligned = self.fixture()
        review_delivery(aligned)
        self.flow(aligned, "prepare")
        results(aligned, 1, {})
        self.flow(aligned, "reduce")
        code, out = self.flow(aligned, "end", "--reason", "Stopped.")
        self.assertEqual(code, 2, out)
        self.assertIn("every lens aligned", out)


PILOT_PLAN = """# Fixture delivery

## Stories

### S1 (FX-2). Thing one (AC1). Regex `/api/one$`

### S2 (FX-3). Thing two (AC2)

## Approval Handoffs

1. The Product Owner signs off the copy.
2. Security reviews the token scope,
   including the refresh path.
3. The release manager picks the batch.

## Out of scope

- A per-delivery base branch.
- Importing saved addresses.

## Risks

Prose, not a list.
"""


def synthetic_rounds(fx: Fixture, count: int, *, long_round: int | None = None) -> None:
    """A store of `count` reduced rounds written directly, each raising 30 full-length findings and closing the round
    before's, ids at 64 characters: the shape of a review past every cap. `long_round` raises 35."""
    folder = store(fx)
    folder.mkdir(parents=True, exist_ok=True)
    ident = lambda n, k: f"outcome-correctness-r{n}-{k}-".ljust(64, "x")  # noqa: E731
    raised = {n: 35 if n == long_round else 30 for n in range(1, count + 1)}
    for n in range(1, count + 1):
        candidate = f"{n:040x}"
        last = n == count
        entries = []
        for lens in (OC, AT):
            findings = [{"id": ident(n, k), "severity": "P1", "summary": "s" * 280} for k in range(raised[n])] \
                if lens == OC and not last else []
            closures = [{"id": ident(n - 1, k), "state": "fixed", "resolution": "r" * 600}
                        for k in range(raised[n - 1])] if lens == OC and n > 1 else []
            binding = {"subjectRef": "FX-1.plan", "candidateRef": candidate, "round": n}
            entries.append({"lens": lens, "binding": binding, "state": "obtained", "result": {
                "schemaVersion": "review-lens-result/1", "lensId": lens, **binding,
                "outcome": "changes-requested" if findings else "aligned", "findings": findings,
                "resolvedFindings": closures, "evidence": ["tree"]}})
        (folder / f"round-{n}.json").write_text(json.dumps({
            "round": n, "outcome": "aligned" if last else "unresolved", "candidate": candidate, "tree": "t" * 40,
            "lenses": [OC, AT], "startedAt": "2026-10-30T10:00:00.000000Z", "finishedAt": "2026-10-30T10:30:00.000000Z",
            "open": [] if last else [{"id": ident(n, k), "lens": OC, "severity": "P1", "summary": "s"}
                                     for k in range(raised[n])], "entries": entries}))


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class PlanWriteTests(PlanReviewCase):
    """C7 and C8: build_plan reads the store on every build, freezing refuses an open review, the producer refuses a
    plan over 1 MiB, and lead-setup drafts approvals and out of scope from the plan."""

    def plan(self, fx: Fixture) -> dict:
        path = fx.tmp / "record/plan.json"
        checked = sh(sys.executable, "-B", fx.release / "skills/delivery-views/scripts/views_common.py", "check-plan",
                     path, check=False)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        return json.loads(path.read_text())

    def approvals(self, fx: Fixture, path: Path, blocking=(True, True, False)) -> None:
        d = json.loads(path.read_text())
        d["approvals"] = [{"id": f"approval-{i + 1}", "text": text, "state": "waiting", "blocking": flag}
                          for i, (text, flag) in enumerate(zip(("Copy sign-off", "Token scope", "Batch"), blocking))]
        d["outOfScope"] = ["A per-delivery base branch."]
        path.write_text(json.dumps(d, indent=2))
        commit(fx.main, "FX-1: approvals")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)

    def test_a_frozen_plan_carries_the_review_and_every_rebuild_keeps_it(self) -> None:
        fx = self.fixture()
        path = review_delivery(fx)
        candidates = self.three_rounds(fx)
        self.approvals(fx, path)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        plan = self.plan(fx)
        review = plan["planReview"]
        self.assertEqual((review["lenses"], review["state"], [e["round"] for e in review["rounds"]]),
                         ([OC, AT], "aligned", [1, 2, 3]))
        self.assertEqual({note["fixCommit"] for note in review["rounds"][1]["settled"]}, {candidates[1]})
        self.assertEqual([note["fixCommit"] for note in review["rounds"][2]["settled"]], [candidates[2]])
        self.assertEqual(plan["approved"]["review"], "after 3 review rounds · every lens aligned")
        self.assertEqual([(a["id"], a.get("blocking")) for a in plan["approvals"]],
                         [("approval-1", True), ("approval-2", True), ("approval-3", False)])
        # A sweep changes nothing; an approval marked done keeps the review and the line's words.
        before = (fx.tmp / "record/plan.json").read_bytes()
        code, out = fx.flow("lead-reconcile", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("plan: unchanged", out)
        self.assertEqual((fx.tmp / "record/plan.json").read_bytes(), before)
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "done", "--who", "the operator", cwd=fx.main)
        self.assertEqual(code, 0, out)
        plan = self.plan(fx)
        self.assertEqual(plan["approvals"][0], {"id": "approval-1", "text": "Copy sign-off", "state": "done",
                                                "blocking": True, "who": "the operator"})
        self.assertEqual(plan["planReview"], review)
        self.assertEqual(plan["approved"]["review"], "after 3 review rounds · every lens aligned")
        self.assertEqual(sh("git", "log", "-1", "--format=%s", cwd=fx.main).stdout.strip(),
                         "FX-1: approval approval-1 done")
        self.assertEqual(sh("git", "status", "--porcelain", cwd=fx.main).stdout, "")
        # An amendment continues the numbering with the same lenses; the line reads still open until it aligns.
        revise(fx, "An amendment.")
        self.flow(fx, "prepare")
        results(fx, 4, {OC: {"findings": [finding(OC, 4, 1)]}})
        code, out = self.flow(fx, "reduce")
        self.assertEqual(code, 0, out)
        self.assertIn("plan: changed", out)
        plan = self.plan(fx)
        self.assertEqual((plan["planReview"]["state"], plan["approved"]["review"]),
                         ("open", "after 4 review rounds · still open"))

    def test_freezing_refuses_an_open_review_and_an_ended_one_freezes(self) -> None:
        fx = self.fixture()
        review_delivery(fx)
        self.flow(fx, "prepare")
        results(fx, 1, {OC: {"findings": [finding(OC, 1, 1)]}})
        self.flow(fx, "reduce")
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("the plan review is still open after round 1", out)
        self.assertFalse((fx.tmp / "record/state.json").exists())
        code, out = self.flow(fx, "end", "--reason", "The operator approved it as it stands.")
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        plan = self.plan(fx)
        self.assertEqual(plan["approved"]["review"], "after 1 review round · ended without alignment")
        self.assertEqual(plan["planReview"]["endedReason"], "The operator approved it as it stands.")

    def test_a_delivery_without_a_review_freezes_as_before(self) -> None:
        fx = self.fixture()
        plan_delivery(fx)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        plan = self.plan(fx)
        self.assertNotIn("planReview", plan)
        self.assertNotIn("review", plan["approved"])

    def test_a_late_snapshot_keeps_its_words_and_adds_the_reviews(self) -> None:
        fx = self.fixture()
        from tests.test_run_delivery import setup_delivery
        path = setup_delivery(fx)
        fx.flow("lead-init", cwd=fx.main)
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["criteria"] = [{"id": "one-loads", "text": "Thing one loads"}]
        d["planReview"] = {"lenses": [OC]}
        path.write_text(json.dumps(d, indent=2))
        commit(fx.main, "FX-1: criteria and a plan review, after the start")
        self.flow(fx, "prepare")
        results(fx, 1, {})
        code, out = self.flow(fx, "reduce")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.plan(fx)["approved"]["review"],
                         "taken after the delivery started · after 1 review round · every lens aligned")

    def test_a_plan_over_one_mebibyte_is_refused(self) -> None:
        fx = self.fixture()
        path = plan_delivery(fx)
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["criteria"] = [{"id": f"c{i}", "text": f"{i:04d}" + "x" * 596} for i in range(999)]
        d["finish"] = [{"id": "acs", "label": "Every criterion", "text": "All met", "covers": {"criteria": "all", "proofs": ["merged"]}}]
        path.write_text(json.dumps(d, indent=2))
        commit(fx.main, "FX-1: a very large plan")
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("over the 1 MiB a plan may hold", out)
        self.assertFalse((fx.tmp / "record/plan.json").exists())

    def test_a_review_past_every_cap_still_writes_a_valid_plan_on_every_path(self) -> None:
        fx = self.fixture()
        path = review_delivery(fx)
        self.approvals(fx, path)
        synthetic_rounds(fx, 40, long_round=20)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        plan = self.plan(fx)
        entries = plan["planReview"]["rounds"]
        self.assertEqual(len(entries), 32)
        self.assertEqual([entries[0]["round"], entries[1]["fromRound"], entries[1]["round"], entries[2]["round"],
                          entries[-1]["round"]], [1, 2, 10, 11, 40])
        twenty = next(entry for entry in entries if entry["round"] == 20)
        self.assertEqual(twenty["findings"]["P1"], 35)
        self.assertEqual(len(twenty["raised"]) + twenty["clipped"]["findings"], 35)
        self.assertEqual(plan["approved"]["review"], "after 40 review rounds · every lens aligned")
        for entry in entries:
            for note in entry["settled"]:
                self.assertEqual(note["fixCommit"], f"{entry['round']:040x}")
        code, out = fx.flow("lead-reconcile", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("plan: unchanged", out)
        code, out = fx.flow("lead-plan", "approval", "approval-2", "--state", "done", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.plan(fx)["approvals"][1]["state"], "done")

    def test_lead_plan_approval_refuses_a_dirty_checkout_and_an_unknown_id(self) -> None:
        fx = self.fixture()
        path = review_delivery(fx)
        self.approvals(fx, path)
        fx.flow("lead-init", cwd=fx.main)
        code, out = fx.flow("lead-plan", "approval", "approval-9", "--state", "done", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("no approval approval-9", out)
        (fx.main / "src/app.txt").write_text("edited\n")
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "done", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("uncommitted changes", out)
        self.assertEqual(json.loads(path.read_text())["approvals"][0]["state"], "waiting")

    def test_lead_setup_drafts_approvals_and_out_of_scope_from_the_plan_and_force_keeps_them(self) -> None:
        from tests.test_run_delivery import PATTERN
        fx = self.fixture()
        (fx.main / "docs/plan.md").write_text(PILOT_PLAN)
        commit(fx.main, "FX-1: a plan shaped like the pilot's")
        setup = ("lead-setup", "FX-1", "--plan", "docs/plan.md", "--base", "feature/FX-1", "--pattern", PATTERN,
                 "--approvals-section", "Approval Handoffs", "--out-of-scope-section", "Out of scope")
        code, out = fx.flow(*setup, cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("drafted 3 approvals from 'Approval Handoffs'", out)
        self.assertIn("drafted 2 out-of-scope items from 'Out of scope'", out)
        path = fx.main / ".agents/deliveries/FX-1.json"
        d = json.loads(path.read_text())
        self.assertEqual(d["approvals"], [
            {"id": "approval-1", "text": "The Product Owner signs off the copy.", "state": "waiting"},
            {"id": "approval-2", "text": "Security reviews the token scope, including the refresh path.", "state": "waiting"},
            {"id": "approval-3", "text": "The release manager picks the batch.", "state": "waiting"}])
        self.assertEqual(d["outOfScope"], ["A per-delivery base branch.", "Importing saved addresses."])
        # The lead marks one blocking and declares the review's lenses; a forced redraft keeps all of it.
        d["approvals"][0]["blocking"] = True
        d["planReview"] = {"lenses": [OC]}
        path.write_text(json.dumps(d, indent=2))
        code, out = fx.flow(*setup, "--force", cwd=fx.main)
        self.assertEqual(code, 0, out)
        kept = json.loads(path.read_text())
        self.assertEqual((kept["approvals"], kept["outOfScope"], kept["planReview"]),
                         (d["approvals"], d["outOfScope"], d["planReview"]))
        # A named section that is missing, or holds no list, drafts nothing and says so; an unnamed one is silent.
        code, out = fx.flow("lead-setup", "FX-1", "--plan", "docs/plan.md", "--base", "feature/FX-1", "--pattern", PATTERN,
                            "--approvals-section", "Sign-offs", "--out-of-scope-section", "Risks", cwd=fx.main)
        self.assertEqual(code, 2, out)  # the file exists and --force wasn't given
        path.unlink()
        code, out = fx.flow("lead-setup", "FX-1", "--plan", "docs/plan.md", "--base", "feature/FX-1", "--pattern", PATTERN,
                            "--approvals-section", "Sign-offs", "--out-of-scope-section", "Risks", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("note: the plan has no 'Sign-offs' section: drafted no approvals", out)
        self.assertIn("note: the plan's 'Risks' section holds no list: drafted no out-of-scope items", out)
        d = json.loads(path.read_text())
        self.assertNotIn("approvals", d)
        self.assertNotIn("outOfScope", d)
        path.unlink()
        code, out = fx.flow("lead-setup", "FX-1", "--plan", "docs/plan.md", "--base", "feature/FX-1", "--pattern", PATTERN,
                            cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertNotIn("approvals", out)
        self.assertNotIn("approvals", json.loads(path.read_text()))

    def test_the_finish_line_lead_setup_a_three_round_review_and_lead_init_give_the_page_its_review_and_flag(self) -> None:
        from tests.test_run_delivery import PATTERN
        fx = self.fixture()
        (fx.main / "docs/plan.md").write_text(PILOT_PLAN)
        commit(fx.main, "FX-1: the plan")
        code, out = fx.flow("lead-setup", "FX-1", "--plan", "docs/plan.md", "--base", "feature/FX-1", "--pattern", PATTERN,
                            "--approvals-section", "Approval Handoffs", "--out-of-scope-section", "Out of scope",
                            cwd=fx.main)
        self.assertEqual(code, 0, out)
        path = fx.main / ".agents/deliveries/FX-1.json"
        d = json.loads(path.read_text())
        d["stories"]["FX-2"].pop("evidence", None)
        d["stories"]["FX-2"].update(lane="lane-1", criteria=[{"id": "one-loads", "text": "Thing one loads"}])
        d["stories"]["FX-3"].update(lane="lane-2", inApp="loaded", criteria=[{"id": "two-renders", "text": "Thing two renders"}])
        d["lanes"] = {"lane-1": {"owns": ["src/one*"]}, "lane-2": {"owns": ["src/two*"]}}
        d["finish"] = [{"id": "acs", "text": "Every criterion met"}]
        d["stateDirectory"] = str(fx.tmp / "record")
        d["planReview"] = {"lenses": [OC, AT]}
        for approval in d["approvals"][:2]:
            approval["blocking"] = True
        path.write_text(json.dumps(d, indent=2))
        commit(fx.main, "FX-1: delivery")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        candidates = self.three_rounds(fx)
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        plan = self.plan(fx)
        self.assertEqual(plan["approved"]["review"], "after 3 review rounds · every lens aligned")
        self.assertEqual({note["fixCommit"] for e in plan["planReview"]["rounds"] for note in e["settled"]},
                         set(candidates[1:]))
        self.assertEqual([(a["id"], a["state"], a.get("blocking", False)) for a in plan["approvals"]],
                         [("approval-1", "waiting", True), ("approval-2", "waiting", True), ("approval-3", "waiting", False)])
        self.assertEqual(plan["outOfScope"], ["A per-delivery base branch.", "Importing saved addresses."])
        # The page embeds exactly this plan, so it draws the line, the fold and the flag the page tests pin.
        sys.path.insert(0, str(fx.release / "skills/delivery-views/scripts"))
        import render  # noqa: E402
        block = render.read_plan_block(fx.tmp / "record")
        self.assertEqual(block, {"plan": plan})
        blocking = lambda: [a["id"] for a in self.plan(fx)["approvals"] if a.get("blocking") and a["state"] == "waiting"]  # noqa: E731
        for ident in ("approval-1", "approval-2"):
            code, out = fx.flow("lead-reconcile", cwd=fx.main)
            self.assertEqual(code, 0, out)
            self.assertEqual(self.plan(fx)["planReview"], plan["planReview"])
            code, out = fx.flow("lead-plan", "approval", ident, "--state", "done", cwd=fx.main)
            self.assertEqual(code, 0, out)
        self.assertEqual(blocking(), [])
        self.assertEqual(self.plan(fx)["planReview"], plan["planReview"])

    def test_lead_close_archives_the_review_store_without_its_checkouts(self) -> None:
        fx = self.fixture()
        review_delivery(fx)
        self.flow(fx, "prepare")
        results(fx, 1, {})
        self.flow(fx, "reduce")
        (store(fx) / "checkouts").mkdir(exist_ok=True)
        archive = fx.call("archive_plan_review", fx.tmp / "archive", cwd=fx.main, delivery="FX-1")
        self.assertEqual(sorted(p.name for p in Path(archive).iterdir()),
                         sorted(p.name for p in store(fx).iterdir() if p.name != "checkouts"))


if __name__ == "__main__":
    unittest.main()
