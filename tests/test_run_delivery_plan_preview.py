"""run-delivery's plan preview: `plan-view --plan` builds a plan under review into a record of its own.

The preview reads the plan as committed at HEAD and its plan-review store, needs no delivery file, binding, record,
tracker or forge, and keeps its record per `reviewSlug` at `<main>/<work>/plan-preview/<slug>/`, a folder made once and
never replaced, so whatever follows it keeps following it.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
import types
import unittest
from pathlib import Path

import re
import signal
import socket
import urllib.request

from tests import test_delivery_views_hosted as publisher_fixtures
from tests.test_orchestration_render import INVENTORY, RUNS_PAGE, python_argv
from tests.test_run_delivery import POSIX_ONLY, Fixture, commit, installed_release, sh
from tests.test_run_delivery_plan_review import OC, finding, results, slug_store

hosted = publisher_fixtures.hosted
TOKEN = publisher_fixtures.TOKEN

PREFIX = "Plan preview (agent review only): "
PREVIEW_SECONDS = 120  # the handed finish's bound
HOSTED = {"spec": "delivery-views-config/1", "hosted": {"origin": "https://deliveries.example.com",
                                                        "repositoryId": "repo_A", "tokenEnv": "FIXTURE_PUBLISH_TOKEN"}}
UNPUBLISHED = "plan preview (hosted): not published: FIXTURE_PUBLISH_TOKEN is unset"

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
        # Hosted publishing configured with no token: the preview is built and says it wasn't published, and nothing
        # starts. With neither a live view nor hosted publishing, plan-view builds nothing at all.
        (cls.fx.main / ".agents/delivery-views.json").write_text(json.dumps(HOSTED))
        commit(cls.fx.main, "hosted publishing, with no token here")
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
        if code == 0:
            self.assertEqual(out, UNPUBLISHED + "\n")
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
            "decisions": [{"head": "Per account.", "body": ["Addresses are kept per account.", {"items": ["never per device;"]}]},
                          {"head": "Exports are CSV only."}]})
        self.assertEqual((plan["requiredLenses"], plan["changes"], plan["evidence"]), ([], [], {}))
        self.assertEqual([kind["id"] for kind in plan["proofKinds"]], ["review", "handed", "merged"])
        self.assertNotIn("planReview", plan)
        self.assertEqual(facts, {"slug": "addresses", "plan": "docs/plans/addresses.md", "title": "Saved addresses",
                                 "commit": head})
        self.assertFalse((here := folder(self.fx, "addresses") / "live.json").exists(), here)

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
        here = folder(self.fx, slug)

        def finish() -> str:
            """A handed finish started by plan-view, through to its finisher's exit: its log's last line."""
            done, out = self.fx.flow("plan-view", "--plan", path, cwd=self.fx.main)
            self.assertEqual((done, out), (0, "plan preview: handed to delivery FX-9; its preview stops once the "
                                              "hosted page shows that\n"))
            finisher = json.loads((here / "handed.json").read_text())["finisher"]
            deadline = time.monotonic() + 60
            while runs(finisher):
                self.assertLess(time.monotonic(), deadline, "the handed finish ends")
                time.sleep(0.1)
            return (here / "finish.log").read_text().splitlines()[-1]
        # live.json names a pid that no longer runs the live view: the finisher says so and leaves it alone.
        bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: (bystander.kill(), bystander.wait()))
        (here / "live.json").write_text(json.dumps({"pid": bystander.pid, "port": 9}))
        self.assertTrue(finish().endswith(f" live: left pid {bystander.pid} alone: live.json names it, but it no "
                                          "longer runs the live view"))
        self.assertTrue(runs(bystander.pid))
        # Once that pid has gone, a finish started again says the view isn't running.
        bystander.kill()
        bystander.wait()
        (here / "handed.json").unlink()
        self.assertTrue(finish().endswith(" live: not running"))
        # With no live view recorded at all, it says the same.
        (here / "live.json").unlink()
        (here / "handed.json").unlink()
        self.assertTrue(finish().endswith(" live: not running"))
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
        # On a first build the plan written is removed, so the next build is a first build again.
        first, slug = "docs/plans/refused-first.md", "refused-first"
        self.write(first, PLAN.format(slug=slug), "refused-first: the plan")
        fresh = folder(self.fx, slug)
        fresh.mkdir(parents=True)
        (fresh / "journal.jsonl").touch(0o444)
        self.addCleanup((fresh / "journal.jsonl").chmod, 0o644)
        refused = self.start(first)
        out, _ = refused.communicate(timeout=120)
        self.assertNotEqual(refused.returncode, 0, out)
        self.assertFalse((fresh / "plan.json").exists())
        (fresh / "journal.jsonl").chmod(0o644)
        self.view(first)
        self.assertEqual([entry["kind"] for entry in journal(self.fx, slug)], ["plan.updated"])
        self.assertTrue((fresh / "plan.json").exists())


LINE_MAP = {"id": "line-map", "argv": ["python3", "-B", ".agent-skills/current/skills/delivery-views/scripts/render.py",
                                       "line-map", "--config", ".agents/delivery-views.json", "--binding",
                                       ".agents/orchestration.json", "--state-dir", "{stateDirectory}"],
            "output": "line-map.html", "timeoutSeconds": 30}
# The fixture's HTTPS goes to the fake origin, and the handed finisher, alone, reads the case's clock.
SHIM = """import http.client, pathlib, sys, time
Real = http.client.HTTPConnection
def fixture(host, port=None, timeout=None, context=None):
    assert host == "deliveries.example.com"
    return Real("127.0.0.1", {port}, timeout=timeout)
http.client.HTTPSConnection = fixture
if "PREVIEW_SLOW_PUBLISHER" in __import__("os").environ and sys.argv[0].endswith("hosted.py") and "start" in sys.argv:
    time.sleep(8)  # past the 5 seconds run-delivery gives the publisher's start
if "--finish-handed" in sys.argv:
    real, clock = time.time, pathlib.Path({clock!r})
    time.time = lambda: real() + (float(clock.read_text()) if clock.exists() else 0.0)
    if "PREVIEW_SLOW_STOP" in __import__("os").environ:  # the finisher dwells after each signal it sends
        import os
        def dwelling(send):
            return lambda pid, sig: (send(pid, sig), time.sleep(1))[0]
        os.kill, os.killpg = dwelling(os.kill), dwelling(os.killpg)
"""
LOCAL = re.compile(r"^plan preview: (http://127\.0\.0\.1:([0-9]+)/)$", re.M)
LINK = re.compile(r"^plan preview \(hosted\): (https://deliveries\.example\.com/d/[0-9a-f]{64})$", re.M)


def plan_in(value):
    """The delivery-plan/1 document inside a published cut or a page's plan block."""
    if isinstance(value, dict):
        if value.get("schemaVersion") == "delivery-plan/1":
            return value
        for item in value.values():
            found = plan_in(item)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = plan_in(item)
            if found is not None:
                return found
    return None


def runs(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return sh("ps", "-o", "stat=", "-p", pid, check=False).stdout.strip()[:1] not in ("", "Z")


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class PlanDecisionTests(unittest.TestCase):
    """How the plan reader turns a plan's decisions section into headed decisions, without a fixture repository."""

    @classmethod
    def setUpClass(cls) -> None:
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "skills/run-delivery/scripts/run_delivery.py"
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(path.parent))
        try:
            spec = importlib.util.spec_from_file_location("run_delivery_for_decisions", path)
            cls.rd = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.rd)
        finally:
            sys.path.remove(str(path.parent))
            sys.dont_write_bytecode = previous

    def read(self, section: str) -> list:
        return self.rd.plan_decisions("## Decisions\n\n" + section)

    def test_a_bold_lead_heads_the_decision_and_its_paragraphs_and_bullets_are_kept(self) -> None:
        self.assertEqual(self.read(
            "Read me first, not a decision.\n\n"
            "1. **The supervisor words each item.** Headlines embed the actor, so the page\n"
            "   can't swap a name in. Three wordings:\n"
            "   - `said`, for the seat that wrote\n"
            "     the list;\n"
            "   - `saidAll`, for no seat.\n"
            "\n"
            "   The hosted page reads `saidAll`.\n"
            "2. **The summary is the pass's.**\n"
            "## Next\n- not a decision\n"), [
            {"head": "The supervisor words each item.",
             "body": ["Headlines embed the actor, so the page can't swap a name in. Three wordings:",
                      {"items": ["`said`, for the seat that wrote the list;", "`saidAll`, for no seat."]},
                      "The hosted page reads `saidAll`."]},
            {"head": "The summary is the pass's."}])

    def test_a_lead_that_runs_on_into_its_sentence_ends_at_the_bold(self) -> None:
        self.assertEqual(self.read("- **Compounding is the lead's work**, in the `delivery` scope.\n"
                                   "- **No round bound**; it runs to alignment.\n"
                                   "- **Two lenses**. Both of them.\n"), [
            {"head": "Compounding is the lead's work.", "body": ["In the `delivery` scope."]},
            {"head": "No round bound.", "body": ["It runs to alignment."]},
            {"head": "Two lenses.", "body": ["Both of them."]}])

    def test_without_a_bold_lead_the_first_sentence_heads_it(self) -> None:
        self.assertEqual(self.read("- Exports are CSV only. Nothing else is asked for, and **Excel** waits.\n"
                                   "- One sentence, with no full stop\n"), [
            {"head": "Exports are CSV only.", "body": ["Nothing else is asked for, and Excel waits."]},
            {"head": "One sentence, with no full stop"}])

    def test_a_long_decision_is_kept_whole_and_only_an_overlong_block_is_cut(self) -> None:
        long, overlong = "a" * 500 + ".", "b" * 700
        decided = self.read(f"- **Long.** {long}\n\n  {long}\n\n  {overlong}\n")
        self.assertEqual(decided, [{"head": "Long.", "body": [long, long, "b" * 599 + "\u2026"]}])
        many = self.read("- **Many.** One.\n" + "".join(f"  - item {n}\n" for n in range(25)) + "".join(
            f"\n  Paragraph {n}.\n" for n in range(25)))
        self.assertEqual(len(many[0]["body"]), self.rd.PLAN_DECISION_BLOCKS)
        self.assertEqual(len(many[0]["body"][1]["items"]), self.rd.PLAN_DECISION_ITEMS)


class ServedPreviewTests(unittest.TestCase):
    """plan-view and the plan-review commands serving and publishing the preview, end to end: one fixture repository,
    a fake hosted origin that the publisher reaches through a shim, and real live servers and watchers, each stopped by
    its recorded pid."""

    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)
        cls.fx = fx = Fixture(cls.generation, cls.receipt)
        cls.addClassCleanup(fx.close)
        cls.origin = publisher_fixtures.ReceiverTests("test_actual_lost_response_reconciles_and_confirmation_is_fresh_"
                                                       "collection_only")
        cls.origin.setUp()
        cls.addClassCleanup(cls.origin.doCleanups)
        shim = fx.tmp / "shim"
        shim.mkdir()
        cls.clock = fx.tmp / "finisher-clock"
        (shim / "sitecustomize.py").write_text(SHIM.format(port=cls.origin.server.server_port, clock=str(cls.clock)))
        cls.marker = fx.tmp / "tracker-publisher-ran"
        cls.env = {**fx.env, "FIXTURE_PUBLISH_TOKEN": TOKEN, "PYTHONPATH": str(shim)}

    def setUp(self) -> None:
        self.clock.unlink(missing_ok=True)
        self.origin.api.code = None
        self.origin.api = publisher_fixtures.FakeAPI()

    def configure(self, *, live: bool | str = True, publish: bool | str = True) -> None:
        """The binding and delivery-views configuration: a liveHome naming the line map (or, with live="builtin", a
        builtin renderer), and hosted publishing (or, with publish="invalid", a hosted member the publisher refuses),
        each on or off; committed when they change."""
        main = self.fx.main
        binding = main / ".agents/orchestration.json"
        views = main / ".agents/delivery-views.json"
        renderers = [LINE_MAP, {"id": "page", "builtin": "static-page", "output": "delivery.html"}]
        state = {"directory": ".orchestration/state", "renderers": renderers,
                 "publishers": [{"id": "tracker", "argv": [sys.executable, "-c",
                                                           f"open({str(self.marker)!r}, 'w').close()"]}]}
        if live:
            state["liveHome"] = "page" if live == "builtin" else "line-map"
        for path, wanted in ((binding, {"base": {"ref": "feature/FX-1", "remote": "origin"}, "state": state}),
                             (views, None if not publish else publisher_fixtures.CFG if publish is True else
                              {**publisher_fixtures.CFG, "hosted": {**publisher_fixtures.CFG["hosted"],
                                                                    "origin": "http://invalid.example.com"}})):
            if wanted is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(json.dumps(wanted, indent=2) + "\n")
        if sh("git", "status", "--porcelain", cwd=main).stdout.strip():
            commit(main, "the preview's configuration")

    def plan(self, slug: str, text: str | None = None) -> str:
        path = f"docs/plans/{slug}.md"
        target = self.fx.main / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text if text is not None else PLAN.format(slug=slug))
        commit(self.fx.main, f"{slug}: the plan")
        self.addCleanup(self.stop_all, slug)
        return path

    def stop_all(self, slug: str) -> None:
        """What the case's preview started, stopped by the pids its folder records."""
        here = folder(self.fx, slug)
        with contextlib.suppress(OSError, hosted.PublishError):
            hosted.stop(here)
        live = json.loads((here / "live.json").read_text()) if (here / "live.json").exists() else {}
        if live.get("pid") and runs(live["pid"]):
            os.killpg(live["pid"], signal.SIGTERM)

    def flow(self, *argv, env: dict | None = None, code: int = 0) -> str:
        done, out = self.fx.flow(*argv, cwd=self.fx.main, env=env or self.env)
        self.assertEqual(done, code, out)
        return out

    def page_plan(self, address: str) -> dict | None:
        with urllib.request.urlopen(address, timeout=10) as answer:
            page = answer.read().decode()
        found = re.search(r'<script type="application/json" id="delivery-plan">(.*?)</script>', page, re.S)
        return plan_in(json.loads(found.group(1))) if found else None

    def cut_plan(self) -> dict | None:
        cuts = [call for call in self.origin.api.calls if call[1] == "snapshots"]
        return plan_in(json.loads(cuts[-1][2])) if cuts else None

    def until(self, check, what: str, seconds: float = 60) -> None:
        deadline = time.monotonic() + seconds
        while not check():
            self.assertLess(time.monotonic(), deadline, what)
            time.sleep(0.2)

    def pids(self, slug: str) -> tuple[int, int]:
        here = folder(self.fx, slug)
        return (json.loads((here / "live.json").read_text())["pid"],
                json.loads((here / hosted.OWNER).read_text())["pid"])

    def review_round(self, path: str, slug: str, n: int) -> str:
        """One plan-review round, prepared and reduced: what reduce printed."""
        store = slug_store(self.fx, slug)
        if n > 1:
            target = self.fx.main / path
            target.write_text(target.read_text() + f"\nRound {n - 1} is answered.\n")
            commit(self.fx.main, f"{slug}: round {n - 1} answered")
        self.flow("plan-review", "prepare", "--plan", path, *(["--lenses", OC] if n == 1 else []))
        given = {OC: {"findings": [finding(OC, n, 1)]}} if n == 1 else {
            OC: {"resolved": [{"id": "outcome-correctness-r1-1", "state": "fixed", "resolution": "Answered."}]}}
        results(self.fx, n, given, store, f"{slug}.plan")
        return self.flow("plan-review", "reduce", "--plan", path)

    def test_a_preview_is_served_published_and_followed_through_a_review_round(self) -> None:
        self.configure()
        path, slug = self.plan("served"), "served"
        out = self.flow("plan-view", "--plan", path)
        local, link = LOCAL.search(out), LINK.search(out)
        self.assertEqual(out, f"plan preview: {local.group(1)}\nplan preview (hosted): {link.group(1)}\n")
        here = folder(self.fx, slug)
        built = json.loads((here / "plan.json").read_text())
        shown = self.page_plan(local.group(1))
        self.assertEqual((shown["preview"], shown["outcome"]), (built["preview"], built["outcome"]))
        self.assertEqual([unit["subject"] for unit in shown["units"]], ["SHOP-91", "SHOP-92", "SHOP-93"])
        self.until(lambda: self.cut_plan() is not None, "the origin holds a cut")
        cut = self.cut_plan()
        self.assertEqual(cut["preview"], built["preview"])
        self.assertTrue(cut["outcome"]["summary"].startswith(PREFIX))
        live_pid, watcher_pid = self.pids(slug)
        inode = here.stat().st_ino
        # The live server runs the derived configuration alone, so the binding's tracker publisher never runs.
        argv = sh("ps", "-ww", "-o", "args=", "-p", live_pid).stdout
        config = json.loads(re.search(r"--state-config (\{.*\}) --preview", argv).group(1))
        self.assertEqual(config, {"directory": str(here), "renderers": [LINE_MAP], "liveHome": "line-map"})
        # A round reduced reaches the page and the cut, with no restart of either process.
        reduced = self.review_round(path, slug, 1)
        self.assertEqual([line for line in reduced.splitlines() if line.startswith("plan preview")],
                         [f"plan preview: {local.group(1)}", f"plan preview (hosted): {link.group(1)}"])
        rounds = lambda plan: [entry["round"] for entry in (plan or {}).get("planReview", {}).get("rounds", [])]
        self.until(lambda: rounds(self.page_plan(local.group(1))) == [1], "the page shows round 1")
        self.until(lambda: rounds(self.cut_plan()) == [1], "the origin holds round 1")
        self.assertEqual((self.pids(slug), here.stat().st_ino), ((live_pid, watcher_pid), inode))
        self.assertFalse(self.marker.exists(), "the binding's tracker publisher never runs for a preview")
        # Every sidecar is the preview folder's own: nothing lands beside it or in a delivery's record folder.
        self.assertTrue({"live.json", "hosted-view-config.json", "hosted-view-binding.json", hosted.OWNER,
                         hosted.IDENTITY} <= {item.name for item in here.iterdir()})
        self.assertEqual([item.name for item in here.parent.iterdir() if not item.is_dir()], [])
        self.assertFalse((self.fx.main / ".orchestration/state").exists())
        # The standing server's runs page lists the running preview, with both links.
        standing = self.fx.tmp / "standing"
        (standing / "runs-store").mkdir(parents=True)
        (standing / "empty").mkdir()
        tools = self.fx.tmp / "tools"
        tools.mkdir(exist_ok=True)
        config = {"directory": str(standing / "state"), "publishers": [],
                  "renderers": [{"id": "runs", "output": "runs.html", "argv": [*python_argv(RUNS_PAGE, tools, "runs"),
                                 str(standing / "runs-store"), str(standing / "runs.count")]}],
                  "liveRuns": {"renderer": "runs", "inventory": {"argv": [*python_argv(INVENTORY, tools, "inventory"),
                               str(standing / "runs-store"), str(standing / "inventory.count"), "ok"],
                               "timeoutSeconds": 10}}}
        server = subprocess.Popen([sys.executable, "-B", str(self.fx.release / "skills/orchestrate-work/scripts/"
                                   "render_state.py"), "live", "--dir", str(standing / "empty"), "--repo",
                                   str(self.fx.main), "--state-config", json.dumps(config)],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: server.poll() is None and (os.killpg(server.pid, signal.SIGTERM), server.wait(10)))
        address = "http://" + json.loads(server.stdout.readline())["live"] + "/view/runs.html"
        row = {"live": f"127.0.0.1:{local.group(2)}", "plan": path, "title": "Saved addresses", "hosted": link.group(1)}

        def listed() -> bool:
            with urllib.request.urlopen(address, timeout=10) as answer:
                said = re.search(r'id="said">(.*?)</script>', answer.read().decode(), re.S)
            return said is not None and json.loads(json.loads(said.group(1))["previews"] or "[]") == [row]
        self.until(listed, "the runs page lists the preview")
        # --stop stops what the preview recorded, and only that.
        bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: (bystander.kill(), bystander.wait()))
        stopped = self.flow("plan-view", "--plan", path, "--stop")
        self.assertIn("plan preview (hosted): stopped", stopped)
        self.assertIn(f"stopped the live view (pid {live_pid})", stopped)
        self.until(lambda: not runs(live_pid) and not runs(watcher_pid), "both preview processes stop", 15)
        self.assertTrue(runs(bystander.pid) and server.poll() is None)

    def test_adoption_hands_the_preview_over_and_its_processes_stop(self) -> None:
        self.configure()
        path, slug = self.plan("adopted"), "adopted"
        self.flow("plan-view", "--plan", path)
        for n in (1, 2):
            self.review_round(path, slug, n)
        live_pid, watcher_pid = self.pids(slug)
        expected = f"adopted the plan review (2 rounds) from {slug_store(self.fx, slug)} into "
        # The finisher dwells a second after each signal, so a write after the view has gone can't hide in the poll.
        said = self.adopt(slug, env={**self.env, "PREVIEW_SLOW_STOP": "1"})
        self.assertTrue(said.startswith(expected), said)
        self.until(lambda: not runs(live_pid) and not runs(watcher_pid), "the handed finish stops both", 90)
        here = folder(self.fx, slug)
        # Once both have gone, the finish writes nothing more in the folder, up to its finisher's exit.
        stopped = files_of(here)
        finisher = json.loads((here / "handed.json").read_text())["finisher"]
        self.until(lambda: not runs(finisher), "the finisher exits", 30)
        self.assertEqual(files_of(here), stopped)
        self.assertRegex((here / "finish.log").read_text().splitlines()[-1],
                         rf" live: stopping the live view \(pid {live_pid}\) on port [0-9]+$")
        built = json.loads((here / "plan.json").read_text())
        self.assertEqual((built["preview"]["approval"], built["preview"]["delivery"]), ("handed", "FX-9"))
        self.assertEqual([entry["round"] for entry in built["planReview"]["rounds"]], [1, 2])
        self.assertEqual(self.cut_plan()["preview"]["approval"], "handed")
        # A later plan-view on the adopted store changes nothing.
        before = files_of(here)
        out = self.flow("plan-view", "--plan", path)
        self.assertEqual(out, f"plan preview: the review of {path} was adopted by delivery FX-9; its plan shows on that "
                              "delivery's own views\n")
        self.assertEqual(files_of(here), before)

    def test_plan_open_takes_the_preview_over_and_only_the_planning_record_is_rebuilt(self) -> None:
        self.configure()
        path, slug = self.plan("opened"), "opened"
        self.flow("plan-view", "--plan", path)
        self.until(lambda: self.cut_plan() is not None, "the origin holds a cut")
        live_pid, watcher_pid = self.pids(slug)
        env = {**self.env, "RUN_DELIVERY_SUPERVISOR": "off"}
        out = self.flow("plan-open", "--plan", path, "--key", "FX-7", env=env)
        record = Path(re.search(r"planning record of FX-7 at (\S+):", out).group(1))
        self.addCleanup(self.stop_record, record)
        self.assertIn("plan preview: handed to the planning record of FX-7", out)
        # The handed finish leaves the preview's last cut handed to the key, with its server and publisher gone.
        self.until(lambda: not runs(live_pid) and not runs(watcher_pid), "the handed finish stops both", 90)
        here = folder(self.fx, slug)
        finisher = json.loads((here / "handed.json").read_text())["finisher"]
        self.until(lambda: not runs(finisher), "the finisher exits", 30)
        handed = json.loads((here / "plan.json").read_text())["preview"]
        self.assertEqual((handed["approval"], handed["delivery"]), ("handed", "FX-7"))
        cuts = [plan_in(json.loads(call[2])) for call in self.origin.api.calls if call[1] == "snapshots"]
        own = [cut for cut in cuts if cut and "endorsements" not in cut["preview"]]
        self.assertEqual((own[-1]["preview"]["approval"], own[-1]["preview"]["delivery"]), ("handed", "FX-7"))
        # A plan-review round from here rebuilds the planning record's plan, and no preview folder is touched.
        before = files_of(here)
        reduced = self.review_round(path, slug, 1)
        self.assertIn("plan: the planning record of FX-7: ", reduced)
        built = json.loads((record / "plan.json").read_text())
        self.assertEqual(([entry["round"] for entry in built["planReview"]["rounds"]], built["preview"]["endorsements"]),
                         ([1], []))
        self.assertEqual(files_of(here), before)

    def stop_record(self, record: Path) -> None:
        """The planning record's hosted publisher, which plan-open started, stopped by the pid its folder records."""
        with contextlib.suppress(OSError, hosted.PublishError):
            hosted.stop(record)

    def adopt(self, slug: str, delivery: str = "FX-9", env: dict | None = None) -> str:
        """adopt_plan_review on the slug's store into `delivery`, as lead-setup and lead-init call it: what it
        returned."""
        probe = ("import json, sys\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\nimport run_delivery as flow\n"
                 "c = flow.CFG = flow.Config(Path.cwd(), need_delivery=False, pick=False)\n"
                 "print(json.dumps(flow.adopt_plan_review(c, Path(sys.argv[2]), sys.argv[3])))\n")
        done = sh(sys.executable, "-B", "-c", probe, self.fx.tool.parent, slug_store(self.fx, slug), delivery,
                  cwd=self.fx.main,
                  env=env or self.env)
        self.assertEqual(done.stdout.count("\n"), 1, done.stdout)
        return json.loads(done.stdout)

    def test_with_the_origin_silent_the_handed_finish_waits_its_bound_then_stops(self) -> None:
        self.configure()
        path, slug = self.plan("silent"), "silent"
        self.flow("plan-view", "--plan", path)
        self.until(lambda: self.cut_plan() is not None, "the origin holds a cut")
        live_pid, watcher_pid = self.pids(slug)
        # From here the origin takes no cut: every snapshot waits until the case ends.
        released = threading.Event()
        self.addCleanup(released.set)
        answering = self.origin.api

        def silent(method, operation, body=None, headers=None):
            if operation == "snapshots":
                released.wait(120)
            return answering(method, operation, body, headers)
        self.origin.api = silent
        self.review_round(path, slug, 1)
        here = folder(self.fx, slug)
        identity = lambda: json.loads((here / hosted.IDENTITY).read_text())
        self.until(lambda: identity()["pending"] is not None, "the round's cut waits at the origin")
        pending = identity()["pending"]
        self.adopt(slug, "FX-8")
        self.until(lambda: json.loads((here / "plan.json").read_text())["preview"]["approval"] == "handed",
                   "the finisher rebuilds the preview as handed")
        log = here / "finish.log"
        waiting = re.compile(r"waiting for a cut after sequence [0-9]+ until ([0-9.]+)")
        self.until(lambda: log.exists() and waiting.search(log.read_text()), "the finisher waits")
        deadline = float(waiting.search(log.read_text()).group(1))
        # The round's cut is still pending at the origin: the finisher counts it, so its acceptance can't end the wait.
        self.assertIn(f"waiting for a cut after sequence {pending['sequence']} until", log.read_text())
        self.assertAlmostEqual(deadline - time.time(), PREVIEW_SECONDS, delta=30)
        # Its clock moved to five seconds before its bound: it still waits two seconds on.
        self.clock.write_text(str(deadline - 5 - time.time()))
        time.sleep(2)
        self.assertTrue(runs(live_pid) and runs(watcher_pid), "both still run just before the bound")
        self.clock.write_text(str(deadline + 1 - time.time()))
        self.until(lambda: not runs(live_pid) and not runs(watcher_pid), "both stop after the bound", 30)

    def test_what_runs_follows_what_the_repository_configures(self) -> None:
        cases = [  # (live, hosted, token, the lines printed, whether a record is built)
            ("neither", False, False, True, [], False),
            ("builtin", "builtin", False, True, [], False),
            ("hosted only", False, True, True, ["hosted"], True),
            ("live only", True, False, True, ["local"], True),
            ("no token", True, True, False, ["local", "unpublished"], True),
            ("invalid hosted", False, "invalid", True, ["invalid"], True),
            ("slow publisher", True, True, True, ["local", "unavailable"], True),
        ]
        for name, live, publish, token, lines, built in cases:
            with self.subTest(name):
                self.configure(live=live, publish=publish)
                slug = "configured-" + re.sub(r"[^a-z]+", "-", name)
                path = self.plan(slug)
                env = dict(self.env) if token else {k: v for k, v in self.env.items() if k != "FIXTURE_PUBLISH_TOKEN"}
                if name == "slow publisher":
                    env["PREVIEW_SLOW_PUBLISHER"] = "1"
                out = self.flow("plan-view", "--plan", path, env=env)
                said = [("local" if LOCAL.fullmatch(line) else "hosted" if LINK.fullmatch(line) else
                         "unpublished" if line == UNPUBLISHED else
                         "invalid" if line == "plan preview (hosted): not published: hosted: configuration_invalid" else
                         "unavailable" if line == "plan preview (hosted): not published: publisher_unavailable" else
                         line) for line in out.splitlines()]
                self.assertEqual(said, lines)
                here = folder(self.fx, slug)
                self.assertEqual(here.exists(), built)
                self.assertEqual((here / "live.json").exists(), "local" in lines)
                self.assertEqual((here / hosted.OWNER).exists(), "hosted" in lines)
                if built:
                    # preview.json keeps a hosted link only when the publisher gave one.
                    facts = json.loads((here / "preview.json").read_text())
                    self.assertEqual(facts.get("hosted"), LINK.search(out).group(1) if "hosted" in lines else None)
                else:
                    # --stop with no preview says so, and stops nothing.
                    self.assertEqual(self.flow("plan-view", "--plan", path, "--stop"),
                                     f"plan preview: {path} has no preview\n")
                if not built:
                    # The plan-review commands say nothing of a preview either, and build none.
                    reduced = self.review_round(path, slug, 1)
                    ended = self.flow("plan-review", "end", "--plan", path, "--reason", "The operator decided.")
                    self.assertNotIn("plan preview", reduced + ended)
                    self.assertFalse(here.exists())

    def test_the_live_view_is_reused_from_its_generation_restarted_from_another_and_asks_for_its_derived_port(self):
        self.configure(publish=False)
        path, slug = self.plan("ported"), "ported"
        top = str(self.fx.main.resolve())
        derived = self.fx.call("derived_live_port", top, "plan-ported", cwd=self.fx.main)
        env = {k: v for k, v in self.env.items() if k != "RUN_DELIVERY_LIVE_PORT"}
        # The test holds the derived port, so the view binds a free one and records the one it asked for.
        with socket.socket() as held:
            held.bind(("127.0.0.1", derived))
            held.listen()
            first = self.flow("plan-view", "--plan", path, env=env)
            live = json.loads((folder(self.fx, slug) / "live.json").read_text())
        self.assertEqual(live["fallback"]["wanted"], derived)
        self.assertEqual(first, f"plan preview: http://127.0.0.1:{live['port']}/\n")
        # The same generation's view is reused.
        self.assertEqual(self.flow("plan-view", "--plan", path, env=env), first)
        self.assertEqual(json.loads((folder(self.fx, slug) / "live.json").read_text())["pid"], live["pid"])
        # One from another generation's render_state.py is stopped by its pid and started again.
        recorded = folder(self.fx, slug) / "live.json"
        recorded.write_text(json.dumps({**live, "renderState": "/another/generation/render_state.py"}))
        self.flow("plan-view", "--plan", path, env=env)
        self.until(lambda: not runs(live["pid"]), "the other generation's view stops", 15)
        again = json.loads(recorded.read_text())
        self.assertNotEqual(again["pid"], live["pid"])
        self.assertTrue(runs(again["pid"]))

    def test_a_preview_that_fails_never_changes_a_plan_review_command_or_an_adoption(self) -> None:
        self.configure(publish=False)
        scenarios = "".join(f"  - {'S' * 590} {n};\n" for n in range(1, 1002))
        slug = "broken"
        path = self.plan(slug, f"---\nreviewSlug: {slug}\n---\n\n# Broken\n\n## Stories\n\n### BIG-1. Big\n\n"
                               f"- Scenarios:\n{scenarios}")
        out = self.flow("plan-view", "--plan", path, code=2)
        self.assertIn(f"the preview of {path} holds", out)
        refused = f"plan preview: not built: the preview of {path} holds"
        for argv in (["prepare", "--lenses", OC], ["reduce"], ["end", "--reason", "The operator decided."]):
            if argv[0] == "reduce":
                results(self.fx, 1, {OC: {"findings": [finding(OC, 1, 1)]}}, slug_store(self.fx, slug), f"{slug}.plan")
            said = self.flow("plan-review", *argv, "--plan", path)
            self.assertEqual([line for line in said.splitlines() if line.startswith("plan preview")][0][:len(refused)],
                             refused)
        # Adoption: with no preview folder, or with a handed finish that fails, it says and does what it always did.
        self.configure(live=False, publish=False)
        for name in ("none", "failing"):
            with self.subTest(name):
                other = f"adopt-{name}"
                path = self.plan(other)
                self.review_round(path, other, 1)
                here = folder(self.fx, other)
                if name == "failing":
                    here.mkdir(parents=True)
                    (here / "preview.json").write_text(json.dumps({"slug": other, "plan": path}))
                    (here / "finish.log").mkdir()  # the finisher's log can't be opened
                before = files_of(self.fx.main / ".orchestration/plan-preview")
                said = self.adopt(other, f"FX-{7 if name == 'none' else 6}")
                self.assertTrue(said.startswith("adopted the plan review (1 round) from "), said)
                # Neither writes a preview: none was there, and the failing one never marks itself handed.
                self.assertEqual(files_of(self.fx.main / ".orchestration/plan-preview"), before)
        # With a preview configured again, plan-view on the store adopted with none says where its plan shows.
        self.configure(publish=False)
        delivery = "FX-7"
        self.assertEqual(self.flow("plan-view", "--plan", "docs/plans/adopt-none.md"),
                         f"plan preview: the review of docs/plans/adopt-none.md was adopted by delivery {delivery}; its "
                         "plan shows on that delivery's own views\n")
        self.assertFalse(folder(self.fx, "adopt-none").exists())


if __name__ == "__main__":
    unittest.main()
