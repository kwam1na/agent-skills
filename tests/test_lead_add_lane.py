"""`lead-add-lane`: the lead adds a lane to a running delivery with one command.

It adds the lane to the delivery file and to the plan's `Units and order` table in one commit, and writes the
lane's ownership and its `lanes[]` row in one state-tool patch. Every refusal leaves the delivery file, the plan, the
record and the commit count as they were. These cases run the installed tool on the fixture delivery, started with
the plan chapter on and a Units table in its plan.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from tests.test_run_delivery import POSIX_ONLY, Fixture, commit, installed_release, plan_delivery, sh

ROOT = Path(__file__).resolve().parents[1]

UNITS = """
## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | FX-2 | `src/one*` |
| {tier} | 2 | FX-3 | `src/two*` |
"""


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class LeadAddLaneTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def lead(self, tiers: int = 1) -> Fixture:
        """The fixture delivery with a Units table in its plan, lane-2's row at tier `tiers`, started."""
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        plan_delivery(fx)
        plan = fx.main / "docs/plan.md"
        plan.write_text(plan.read_text() + UNITS.format(tier=tiers))
        commit(fx.main, "FX-1: the plan's units")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        return fx

    def record(self, fx: Fixture) -> Path:
        return fx.tmp / "record"

    def state(self, fx: Fixture) -> dict:
        return json.loads((self.record(fx) / "state.json").read_text())

    def changes(self, fx: Fixture) -> list[dict]:
        path = self.record(fx) / "changes.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def held(self, fx: Fixture) -> tuple:
        """What a refusal leaves as it was: the delivery file, the plan, the record, its journal and the commits."""
        return ((fx.main / ".agents/deliveries/FX-1.json").read_bytes(), (fx.main / "docs/plan.md").read_bytes(),
                (self.record(fx) / "state.json").read_bytes(), (self.record(fx) / "journal.jsonl").read_bytes(),
                sh("git", "rev-list", "--count", "HEAD", cwd=fx.main).stdout)

    def add(self, fx: Fixture, *argv: str) -> tuple[int, str]:
        return fx.flow("lead-add-lane", *argv, cwd=fx.main)

    def test_the_lane_lands_in_the_delivery_file_the_plan_and_the_record_in_one_commit_and_one_patch(self) -> None:
        fx = self.lead()
        before = self.state(fx)["version"]
        seen = len(self.changes(fx))
        commits = int(sh("git", "rev-list", "--count", "HEAD", cwd=fx.main).stdout)
        code, out = self.add(fx, "--lane", "lane-3", "--grant", "src/three*", "--grant", "docs/three.md",
                             "--why", "A third surface needs its own lane.")
        self.assertEqual(code, 0, out)
        # One commit, holding the delivery file and the plan, with the reason; the checkout is left clean.
        self.assertEqual(int(sh("git", "rev-list", "--count", "HEAD", cwd=fx.main).stdout), commits + 1)
        self.assertEqual(sh("git", "show", "--name-only", "--format=", "HEAD", cwd=fx.main).stdout.split(),
                         [".agents/deliveries/FX-1.json", "docs/plan.md"])
        message = sh("git", "log", "-1", "--format=%B", cwd=fx.main).stdout
        self.assertEqual(message.splitlines()[0], "FX-1: add lane lane-3")
        self.assertIn("A third surface needs its own lane.", message)
        self.assertEqual(sh("git", "status", "--porcelain", cwd=fx.main).stdout, "")
        committed = json.loads(sh("git", "show", "HEAD:.agents/deliveries/FX-1.json", cwd=fx.main).stdout)
        self.assertEqual(committed["lanes"]["lane-3"], {"owns": ["src/three*", "docs/three.md"]})
        self.assertIn("| 1 | 3 |  | `src/three*`, `docs/three.md` |\n",
                      sh("git", "show", "HEAD:docs/plan.md", cwd=fx.main).stdout)
        # c4: one state-tool patch: the version moves by one, the changes file holds one patch line for it, and the
        # state it wrote holds both the lane's ownership and its row.
        state = self.state(fx)
        self.assertEqual(state["version"], before + 1)
        self.assertEqual([line["change"] for line in self.changes(fx)[seen:]], ["patch"])
        self.assertEqual(self.changes(fx)[-1]["version"], before + 1)
        self.assertEqual(state["laneOwnership"]["lane-3"], ["src/three*", "docs/three.md"])
        row = next(entry for entry in state["lanes"] if entry["id"] == "lane-3")
        self.assertEqual((row["title"], row["focus"], row["state"], row["waitingOn"]), ("lane-3", "", "idle", None))
        # lead-own and lead-assign then take the lane.
        code, out = fx.flow("lead-own", "--lane", "lane-3", "--grant", "src/four*", cwd=fx.main)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-assign", "FX-3", "--lane", "lane-3", "--title", "Third", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertEqual(next(p["lane"] for p in self.state(fx)["planned"] if p["subject"] == "FX-3"), "lane-3")

    def test_a_grant_that_overlaps_a_claim_a_shared_output_or_a_region_is_refused_and_nothing_is_written(self) -> None:
        fx = self.lead()
        binding = fx.main / ".agents/orchestration.json"
        binding.write_text(json.dumps({"regenerable": {"listings": [{"path": "src/listing.json"}],
                                                       "regenerators": [{"argv": ["true"]}]}}))
        commit(fx.main, "FX-1: a shared output")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        state = self.state(fx)
        region = {"path": "src/region.txt", "lane": "lane-1", "start": 1, "end": 2, "baseHead": "a" * 40}
        sh("python3", "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py", "--dir", self.record(fx),
           "patch", "--writer", "lead", "--version", str(state["version"]), "--patch",
           json.dumps({"laneRegions": [region]}), env=fx.env)
        cases = [("src/one*", "lane-1 and lane-3 both own"), ("src/listing.json", "src/listing.json is a shared output"),
                 ("src/region*", "whole-file claim conflicts with region claims for src/region.txt")]
        for grant, said in cases:
            with self.subTest(grant=grant):
                before = self.held(fx)
                code, out = self.add(fx, "--lane", "lane-3", "--grant", "src/three*", "--grant", grant)
                self.assertEqual(code, 2, out)
                self.assertIn(said, out)
                self.assertEqual(self.held(fx), before)
                self.assertEqual(sh("git", "status", "--porcelain", cwd=fx.main).stdout, "")

    def test_a_lane_the_delivery_holds_or_one_not_named_lane_n_is_refused_and_nothing_is_written(self) -> None:
        fx = self.lead()
        cases = [(["--lane", "lane-2"], "lane-2 is already a lane of FX-1"),
                 (["--lane", "extra"], "name the lane lane-N"),
                 (["--lane", "lane-03"], "name the lane lane-N")]
        for argv, said in cases:
            with self.subTest(argv=argv):
                before = self.held(fx)
                code, out = self.add(fx, *argv, "--grant", "src/three*")
                self.assertEqual(code, 2, out)
                self.assertIn(said, out)
                self.assertEqual(self.held(fx), before)
        # A lane the record holds but the delivery file doesn't is held too.
        state = self.state(fx)
        sh("python3", "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py", "--dir", self.record(fx),
           "patch", "--writer", "lead", "--version", str(state["version"]), "--set",
           json.dumps({"/laneOwnership": {**state["laneOwnership"], "lane-4": []}}), env=fx.env)
        before = self.held(fx)
        code, out = self.add(fx, "--lane", "lane-4")
        self.assertEqual(code, 2, out)
        self.assertIn("lane-4 is already a lane of FX-1", out)
        self.assertEqual(self.held(fx), before)

    def test_in_a_plan_with_two_tiers_the_row_goes_at_tier_two(self) -> None:
        fx = self.lead(tiers=2)
        code, out = self.add(fx, "--lane", "lane-3")
        self.assertEqual(code, 0, out)
        plan = (fx.main / "docs/plan.md").read_text()
        self.assertIn("| 2 | 2 | FX-3 | `src/two*` |\n| 2 | 3 |  |  |\n", plan)
        self.assertEqual(self.state(fx)["laneOwnership"]["lane-3"], [])

    def test_a_story_added_on_the_new_lane_is_taken_and_before_it_the_refusal_names_lead_add_lane(self) -> None:
        fx = self.lead()
        story = ["lead-plan", "change", "--kind", "added", "--subjects", "FX-9", "--title", "Thing nine",
                 "--text", "Nine works", "--lane", "lane-3", "--why", "Nine joins the delivery."]
        code, out = fx.flow(*story, cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("--lane lane-3 isn't a lane of FX-1", out)
        self.assertIn("lead-add-lane --lane lane-3", out)
        code, out = self.add(fx, "--lane", "lane-3", "--grant", "src/nine*")
        self.assertEqual(code, 0, out)
        code, out = fx.flow(*story, cwd=fx.main)
        self.assertEqual(code, 0, out)
        committed = json.loads(sh("git", "show", "HEAD:.agents/deliveries/FX-1.json", cwd=fx.main).stdout)
        self.assertEqual(committed["stories"]["FX-9"]["lane"], "lane-3")
        self.assertEqual(next(p["lane"] for p in self.state(fx)["planned"] if p["subject"] == "FX-9"), "lane-3")


class LeadAddLaneDocumentationTests(unittest.TestCase):
    """The run-delivery skill's command table names the command, after lead-plan's row."""

    def test_the_skill_page_has_its_row_after_lead_plans(self) -> None:
        rows = [line for line in (ROOT / "skills/run-delivery/SKILL.md").read_text().splitlines()
                if line.startswith("| Lead | `lead-")]
        names = [row.split("`")[1].split()[0] for row in rows]
        self.assertEqual(names[names.index("lead-plan") + 1], "lead-add-lane")
        row = " ".join(rows[names.index("lead-add-lane")].split())
        for phrase in ("`lead-add-lane --lane lane-N [--grant PATTERN]... [--why T]`",
                       "one commit in the lead's checkout and one state-tool patch",
                       "its row in the plan's `Units and order` table, at the highest tier, with no items",
                       "The name must be `lane-N`.", "Each grant takes `lead-own --grant`'s checks",
                       "any refusal writes nothing"):
            self.assertIn(phrase, row)


if __name__ == "__main__":
    unittest.main()
