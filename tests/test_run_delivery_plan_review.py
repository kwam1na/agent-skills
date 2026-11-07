"""run-delivery's plan review: `plan-review prepare|reduce|end`, its durable round store, and the plan it writes.

A plan review runs the way a story's review does, in a fixture repository: one clean checkout and one brief per lens,
carried findings, and a reduction per round. It is unbounded, records the commit that closed each finding, and says
when it stops converging. `build_plan` reads its store, so every plan write keeps it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.test_run_delivery import POSIX_ONLY, ROOT, Fixture, commit, installed_release, plan_delivery, sh

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


def results(fx: Fixture, n: int, by_lens: dict, folder: Path | None = None, subject: str = "FX-1.plan") -> None:
    """Each lens's result for round n, as its reviewer returns it: aligned unless given findings or an outcome."""
    folder = folder or store(fx)
    meta = json.loads((folder / f"round-{n}.meta.json").read_text())
    for lens in meta["lenses"]:
        given = by_lens.get(lens, {})
        result = {"schemaVersion": "review-lens-result/1", "lensId": lens, "subjectRef": subject,
                  "candidateRef": meta["candidate"], "round": n,
                  "outcome": "changes-requested" if given.get("findings") else "aligned",
                  "findings": given.get("findings", []), "evidence": [f"tree {meta['tree']} checked at start and end"]}
        if n > 1:
            result["resolvedFindings"] = given.get("resolved", [])
        result.update(given.get("override", {}))
        (folder / f"result-r{n}-{SHORT[lens]}.json").write_text(json.dumps(result))


def slug_store(fx: Fixture, slug: str) -> Path:
    return fx.main.resolve() / ".orchestration/plan-review" / slug


def with_slug(fx: Fixture, path: str, slug: str, body: str | None = None) -> None:
    """The plan at `path`, committed with `reviewSlug` in its front matter."""
    plan = fx.main / path
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(f"---\nreviewSlug: {slug}\n---\n\n" + (body if body is not None else plan.read_text()))
    commit(fx.main, f"{path}: its review slug")


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
class PlanReviewWithoutDeliveryTests(PlanReviewCase):
    """`plan-review --plan` reviews any plan, keyed by its `reviewSlug`, and a later delivery adopts the store."""

    def test_a_plan_with_no_delivery_binding_or_record_is_prepared_reduced_and_ended(self) -> None:
        fx = self.fixture()
        with_slug(fx, "docs/plans/x.md", "x-plan", "# X\n\n## Outcome\n\nSomething works.\n")
        self.assertFalse((fx.main / ".agents/deliveries").exists())
        self.assertFalse((fx.main / ".agents/orchestration.json").exists())
        folder = slug_store(fx, "x-plan")
        code, out = self.flow(fx, "prepare", "--plan", "docs/plans/x.md")
        self.assertEqual(code, 2, out)
        self.assertIn("--lenses", out)
        self.assertFalse(folder.exists())
        code, out = self.flow(fx, "reduce", "--plan", "docs/plans/x.md")
        self.assertEqual(code, 2, out)
        self.assertIn("plan-review prepare --plan docs/plans/x.md first", out)
        code, out = self.flow(fx, "prepare", "--plan", "docs/plans/x.md", "--lenses", ",")
        self.assertEqual(code, 2, out)
        self.assertIn("--lenses must be 1 to 12 different lenses", out)
        self.assertFalse(folder.exists())
        code, out = self.flow(fx, "prepare", "--plan", "docs/plans/x.md", "--lenses", f"{OC},{AT}")
        self.assertEqual(code, 0, out)
        self.assertIn("plan docs/plans/x.md plan review round 1", out)
        self.assertIn("Then: python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py plan-review "
                      "reduce --plan docs/plans/x.md", out)
        meta = json.loads((folder / "round-1.meta.json").read_text())
        self.assertEqual(meta["lenses"], [OC, AT])
        self.assertEqual(json.loads((folder / "store.json").read_text()),
                         {"slug": "x-plan", "plan": "docs/plans/x.md", "subject": "x-plan.plan"})
        for lens in (OC, AT):
            brief = (folder / f"brief-r1-{SHORT[lens]}.md").read_text()
            self.assertIn("`subjectRef`: `x-plan.plan`", brief)
            self.assertIn("The plan under review is `docs/plans/x.md` in your checkout.", brief)
            self.assertNotIn("delivery None", brief)
        results(fx, 1, {OC: {"findings": [finding(OC, 1, 1)]}}, folder, "x-plan.plan")
        code, out = self.flow(fx, "reduce", "--plan", "docs/plans/x.md")
        self.assertEqual(code, 0, out)
        self.assertIn("plan docs/plans/x.md plan review round 1: changes requested; 1 open finding", out)
        self.assertIn("then: python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py plan-review "
                      "prepare --plan docs/plans/x.md", out)
        code, out = self.flow(fx, "reduce", "--plan", "docs/plans/x.md")
        self.assertIn("plan-review prepare --plan docs/plans/x.md", out)
        self.assertFalse((folder / "checkouts" / OC).exists())
        plan = fx.main / "docs/plans/x.md"
        plan.write_text(plan.read_text() + "\nRound 1 is answered.\n")
        commit(fx.main, "x: round 1 answered")
        # Later rounds keep round 1's lenses without naming them again, and refuse a different set.
        code, out = self.flow(fx, "prepare", "--plan", "docs/plans/x.md", "--lenses", OC)
        self.assertEqual(code, 2, out)
        self.assertIn("fixed at round 1", out)
        code, out = self.flow(fx, "prepare", "--plan", "docs/plans/x.md")
        self.assertEqual(code, 0, out)
        results(fx, 2, {OC: {"findings": [finding(OC, 2, 1)], "resolved": [
            {"id": "outcome-correctness-r1-1", "state": "open", "resolution": "Still open."}]}}, folder, "x-plan.plan")
        code, out = self.flow(fx, "reduce", "--plan", "docs/plans/x.md")
        self.assertEqual(code, 0, out)
        self.assertIn("isn't converging", out)
        self.assertIn("plan-review end --plan docs/plans/x.md --reason", out)
        # A round whose checkouts are gone fails, and says how to prepare it again with --plan.
        (folder / "round-3.meta.json").write_text((folder / "round-2.json").read_text())
        still = [{"id": f"outcome-correctness-r{k}-1", "state": "open", "resolution": "Still open."} for k in (1, 2)]
        results(fx, 3, {OC: {"resolved": still, "override": {"outcome": "changes-requested"}}}, folder, "x-plan.plan")
        code, out = self.flow(fx, "reduce", "--plan", "docs/plans/x.md")
        self.assertEqual(code, 2, out)
        self.assertIn("plan-review prepare --plan docs/plans/x.md --restart", out)
        for name in ("round-3.meta.json", "result-r3-outcome-correctness.json", "result-r3-adversarial-testing.json"):
            (folder / name).unlink()
        code, out = self.flow(fx, "end", "--plan", "docs/plans/x.md", "--reason", "The operator decided.")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads((folder / "ended.json").read_text())["reason"], "The operator decided.")
        self.assertEqual([p.name for p in sorted(folder.glob("round-*.json")) if ".meta" not in p.name],
                         ["round-1.json", "round-2.json"])
        self.assertFalse((fx.main / ".agents/deliveries").exists())
        self.assertFalse((fx.main / ".orchestration/review").exists())

    def test_a_plan_without_a_review_slug_is_refused_with_the_line_to_add(self) -> None:
        fx = self.fixture()
        plan = fx.main / "docs/plans/My Plan.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("# My plan\n")
        commit(fx.main, "a plan with no slug")
        code, out = self.flow(fx, "prepare", "--plan", "docs/plans/My Plan.md", "--lenses", OC)
        self.assertEqual(code, 2, out)
        self.assertIn("reviewSlug: my-plan", out)
        self.assertFalse((fx.main / ".orchestration/plan-review").exists())
        plan.write_text("---\nreviewSlug: Not A Slug\n---\n# My plan\n")
        commit(fx.main, "a bad slug")
        code, out = self.flow(fx, "prepare", "--plan", "docs/plans/My Plan.md", "--lenses", OC)
        self.assertEqual(code, 2, out)
        self.assertIn("reviewSlug", out)
        self.assertFalse((fx.main / ".orchestration/plan-review").exists())
        # Front matter is the file's leading block only; a quoted slug is the slug.
        plan.write_text("# My plan\n\n---\nreviewSlug: r-plan\n---\n")
        commit(fx.main, "a block below the title")
        code, out = self.flow(fx, "prepare", "--plan", "docs/plans/My Plan.md", "--lenses", OC)
        self.assertEqual(code, 2, out)
        self.assertIn("has no review slug", out)
        plan.write_text('---\nreviewSlug: "q-plan"\n---\n# My plan\n')
        commit(fx.main, "a quoted slug")
        code, out = self.flow(fx, "prepare", "--plan", "docs/plans/My Plan.md", "--lenses", OC)
        self.assertEqual(code, 0, out)
        self.assertTrue((slug_store(fx, "q-plan") / "round-1.meta.json").exists())

    def test_a_renamed_plan_keeps_its_review(self) -> None:
        fx = self.fixture()
        with_slug(fx, "docs/plans/x.md", "x-plan", "# X\n")
        code, out = self.flow(fx, "prepare", "--plan", "docs/plans/x.md", "--lenses", OC)
        self.assertEqual(code, 0, out)
        results(fx, 1, {OC: {"findings": [finding(OC, 1, 1)]}}, slug_store(fx, "x-plan"), "x-plan.plan")
        self.assertEqual(self.flow(fx, "reduce", "--plan", "docs/plans/x.md")[0], 0)
        sh("git", "mv", "docs/plans/x.md", "docs/plans/renamed.md", cwd=fx.main)
        commit(fx.main, "rename the plan")
        code, out = self.flow(fx, "prepare", "--plan", "docs/plans/renamed.md")
        self.assertEqual(code, 0, out)
        self.assertIn("plan review round 2", out)
        self.assertIn("The plan under review is `docs/plans/renamed.md`",
                      (slug_store(fx, "x-plan") / "brief-r2-outcome-correctness.md").read_text())

    def test_a_later_lead_setup_adopts_the_store_and_the_plan_chapter_shows_its_rounds(self) -> None:
        fx = self.fixture()
        with_slug(fx, "docs/plan.md", "fixture-plan")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        source = slug_store(fx, "fixture-plan")
        code, out = self.flow(fx, "prepare", "--plan", "docs/plan.md", "--lenses", f"{OC},{AT}")
        self.assertEqual(code, 0, out)
        results(fx, 1, {OC: {"findings": [finding(OC, 1, 1)]}}, source, "fixture-plan.plan")
        self.assertEqual(self.flow(fx, "reduce", "--plan", "docs/plan.md")[0], 0)
        plan = fx.main / "docs/plan.md"
        plan.write_text(plan.read_text() + "\nRound 1 is answered.\n")
        commit(fx.main, "FX-1: round 1 answered")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        # A prepared round can't move: its checkouts live in the store.
        code, out = self.flow(fx, "prepare", "--plan", "docs/plan.md")
        self.assertEqual(code, 0, out)
        from tests.test_run_delivery import PATTERN
        setup = ("lead-setup", "FX-1", "--plan", "docs/plan.md", "--base", "feature/FX-1", "--pattern", PATTERN,
                 "--no-authority")
        code, out = fx.flow(*setup, cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("round 2 is prepared", out)
        self.assertFalse((fx.main / ".agents/deliveries/FX-1.json").exists())
        results(fx, 2, {OC: {"resolved": [{"id": "outcome-correctness-r1-1", "state": "fixed", "resolution": "Done."}]}},
                source, "fixture-plan.plan")
        code, out = self.flow(fx, "reduce", "--plan", "docs/plan.md")
        self.assertEqual(code, 0, out)
        self.assertIn("every lens aligned", out)
        rounds = [json.loads((source / f"round-{n}.json").read_text()) for n in (1, 2)]
        path = plan_delivery(fx)  # lead-setup adopts the store here
        self.assertEqual(json.loads(path.read_text())["planReview"], {"lenses": [OC, AT]})
        self.assertEqual([json.loads((store(fx) / f"round-{n}.json").read_text()) for n in (1, 2)], rounds)
        self.assertFalse((source / "round-1.json").exists())
        self.assertEqual(json.loads((source / "adopted.json").read_text())["delivery"], "FX-1")
        code, out = self.flow(fx, "prepare", "--plan", "docs/plan.md")
        self.assertEqual(code, 2, out)
        self.assertIn("adopted by delivery FX-1", out)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        plan_file = json.loads((fx.tmp / "record/plan.json").read_text())
        self.assertEqual([entry["round"] for entry in plan_file["planReview"]["rounds"]], [1, 2])
        self.assertEqual(plan_file["planReview"]["state"], "aligned")
        self.assertEqual(plan_file["approved"]["review"], "after 2 review rounds · every lens aligned")
        # The adopted review goes on as the delivery's, under its own subject.
        revise(fx, "An amendment.")
        code, out = self.flow(fx, "prepare")
        self.assertEqual(code, 0, out)
        self.assertIn("`subjectRef`: `fixture-plan.plan`", (store(fx) / "brief-r3-outcome-correctness.md").read_text())

    def test_lead_init_adopts_a_review_run_after_lead_setup(self) -> None:
        fx = self.fixture()
        with_slug(fx, "docs/plan.md", "fixture-plan")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        plan_delivery(fx)
        code, out = self.flow(fx, "prepare", "--plan", "docs/plan.md", "--lenses", OC)
        self.assertEqual(code, 0, out)
        results(fx, 1, {}, slug_store(fx, "fixture-plan"), "fixture-plan.plan")
        self.assertEqual(self.flow(fx, "reduce", "--plan", "docs/plan.md")[0], 0)
        self.assertFalse(store(fx).exists())
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("adopted the plan review", out)
        self.assertTrue((store(fx) / "round-1.json").exists())
        plan_file = json.loads((fx.tmp / "record/plan.json").read_text())
        self.assertEqual(plan_file["planReview"]["state"], "aligned")
        # Another plan's own review leaves this delivery's plan chapter alone, even with the delivery named.
        before = (fx.tmp / "record/plan.json").read_bytes()
        with_slug(fx, "docs/plans/next.md", "next-plan", "# Next\n")
        env = {**fx.env, "RUN_DELIVERY_DELIVERY": "FX-1"}
        code, out = fx.flow("plan-review", "prepare", "--plan", "docs/plans/next.md", "--lenses", OC, cwd=fx.main, env=env)
        self.assertEqual(code, 0, out)
        results(fx, 1, {OC: {"findings": [finding(OC, 1, 1)]}}, slug_store(fx, "next-plan"), "next-plan.plan")
        code, out = fx.flow("plan-review", "reduce", "--plan", "docs/plans/next.md", cwd=fx.main, env=env)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("plan-review", "end", "--plan", "docs/plans/next.md", "--reason", "Stop.", cwd=fx.main,
                            env=env)
        self.assertEqual(code, 0, out)
        self.assertEqual((fx.tmp / "record/plan.json").read_bytes(), before)

    def test_a_delivery_with_its_own_plan_review_refuses_to_adopt_another(self) -> None:
        fx = self.fixture()
        with_slug(fx, "docs/plan.md", "fixture-plan")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        path = review_delivery(fx, (OC,))
        code, out = self.flow(fx, "prepare")
        self.assertEqual(code, 0, out)
        results(fx, 1, {})
        self.assertEqual(self.flow(fx, "reduce")[0], 0)
        source = slug_store(fx, "fixture-plan")
        code, out = self.flow(fx, "prepare", "--plan", "docs/plan.md", "--lenses", OC)
        self.assertEqual(code, 0, out)
        results(fx, 1, {}, source, "fixture-plan.plan")
        self.assertEqual(self.flow(fx, "reduce", "--plan", "docs/plan.md")[0], 0)
        drafted = path.read_bytes()
        from tests.test_run_delivery import PATTERN
        code, out = fx.flow("lead-setup", "FX-1", "--plan", "docs/plan.md", "--base", "feature/FX-1", "--pattern", PATTERN,
                            "--no-authority", "--force", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("already has a plan review", out)
        self.assertEqual(path.read_bytes(), drafted)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("already has a plan review", out)
        self.assertTrue((source / "round-1.json").exists())
        self.assertFalse((source / "adopted.json").exists())


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
            self.assertIn("- scoped sensor commands: none; a plan review runs no checks", brief)
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
        # --replace sets an earlier snapshot aside, so it can't stand in for the review's end.
        conf_path = fx.main / ".agents/run-delivery.json"
        conf_path.write_text(json.dumps({**json.loads(conf_path.read_text()),
                                         "cleanup": {"archive": str(fx.tmp / "archive/{delivery}")}}, indent=2))
        log = fx.tmp / "flow-log/plan-changes.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({"type": "snapshot", "at": "2026-01-01T00:00:00Z", "late": False, "units": [],
                                   "finish": []}) + "\n")
        code, out = fx.flow("lead-init", "--replace", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("the plan review is still open after round 1", out)
        self.assertTrue(log.exists(), "the refusal archived the flow log")
        log.unlink()
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
                 "--approvals-section", "Approval Handoffs", "--out-of-scope-section", "Out of scope", "--no-authority")
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
                            "--approvals-section", "Sign-offs", "--out-of-scope-section", "Risks", "--no-authority",
                            cwd=fx.main)
        self.assertEqual(code, 2, out)  # the file exists and --force wasn't given
        path.unlink()
        code, out = fx.flow("lead-setup", "FX-1", "--plan", "docs/plan.md", "--base", "feature/FX-1", "--pattern", PATTERN,
                            "--approvals-section", "Sign-offs", "--out-of-scope-section", "Risks", "--no-authority",
                            cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertIn("note: the plan has no 'Sign-offs' section: drafted no approvals", out)
        self.assertIn("note: the plan's 'Risks' section holds no list: drafted no out-of-scope items", out)
        d = json.loads(path.read_text())
        self.assertNotIn("approvals", d)
        self.assertNotIn("outOfScope", d)
        path.unlink()
        code, out = fx.flow("lead-setup", "FX-1", "--plan", "docs/plan.md", "--base", "feature/FX-1", "--pattern", PATTERN,
                            "--no-authority", cwd=fx.main)
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
                            "--no-authority", cwd=fx.main)
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


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class PlanReviewRecordTests(PlanReviewCase):
    """A standalone plan's review writes its `plan-review-evidence/1` record beside the plan when the lenses align or the
    operator ends it, and `plan-review check` validates that shape, the hand-written records included."""

    PLAN = "docs/plans/x.md"
    RECORD = "docs/plans/x.review.json"

    def standalone(self, fx: Fixture) -> Path:
        with_slug(fx, self.PLAN, "x-plan", "# X\n\n## Outcome\n\nSomething works.\n")
        code, out = self.flow(fx, "prepare", "--plan", self.PLAN, "--lenses", f"{OC},{AT}")
        self.assertEqual(code, 0, out)
        folder = slug_store(fx, "x-plan")
        results(fx, 1, {OC: {"findings": [finding(OC, 1, 1), finding(OC, 1, 2, "A second case is left out.")]}}, folder, "x-plan.plan")
        code, out = self.flow(fx, "reduce", "--plan", self.PLAN)
        self.assertEqual(code, 0, out)
        self.assertFalse((fx.main / self.RECORD).exists(), "an unresolved round wrote a record")
        plan = fx.main / self.PLAN
        plan.write_text(plan.read_text() + "\nRound 1 is answered.\n")
        commit(fx.main, "x: round 1 answered")
        code, out = self.flow(fx, "prepare", "--plan", self.PLAN)
        self.assertEqual(code, 0, out)
        return folder

    def check(self, fx: Fixture, *paths) -> tuple[int, str]:
        return self.flow(fx, "check", *[str(p) for p in paths])

    def test_an_aligned_review_writes_its_record_beside_the_plan_and_the_check_accepts_it(self) -> None:
        fx = self.fixture()
        folder = self.standalone(fx)
        closed = [{"id": f"outcome-correctness-r1-{k}", "state": "fixed", "resolution": "Answered."} for k in (1, 2)]
        results(fx, 2, {OC: {"resolved": closed}}, folder, "x-plan.plan")
        code, out = self.flow(fx, "reduce", "--plan", self.PLAN)
        self.assertEqual(code, 0, out)
        self.assertIn(f"wrote the review record {self.RECORD}", out)
        record = json.loads((fx.main / self.RECORD).read_text())
        rounds = [json.loads((folder / f"round-{n}.json").read_text()) for n in (1, 2)]
        candidate = rounds[1]["candidate"]
        reviewed = sh("git", "show", f"{candidate}:{self.PLAN}", cwd=fx.main).stdout
        self.assertEqual(record["schemaVersion"], "plan-review-evidence/1")
        self.assertEqual(record["writtenBy"], "run-delivery plan-review")
        self.assertEqual((record["plan"], record["planCommit"], record["outcome"], record["requiredLenses"]),
                         (self.PLAN, candidate, "unanimous-alignment", [OC, AT]))
        self.assertEqual(record["planSha256"], hashlib.sha256(reviewed.encode()).hexdigest())
        self.assertEqual([(r["round"], r["candidate"], r["outcome"], r["open"]) for r in record["rounds"]],
                         [(1, rounds[0]["candidate"], "unresolved", ["outcome-correctness-r1-1", "outcome-correctness-r1-2"]),
                          (2, candidate, "aligned", [])])
        by_lens = {r["lens"]: r for r in record["reviews"]}
        self.assertEqual(sorted(by_lens), [AT, OC])
        for lens, review in by_lens.items():
            raw = (folder / f"result-r2-{SHORT[lens]}.json").read_bytes()
            self.assertEqual((review["round"], review["result"], review["resultSha256"], review["findings"]),
                             (2, json.loads(raw), hashlib.sha256(raw).hexdigest(), []))
        self.assertEqual(record["residualRisks"], ["2 review rounds; findings open after each round: 2, 0."])
        self.assertNotIn("ended", record)
        self.assertEqual(self.check(fx, fx.main / self.RECORD), (0, f"{fx.main / self.RECORD}: a valid plan-review-evidence/1 record\n"))

    def test_an_ended_review_writes_its_record_with_the_reason_and_the_findings_left_open(self) -> None:
        fx = self.fixture()
        folder = self.standalone(fx)
        still = [{"id": "outcome-correctness-r1-1", "state": "open", "resolution": "Still open."},
                 {"id": "outcome-correctness-r1-2", "state": "fixed", "resolution": "Answered."}]
        results(fx, 2, {OC: {"resolved": still, "override": {"outcome": "changes-requested"}}}, folder, "x-plan.plan")
        self.assertEqual(self.flow(fx, "reduce", "--plan", self.PLAN)[0], 0)
        self.assertFalse((fx.main / self.RECORD).exists())
        # The plan moves on after the last reviewed candidate; the record says the text it covers isn't the current one.
        plan = fx.main / self.PLAN
        plan.write_text(plan.read_text() + "\nAn edit after the review.\n")
        code, out = self.flow(fx, "end", "--plan", self.PLAN, "--reason", "The operator took the risk.")
        self.assertEqual(code, 0, out)
        self.assertIn(f"wrote the review record {self.RECORD}", out)
        record = json.loads((fx.main / self.RECORD).read_text())
        self.assertEqual(record["outcome"], "ended-without-alignment")
        reviewed = sh("git", "show", f"{record['planCommit']}:{self.PLAN}", cwd=fx.main).stdout
        self.assertEqual(record["planSha256"], hashlib.sha256(reviewed.encode()).hexdigest(), "the digest isn't the reviewed text's")
        self.assertEqual((record["ended"]["round"], record["ended"]["reason"]), (2, "The operator took the risk."))
        self.assertEqual([r["outcome"] for r in record["rounds"]], ["unresolved", "unresolved"])
        risks = record["residualRisks"]
        self.assertIn("Ended without alignment after round 2: The operator took the risk.", risks)
        self.assertIn("Open P1 outcome-correctness-r1-1 (lens.outcome-correctness): The plan leaves a case out.", risks)
        self.assertFalse(any("outcome-correctness-r1-2" in r for r in risks), risks)
        self.assertTrue(any(r.startswith(f"The plan's current text differs from round 2's candidate {record['planCommit'][:12]}")
                            for r in risks), risks)
        self.assertEqual(self.check(fx, fx.main / self.RECORD)[0], 0)

    def test_an_amendment_after_alignment_rewrites_the_record_when_it_aligns_or_is_ended(self) -> None:
        for ending in ("aligned", "ended"):
            with self.subTest(ending):
                fx = self.fixture()
                with_slug(fx, self.PLAN, "x-plan", "# X\n")
                folder = slug_store(fx, "x-plan")
                self.assertEqual(self.flow(fx, "prepare", "--plan", self.PLAN, "--lenses", OC)[0], 0)
                results(fx, 1, {}, folder, "x-plan.plan")
                self.assertEqual(self.flow(fx, "reduce", "--plan", self.PLAN)[0], 0)
                first = json.loads((fx.main / self.RECORD).read_text())
                # The operator commits the record with an amendment, and the review goes on.
                plan = fx.main / self.PLAN
                plan.write_text(plan.read_text() + "\nAn amendment.\n")
                commit(fx.main, "x: an amendment")
                self.assertEqual(self.flow(fx, "prepare", "--plan", self.PLAN)[0], 0)
                if ending == "aligned":
                    results(fx, 2, {}, folder, "x-plan.plan")
                    code, out = self.flow(fx, "reduce", "--plan", self.PLAN)
                else:
                    results(fx, 2, {OC: {"findings": [finding(OC, 2, 1)]}}, folder, "x-plan.plan")
                    self.assertEqual(self.flow(fx, "reduce", "--plan", self.PLAN)[0], 0)
                    code, out = self.flow(fx, "end", "--plan", self.PLAN, "--reason", "The amendment stands as it is.")
                self.assertEqual(code, 0, out)
                self.assertIn(f"wrote the review record {self.RECORD}", out)
                record = json.loads((fx.main / self.RECORD).read_text())
                candidate = json.loads((folder / "round-2.json").read_text())["candidate"]
                self.assertNotEqual(candidate, first["planCommit"])
                self.assertEqual(record["planCommit"], candidate)
                self.assertEqual([r["outcome"] for r in record["rounds"]],
                                 ["aligned", "aligned" if ending == "aligned" else "unresolved"])
                self.assertEqual(record["outcome"], "unanimous-alignment" if ending == "aligned" else "ended-without-alignment")
                self.assertEqual(self.check(fx, fx.main / self.RECORD)[0], 0)

    def test_the_plans_digest_is_its_blobs_own_bytes(self) -> None:
        fx = self.fixture()
        plan = fx.main / self.PLAN
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_bytes(b"---\r\nreviewSlug: x-plan\r\n---\r\n# X\r\n\r\nA line with CRLF.\r\n")
        commit(fx.main, "x: a CRLF plan")
        folder = slug_store(fx, "x-plan")
        self.assertEqual(self.flow(fx, "prepare", "--plan", self.PLAN, "--lenses", OC)[0], 0)
        results(fx, 1, {}, folder, "x-plan.plan")
        self.assertEqual(self.flow(fx, "reduce", "--plan", self.PLAN)[0], 0)
        record = json.loads((fx.main / self.RECORD).read_text())
        blob = subprocess.run(["git", "show", f"{record['planCommit']}:{self.PLAN}"], cwd=fx.main, capture_output=True,
                              check=True).stdout
        self.assertIn(b"\r\n", blob)
        self.assertEqual(record["planSha256"], hashlib.sha256(blob).hexdigest())
        self.assertFalse(any("differs" in r for r in record["residualRisks"]), record["residualRisks"])

    def test_a_hand_written_record_beside_the_plan_is_kept(self) -> None:
        fx = self.fixture()
        folder = self.standalone(fx)
        own = (ROOT / "docs/plans/2026-11-02-adoption-gaps.review.json").read_text()
        (fx.main / self.RECORD).write_text(own)
        commit(fx.main, "x: a hand-written record")
        closed = [{"id": f"outcome-correctness-r1-{k}", "state": "fixed", "resolution": "Answered."} for k in (1, 2)]
        results(fx, 2, {OC: {"resolved": closed}}, folder, "x-plan.plan")
        code, out = self.flow(fx, "reduce", "--plan", self.PLAN)
        self.assertEqual(code, 0, out)
        self.assertIn(f"kept the hand-written review record {self.RECORD}", out)
        self.assertEqual((fx.main / self.RECORD).read_text(), own)

    def test_a_deliverys_own_plan_review_writes_no_record_into_the_checkout(self) -> None:
        fx = self.fixture()
        review_delivery(fx)
        self.three_rounds(fx)
        self.assertEqual(sorted(p.name for p in (fx.main / "docs").rglob("*.review.json")), [])

    def test_the_check_accepts_every_record_in_the_repository(self) -> None:
        fx = self.fixture()
        records = sorted((ROOT / "docs/plans").glob("*.review.json"))
        self.assertGreater(len(records), 5)
        code, out = self.check(fx, *records)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("a valid plan-review-evidence/1 record"), len(records), out)

    def test_the_check_refuses_each_broken_shape_and_names_why(self) -> None:
        fx = self.fixture()
        folder = self.standalone(fx)
        closed = [{"id": f"outcome-correctness-r1-{k}", "state": "fixed", "resolution": "Answered."} for k in (1, 2)]
        results(fx, 2, {OC: {"resolved": closed}}, folder, "x-plan.plan")
        self.assertEqual(self.flow(fx, "reduce", "--plan", self.PLAN)[0], 0)
        good = json.loads((fx.main / self.RECORD).read_text())
        hand = json.loads((ROOT / "docs/plans/2026-11-02-adoption-gaps.review.json").read_text())

        def broken(base: dict, change) -> dict:
            record = copy.deepcopy(base)
            change(record)
            return record

        cases = {
            "not an object": ([], "a JSON object"),
            "another schema": (broken(hand, lambda r: r.update(schemaVersion="plan-review-evidence/2")), "schemaVersion"),
            "an absolute plan": (broken(hand, lambda r: r.update(plan="/etc/plan.md")), "plan"),
            "a plan outside the repository": (broken(hand, lambda r: r.update(plan="../plan.md")), "plan"),
            "a short digest": (broken(hand, lambda r: r.update(planSha256="abc")), "planSha256"),
            "an unknown outcome": (broken(hand, lambda r: r.update(outcome="mostly-aligned")), "outcome"),
            "no lenses": (broken(hand, lambda r: r.update(requiredLenses=[])), "requiredLenses"),
            "a repeated lens": (broken(hand, lambda r: r.update(requiredLenses=["adversarial", "adversarial"])), "requiredLenses"),
            "a lens with no review": (broken(hand, lambda r: r["reviews"].pop()), "reviews"),
            "a review of another lens": (broken(hand, lambda r: r["reviews"][0].update(lens="security")), "reviews"),
            "findings that aren't a list": (broken(hand, lambda r: r["reviews"][0].update(findings="none")), "findings"),
            "aligned with a finding": (broken(hand, lambda r: r["reviews"][0].update(findings=[{"id": "a-r1-1", "severity": "P2", "summary": "s"}])),
                                       "unanimous-alignment"),
            "an empty risk": (broken(hand, lambda r: r["residualRisks"].append(" ")), "residualRisks"),
            "risks that aren't a list": (broken(hand, lambda r: r.update(residualRisks="none")), "residualRisks"),
            "ended with no reason": (broken(hand, lambda r: r.update(outcome="ended-without-alignment")), "ended"),
            "ended while aligned": (broken(good, lambda r: r.update(ended={"round": 2, "reason": "x", "at": "t"})), "ended"),
            "a skipped round": (broken(good, lambda r: r["rounds"][1].update(round=3)), "rounds"),
            "a round of no known outcome": (broken(good, lambda r: r["rounds"][0].update(outcome="maybe")), "rounds"),
            "a last round that disagrees": (broken(good, lambda r: r["rounds"][1].update(outcome="unresolved")), "rounds"),
            "a commit that isn't the last round's": (broken(good, lambda r: r.update(planCommit=r["rounds"][0]["candidate"])), "planCommit"),
            "another lens's result": (broken(good, lambda r: r["reviews"][0]["result"].update(lensId=AT)), "lensId"),
            "a short candidate": (broken(good, lambda r: r["rounds"][0].update(candidate="abc123")), "candidate"),
            "a result with no digest": (broken(good, lambda r: r["reviews"][0].update(resultSha256="")), "resultSha256"),
            "a backslashed plan": (broken(hand, lambda r: r.update(plan="docs\\plans\\x.md")), "plan"),
            "ended at an earlier round": (broken(good, lambda r: r.update(
                outcome="ended-without-alignment", ended={"round": 1, "reason": "x", "at": "t"},
                rounds=[dict(x, outcome="unresolved") for x in r["rounds"]])), "ended.round"),
            "a review from a round never run": (broken(good, lambda r: (r["reviews"][0].update(round=3),
                                                                         r["reviews"][0]["result"].update(round=3))),
                                                "round must be one of the review's rounds"),
            "a result from another round than its review's": (broken(good, lambda r: r["reviews"][0]["result"].update(round=1)),
                                                              "lensId and round"),
            "a result whose findings differ": (broken(good, lambda r: r.update(
                outcome="ended-without-alignment", ended={"round": 2, "reason": "x", "at": "t"},
                rounds=[dict(x, outcome="unresolved") for x in r["rounds"]],
                reviews=[dict(r["reviews"][0], findings=[{"id": "x", "severity": "P1", "summary": "s"}]), *r["reviews"][1:]])),
                "its result's findings"),
        }
        for name, (record, word) in cases.items():
            with self.subTest(name):
                path = fx.tmp / "broken.review.json"
                path.write_text(json.dumps(record))
                code, out = self.check(fx, path)
                self.assertEqual(code, 2, out)
                self.assertIn(word, out)
        self.assertEqual(self.check(fx, fx.tmp / "missing.review.json")[0], 2)
        # A broken record ahead of a good one still refuses the whole check.
        bad = fx.tmp / "bad.review.json"
        bad.write_text(json.dumps(broken(hand, lambda r: r.update(outcome="mostly-aligned"))))
        code, out = self.check(fx, bad, fx.main / self.RECORD)
        self.assertEqual(code, 2, out)
        self.assertIn(f"{fx.main / self.RECORD}: a valid plan-review-evidence/1 record", out)


if __name__ == "__main__":
    unittest.main()
