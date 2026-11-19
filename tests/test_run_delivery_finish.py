"""`lead-finish`: the lead marks a finish line the record can't prove met, with its evidence.

The cases run on the supervisor's fixture delivery, `lead-run --once` one pass at a time, with two finish lines: `deploy`,
whose `covers` names no criteria and no proofs, so only the lead marks it, and `merged`, which the record proves once
every story merges. The fixture is built once for the class and put back for each case. The last cases show that a
line the record can't prove waits for the lead, that a covered line is still marked once its stories merge, and that a
deferral a later round lists as fixed files no follow-up.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.test_run_delivery import Fixture, commit, installed_release, kept_fixture, sh
from tests.test_run_delivery_supervisor import (hand_off, kinds, listed, loaded_run_delivery, passes, queue_run,
                                                record, state_tool, stop_supervisors, supervised)

EVIDENCE = "release v3.4.0 applied"
PROVES = "the record proves this line: the supervisor marks it when its criteria and proofs are done"


def status_line(out: str) -> str:
    """The supervisor's status line a lead command ends with."""
    return next(line for line in out.splitlines() if line.startswith("supervisor: "))


class LeadFinishTests(unittest.TestCase):
    """One pass at a time, with `lead-run --once`: what `lead-finish` writes, what it refuses, and the close it
    releases."""

    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def fixture(self) -> Fixture:
        return kept_fixture(self, self.build_fixture)

    def build_fixture(self) -> Fixture:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        self.addCleanup(stop_supervisors, fx.tmp)
        path = supervised(fx)
        d = json.loads(path.read_text())
        d["stories"]["FX-2"]["criteria"] = [{"id": "one-works", "text": "One works"}]
        d["stories"]["FX-3"]["criteria"] = [{"id": "two-works", "text": "Two works"}]
        d["finish"] = [{"id": "deploy", "text": "Release v3.4.0 is applied", "covers": {}},
                       {"id": "merged", "text": "Every story merged", "covers": {"criteria": [], "proofs": ["merged"]}}]
        path.write_text(json.dumps(d, indent=2))
        commit(fx.main, "FX-1: a line the lead checks, and one the record proves")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        return fx

    def once(self, fx: Fixture) -> dict:
        code, out = fx.flow("lead-run", "--once", cwd=fx.main)
        self.assertEqual(code, 0, out)
        return listed(self, passes(fx)[-1])

    def merged(self, fx: Fixture, key: str, lane: str) -> None:
        hand_off(fx, key, lane)
        code, out = fx.flow("lead-accept", key, cwd=fx.main)
        self.assertEqual(code, 0, out)
        queue_run(fx, key)
        self.assertTrue([e for e in kinds(fx, "candidate.merged") if e.get("subject") == key], record(fx)["journal"][-3:])

    def met(self, fx: Fixture) -> dict[str, bool]:
        return {line["id"]: line["met"] for line in record(fx)["state"]["finish"]}

    def test_lead_finish_marks_a_lead_held_line_with_its_evidence_and_the_status_line_drops_it(self) -> None:
        fx = self.fixture()
        self.assertIn("finish lines not met: deploy, merged", self.once(fx)["closeHeldBy"])
        code, out = fx.flow("lead-finish", "deploy", "--evidence", EVIDENCE, cwd=fx.main)
        self.assertEqual(code, 0, out)
        [entry] = kinds(fx, "finish.met")
        self.assertEqual((entry["subject"], entry["lane"], entry["data"]),
                         ("deploy", None, {"finish": "deploy", "evidence": EVIDENCE, "by": "lead"}))
        self.assertIn(f"finish line deploy met (journal entry {entry['id']}); views: rendered", out)
        self.assertEqual(self.met(fx), {"deploy": True, "merged": False})
        # With no pass in between, the status line reads the lines not met from the record.
        said = status_line(out)
        self.assertIn("The close waits on: not handed off: FX-2, FX-3; finish lines not met: merged", said)
        self.assertNotIn("deploy", said)
        # The next lead command's status line says the same, and so does the next pass.
        code, out = fx.flow("lead-decide", "--text", "Deploy checked", "--why", "the release is out", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertNotIn("deploy", status_line(out))
        last = self.once(fx)
        self.assertIn("finish lines not met: merged", last["closeHeldBy"])
        self.assertEqual(len(kinds(fx, "finish.met")), 1)

    def test_the_evidence_is_read_from_a_file(self) -> None:
        fx = self.fixture()
        evidence = fx.tmp / "evidence.md"
        evidence.write_text("Applied in production.\nhttps://example.test/release/3.4.0\n")
        code, out = fx.flow("lead-finish", "deploy", "--evidence", f"@{evidence}", cwd=fx.main)
        self.assertEqual(code, 0, out)
        [entry] = kinds(fx, "finish.met")
        self.assertEqual(entry["data"]["evidence"], "Applied in production.\nhttps://example.test/release/3.4.0")

    def test_each_refusal_journals_nothing_and_changes_nothing(self) -> None:
        fx = self.fixture()
        empty = fx.tmp / "empty.md"
        empty.write_text(" \n\n")
        refusals = [(("merged", "--evidence", EVIDENCE), f"finish line merged: {PROVES}"),
                    (("nowhere", "--evidence", EVIDENCE), "the record holds no finish line nowhere: its lines are "
                                                          "deploy, merged"),
                    (("deploy", "--evidence", "   "), "--evidence is required"),
                    (("deploy", "--evidence", f"@{empty}"), "--evidence is required"),
                    (("deploy", "--evidence", f"@{fx.tmp / 'missing.md'}"), "the file can't be read")]
        before = record(fx)
        for argv, words in refusals:
            with self.subTest(argv=argv):
                code, out = fx.flow("lead-finish", *argv, cwd=fx.main)
                self.assertEqual(code, 2, out)
                self.assertIn(words, out)
                self.assertEqual(record(fx), before)
        # A line already met is refused, and its one entry stands.
        code, out = fx.flow("lead-finish", "deploy", "--evidence", EVIDENCE, cwd=fx.main)
        self.assertEqual(code, 0, out)
        marked = record(fx)
        code, out = fx.flow("lead-finish", "deploy", "--evidence", "again", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("finish line deploy is already met", out)
        self.assertEqual(record(fx), marked)

    def test_a_lead_held_line_holds_the_close_until_lead_finish_and_a_covered_line_is_marked_on_its_merges(self) -> None:
        """V26-3477's first, second and fourth criteria: every story merged and nothing else to hold the close, the
        line the record can't prove stays unmarked across passes and alone holds it, while the covered line is marked
        by the supervisor. lead-finish releases the close on the next pass, which journals no second entry."""
        fx = self.fixture()
        self.merged(fx, "FX-2", "lane-1")
        self.merged(fx, "FX-3", "lane-2")
        for lane in ("lane-1", "lane-2"):
            state_tool(fx, "focus", "--lane", lane, "--text", "done", "--state", "done")
        for _ in range(3):
            last = self.once(fx)
            self.assertEqual(self.met(fx), {"deploy": False, "merged": True})
            # Every story merged, its lane done and its checkout cleaned up: the lead's line is all that holds.
            self.assertEqual(last["closeHeldBy"], ["finish lines not met: deploy"])
        self.assertEqual(kinds(fx, "delivery.closed"), [])
        [entry] = kinds(fx, "finish.met")
        self.assertEqual((entry["subject"], entry["data"]), ("merged", {"finish": "merged", "by": "supervisor"}))
        code, out = fx.flow("lead-finish", "deploy", "--evidence", EVIDENCE, cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertNotIn("The close waits on", status_line(out))
        self.once(fx)
        self.assertEqual([(e["subject"], e["data"].get("by")) for e in kinds(fx, "finish.met")],
                         [("merged", "supervisor"), ("deploy", "lead")])
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1, record(fx)["journal"][-5:])


class FixedDeferralTests(unittest.TestCase):
    """V26-3477's third criterion: a deferral that a later round of its lens lists as fixed files no follow-up at
    close-out, while one still deferred files one."""

    def test_a_deferral_a_later_round_lists_as_fixed_files_no_follow_up(self) -> None:
        tool = loaded_run_delivery(self, "run_delivery_for_fixed_deferrals")
        tmp = Path(tempfile.mkdtemp(prefix="fixed-deferrals-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        folder = tmp / "evidence" / "S-1"
        folder.mkdir(parents=True)
        deferred = lambda ident: {"id": ident, "severity": "P3", "summary": "later", "state": "deferred",
                                  "deferredIssue": f"Follow {ident}"}
        rounds = [[deferred("a-r1-1"), deferred("a-r1-2")], []]
        for n, findings in enumerate(rounds, 1):
            resolved = {"resolvedFindings": [{"id": "a-r1-1", "state": "fixed", "resolution": "fixed"}]} if n > 1 else {}
            result = {"lensId": "lens.a", "round": n, "findings": findings, **resolved}
            (folder / f"round-{n}.json").write_text(json.dumps({"round": n, "entries": [
                {"lens": "lens.a", "state": "obtained", "result": result}]}))
        c = types.SimpleNamespace(stories={"S-1": {}}, lane_checkout=lambda key: tmp / "gone", work="work",
                                  no_deferrals=False)
        doc = {"state": {"queue": [{"subject": "S-1", "stage": "merged"}], "followups": []}, "journal": []}
        filed, patched = [], []

        def journal(*argv, **_):
            filed.append(json.loads(argv[argv.index("--data") + 1]))
            return types.SimpleNamespace(stdout=json.dumps({"entry": {"id": len(filed)}}))
        for name, value in (("evidence_dir", lambda key: tmp / "evidence" / key), ("state_tool", journal),
                            ("marked_decision", lambda c, **_: contextlib.nullcontext([])),
                            ("patch_from", lambda compute, what: patched.append(compute(doc))),
                            ("render_views", lambda c: "rendered"), ("load_state", lambda: doc),
                            ("tracker_conf", lambda c: {"mode": "hand"})):
            self.enterContext(mock.patch.object(tool, name, value))
        self.assertEqual([f["finding"] for f in tool.deferred_findings(c)], ["a-r1-2"])
        tool.followups_step(c, doc)
        self.assertEqual(filed, [{"finding": "a-r1-2", "followup": "S-1-a-r1-2", "title": "Follow a-r1-2"}])
        self.assertEqual([[item["id"] for item in body["followups"]] for body in patched], [["S-1-a-r1-2"]])


if __name__ == "__main__":
    unittest.main()
