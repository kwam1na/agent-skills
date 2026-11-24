"""run-delivery's plan preview: `plan-view --plan` builds a plan under review into a record of its own.

The preview reads the plan as committed at HEAD and its plan-review store, needs no delivery file, binding, record,
tracker or forge, and keeps its record per `reviewSlug` at `<main>/<work>/plan-preview/<slug>/`, a folder made once and
never replaced, so whatever follows it keeps following it.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
import types
import unittest
from pathlib import Path

from tests.test_run_delivery import POSIX_ONLY, Fixture, commit, installed_release, sh
from tests.test_run_delivery_plan_review import OC, finding, results, slug_store

PREFIX = "Plan preview (agent review only): "

PLAN = """---
title: Saved addresses
type: feat
reviewSlug: {slug}
---

# Saved addresses - Plan

## Goal

Shoppers keep their addresses
between visits.

A second paragraph is not the outcome.

## Scope

In scope:

1. Saving addresses.

Out of scope:

- Importing addresses.
- Sharing addresses
  between accounts.

The scope ends here.

## Decisions recorded here

- **Per account.** Addresses are kept per account.
  - never per device;
- Exports are CSV only.

## Finish line

All of these hold:

1. Every saved address survives a sign-out.
2. The export opens in a spreadsheet.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | SHOP-91, SHOP-92 | `src/a.py` |
| 2 | 2 | SHOP-93 | `src/b.py` |

## Stories

### SHOP-91. Saved addresses

Lane 1, tier 1, not blocked. Test-first. Files: `src/a.py`.

- Save an address.
- Scenarios:
  - an address saved
    on one device shows on another;
  - a deleted address is gone.
- Done when the sign-out row passes.

### SHOP-92. Address book export

Lane 2, tier 2, after SHOP-91. Files: `src/b.py`.

Scenarios:

- an export holds every address;
- an empty book exports a header.

Done when:

- the export opens.

### SHOP-93. A story with no criteria

It opens with no lane sentence.

## Approval handoffs

1. The operator approves the plan.
2. Product signs off the copy.
"""

OTHER = """---
reviewSlug: {slug}
---

# A plan outside the delivery layout

## Goal

Make the thing.

## Finish line

- The thing is made.

## Out of scope

- Making it twice.
"""


def folder(fx: Fixture, slug: str) -> Path:
    return fx.main.resolve() / ".orchestration/plan-preview" / slug


def files_of(path: Path) -> dict[str, tuple[int, int, int]]:
    """Each file under `path`, by its relative path: its inode, size and modification time."""
    found = {}
    for item in sorted(path.rglob("*")):
        if item.is_file() and not item.is_symlink():
            info = item.stat()
            found[str(item.relative_to(path))] = (info.st_ino, info.st_size, info.st_mtime_ns)
    return found


def journal(fx: Fixture, slug: str) -> list[dict]:
    path = folder(fx, slug) / "journal.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def committer_time(fx: Fixture, commit_id: str) -> str:
    stamp = int(sh("git", "log", "-1", "--format=%ct", commit_id, cwd=fx.main).stdout)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class PlanPreviewTests(unittest.TestCase):
    """One fixture repository for the class; each case previews its own plan, under its own slug."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)
        cls.fx = Fixture(cls.generation, cls.receipt)
        cls.addClassCleanup(cls.fx.close)
        scripts = str(cls.fx.release / "skills/delivery-views/scripts")
        sys.path.insert(0, scripts)
        cls.addClassCleanup(sys.path.remove, scripts)
        import views_common
        cls.views = views_common

    def write(self, path: str, text: str, message: str | None = None) -> str:
        """The plan at `path`, committed unless `message` is None; returns HEAD."""
        target = self.fx.main / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        if message is not None:
            commit(self.fx.main, message)
        return sh("git", "rev-parse", "HEAD", cwd=self.fx.main).stdout.strip()

    def view(self, path: str, code: int = 0) -> str:
        done, out = self.fx.flow("plan-view", "--plan", path, cwd=self.fx.main)
        self.assertEqual(done, code, out)
        return out

    def start(self, path: str) -> subprocess.Popen:
        return subprocess.Popen([sys.executable, "-B", str(self.fx.tool), "plan-view", "--plan", path], cwd=self.fx.main,
                                env=self.fx.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def built(self, slug: str) -> tuple[dict, dict, dict]:
        """The preview's plan.json, state.json and preview.json."""
        here = folder(self.fx, slug)
        return tuple(json.loads((here / name).read_text()) for name in ("plan.json", "state.json", "preview.json"))

    def test_a_delivery_layout_plan_shows_its_stories_criteria_and_sections(self) -> None:
        head = self.write("docs/plans/addresses.md", PLAN.format(slug="addresses"), "addresses: the plan")
        self.view("docs/plans/addresses.md")
        plan, state, facts = self.built("addresses")
        self.views.validate_plan(plan)
        self.assertEqual(plan["outcome"], {"summary": PREFIX + "Saved addresses",
                                           "text": "Shoppers keep their addresses between visits."})
        self.assertEqual(state["meta"]["headline"], plan["outcome"]["summary"])
        self.assertEqual(plan["units"], [
            {"subject": "SHOP-91", "title": "Saved addresses", "proofs": ["review", "handed", "merged"], "criteria": [
                {"id": "shop-91-1", "text": "an address saved on one device shows on another;"},
                {"id": "shop-91-2", "text": "a deleted address is gone."},
                {"id": "shop-91-3", "text": "Done when the sign-out row passes."}]},
            {"subject": "SHOP-92", "title": "Address book export", "proofs": ["review", "handed", "merged"], "criteria": [
                {"id": "shop-92-1", "text": "an export holds every address;"},
                {"id": "shop-92-2", "text": "an empty book exports a header."},
                {"id": "shop-92-3", "text": "the export opens."}]},
            {"subject": "SHOP-93", "title": "A story with no criteria", "proofs": ["review", "handed", "merged"],
             "criteria": []}])
        self.assertEqual(state["planned"], [
            {"subject": "SHOP-91", "title": "Saved addresses", "lane": "lane-1", "stage": "planned", "files": ["src/a.py"]},
            {"subject": "SHOP-92", "title": "Address book export", "lane": "lane-2", "stage": "planned",
             "after": ["SHOP-91"], "files": ["src/b.py"]},
            {"subject": "SHOP-93", "title": "A story with no criteria", "lane": "lane-2", "stage": "planned"}])
        at = committer_time(self.fx, head)
        self.assertEqual(plan["approved"], {"at": at, "snapshot": {"units": plan["units"], "finish": plan["finish"]}})
        self.assertEqual(plan["finish"], [
            {"id": "finish-1", "label": "Finish 1", "text": "Every saved address survives a sign-out.", "at": at,
             "covers": {"criteria": [], "proofs": []}},
            {"id": "finish-2", "label": "Finish 2", "text": "The export opens in a spreadsheet.", "at": at,
             "covers": {"criteria": [], "proofs": []}}])
        # Seeded, so the line map finds the plan and the record agreeing on finish lines.
        self.assertEqual(state["finish"], [{"id": "finish-1", "text": "Every saved address survives a sign-out.",
                                            "met": False},
                                           {"id": "finish-2", "text": "The export opens in a spreadsheet.", "met": False}])
        self.assertEqual(plan["approvals"], [{"text": "The operator approves the plan.", "state": "waiting"},
                                             {"text": "Product signs off the copy.", "state": "waiting"}])
        self.assertEqual(plan["outOfScope"], ["Importing addresses.", "Sharing addresses between accounts."])
        self.assertEqual(plan["preview"], {
            "plan": "docs/plans/addresses.md", "commit": head, "approval": "agents-only", "layout": "delivery",
            "decisions": ["**Per account.** Addresses are kept per account. - never per device;",
                          "Exports are CSV only."]})
        self.assertEqual((plan["requiredLenses"], plan["changes"], plan["evidence"]), ([], [], {}))
        self.assertEqual([kind["id"] for kind in plan["proofKinds"]], ["review", "handed", "merged"])
        self.assertNotIn("planReview", plan)
        self.assertEqual(facts, {"slug": "addresses", "plan": "docs/plans/addresses.md", "commit": head})

    def test_a_plan_outside_the_delivery_layout_still_previews(self) -> None:
        self.write("docs/plans/other.md", OTHER.format(slug="other"), "other: the plan")
        self.view("docs/plans/other.md")
        plan, state, _ = self.built("other")
        self.views.validate_plan(plan)
        self.assertEqual((plan["preview"]["layout"], plan["units"], state.get("planned", [])), ("other", [], []))
        self.assertEqual(plan["outcome"], {"summary": PREFIX + "A plan outside the delivery layout",
                                           "text": "Make the thing."})
        self.assertEqual(plan["outOfScope"], ["Making it twice."])
        self.assertEqual([line["text"] for line in plan["finish"]], ["The thing is made."])
        self.assertEqual(plan["preview"]["decisions"], [])
        self.assertNotIn("approvals", plan)

    def test_a_long_title_and_lists_past_the_caps_are_cut_and_said(self) -> None:
        title = "T" * 700
        goal = "G" * 2100
        lines = "".join(f"- Finish line {n} holds.\n" for n in range(1, 32))
        decisions = "".join(f"- Decision {n}.\n" for n in range(1, 103))
        scenarios = "".join(f"  - scenario {n};\n" for n in range(1, 1002))
        stories = "".join(f"### CAP-{n}. Story {n}\n\nLane 1, tier 1, not blocked.\n\n"
                          + (f"- Scenarios:\n{scenarios}\n" if n == 1 else "") for n in range(1, 202))
        text = (f"---\nreviewSlug: capped\ntitle: {title}\n---\n\n# Capped\n\n## Goal\n\n{goal}\n\n"
                f"## Decisions\n\n{decisions}\n## Finish line\n\n{lines}\n## Stories\n\n{stories}")
        self.write("docs/plans/capped.md", text, "capped: the plan")
        self.view("docs/plans/capped.md")
        plan, state, _ = self.built("capped")
        self.views.validate_plan(plan)
        summary = plan["outcome"]["summary"]
        self.assertTrue(summary.startswith(PREFIX), summary)
        self.assertLessEqual(len(summary), 600)
        self.assertEqual(summary, PREFIX + "T" * (600 - len(PREFIX) - 1) + "\u2026")
        self.assertEqual(state["meta"]["headline"], summary)
        self.assertEqual(len(plan["finish"]), 30)
        self.assertEqual(plan["finish"][-1]["text"], "Finish line 30 holds.")
        self.assertEqual(len(state["finish"]), 30)
        self.assertEqual(len(plan["preview"]["decisions"]), 100)
        self.assertEqual(([unit["subject"] for unit in plan["units"]], [row["subject"] for row in state["planned"]]),
                         ([f"CAP-{n}" for n in range(1, 201)],) * 2)
        self.assertEqual(sum(len(unit["criteria"]) for unit in plan["units"]), 1000)
        said = (" This preview leaves out what is over the plan's caps: 1 story, 1 finish line, 2 decisions, "
                "1 criterion.")
        self.assertEqual(plan["outcome"]["text"], "G" * (2000 - len(said) - 1) + "\u2026" + said)

    def test_a_preview_the_contract_refuses_is_not_written(self) -> None:
        # 1001 long criteria: the cap keeps 1000, and the units with their snapshot copy pass the 1 MiB a plan holds.
        scenarios = "".join(f"  - {'S' * 590} {n};\n" for n in range(1, 1002))
        text = (f"---\nreviewSlug: oversized\n---\n\n# Oversized\n\n## Stories\n\n### BIG-1. Big\n\n"
                f"- Scenarios:\n{scenarios}")
        self.write("docs/plans/oversized.md", text, "oversized: the plan")
        out = self.view("docs/plans/oversized.md", code=2)
        self.assertRegex(out, r"the preview of docs/plans/oversized.md holds [0-9]+ bytes, over the 1 MiB a plan may "
                              r"hold")
        here = folder(self.fx, "oversized")
        self.assertEqual(sorted(path.name for path in here.iterdir()), ["preview.lock"])

    def test_it_reads_the_plan_at_head_and_refuses_one_head_does_not_hold(self) -> None:
        self.write("docs/plans/head.md", PLAN.format(slug="head"))
        out = self.view("docs/plans/head.md", code=2)
        self.assertIn("docs/plans/head.md isn't committed at HEAD", out)
        self.assertFalse(folder(self.fx, "head").exists())
        head = self.write("docs/plans/head.md", PLAN.format(slug="head"), "head: the plan")
        (self.fx.main / "docs/plans/head.md").write_text(
            PLAN.format(slug="head").replace("title: Saved addresses", "title: Not committed"))
        self.view("docs/plans/head.md")
        plan, _, facts = self.built("head")
        self.assertEqual(plan["outcome"]["summary"], PREFIX + "Saved addresses")
        self.assertEqual((plan["preview"]["commit"], facts["commit"]), (head, head))
        sh("git", "checkout", "-q", "--", "docs/plans/head.md", cwd=self.fx.main)
        # A later commit that leaves the plan alone changes nothing: the preview names the plan's own commit.
        here, before = folder(self.fx, "head"), files_of(folder(self.fx, "head"))
        self.write("src/unrelated.txt", "unrelated\n", "an unrelated commit")
        self.assertNotEqual(sh("git", "rev-parse", "HEAD", cwd=self.fx.main).stdout.strip(), head)
        self.view("docs/plans/head.md")
        self.assertEqual(files_of(here), before)
        self.assertEqual(self.built("head")[0]["preview"]["commit"], head)

    def test_a_plan_with_no_review_slug_gets_plan_reviews_advice(self) -> None:
        self.write("docs/plans/No Slug.md", "# No slug\n\n## Goal\n\nNone.\n", "no slug: the plan")
        out = self.view("docs/plans/No Slug.md", code=2)
        self.assertIn("docs/plans/No Slug.md has no review slug, which keeps its review when the plan is renamed. Add "
                      "this front matter at its top and commit it before round 1:\n---\nreviewSlug: no-slug\n---", out)
        self.assertFalse(folder(self.fx, "no-slug").exists())

    def test_the_review_store_s_rounds_are_the_plan_review(self) -> None:
        path, slug = "docs/plans/reviewed.md", "reviewed"
        self.write(path, PLAN.format(slug=slug), "reviewed: the plan")
        store = slug_store(self.fx, slug)
        flow = lambda *argv: self.fx.flow("plan-review", *argv, "--plan", path, cwd=self.fx.main)
        self.assertEqual(flow("prepare", "--lenses", OC)[0], 0)
        results(self.fx, 1, {OC: {"findings": [finding(OC, 1, 1)]}}, store, f"{slug}.plan")
        self.assertEqual(flow("reduce")[0], 0)
        self.write(path, PLAN.format(slug=slug) + "\nRound 1 is answered.\n", "reviewed: round 1 answered")
        self.assertEqual(flow("prepare")[0], 0)
        results(self.fx, 2, {OC: {"resolved": [{"id": "outcome-correctness-r1-1", "state": "fixed",
                                                "resolution": "Answered."}]}}, store, f"{slug}.plan")
        self.assertEqual(flow("reduce")[0], 0)
        self.view(path)
        plan, _, _ = self.built(slug)
        probe = ("import json, sys, types\nsys.path.insert(0, sys.argv[1])\nimport run_delivery as flow\n"
                 "flow.CFG = flow.plan_review_config(types.SimpleNamespace(plan=sys.argv[2], action='reduce', "
                 "lenses=None))\nprint(json.dumps(flow.plan_review_of(flow.CFG)))\n")
        expected = json.loads(sh(sys.executable, "-B", "-c", probe, self.fx.tool.parent, path, cwd=self.fx.main,
                                 env=self.fx.env).stdout)
        self.assertEqual([entry["round"] for entry in expected["rounds"]], [1, 2])
        self.assertEqual(plan["planReview"], expected)
        self.views.validate_plan(plan)
        # Adopted by a delivery: the rounds are read where the store went, and the preview says it was handed.
        moved = self.fx.tmp / "adopted-store"
        store.rename(moved)
        store.mkdir()
        (store / "adopted.json").write_text(json.dumps({"delivery": "FX-9", "store": str(moved), "at": "now"}))
        self.view(path)
        plan, _, _ = self.built(slug)
        self.assertEqual((plan["preview"]["approval"], plan["preview"]["delivery"]), ("handed", "FX-9"))
        self.assertEqual(plan["planReview"], expected)
        self.views.validate_plan(plan)

    def test_a_rebuild_writes_only_what_changed_into_the_same_folder(self) -> None:
        path, slug = "docs/plans/rebuilt.md", "rebuilt"
        self.write(path, PLAN.format(slug=slug), "rebuilt: the plan")
        outside = files_of(self.fx.tmp)
        self.view(path)
        here = folder(self.fx, slug)
        made = files_of(self.fx.tmp)
        self.assertEqual({name: seen for name, seen in made.items() if not name.startswith(
            str(here.relative_to(self.fx.tmp.resolve())))}, outside, "nothing is written outside the preview folder")
        self.assertEqual([entry["kind"] for entry in journal(self.fx, slug)], ["plan.updated"])
        inode, before = here.stat().st_ino, files_of(here)
        self.view(path)
        self.assertEqual(files_of(here), before, "a build with nothing changed writes nothing")
        self.assertEqual(len(journal(self.fx, slug)), 1)
        # A story's new title, the plan's and a finish line's: the plan and the record are replaced, in the same folder,
        # with one plan.updated.
        head = self.write(path, PLAN.format(slug=slug).replace("### SHOP-93. A story with no criteria", "### SHOP-93. Renamed")
                          .replace("title: Saved addresses", "title: Kept addresses")
                          .replace("2. The export opens in a spreadsheet.", "2. The export opens anywhere."),
                          "rebuilt: renamed")
        self.view(path)
        after = files_of(here)
        self.assertEqual(here.stat().st_ino, inode)
        for name in ("plan.json", "state.json"):
            self.assertNotEqual(after[name][0], before[name][0], f"{name} is replaced")
        self.assertEqual([entry["kind"] for entry in journal(self.fx, slug)], ["plan.updated", "plan.updated"])
        plan, state, facts = self.built(slug)
        self.assertEqual(plan["units"][-1]["title"], "Renamed")
        self.assertEqual(state["planned"][-1]["title"], "Renamed")
        self.assertEqual((state["meta"]["headline"], plan["outcome"]["summary"]), (PREFIX + "Kept addresses",) * 2)
        self.assertEqual([line["text"] for line in state["finish"]],
                         ["Every saved address survives a sign-out.", "The export opens anywhere."])
        self.assertEqual((facts["commit"], plan["preview"]["commit"]), (head, head))
        # A story removed from the plan leaves both.
        text = PLAN.format(slug=slug)
        head = self.write(path, text[:text.index("### SHOP-93.")] + text[text.index("## Approval handoffs"):],
                          "rebuilt: SHOP-93 removed")
        self.view(path)
        plan, state, facts = self.built(slug)
        self.assertEqual(facts["commit"], head)
        self.assertEqual([unit["subject"] for unit in plan["units"]], ["SHOP-91", "SHOP-92"])
        self.assertEqual([row["subject"] for row in state["planned"]], ["SHOP-91", "SHOP-92"])
        self.assertEqual(here.stat().st_ino, inode)

    def test_rebuilds_wait_for_the_preview_lock_and_run_one_after_the_other(self) -> None:
        path, slug = "docs/plans/locked.md", "locked"
        self.write(path, PLAN.format(slug=slug), "locked: the plan")
        self.view(path)
        here = folder(self.fx, slug)
        self.write(path, PLAN.format(slug=slug).replace("title: Saved addresses", "title: Locked"),
                   "locked: a revision")
        before = {name: (here / name).read_bytes() for name in ("plan.json", "state.json")}
        with open(here / "preview.lock", "a+") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            waiting = self.start(path)
            self.addCleanup(lambda: waiting.poll() is None and waiting.kill())
            time.sleep(2)
            self.assertIsNone(waiting.poll(), "plan-view waits while the lock is held")
            self.assertEqual({name: (here / name).read_bytes() for name in before}, before)
        out, _ = waiting.communicate(timeout=120)
        self.assertEqual(waiting.returncode, 0, out)
        self.assertEqual([entry["kind"] for entry in journal(self.fx, slug)], ["plan.updated", "plan.updated"])
        self.assertEqual(self.built(slug)[1]["meta"]["headline"], PREFIX + "Locked")
        # Two builds started together both succeed, and the second finds nothing left to write.
        self.write(path, PLAN.format(slug=slug).replace("Saved addresses - Plan", "Together"), "locked: together")
        both = [self.start(path) for _ in range(2)]
        for started in both:
            out, _ = started.communicate(timeout=120)
            self.assertEqual(started.returncode, 0, out)
        self.assertEqual(len(journal(self.fx, slug)), 3)

    def test_a_refused_journal_puts_the_plan_back_so_the_next_build_journals_it(self) -> None:
        path, slug = "docs/plans/refused.md", "refused"
        self.write(path, PLAN.format(slug=slug), "refused: the plan")
        self.view(path)
        here = folder(self.fx, slug)
        before = (here / "plan.json").read_bytes()
        self.write(path, PLAN.format(slug=slug).replace("1. Saving addresses.", "1. Saving them."), "refused: wording")
        (here / "journal.jsonl").chmod(0o444)
        self.addCleanup((here / "journal.jsonl").chmod, 0o644)
        refused = self.start(path)
        out, _ = refused.communicate(timeout=120)
        self.assertNotEqual(refused.returncode, 0, out)
        self.assertEqual((here / "plan.json").read_bytes(), before)
        (here / "journal.jsonl").chmod(0o644)
        self.view(path)
        self.assertEqual([entry["kind"] for entry in journal(self.fx, slug)], ["plan.updated", "plan.updated"])
        self.assertNotEqual((here / "plan.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
