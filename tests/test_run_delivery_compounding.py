"""The close waits for the delivery's compound-learning decision, which `lead-compound` records.

The fixture cases run on the supervisor's fixture delivery, `lead-run --once` one pass at a time, with two finish
lines: `deploy`, which only the lead marks, and `merged`, which the record proves once every story merges. Both
stories are merged and their lanes done when the fixture is built, which is once for the class, put back for each
case. The reads of the journal and the open work run on the loaded module, with the record stood in for.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.test_run_delivery import Fixture, commit, installed_release, kept_fixture, sh
from tests.test_run_delivery_supervisor import (hand_off, kinds, listed, loaded_run_delivery, passes, queue_run,
                                                record, state_tool, stop_supervisors, supervised)

ROOT = Path(__file__).resolve().parents[1]
DECISION = "the compound-learning decision"
REF = "docs/solutions/delivery/close-on-proof-2026-11-18.md"


def status_line(out: str) -> str:
    """The supervisor's status line a lead command ends with."""
    return next(line for line in out.splitlines() if line.startswith("supervisor: "))


class CompoundingCloseTests(unittest.TestCase):
    """What holds the close, what lead-compound records and refuses, the friction stub across passes, and lead-close."""

    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def fixture(self, merged: bool = True) -> Fixture:
        return kept_fixture(self, self.build_fixture, merged=merged)

    def build_fixture(self, merged: bool) -> Fixture:
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
        if merged:
            for key, lane in (("FX-2", "lane-1"), ("FX-3", "lane-2")):
                hand_off(fx, key, lane)
                code, out = fx.flow("lead-accept", key, cwd=fx.main)
                self.assertEqual(code, 0, out)
                queue_run(fx, key)
            for lane in ("lane-1", "lane-2"):
                state_tool(fx, "focus", "--lane", lane, "--text", "done", "--state", "done")
        return fx

    def once(self, fx: Fixture) -> dict:
        code, out = fx.flow("lead-run", "--once", cwd=fx.main)
        self.assertEqual(code, 0, out)
        return listed(self, passes(fx)[-1])

    def flow(self, fx: Fixture, *argv: str, code: int = 0) -> str:
        done, out = fx.flow(*argv, cwd=fx.main)
        self.assertEqual(done, code, out)
        return out

    def stub(self, fx: Fixture) -> Path:
        return fx.tmp / "flow-log/FX-1.friction.md"

    def settle(self, fx: Fixture, holds: list[str]) -> dict:
        """Passes until the pass's holds settle on `holds`, which cleanup reaches within two."""
        for _ in range(3):
            last = self.once(fx)
            if last["closeHeldBy"] == holds:
                return last
        self.fail(f"the close held on {last['closeHeldBy']}, not {holds}")

    def test_the_decision_alone_holds_the_close_and_lead_compound_releases_it_with_the_stub_archived(self) -> None:
        fx = self.fixture()
        self.flow(fx, "lead-finish", "deploy", "--evidence", "release v3.4.0 applied")
        last = self.settle(fx, [DECISION])
        self.assertEqual(kinds(fx, "delivery.closed"), [])
        # "Needs you" names the decision and the friction stub's path, and the stub is there.
        [needed] = [item for item in last["needsYou"] if item["source"] == "compound-learning"]
        self.assertTrue(needed["holding"])
        self.assertIn(f"write up the friction stub {self.stub(fx)}", needed["text"])
        self.assertIn("lead-compound", needed["text"])
        # Its row, as the views draw it, names the decision and the stub, not something unnamed.
        self.assertEqual(needed["said"]["headline"], "The compound-learning decision is not recorded")
        self.assertIn(f"Write up the friction stub {self.stub(fx)}", needed["said"]["why"])
        self.assertIn("lead-compound --required --ref REF", needed["said"]["clears"])
        self.assertTrue(self.stub(fx).is_file())
        self.assertIn(f"The close waits on: {DECISION}", status_line(self.flow(fx, "lead-brief")))
        # The lead writes under "What was hard", then notes a gap; the next pass keeps the one and lists the other.
        text = self.stub(fx).read_text()
        self.assertIn("## What was hard", text)
        self.assertIn("## Gaps noted", text)
        written = ("- The queue's sync merge refused the push.\n- Region claims needed a line of slack.\n\n"
                   "## Root cause\n\n- Two writers shared one branch.\n")
        self.stub(fx).write_text(text.replace("- (yours: what was hard, and why)\n", written))
        self.flow(fx, "note-gap", "--class", "repo", "--text", "The catalogue order is manual",
                  "--why", "a lane had to place its entry by hand")
        self.settle(fx, [DECISION])
        text = self.stub(fx).read_text()
        hard = text[text.index("## What was hard"):text.index("## Shown, never holding")]
        self.assertEqual(hard, "## What was hard\n\n" + written + "\n")
        self.assertIn("The catalogue order is manual", text[text.index("## Gaps noted"):])
        # The decision, recorded: its status line no longer names it, and the next pass closes.
        out = self.flow(fx, "lead-compound", "--required", "--ref", REF)
        [decided] = kinds(fx, "compounding.recorded")
        self.assertEqual((decided["subject"], decided["data"]), (None, {"outcome": "required", "reference": REF}))
        self.assertIn(f"recorded {DECISION}, required (journal entry {decided['id']})", out)
        self.assertNotIn(DECISION, status_line(out))
        last = self.once(fx)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1, last)
        closed = json.loads((fx.tmp / "flow-log/FX-1.closed.json").read_text())
        archived = Path(closed["friction"])
        self.assertEqual(archived, Path(closed["archive"]) / "flow-log" / "FX-1.friction.md")
        kept = archived.read_text()
        self.assertIn(written, kept)
        self.assertIn("The catalogue order is manual", kept)
        self.assertIn("## What the close-out left to you", kept)
        self.assertIn(f"archived the friction note {archived}", "\n".join(last["closeOut"]))
        # The stop message names the archived copy, and asks for no write-up after the close.
        said = status_line(self.flow(fx, "lead-brief"))
        self.assertIn(f"supervisor: stopped; the delivery is closed. Archive {closed['archive']}; its friction note "
                      f"is {archived}", said)
        self.assertNotIn("write up", said)

    def test_with_the_decision_first_a_lead_held_line_alone_holds_and_lead_finish_releases_it(self) -> None:
        fx = self.fixture()
        out = self.flow(fx, "lead-compound", "--not-required", "--why", "nothing new")
        self.assertEqual(kinds(fx, "compounding.recorded")[0]["data"], {"outcome": "not-required", "why": "nothing new"})
        self.assertNotIn(DECISION, status_line(out))
        self.settle(fx, ["finish lines not met: deploy"])
        self.assertEqual(kinds(fx, "delivery.closed"), [])
        self.assertFalse(self.stub(fx).exists(), "a pass wrote the stub while a finish line held the close")
        self.flow(fx, "lead-finish", "deploy", "--evidence", "release v3.4.0 applied")
        last = self.once(fx)
        self.assertEqual(len(kinds(fx, "delivery.closed")), 1, last)
        # No pass wrote the stub, so the close-out did, before the archive that holds it.
        closed = json.loads((fx.tmp / "flow-log/FX-1.closed.json").read_text())
        self.assertTrue(Path(closed["friction"]).is_file(), closed)
        self.assertEqual(Path(closed["friction"]).parent.parent, Path(closed["archive"]))
        # Once the close is archived, a pass that finds something holding leaves the stub as it was archived.
        before = (self.stub(fx).read_bytes(), self.stub(fx).stat().st_mtime_ns)
        self.flow(fx, "lead-block", "--text", "a late blocker")
        self.assertTrue(self.once(fx)["closeHeldBy"])
        self.assertEqual((self.stub(fx).read_bytes(), self.stub(fx).stat().st_mtime_ns), before)

    def test_lead_close_without_compounding_leaves_a_recorded_decision_as_it_is(self) -> None:
        fx = self.fixture()
        self.flow(fx, "lead-compound", "--required", "--ref", REF)
        self.flow(fx, "lead-finish", "deploy", "--evidence", "release v3.4.0 applied")
        state_tool(fx, "journal", "--kind", "delivery.closed", "--data", "{}")
        code, out = fx.flow("lead-close", "--without-compounding", "operator closed early", cwd=fx.main)
        self.assertIn(code, (0, 1), out)  # 1 for what it leaves to the operator
        self.assertIn("FX-1: closed out", out)
        self.assertNotIn("as skipped", out)
        self.assertEqual([e["data"] for e in kinds(fx, "compounding.recorded")],
                         [{"outcome": "required", "reference": REF}])

    def test_each_refusal_journals_no_decision(self) -> None:
        fx = self.fixture()
        for argv, words in ((["--required"], "--ref is required"), (["--required", "--ref", "  "], "--ref is required"),
                            (["--not-required"], "--why is required"),
                            (["--not-required", "--why", " "], "--why is required")):
            with self.subTest(argv=argv):
                self.assertIn(words, self.flow(fx, "lead-compound", *argv, code=2))
        self.assertEqual(kinds(fx, "compounding.recorded"), [])

    def test_open_work_refuses_the_decision_and_writes_no_stub(self) -> None:
        fx = self.fixture(merged=False)
        out = self.flow(fx, "lead-compound", "--not-required", "--why", "nothing new", code=2)
        self.assertIn("work is still open (not handed off: FX-2, FX-3)", out)
        self.assertEqual(kinds(fx, "compounding.recorded"), [])
        last = self.once(fx)
        self.assertIn(DECISION, last["closeHeldBy"])
        self.assertEqual([item for item in last["needsYou"] if item["source"] == "compound-learning"], [])
        self.assertFalse(self.stub(fx).exists(), "a pass wrote the stub while stories were open")

    def test_a_close_journalled_by_hand_waits_for_the_decision_under_the_supervisor_and_lead_close(self) -> None:
        fx = self.fixture()
        self.flow(fx, "lead-finish", "deploy", "--evidence", "release v3.4.0 applied")
        state_tool(fx, "journal", "--kind", "delivery.closed", "--data", "{}")
        last = self.once(fx)
        self.assertIn(DECISION, last["closeHeldBy"])
        self.assertFalse((fx.tmp / "flow-log/FX-1.closed.json").exists(), "the pass archived without a decision")
        # The pass's own copy of the record, `latest`, is no archive.
        self.assertEqual([p.name for p in (fx.tmp / "archive").glob("*/*")], ["latest"], "the pass archived")
        # Stopped partway with no decision: the status line names lead-compound first.
        said = status_line(self.flow(fx, "lead-brief"))
        self.assertIn("stopped partway through the close; finish it with", said)
        self.assertIn("lead-compound --required --ref REF, or --not-required --why TEXT, then", said)
        self.assertIn("lead-close --without-compounding WHY", said)
        # lead-close refuses, naming the decision, before it stops anything.
        out = self.flow(fx, "lead-close", code=2)
        self.assertIn(f"{DECISION} is not recorded", out)
        self.assertNotIn("closed out", out)
        # A reason that is empty or only spaces is no reason.
        self.assertIn("--without-compounding is required", self.flow(fx, "lead-close", "--without-compounding", " ", code=2))
        self.assertEqual(kinds(fx, "compounding.recorded"), [])
        self.assertFalse((fx.tmp / "flow-log/FX-1.closed.json").exists())
        out = self.flow(fx, "lead-close", "--without-compounding", "operator closed early")
        [skipped] = kinds(fx, "compounding.recorded")
        self.assertEqual(skipped["data"], {"outcome": "skipped", "why": "operator closed early"})
        self.assertIn(f"done: recorded {DECISION} as skipped (journal entry {skipped['id']}): operator closed early", out)
        self.assertIn("FX-1: closed out", out)

    def test_a_close_finished_before_decisions_reads_as_closed(self) -> None:
        fx = self.fixture()
        state_tool(fx, "journal", "--kind", "delivery.closed", "--data", "{}")
        [closed] = kinds(fx, "delivery.closed")
        state_tool(fx, "close-finished", "--entry", str(closed["id"]))
        said = status_line(self.flow(fx, "lead-brief"))
        self.assertTrue(said.startswith("supervisor: stopped; the delivery is closed."), said)
        self.assertNotIn(DECISION, said)


class DecisionReadsTests(unittest.TestCase):
    """Which decision counts, after reopenings, and the open work the decision and the close read alike."""

    def setUp(self) -> None:
        self.tool = loaded_run_delivery(self, "run_delivery_for_compounding_reads")

    @staticmethod
    def journal(*kinds: str) -> list[dict]:
        return [{"id": n, "kind": kind, "data": {}} for n, kind in enumerate(kinds, 1)]

    def test_a_delivery_reopened_twice_needs_a_decision_after_each_reopening(self) -> None:
        decided = lambda *kinds: self.tool.compounding_decision(self.journal(*kinds))
        self.assertIsNone(decided())
        self.assertEqual(decided("compounding.recorded")["id"], 1)
        # A decision, then a close, then new work: the decision no longer counts, until a later one.
        once = ("compounding.recorded", "delivery.closed", "lane.assigned")
        self.assertIsNone(decided(*once))
        self.assertEqual(self.tool.reopened_at(self.journal(*once)), 3)
        twice = (*once, "compounding.recorded", "delivery.closed", "candidate.handed", "check.started")
        self.assertIsNone(decided(*twice))
        self.assertEqual(self.tool.reopened_at(self.journal(*twice)), 6)
        self.assertEqual(decided(*twice, "compounding.recorded")["id"], 8)
        # New work before any close reopens nothing.
        self.assertEqual(decided("compounding.recorded", "lane.assigned")["id"], 1)

    def test_a_finished_close_owes_no_decision_and_any_other_does(self) -> None:
        c = types.SimpleNamespace()
        closed = {"id": 1, "kind": "delivery.closed", "data": {}}
        for progress, kept, owed in (("finished", None, False), ("partway", {"entry": 1}, False),
                                     ("partway", None, True), (None, None, True)):
            with self.subTest(progress=progress, kept=kept):
                with mock.patch.object(self.tool, "close_progress", lambda doc, p=progress: p), \
                        mock.patch.object(self.tool, "kept_close", lambda c, entry, k=kept: k):
                    self.assertIs(self.tool.decision_owed(c, {"journal": [closed], "state": {}}), owed)
        # With no close at all, only a decision clears it.
        self.assertTrue(self.tool.decision_owed(c, {"journal": [], "state": {}}))

    def test_open_work_leaves_out_what_a_plan_change_removed_and_settled_stories(self) -> None:
        doc = {"journal": [], "state": {
            "planned": [{"subject": "S-1", "stage": "planned"}, {"subject": "S-2", "stage": "settled"},
                        {"subject": "S-5", "stage": "planned"}],
            "queue": [{"subject": "S-3", "stage": "stopped"}, {"subject": "S-4", "stage": "merged"},
                      {"subject": "S-6", "stage": "queued"}]}}
        with mock.patch.object(self.tool, "removed_subjects", lambda c: {"S-5", "S-6"}):
            self.assertEqual(self.tool.open_work(None, doc), ["not handed off: S-1", "not merged: S-3"])
        # A story the plan removed, stopped in the queue, with every other merged: nothing is open, so lead-compound
        # journals, and the close condition holds on nothing open either.
        doc["state"]["planned"] = [{"subject": "S-2", "stage": "settled"}]
        journalled = []
        with mock.patch.object(self.tool, "removed_subjects", lambda c: {"S-3", "S-6"}), \
                mock.patch.object(self.tool, "cfg", lambda: None), mock.patch.object(self.tool, "load_state", lambda: doc), \
                mock.patch.object(self.tool, "state_tool", lambda *argv, **_: journalled.append(argv) or
                                  types.SimpleNamespace(stdout=json.dumps({"entry": {"id": 9}}))):
            self.assertEqual(self.tool.open_work(None, doc), [])
            with contextlib.redirect_stdout(io.StringIO()) as said:
                self.tool.lead_compound(types.SimpleNamespace(required=False, ref=None, why="nothing new"))
        self.assertEqual(said.getvalue(), f"recorded {DECISION}, not-required (journal entry 9)\n")
        self.assertEqual(journalled, [("journal", "--kind", "compounding.recorded", "--data",
                                       json.dumps({"outcome": "not-required", "why": "nothing new"}))])

    def test_the_archived_stub_gains_what_the_close_out_left_once(self) -> None:
        archive = Path(self.enterContext(tempfile.TemporaryDirectory()))
        c = types.SimpleNamespace(flow_log=archive / "flow-log", delivery="FX-1",
                                  lane_checkout=lambda key: archive / "lanes" / key)
        note = archive / "flow-log/FX-1.friction.md"
        note.parent.mkdir()
        note.write_text("# FX-1: friction\n")
        said = ["left /main where it is: it has uncommitted changes"]
        for _ in range(2):
            self.assertEqual(self.tool.archived_stub(c, archive, said), note)
        text = note.read_text()
        self.assertEqual(text.count("## What the close-out left to you"), 1, text)
        self.assertIn("- left /main where it is: it has uncommitted changes", text)

    def test_lead_close_journals_a_skipped_decision_only_once_the_supervisor_has_stopped(self) -> None:
        """A pass still running would otherwise close out on the decision lead-close just freed, beside lead-close."""
        calls: list[str] = []

        class Stopped(Exception):
            pass

        def ask(c, meanwhile, why):
            calls.append("stop")
            raise Stopped
        doc = {"journal": [{"id": 1, "kind": "delivery.closed", "data": {}}],
               "state": {"queue": [], "meta": {}}}
        c = types.SimpleNamespace(root=Path("/repo"), binding_path=Path("/repo/.agents/orchestration.json"))
        for name, value in (("cfg", lambda: c), ("git", lambda *argv, **_: ""), ("load_state", lambda: doc),
                            ("decision_owed", lambda c, doc: True), ("ask_supervisor_to_stop", ask),
                            ("state_tool", lambda *argv, **_: calls.append(" ".join(map(str, argv))))):
            self.enterContext(mock.patch.object(self.tool, name, value))
        with self.assertRaises(Stopped):
            self.tool.lead_close(types.SimpleNamespace(without_compounding="operator closed early"))
        self.assertEqual(calls, ["stop"], "the skipped decision was journalled before the supervisor stopped")
        # And without a reason, it refuses before it asks anything to stop.
        calls.clear()
        with self.assertRaises(self.tool.Stop) as refused:
            self.tool.lead_close(types.SimpleNamespace(without_compounding=None))
        self.assertIn(f"{DECISION} is not recorded", str(refused.exception))
        self.assertEqual(calls, [])


ORDER = ("Note gaps as they happen. When the last story lands, write up the friction stub, run `$compound-learning` "
         "and record its decision; a note it preserves rides the integration change request and is reviewed with it. "
         "Then the close.")


def section(text: str, heading: str) -> str:
    """The text under a heading, up to the next heading of the same or a higher level."""
    level = heading.split(" ")[0]
    start = text.index(heading + "\n")
    rest = text[start + len(heading) + 1:]
    stop = re.search(rf"^#{{1,{len(level)}}} ", rest, re.MULTILINE)
    return rest[:stop.start()] if stop else rest


def normal(text: str) -> str:
    return " ".join(text.split())


class WrittenOrderTests(unittest.TestCase):
    """The guide's close-out, supervised and by hand, its integration recipe and run-delivery's cleaning up say the
    same order: gaps as they happen, the stub and the decision once the last story lands, the note with the
    integration change request, then the close."""

    def test_each_written_path_says_the_same_order(self) -> None:
        guide = (ROOT / "skills/orchestrate-work/references/orchestration-guide.md").read_text()
        close = section(guide, "### 8. Close out")
        supervised = close[:close.index("The steps below are for a delivery without it.")]
        manual = close[close.index("The steps below are for a delivery without it."):]
        recipe = section(guide, "### Reviewing an integration base for the default branch")
        cleaning = section((ROOT / "skills/run-delivery/SKILL.md").read_text(), "## Cleaning up")
        for name, text in (("supervised close-out", supervised), ("manual close-out", manual),
                           ("integration recipe", recipe), ("run-delivery's cleaning up", cleaning)):
            with self.subTest(name):
                self.assertIn(normal(ORDER), normal(text))
        # By hand, the decision comes before delivery.closed is journalled.
        self.assertLess(normal(manual).index(normal(ORDER)), normal(manual).index("Journal `delivery.closed`"))
        # And no written path still sends the lead to write the note up after the close.
        for text in (guide, cleaning):
            self.assertNotIn("writes a friction note stub for `$compound-learning`, in that order", normal(text))

    def test_the_command_table_names_the_three_commands(self) -> None:
        skill = (ROOT / "skills/run-delivery/SKILL.md").read_text()
        for name in ("lead-finish", "note-gap", "lead-compound"):
            with self.subTest(name):
                self.assertRegex(skill, rf"(?m)^\| [^|]+ \| `{name}[ `]")


if __name__ == "__main__":
    unittest.main()
