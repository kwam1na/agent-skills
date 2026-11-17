"""`lead-plan change`: a flag its kind doesn't use is refused, and `--kind removed --criterion` drops one criterion.

A plan change once meant to drop one criterion removed its whole story, because `--kind removed` read only
`--subjects` and ignored `--criterion`. These cases run the installed tool on the fixture delivery with the plan
chapter on: every refusal leaves the delivery file, the plan, the record's journal, the changes log and the lead's
commit as they were, and a dropped criterion leaves its story and its other criteria in place.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from tests.test_run_delivery import Fixture, add_story, installed_release, plan_delivery, sh

RUN_QUIETLY = True


class LeadPlanChangeTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def lead(self) -> tuple[Fixture, object]:
        """The fixture delivery, started, and what a refused change must leave as it was."""
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        path = plan_delivery(fx)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        read = lambda p: p.read_bytes() if p.exists() else None
        held = lambda: (path.read_bytes(), (fx.main / "docs/plan.md").read_bytes(), read(fx.tmp / "record/plan.json"),
                        read(fx.tmp / "record/journal.jsonl"), read(fx.tmp / "flow-log/plan-changes.jsonl"),
                        sh("git", "rev-parse", "HEAD", cwd=fx.main).stdout)
        return fx, held

    def refused(self, fx: Fixture, held, argv: list[str], words: str) -> None:
        before = held()
        code, out = fx.flow("lead-plan", "change", *argv, "--why", "W", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn(words, out)
        self.assertEqual(held(), before, argv)
        self.assertEqual(sh("git", "status", "--porcelain", cwd=fx.main).stdout, "", argv)

    def removed_subjects(self, fx: Fixture) -> list[str]:
        """The stories the close no longer waits on, as the tool's own `removed_subjects` reads the changes log."""
        probe = ("import json, sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import run_delivery as flow; "
                 "print(json.dumps(sorted(flow.removed_subjects(flow.Config(Path.cwd(), 'FX-1')))))")
        done = subprocess.run([sys.executable, "-B", "-c", probe, str(fx.tool.parent)], cwd=fx.main, env=fx.env, text=True,
                              capture_output=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def plan(self, fx: Fixture) -> dict:
        path = fx.tmp / "record/plan.json"
        checked = sh(sys.executable, "-B", fx.release / "skills/delivery-views/scripts/views_common.py", "check-plan", path,
                     check=False)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        return json.loads(path.read_text())

    def test_a_flag_the_kind_does_not_use_is_refused_by_name_and_nothing_is_written(self) -> None:
        fx, held = self.lead()
        covers = json.dumps({"criteria": "all", "proofs": []})
        for argv, words in (
                (["--kind", "removed", "--subjects", "FX-3", "--text", "x"],
                 "--kind removed doesn't use --text; it is for --kind added or relaxed or changed or finish"),
                (["--kind", "removed", "--subjects", "FX-3", "--lane", "lane-1"],
                 "--kind removed doesn't use --lane; it is for --kind added"),
                (["--kind", "removed", "--subjects", "FX-3", "--after"], "--kind removed doesn't use --after"),
                (["--kind", "removed", "--subjects", "FX-3", "--covers", covers], "--kind removed doesn't use --covers"),
                (["--kind", "removed", "--subjects", "FX-3", "--finish", "acs"], "--kind removed doesn't use --finish"),
                (["--kind", "removed", "--subjects", "FX-3", "--label", "L"], "--kind removed doesn't use --label"),
                (["--kind", "finish", "--finish", "acs", "--covers", covers, "--subjects", "FX-2"],
                 "--kind finish doesn't use --subjects; it is for --kind added or removed or relaxed or changed"),
                (["--kind", "finish", "--finish", "acs", "--covers", covers, "--criterion", "one-loads"],
                 "--kind finish doesn't use --criterion"),
                (["--kind", "relaxed", "--subjects", "FX-2", "--criterion", "one-fails", "--text", "x", "--finish", "acs"],
                 "--kind relaxed doesn't use --finish; it is for --kind finish"),
                (["--kind", "changed", "--subjects", "FX-2", "--criterion", "one-fails", "--text", "x", "--label", "L"],
                 "--kind changed doesn't use --label; it is for --kind finish"),
                (["--kind", "added", "--subjects", "FX-3", "--criterion", "two-x", "--text", "x", "--lane", "lane-1"],
                 "FX-3 is already a story of FX-1, so --kind added doesn't use --lane"),
                (["--kind", "added", "--subjects", "FX-3", "--criterion", "two-x", "--text", "x", "--after", "FX-2"],
                 "FX-3 is already a story of FX-1, so --kind added doesn't use --after"),
                (["--kind", "finish", "--finish", "acs", "--text", "New words", "--label", "L"],
                 "finish line acs already exists, so --kind finish doesn't use --label; it is for a new line")):
            with self.subTest(argv=argv):
                self.refused(fx, held, argv, words)
        # The drop's own refusals: a criterion the story lacks, its last one, one a finish line still names, and
        # more than one story.
        for argv, words in (
                (["--kind", "removed", "--subjects", "FX-2", "--criterion", "no-such"], "FX-2 has no criterion no-such"),
                (["--kind", "removed", "--subjects", "FX-2", "--criterion", "two-renders"], "FX-2 has no criterion two-renders"),
                (["--kind", "removed", "--subjects", "FX-3", "--criterion", "two-renders"],
                 "two-renders is FX-3's last criterion; a story keeps at least one, so remove the story instead, with "
                 "--kind removed --subjects FX-3"),
                (["--kind", "removed", "--subjects", "FX-2", "--criterion", "one-fails"],
                 "finish line acs covers one-fails; take it out of its --covers first"),
                (["--kind", "removed", "--subjects", "FX-2", "FX-3", "--criterion", "one-fails"],
                 "--kind removed with --criterion drops that criterion from one story in --subjects")):
            with self.subTest(argv=argv):
                self.refused(fx, held, argv, words)
        self.assertEqual(self.plan(fx)["changes"], [])
        # A story added by hand without its reason is still refused by name, whether a change removes it or only one of
        # its criteria: a drop keeps the story, so it doesn't excuse it.
        add_story(fx, fx.main / ".agents/deliveries/FX-1.json", "FX-5",
                  criteria=[{"id": "fx-5-works", "text": "FX-5 works"}, {"id": "fx-5-fails", "text": "FX-5 fails"}])
        self.refused(fx, held, ["--kind", "removed", "--subjects", "FX-5", "--criterion", "fx-5-fails"], "FX-5 has no `why`")

    def test_removed_with_a_criterion_drops_only_that_criterion_and_records_it_with_its_reason(self) -> None:
        fx, held = self.lead()
        m = fx.main
        # A line covering every criterion names none, so it holds no criterion back from a drop.
        code, out = fx.flow("lead-plan", "change", "--kind", "finish", "--finish", "acs", "--covers",
                            json.dumps({"criteria": "all", "proofs": ["merged"]}), "--why", "One-fails leaves the plan.", cwd=m)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-plan", "change", "--kind", "removed", "--subjects", "FX-2", "--criterion", "one-fails",
                            "--why", "The failure copy moves to its own story.", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertIn("recorded c2: removed one-fails from FX-2", out)
        self.assertEqual(sh("git", "log", "-1", "--format=%s", cwd=m).stdout.strip(),
                         "FX-1: plan change c2 (removed one-fails from FX-2)")
        committed = json.loads(sh("git", "show", "HEAD:.agents/deliveries/FX-1.json", cwd=m).stdout)
        self.assertEqual(committed["stories"]["FX-2"]["criteria"], [{"id": "one-loads", "text": "Thing one loads"}])
        self.assertEqual(committed["stories"]["FX-3"]["criteria"], [{"id": "two-renders", "text": "Thing two renders"}])
        self.assertEqual(sh("git", "status", "--porcelain", cwd=m).stdout, "")
        plan = self.plan(fx)
        self.assertEqual([(u["subject"], u.get("removed", False), [c["id"] for c in u["criteria"]]) for u in plan["units"]],
                         [("FX-2", False, ["one-loads"]), ("FX-3", False, ["two-renders"])])
        change = plan["changes"][-1]
        self.assertEqual(change, {"id": "c2", "at": change["at"], "kind": "removed", "subjects": ["FX-2"],
                                  "criteria": ["one-fails"], "by": "lead", "why": "The failure copy moves to its own story.",
                                  "source": "lead-plan", "before": "Thing one says when it fails"})
        record = json.loads(sh(sys.executable, "-B", fx.release / "skills/orchestrate-work/scripts/delivery_state.py",
                               "--dir", fx.tmp / "record", "show").stdout)
        self.assertEqual([p["subject"] for p in record["state"]["planned"]], ["FX-2", "FX-3"], "the story left the record")
        # The rebuild after it finds the drop explained, so no unrecorded change joins it.
        code, out = fx.flow("lead-reconcile", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertEqual([c["id"] for c in self.plan(fx)["changes"]], ["c1", "c2"], out)
        # The close still waits on FX-2: a dropped criterion removes no story. A whole story's removal still counts.
        self.assertEqual(self.removed_subjects(fx), [])
        code, out = fx.flow("lead-plan", "change", "--kind", "removed", "--subjects", "FX-3", "--why", "Folded into FX-2.", cwd=m)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.removed_subjects(fx), ["FX-3"])
        # The criterion is gone, so a second drop of it is refused and writes nothing.
        self.refused(fx, held, ["--kind", "removed", "--subjects", "FX-2", "--criterion", "one-fails"],
                     "FX-2 has no criterion one-fails")


if __name__ == "__main__":
    unittest.main()
