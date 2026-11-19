"""A story lens on request: declared and available, run only when the lane's `--lenses` or the story's brief names it.

One fixture delivery per class, built by the real commands and kept (see `kept_fixture`): beside the mandated pair it
declares `lens.standards` for story use, `lens.data` for story use and on request, and `lens.p` for plan use only, and,
with Node, `lens.h` from harness.config.ts, a story lens put on request through `review.default`. Its stories carry
criteria, so the plan chapter is on, and FX-3, the story under review, needs no in-app checks.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path

from tests.test_run_delivery import (POSIX_ONLY, Fixture, commit, doctor_lines, installed_release, kept_fixture,
                                     setup_delivery, sh, write_results)

RUN_QUIETLY = True

OC, AT = "lens.outcome-correctness", "lens.adversarial-testing"
CHARTERS = {
    "standards": "# Standards lens\n\nChecks the change against the repository's standards.\n",
    "data": "# Data lens\n\n## Purpose\n\nChecks what the change does to stored data.\n",
    "p": "# Plan lens P\n\nAsks whether the plan is feasible.\n",
    "h": "# Harness lens H\n\nA harness lens, run only on request.\n",
}
LANE_BRIEF_ORIGIN = "the story's brief asks for it"


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class LensOnRequestTests(unittest.TestCase):
    """`default: false` keeps a story lens out of round 1's default set; the lane's `--lenses` or the story's brief
    brings it in, the round's meta says which, and a lens the brief asks for can't be left out."""

    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def build_delivery(self, brief: bool = False, on_request: bool = True) -> tuple[Fixture, Path, bool]:
        """FX-3's lane, its change committed, in the delivery the module describes; `brief` has FX-3's entry ask for
        `lens.data` before init, and `on_request=False` declares no lens on request."""
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        m = fx.main
        path = setup_delivery(fx)
        (m / "docs/review").mkdir()
        for short, text in CHARTERS.items():
            (m / f"docs/review/{short}.md").write_text(text)
        conf_path = m / ".agents/run-delivery.json"
        conf = json.loads(conf_path.read_text())
        data = {"short": "data", "charter": "docs/review/data.md", "use": ["story"]}
        if on_request:
            data["default"] = False
        conf["review"]["lenses"] = {
            "lens.standards": {"short": "standards", "charter": "docs/review/standards.md", "use": ["story"]},
            "lens.data": data,
            "lens.p": {"short": "p", "charter": "docs/review/p.md", "use": ["plan"]}}
        node = bool(fx.call("find_node22", cwd=m))
        if node:
            (m / "harness.config.ts").write_text(
                "export default { additionalReviewLenses: [{ lensId: 'lens.h', reviewerId: 'h', "
                "charterPath: 'docs/review/h.md' }] };\n")
            conf["review"]["use"] = {"lens.h": ["story"]}
            if on_request:
                conf["review"]["default"] = {"lens.h": False}
        conf_path.write_text(json.dumps(conf, indent=2))
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["criteria"] = [{"id": "one-works", "text": "One works"}]
        d["stories"]["FX-3"]["criteria"] = [{"id": "two-works", "text": "Two works"}]
        d["stories"]["FX-3"]["inApp"] = "none"
        if brief:
            d["stories"]["FX-3"]["lenses"] = ["lens.data"]
        path.write_text(json.dumps(d, indent=2))
        (m / ".gitignore").write_text(".orchestration/\nout/\nlanes/\n")
        commit(m, "FX-1: lenses on request, and criteria")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=m)
        for argv in (("lead-init",), ("lead-assign", "FX-3", "--lane", "lane-2", "--title", "Lane 2")):
            code, out = fx.flow(*argv, cwd=m)
            self.assertEqual(code, 0, out)
        wt = fx.lane("FX-3")
        (wt / "src/two.txt").write_text("two\n")
        commit(wt, "FX-3: two")
        (wt / ".orchestration/review/FX-3").mkdir(parents=True)
        (wt / ".orchestration/review/FX-3/context.md").write_text("- evidence")
        return fx, wt, node

    def fixture(self, **settings) -> tuple[Fixture, Path, bool]:
        return kept_fixture(self, self.build_delivery, **settings)

    def results(self, fx: Fixture, wt: Path, n: int, lenses: list[str]) -> None:
        write_results(wt / ".orchestration/review/FX-3", n, "FX-3",
                      [entry for entry in fx.lenses(wt) if entry[0] in lenses])

    def meta(self, wt: Path, n: int) -> dict:
        return json.loads((wt / f".orchestration/review/FX-3/round-{n}.meta.json").read_text())

    def journal(self, fx: Fixture, kind: str) -> list[dict]:
        lines = (fx.tmp / "record/journal.jsonl").read_text().splitlines()
        return [entry for entry in map(json.loads, lines) if entry["kind"] == kind]

    def plan_log(self, fx: Fixture) -> list[dict]:
        found = list((fx.tmp).rglob("flow-log/plan-changes.jsonl"))
        self.assertEqual(len(found), 1, found)
        return [json.loads(line) for line in found[0].read_text().splitlines()]

    def lead_plan(self, fx: Fixture, *argv) -> tuple[int, str]:
        return fx.flow("lead-plan", "change", *argv, cwd=fx.main)

    def publish(self, fx: Fixture) -> None:
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)

    def hand_off(self, fx: Fixture, wt: Path, first: bool) -> tuple[int, str]:
        if first:
            self.assertEqual(fx.flow("gate", "FX-3", cwd=wt)[0], 0)
            sh("git", "push", "-q", "-u", "origin", "HEAD:feature/FX-3", cwd=wt)
            body = fx.tmp / "body.md"
            body.write_text("x\n")
            code, out = fx.flow("open-pr", "FX-3", "--title", "FX-3: two", "--body-file", body, cwd=wt)
            self.assertEqual(code, 0, out)
        return fx.flow("handoff", "FX-3", "--pr", "https://forge.test/pr/1", cwd=wt)

    def test_round_1_leaves_a_lens_on_request_out_until_the_lane_names_it(self) -> None:
        fx, wt, node = self.fixture()
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, cwd=wt)
        self.assertEqual(code, 0, out)
        meta = self.meta(wt, 1)
        self.assertEqual((meta["lenses"], meta["lensesReason"], meta["requested"]),
                         ([OC, AT, "lens.standards"], "every story lens not on request", {}))
        self.assertEqual(self.journal(fx, "review.started")[-1]["data"]["lenses"], [OC, AT, "lens.standards"])
        self.assertIn(f"lenses: {OC}, {AT}, lens.standards; reason: every story lens not on request\n", out)
        self.assertFalse((wt / ".orchestration/review/FX-3/brief-r1-data.md").exists())
        named = f"{OC},{AT},lens.data" + (",lens.h" if node else "")
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, "--restart", "--lenses", named,
                            "--lenses-reason", "it moves stored rows", cwd=wt)
        self.assertEqual(code, 0, out)
        meta = self.meta(wt, 1)
        asked = {"lens.data": "lane", **({"lens.h": "lane"} if node else {})}
        self.assertEqual((meta["lenses"], meta["requested"]),
                         ([OC, AT, *(["lens.h"] if node else []), "lens.data"], asked))
        self.assertIn("lens.data (asked for by the lane)", out)
        self.assertTrue((wt / ".orchestration/review/FX-3/brief-r1-data.md").exists())

    def test_a_lens_the_brief_asks_for_runs_by_default_and_cant_be_left_out(self) -> None:
        fx, wt, node = self.fixture(brief=True)
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, cwd=wt)
        self.assertEqual(code, 0, out)
        meta = self.meta(wt, 1)
        self.assertEqual((meta["lenses"], meta["lensesReason"], meta["requested"]),
                         ([OC, AT, "lens.standards", "lens.data"],
                          "every story lens not on request, and the brief's lens.data", {"lens.data": "brief"}))
        self.assertIn("lens.data (asked for by the brief)", out)
        review = wt / ".orchestration/review/FX-3"
        before = sorted(p.name for p in review.iterdir())
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, "--restart", "--lenses",
                            f"{OC},{AT},lens.standards", "--lenses-reason", "no data here", cwd=wt)
        self.assertEqual(code, 2, out)
        self.assertIn("--lenses leaves out lens.data", out)
        self.assertIn(LANE_BRIEF_ORIGIN, out)
        self.assertEqual(sorted(p.name for p in review.iterdir()), before, "the refusal wrote something")
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, "--restart", "--lenses", f"{OC},{AT},lens.data",
                            "--lenses-reason", "data only", cwd=wt)
        self.assertEqual(code, 0, out)
        meta = self.meta(wt, 1)
        self.assertEqual((meta["lenses"], meta["lensesReason"], meta["requested"]),
                         ([OC, AT, "lens.data"], "data only", {"lens.data": "brief"}))
        # The lead brief lists the lens on request with its purpose, and the story whose brief asks for it.
        code, out = fx.flow("lead-brief", cwd=fx.main)
        self.assertEqual(code, 0, out)
        text = (fx.main / "docs/lane-brief.md").read_text()
        self.assertIn("- `lens.standards`: Checks the change against the repository's standards.\n", text)
        self.assertNotIn("- `lens.data`:", text)
        self.assertIn("- On request, run only when `--lenses` or the story's brief names it: ", text)
        self.assertIn("`lens.data` (the brief of FX-3 asks for it): Checks what the change does to stored data.", text)
        if node:
            self.assertIn("`lens.h`: A harness lens, run only on request.", text)

    def test_a_brief_lens_cant_be_added_once_round_1_is_prepared(self) -> None:
        fx, wt, node = self.fixture()
        m = fx.main
        path = m / ".agents/deliveries/FX-1.json"
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, cwd=wt)
        self.assertEqual(code, 0, out)
        first = [OC, AT, "lens.standards"]
        # The lens set is fixed at round 1, so the brief can't add a lens to it, and nothing is recorded.
        kept, log = path.read_text(), self.plan_log(fx)
        brief = [*(["lens.h"] if node else []), "lens.data"]
        code, out = self.lead_plan(fx, "--kind", "lenses", "--subjects", "FX-3", "--lenses", ",".join(brief),
                                   "--why", "It now moves stored rows.")
        self.assertEqual(code, 2, out)
        self.assertIn(f"FX-3's review has begun, and its lens set is fixed at round 1, so its brief can't add "
                      f"{', '.join(brief)}", out)
        self.assertEqual((path.read_text(), self.plan_log(fx)), (kept, log), "the refusal recorded something")
        self.results(fx, wt, 1, first)
        code, out = fx.flow("review-reduce", "FX-3", "--round", 1, cwd=wt)
        self.assertEqual(code, 0, out)
        code, out = self.hand_off(fx, wt, first=True)
        self.assertEqual(code, 0, out)
        self.assertIn(f"- review lenses: {OC}, {AT}, lens.standards; reason: every story lens not on request\n", out)
        # A hand edit can still add one: lead-accept and the hand-off refuse the story, naming the lens and its removal.
        d = json.loads(path.read_text())
        d["stories"]["FX-3"]["lenses"] = brief
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: a hand edit")
        self.publish(fx)
        refusal = ("FX-3's brief asks for {lens}, but its review's lens set, fixed at round 1, lacks it: take it out of "
                   "the story's entry with lead-plan change --kind lenses --subjects FX-3")
        code, out = fx.flow("lead-accept", "FX-3", cwd=m)
        self.assertNotEqual(code, 0, out)
        for lens in brief:
            self.assertIn(refusal.format(lens=lens), out)
        # The fixture names no binding, so the lane reads the delivery file from its own tree: it takes the base.
        merge = ("git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "origin/feature/FX-1")
        sh(*merge, cwd=wt)
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-3", cwd=wt)
        code, out = fx.flow("handoff", "FX-3", "--pr", "https://forge.test/pr/1", cwd=wt)
        self.assertEqual(code, 2, out)
        for lens in brief:
            self.assertIn(refusal.format(lens=lens), out)
        # Removing a brief lens after round 1 is accepted; once out, both pass.
        code, out = self.lead_plan(fx, "--kind", "lenses", "--subjects", "FX-3", "--lenses", "", "--why",
                                   "Its review didn't run them; a follow-up does.")
        self.assertEqual(code, 0, out)
        self.publish(fx)
        sh(*merge, cwd=wt)
        code, out = fx.flow("review-prepare", "FX-3", "--round", 2, cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual((self.meta(wt, 2)["lenses"], self.meta(wt, 2)["requested"]), (first, {}))
        self.results(fx, wt, 2, first)
        code, out = fx.flow("review-reduce", "FX-3", "--round", 2, cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(fx.flow("gate", "FX-3", cwd=wt)[0], 0)
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-3", cwd=wt)
        code, out = self.hand_off(fx, wt, first=False)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-accept", "FX-3", cwd=m)
        self.assertNotIn("lacks it", out)
        self.assertIn(f"review lenses: {', '.join(first)}; reason: every story lens not on request", out)
        self.assertEqual(code, 0, out)

    def test_hand_off_and_lead_accept_show_who_asked_for_a_lens(self) -> None:
        fx, wt, node = self.fixture(brief=True)
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, cwd=wt)
        self.assertEqual(code, 0, out)
        lenses = [OC, AT, "lens.standards", "lens.data"]
        self.results(fx, wt, 1, lenses)
        code, out = fx.flow("review-reduce", "FX-3", "--round", 1, cwd=wt)
        self.assertEqual(code, 0, out)
        code, out = self.hand_off(fx, wt, first=True)
        self.assertEqual(code, 0, out)
        said = (f"{', '.join(lenses)}; reason: every story lens not on request, and the brief's lens.data; on request: "
                "lens.data (asked for by the brief)")
        self.assertIn(f"- review lenses: {said}\n", out)
        code, out = fx.flow("lead-accept", "FX-3", cwd=fx.main)
        self.assertIn(f"review lenses: {said}", out)
        self.assertEqual(code, 0, out)

    def test_lenses_on_request_are_listed_and_misdeclared_ones_are_conflicts(self) -> None:
        fx, wt, node = self.fixture()
        m = fx.main
        _, out = fx.flow("doctor", "--offline", cwd=m)
        lines = doctor_lines(out)
        self.assertEqual(lines["lens lens.data"], ("ok", "story, on request, from .agents/run-delivery.json"))
        self.assertEqual(lines["lens lens.standards"], ("ok", "story, from .agents/run-delivery.json"))
        on_request = "lens.data: Checks what the change does to stored data."
        if node:
            self.assertEqual(lines["lens lens.h"], ("ok", "story, on request, from harness.config.ts"))
            on_request = "lens.h: A harness lens, run only on request.; " + on_request
        self.assertEqual(lines["lenses on request"], ("ok", on_request))
        code, out = fx.flow("config", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertRegex(out, rf"review\.lenses on request +{re.escape(on_request)}\n")
        conf_path = m / ".agents/run-delivery.json"
        conf = json.loads(conf_path.read_text())
        lenses = conf["review"]["lenses"]
        data = lenses["lens.data"]
        bad = [
            ("a mandated lens", {"default": {OC: False}},
             f"review.default.{OC} puts {OC}, a mandated lens, on request; it runs in every round"),
            ("a plan-only lens", {"lenses": {**lenses, "lens.p": {**lenses["lens.p"], "default": False}}},
             "review.lenses.lens.p.default puts lens.p on request, but its use is plan only"),
            ("an undeclared lens", {"default": {"lens.nowhere": False}},
             "review.default names lens.nowhere, which isn't a declared lens"),
            ("a disagreement", {"default": {"lens.data": True}},
             "review.default gives lens.data the default True, but review.lenses gives it False"),
            ("not true or false", {"lenses": {**lenses, "lens.data": {**data, "default": "no"}}},
             "review.lenses.lens.data.default must be true or false, not 'no'"),
            ("a review.default that isn't a map", {"default": ["lens.data"]},
             "review.default must map lens ids to true or false"),
        ]
        for what, part, said in bad:
            with self.subTest(what=what):
                conf_path.write_text(json.dumps({**conf, "review": {**conf["review"], **part}}))
                _, out = fx.flow("doctor", "--offline", cwd=m)
                status, detail = doctor_lines(out)["configuration"]
                self.assertEqual(status, "wrong", out)
                self.assertIn(said, detail)
                code, out = fx.flow("config", cwd=m)
                self.assertEqual(code, 2, out)
                self.assertIn(said, out)
        # `default: true` is the same as leaving it out, and two declarations that agree are no conflict.
        conf_path.write_text(json.dumps({**conf, "review": {
            **conf["review"], "lenses": {**lenses, "lens.data": {**data, "default": True}},
            "default": {"lens.data": True, **({"lens.h": False} if node else {})}}}))
        commit(m, "FX-1: lens.data by default")
        self.publish(fx)
        sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "origin/feature/FX-1", cwd=wt)
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.meta(wt, 1)["lenses"], [OC, AT, "lens.standards", "lens.data"])
        self.assertEqual(self.meta(wt, 1)["lensesReason"], "every story lens not on request" if node
                         else "every declared story lens")

    def test_with_nothing_on_request_round_1_runs_every_story_lens_as_before(self) -> None:
        fx, wt, node = self.fixture(on_request=False)
        code, out = fx.flow("review-prepare", "FX-3", "--round", 1, cwd=wt)
        self.assertEqual(code, 0, out)
        meta = self.meta(wt, 1)
        self.assertEqual((meta["lenses"], meta["lensesReason"], meta["requested"]),
                         ([OC, AT, *(["lens.h"] if node else []), "lens.standards", "lens.data"],
                          "every declared story lens", {}))
        self.assertNotIn("on request", out)
        _, out = fx.flow("doctor", "--offline", cwd=fx.main)
        self.assertNotIn("lenses on request", doctor_lines(out))

    def test_a_story_entry_names_only_declared_story_lenses(self) -> None:
        fx, wt, node = self.fixture()
        m = fx.main
        for lens in ("lens.nowhere", "lens.p"):
            with self.subTest(lens=lens):
                code, out = self.lead_plan(fx, "--kind", "lenses", "--subjects", "FX-3", "--lenses", lens, "--why", "W")
                self.assertEqual(code, 2, out)
                self.assertIn(f"--lenses names {lens}, which isn't a declared story lens", out)
                self.assertNotIn("lenses", json.loads((m / ".agents/deliveries/FX-1.json").read_text())["stories"]["FX-3"])
        path = m / ".agents/deliveries/FX-1.json"
        kept = path.read_text()
        d = json.loads(kept)
        d["stories"]["FX-3"]["lenses"] = ["lens.p"]
        path.write_text(json.dumps(d, indent=2))
        _, out = fx.flow("doctor", "--offline", "--delivery", "FX-1", cwd=m)
        status, detail = doctor_lines(out)["review lenses"]
        self.assertEqual(status, "wrong", out)
        self.assertIn("story FX-3: lenses names lens.p, which isn't a declared story lens", detail)
        path.write_text(kept)

    def test_lead_plan_changes_a_storys_lenses_and_a_hand_edit_shows_as_unrecorded(self) -> None:
        fx, wt, node = self.fixture()
        m = fx.main
        path = m / ".agents/deliveries/FX-1.json"
        code, out = self.lead_plan(fx, "--kind", "added", "--subjects", "FX-3", "--criterion", "two-more", "--text",
                                   "More", "--lenses", "lens.data", "--why", "W")
        self.assertEqual(code, 2, out)
        self.assertIn("--kind added doesn't use --lenses; it is for --kind lenses", out)
        code, out = self.lead_plan(fx, "--kind", "lenses", "--subjects", "FX-3", "--lenses", "lens.data", "--why",
                                   "It moves stored rows.")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(path.read_text())["stories"]["FX-3"]["lenses"], ["lens.data"])
        self.assertRegex(sh("git", "log", "-1", "--format=%s", cwd=m).stdout, r"^FX-1: plan change c\d+ \(lenses FX-3\)")
        self.assertEqual(sh("git", "status", "--porcelain", "--", ".agents", cwd=m).stdout, "")
        changes = [entry for entry in self.plan_log(fx) if entry["type"] == "change"]
        self.assertEqual([(c["by"] != "unrecorded", c.get("lenses"), c["subjects"]) for c in changes],
                         [(True, ["lens.data"], ["FX-3"])])
        self.assertEqual((changes[0]["before"], changes[0]["after"]), ("no lenses", "lenses: lens.data"))
        plan = json.loads((fx.tmp / "record/plan.json").read_text())
        self.assertEqual(plan["changes"][-1]["kind"], "changed")
        # An empty list removes the field.
        code, out = self.lead_plan(fx, "--kind", "lenses", "--subjects", "FX-3", "--lenses", "", "--why", "Not now.")
        self.assertEqual(code, 0, out)
        self.assertNotIn("lenses", json.loads(path.read_text())["stories"]["FX-3"])
        # A hand edit after init is an unrecorded plan change; the recorded ones above added none.
        self.assertNotIn("unrecorded", [c["by"] for c in self.plan_log(fx) if c["type"] == "change"])
        d = json.loads(path.read_text())
        d["stories"]["FX-3"]["lenses"] = ["lens.data"]
        path.write_text(json.dumps(d, indent=2))
        commit(m, "FX-1: a hand edit")
        self.assertEqual(fx.flow("lead-brief", cwd=m)[0], 0)
        drift = [c for c in self.plan_log(fx) if c["type"] == "change" and c["by"] == "unrecorded"]
        self.assertEqual([(c["subjects"], c.get("lenses"), c["before"], c["after"]) for c in drift],
                         [(["FX-3"], ["lens.data"], "no lenses", "lenses: lens.data")])

    def test_lead_setup_drafts_a_storys_lenses_from_its_lenses_sentence(self) -> None:
        fx, wt, node = self.fixture()
        m = fx.main
        plan = m / "docs/plan.md"
        plan.write_text(plan.read_text().replace(
            "- Scenarios: it renders.", "- Scenarios: it renders. Lenses: `lens.data`, `lens.nowhere`."))
        commit(m, "FX-1: a Lenses sentence")
        code, out = fx.flow("lead-setup", "FX-1", "--plan", "docs/plan.md", "--base", "feature/FX-1", "--pattern",
                            r"^### (?P<label>S\d+) \((?P<key>FX-\d+)\)\. (?P<title>[^(\n]+?)(?: \(AC\d+\))?"
                            r"(?:\. Regex (?P<pinned>.*))?$", "--force", "--no-authority", cwd=m)
        self.assertEqual(code, 0, out)
        stories = json.loads((m / ".agents/deliveries/FX-1.json").read_text())["stories"]
        self.assertEqual(stories["FX-3"]["lenses"], ["lens.data"])
        self.assertNotIn("lenses", stories["FX-2"])
        self.assertIn("plan story FX-3's Lenses sentence names lens.nowhere, which isn't a declared story lens: "
                      "drafted it without", out)

    def test_the_lens_on_request_is_pinned_where_adopters_and_lanes_read_it(self) -> None:
        root = Path(__file__).resolve().parents[1]

        def normal(path: str) -> str:
            return " ".join((root / path).read_text(encoding="utf-8").split())
        config = normal("skills/run-delivery/references/config.md")
        for phrase in (
                "A story lens with `\"default\": false` is on request: available to every story, it runs only when "
                "the lane's `--lenses` or the story's brief names it.",
                "Without `--lenses`, round 1 runs the mandated pair, every story lens not on request, and every lens "
                "the story's brief names",
                "A round 1 `--lenses` that leaves out a lens the story's brief names is refused",
                "Once a story's round 1 is prepared, `lead-plan change --kind lenses` refuses to add a lens to its "
                "brief"):
            self.assertIn(phrase, config)
        self.assertIn("put a broad lens on request", normal("skills/run-delivery/references/adopting.md"))
        skill = normal("skills/run-delivery/SKILL.md")
        self.assertIn("a lens on request (`\"default\": false`) runs only when `--lenses` or the story's brief names it",
                      skill)
        self.assertIn("`--kind lenses --subjects KEY --lenses IDS` sets the story's brief lenses", skill)
        self.assertTrue(re.search(r"lenses", skill))


if __name__ == "__main__":
    unittest.main()
