"""Gaps noted as they happen: `note-gap`, its matching, and where the close-out routes each class.

The notes run the real command in one fixture delivery, shared by the class's cases, each with texts of its own. The
close-out drives one supervised, tracked delivery to its close in the class's setup, with a gap of each class noted
first, and each case reads one part of what that close left.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.test_run_delivery import Fixture, commit, installed_release, setup_delivery, sh, stop_supervisors
from tests.test_run_delivery_supervisor import (hand_off, kinds, loaded_run_delivery, queue_run, record, state_tool,
                                               supervised, tracked)
from tests.test_run_delivery_tracker import FileBackend

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "skills/orchestrate-work/references/orchestration-guide.md"
LANE_BRIEF = ROOT / "skills/run-delivery/templates/lane-brief.md"

sys.path.insert(0, str(ROOT / "skills" / "run-delivery" / "scripts"))
import gaps  # noqa: E402
import tracker_publisher  # noqa: E402


def flat(text: str) -> str:
    return " ".join(text.split())


def section(text: str, heading: str) -> str:
    """A `## ` section of the guide, up to the next one."""
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else None]


# The trigger every brief names, each phrase whitespace-normalized.
TRIGGER = (
    "Note friction with `note-gap` when it happens, and class it as workflow, repo or host",
    "When you write a script to get behaviour the workflow or a tool should provide, note a workflow gap with the "
    "script as its evidence before moving on.",
    "One-off reads for understanding don't count; anything you'd need again does.",
)


class BriefWordingTests(unittest.TestCase):
    """The guide's "Lead checklist", its "Lane brief template" and the lane brief template each name the trigger, and
    none says only "note friction"."""

    def briefs(self) -> dict[str, str]:
        guide = GUIDE.read_text(encoding="utf-8")
        return {"Lead checklist": section(guide, "Lead checklist"),
                "Lane brief template": section(guide, "Lane brief template"),
                "lane-brief.md": LANE_BRIEF.read_text(encoding="utf-8")}

    def test_each_brief_names_note_gap_its_classes_and_the_script_trigger(self) -> None:
        for name, text in self.briefs().items():
            for phrase in TRIGGER:
                with self.subTest(brief=name, phrase=phrase[:40]):
                    self.assertIn(phrase, flat(text))

    def test_no_brief_says_note_friction_without_note_gap(self) -> None:
        for name, text in self.briefs().items():
            sentences = re.split(r"(?<=[.:;])\s", flat(text))
            said = [s for s in sentences if re.search(r"note (?:the |any )?friction", s, re.I)]
            with self.subTest(brief=name):
                self.assertTrue(said, name)
                for sentence in said:
                    self.assertIn("`note-gap`", sentence)


REOPENING = ("lane.assigned", "candidate.handed", "check.started")


def gap(n: int, text: str, cls: str = "repo", **data) -> dict:
    return {"id": n, "kind": "gap.noted", "subject": None, "data": {"class": cls, "text": text, "why": "w", "by": "lead",
                                                                     **data}}


class GapWindowTests(unittest.TestCase):
    """Which gaps a close routes and which are open: those noted since the close before the standing one, or since
    the latest close once new work reopened the delivery."""

    JOURNAL = [gap(1, "old"), gap(2, "old again", occurrenceOf=1), {"id": 3, "kind": "delivery.closed", "data": {}},
               {"id": 4, "kind": "lane.assigned", "data": {}}, gap(5, "new"), gap(6, "NEW ", occurrenceOf=5)]

    def test_a_close_routes_its_own_gaps_with_their_occurrences(self) -> None:
        closed = [*self.JOURNAL, {"id": 7, "kind": "delivery.closed", "data": {}}]
        found = gaps.gaps(closed, REOPENING)
        self.assertEqual([(g["entry"]["id"], g["count"]) for g in found], [(5, 2)])
        # Before the first close stands, every gap is the close's; while it stands, still its own.
        self.assertEqual([g["entry"]["id"] for g in gaps.gaps(self.JOURNAL[:3], REOPENING)], [1])
        self.assertEqual([g["entry"]["id"] for g in gaps.gaps(self.JOURNAL[:2], REOPENING)], [1])

    def test_a_gap_an_earlier_close_routed_is_neither_matched_nor_named_with_same_as(self) -> None:
        self.assertIsNone(gaps.occurrence_of(self.JOURNAL, "repo", "old", None, None))
        self.assertEqual(gaps.occurrence_of(self.JOURNAL, "repo", "new", None, None), 5)
        for named in (1, 2):
            with self.subTest(named=named), self.assertRaisesRegex(gaps.Refused, "routed by an earlier close-out"):
                gaps.occurrence_of(self.JOURNAL, "repo", "x", None, named)
        self.assertEqual(gaps.occurrence_of(self.JOURNAL, "repo", "x", None, 6), 5)

    def test_while_a_close_stands_its_routed_gaps_are_not_open_and_a_repeat_is_a_new_gap_the_next_close_counts(self) -> None:
        closed = [gap(1, "flaky"), {"id": 2, "kind": "delivery.closed", "data": {}}]
        self.assertEqual(gaps.listing(closed, "repo", None), [])
        self.assertIsNone(gaps.occurrence_of(closed, "repo", "flaky", None, None))
        with self.assertRaisesRegex(gaps.Refused, "routed by an earlier close-out"):
            gaps.occurrence_of(closed, "repo", "again", None, 1)
        # The repeat, noted as a new gap after the close, is routed by the next close once work reopened it.
        later = [*closed, gap(3, "flaky"), {"id": 4, "kind": "lane.assigned", "data": {}},
                 {"id": 5, "kind": "delivery.closed", "data": {}}]
        self.assertEqual([g["entry"]["id"] for g in gaps.gaps(later, REOPENING)], [3])


class FollowupStepTests(unittest.TestCase):
    """followups_step files each repo gap once, also under noDeferrals, and no workflow or host gap."""

    def setUp(self) -> None:
        self.tool = loaded_run_delivery(self, "run_delivery_for_gap_followups")
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "state").mkdir()
        (folder / "flow-log").mkdir()
        self.doc = {"state": {"queue": [], "followups": []},
                    "journal": [gap(1, "No README", "repo"), gap(2, "PATH drops", "host"),
                                gap(3, "brief lost", "workflow", release="r"), gap(4, "no readme", "repo", occurrenceOf=1)]}
        self.doc["journal"][0]["subject"] = "S-1"
        doc = self.doc

        def journal(*argv, **_):
            entry = {"id": len(doc["journal"]) + 1, "kind": argv[argv.index("--kind") + 1],
                     "subject": argv[argv.index("--subject") + 1], "data": json.loads(argv[argv.index("--data") + 1])}
            doc["journal"].append(entry)
            return types.SimpleNamespace(stdout=json.dumps({"entry": entry}))

        def patch(make, _what):
            doc["state"].update(make(doc))
        for name, value in (("state_tool", journal), ("patch_from", patch), ("render_views", lambda c: None),
                            ("load_state", lambda: doc), ("deferred_findings", lambda c: []),
                            ("deferral_breaches", lambda c, d: []), ("tracker_conf", lambda c: {"mode": "hand"})):
            self.enterContext(mock.patch.object(self.tool, name, value))
        self.c = lambda no_deferrals: types.SimpleNamespace(no_deferrals=no_deferrals, delivery="D", state=folder / "state",
                                                            flow_log=folder / "flow-log")

    def filed(self) -> list[dict]:
        return [e for e in self.doc["journal"] if e["kind"] == "followup.filed"]

    def test_a_repo_gap_is_filed_once_across_passes_with_or_without_no_deferrals(self) -> None:
        for no_deferrals in (False, True):
            with self.subTest(no_deferrals=no_deferrals):
                self.doc["journal"] = self.doc["journal"][:4]
                self.doc["state"]["followups"] = []
                for _ in range(2):
                    self.assertEqual(self.tool.followups_step(self.c(no_deferrals), self.doc),
                                     (True, "follow-ups: 1 recorded"))
                self.assertEqual([(e["subject"], e["data"]) for e in self.filed()],
                                 [("S-1", {"finding": "gap-1", "followup": "S-1-gap-1", "title": "No README"})])
                self.assertEqual([f["id"] for f in self.doc["state"]["followups"]], ["S-1-gap-1"])


class NoteGapTests(unittest.TestCase):
    """`note-gap` in one fixture delivery: the lead's checkout, and lane-1's checkout of FX-2."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)
        cls.fx = Fixture(cls.generation, cls.receipt)
        cls.addClassCleanup(cls.fx.close)
        setup_delivery(cls.fx)
        code, out = cls.fx.flow("lead-init", cwd=cls.fx.main)
        assert code == 0, out
        code, out = cls.fx.flow("lead-assign", "FX-2", "--lane", "lane-1", "--title", "lane-1", cwd=cls.fx.main)
        assert code == 0, out
        cls.wt = cls.fx.lane("FX-2")
        cls.release_id = json.loads(cls.receipt.read_text())["release"]["releaseId"]

    def note(self, *argv, cwd: Path | None = None) -> tuple[int, str, list[dict]]:
        """Run note-gap; (exit, output, the gap.noted entries it added)."""
        before = {e["id"] for e in kinds(self.fx, "gap.noted")}
        code, out = self.fx.flow("note-gap", *argv, cwd=cwd or self.fx.main)
        return code, out, [e for e in kinds(self.fx, "gap.noted") if e["id"] not in before]

    def noted(self, *argv, cwd: Path | None = None) -> dict:
        code, out, added = self.note(*argv, cwd=cwd)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(added), 1, out)
        return added[0]

    def evidence(self, name: str, text: str) -> Path:
        path = self.fx.tmp / "evidence-src" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        return path

    def on_release(self, release: str):
        """The lead checkout's receipt naming another release id for the running generation, until the case ends."""
        path = self.fx.main / ".agent-skills/active.json"
        kept = path.read_text()
        receipt = json.loads(kept)
        receipt["release"]["releaseId"] = release
        path.write_text(json.dumps(receipt, indent=2))
        self.addCleanup(path.write_text, kept)

    def workflow(self, text: str, **kw) -> dict:
        evidence = self.evidence(f"{abs(hash(text))}.txt", f"output of {text}\n")
        return self.noted("--class", "workflow", "--text", text, "--why", "it cost a script", "--evidence", evidence,
                          *[a for k, v in kw.items() for a in (f"--{k.replace('_', '-')}", v)])

    def test_a_workflow_gap_records_the_release_its_command_and_its_evidence_copied_with_its_sha256(self) -> None:
        script = self.evidence("read_round.py", "import json\nprint(json.load(open('round-1.json'))['outcome'])\n")
        entry = self.noted("--class", "workflow", "--text", "review-reduce prints no outcome", "--why",
                           "I wrote a script to read it", "--evidence", script, "--command",
                           "run_delivery.py review-reduce FX-2 --round 1")
        path = f"gaps/{entry['id']}/read_round.py"
        self.assertEqual(entry["subject"], "FX-1")
        self.assertIsNone(entry["lane"])
        self.assertEqual(entry["data"], {
            "class": "workflow", "text": "review-reduce prints no outcome", "why": "I wrote a script to read it",
            "by": "lead", "release": self.release_id, "generation": self.generation.name,
            "command": "run_delivery.py review-reduce FX-2 --round 1",
            "evidence": {"path": path, "sha256": hashlib.sha256(script.read_bytes()).hexdigest()}})
        self.assertEqual((self.fx.tmp / "record" / path).read_bytes(), script.read_bytes())

    def test_a_workflow_gap_without_evidence_is_refused_and_journals_nothing(self) -> None:
        code, out, added = self.note("--class", "workflow", "--text", "no evidence here", "--why", "none")
        self.assertNotEqual(code, 0, out)
        self.assertIn("a workflow gap needs --evidence", out)
        self.assertEqual(added, [])

    def test_repo_and_host_gaps_need_no_evidence(self) -> None:
        for cls in ("repo", "host"):
            with self.subTest(cls=cls):
                entry = self.noted("--class", cls, "--text", f"a {cls} gap with no file", "--why", "it slowed me")
                self.assertEqual(entry["data"]["class"], cls)
                self.assertNotIn("evidence", entry["data"])
                self.assertNotIn("occurrenceOf", entry["data"])

    def test_the_same_workflow_text_on_the_same_release_is_an_occurrence(self) -> None:
        first = self.workflow("lead-brief drops the notes")
        again = self.workflow("lead-brief drops the notes")
        self.assertNotIn("occurrenceOf", first["data"])
        self.assertEqual(again["data"]["occurrenceOf"], first["id"])

    def test_text_differing_only_in_case_and_runs_of_whitespace_is_an_occurrence(self) -> None:
        first = self.workflow("The gate  ignores  its timeout")
        again = self.workflow("  the GATE ignores\tits\n timeout ")
        self.assertEqual(again["data"]["occurrenceOf"], first["id"])

    def test_different_text_of_the_same_class_and_release_is_a_new_gap(self) -> None:
        self.workflow("push forgets the upstream")
        other = self.workflow("push forgets the remote")
        self.assertNotIn("occurrenceOf", other["data"])

    def test_same_as_names_the_gap_a_note_repeats_and_the_listing_names_it_first(self) -> None:
        receipt = self.fx.main / ".agent-skills/active.json"
        kept = receipt.read_text()
        self.on_release("a-third-release")
        elsewhere = self.workflow("open-pr loses the body, on another release")
        receipt.write_text(kept)
        other_class = self.noted("--class", "host", "--text", "open-pr loses the body, a host gap", "--why", "w")
        first = self.workflow("open-pr loses the body")
        code, out, added = self.note("--class", "workflow", "--text", "the PR body went missing", "--why", "again",
                                     "--evidence", self.evidence("body.txt", "x\n"), "--same-as", str(first["id"]))
        self.assertEqual(code, 0, out)
        self.assertIn(f"  {first['id']}: open-pr loses the body\n", out)
        # Only open workflow gaps on this release are offered.
        self.assertNotIn(f"  {elsewhere['id']}: ", out)
        self.assertNotIn(f"  {other_class['id']}: ", out)
        self.assertEqual(added[0]["data"]["occurrenceOf"], first["id"])
        # An occurrence named with --same-as stands for its gap.
        third = self.workflow("the body, a third time", same_as=str(added[0]["id"]))
        self.assertEqual(third["data"]["occurrenceOf"], first["id"])

    def test_the_same_workflow_text_on_another_release_is_a_new_gap(self) -> None:
        first = self.workflow("handoff names the wrong round")
        self.on_release("another-release")
        again = self.workflow("handoff names the wrong round")
        self.assertEqual(again["data"]["release"], "another-release")
        self.assertNotEqual(first["data"]["release"], again["data"]["release"])
        self.assertNotIn("occurrenceOf", again["data"])

    def test_the_same_repo_text_on_another_release_is_an_occurrence(self) -> None:
        first = self.noted("--class", "repo", "--text", "the suite budget names no Linux machine", "--why", "w")
        self.on_release("another-release")
        again = self.noted("--class", "repo", "--text", "the suite budget names no Linux machine", "--why", "w")
        self.assertEqual(again["data"]["release"], "another-release")
        self.assertEqual(again["data"]["occurrenceOf"], first["id"])

    def test_the_same_text_in_another_class_is_a_new_gap(self) -> None:
        self.noted("--class", "repo", "--text", "PATH loses Node", "--why", "w")
        other = self.noted("--class", "host", "--text", "PATH loses Node", "--why", "w")
        self.assertNotIn("occurrenceOf", other["data"])

    def test_same_as_naming_a_non_gap_or_a_gap_of_another_class_is_refused_journalling_nothing(self) -> None:
        host = self.noted("--class", "host", "--text", "the terminal wraps commands", "--why", "w")
        started = kinds(self.fx, "record.started")[0]
        for named, words in ((started["id"], "a record.started, not a gap.noted"),
                             (host["id"], "is a host gap, not a repo gap"), (99999, "not in the record")):
            with self.subTest(named=named):
                code, out, added = self.note("--class", "repo", "--text", "anything", "--why", "w", "--same-as",
                                             str(named))
                self.assertNotEqual(code, 0, out)
                self.assertIn(words, out)
                self.assertEqual(added, [])

    def test_a_lane_notes_on_its_own_story_under_its_own_writer(self) -> None:
        entry = self.noted("--class", "repo", "--text", "the lane checkout has no venv", "--why", "w", cwd=self.wt)
        self.assertEqual((entry["lane"], entry["subject"], entry["data"]["by"]), ("lane-1", "FX-2", "lane-1"))
        # The state tool lets a lane journal only on its own work.
        evidence = self.evidence("refused.txt", "never journalled\n")
        following = max(e["id"] for e in record(self.fx)["journal"]) + 1
        code, out, added = self.note("--class", "repo", "--text", "elsewhere", "--why", "w", "--subject", "FX-3",
                                     "--evidence", evidence, cwd=self.wt)
        self.assertNotEqual(code, 0, out)
        self.assertIn("writer_not_owned", out)
        self.assertEqual(added, [])
        # The refused note's evidence copy is gone, so the next note with evidence takes that id.
        self.assertFalse((self.fx.tmp / "record/gaps" / str(following)).exists())
        entry = self.noted("--class", "repo", "--text", "the lane's own, with a file", "--why", "w", "--evidence",
                           evidence, cwd=self.wt)
        self.assertEqual(entry["data"]["evidence"]["path"], f"gaps/{entry['id']}/refused.txt")

    def test_the_registry_refuses_an_unknown_class(self) -> None:
        done = subprocess.run([sys.executable, "-B", str(self.generation / "skills/orchestrate-work/scripts/delivery_state.py"),
                               "--dir", str(self.fx.tmp / "record"), "journal", "--kind", "gap.noted", "--data",
                               json.dumps({"class": "tooling", "text": "t", "why": "w", "by": "lead"})],
                              capture_output=True, text=True, timeout=120)
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn("/data/class", done.stdout + done.stderr)
        self.assertFalse([e for e in kinds(self.fx, "gap.noted") if e["data"]["class"] == "tooling"])


class CloseOutGapTests(unittest.TestCase):
    """One tracked delivery driven to its close, with two workflow gaps on two releases (one noted twice), one repo
    gap noted with no subject, and one host gap noted first."""

    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)
        cls.fx = fx = Fixture(cls.generation, cls.receipt)
        cls.addClassCleanup(fx.close)
        cls.addClassCleanup(stop_supervisors, fx.tmp)
        supervised(fx)
        cls.backend = tracked(fx, FileBackend)
        cls.addClassCleanup(cls.backend.close)
        path = fx.main / ".agents/deliveries/FX-1.json"
        d = json.loads(path.read_text())
        d["finish"] = []
        path.write_text(json.dumps(d, indent=2))
        commit(fx.main, "FX-1: no finish lines")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        code, out = fx.flow("lead-init", cwd=fx.main)
        assert code == 0, out
        cls.evidence = {}
        receipt = fx.main / ".agent-skills/active.json"
        kept = receipt.read_text()
        for release, text in (("release-one", "review-prepare loses the lenses"),
                              ("release-two", "lead-accept forgets the checks")):
            changed = json.loads(kept)
            changed["release"]["releaseId"] = release
            receipt.write_text(json.dumps(changed, indent=2))
            for n in (1, 2) if release == "release-one" else (1,):
                source = fx.tmp / f"{release}-{n}.txt"
                source.write_text(f"{text}, seen {n}\n")
                cls.note(fx, "--class", "workflow", "--text", text, "--why", "a script stood in", "--evidence", source,
                         "--command", f"run_delivery.py {release}")
        receipt.write_text(kept)
        cls.note(fx, "--class", "repo", "--text", "The fixture has no README", "--why", "lanes guess the layout")
        cls.note(fx, "--class", "host", "--text", "The terminal drops PATH", "--why", "Node 22 goes missing")
        cls.gaps = {e["data"]["text"]: e for e in kinds(fx, "gap.noted") if "occurrenceOf" not in e["data"]}
        # FX-2 merged and its lane done, FX-3 removed: nothing but the close-out's own steps holds the close.
        hand_off(fx, "FX-2", "lane-1")
        code, out = fx.flow("lead-accept", "FX-2", cwd=fx.main)
        assert code == 0, out
        queue_run(fx, "FX-2")
        state_tool(fx, "focus", "--lane", "lane-1", "--text", "done", "--state", "done")
        with open(fx.tmp / "flow-log/plan-changes.jsonl", "a") as changes:
            changes.write(json.dumps({"type": "change", "id": "c1", "kind": "removed", "subjects": ["FX-3"], "by": "lead",
                                      "why": "Out of scope.", "at": "2026-01-01T00:00:00Z"}) + "\n")
        closed = fx.tmp / "flow-log/FX-1.closed.json"
        for _ in range(4):
            code, out = fx.flow("lead-run", "--once", cwd=fx.main)
            assert code == 0, out
            if closed.exists():
                break
        assert closed.exists(), out
        cls.closed = json.loads(closed.read_text())
        cls.archive = Path(cls.closed["archive"])

    @staticmethod
    def note(fx: Fixture, *argv) -> None:
        code, out = fx.flow("note-gap", *argv, cwd=fx.main)
        assert code == 0, out

    def test_the_repo_gap_is_one_follow_up_on_the_delivery_and_the_publisher_files_it(self) -> None:
        repo = self.gaps["The fixture has no README"]
        self.assertEqual(repo["subject"], "FX-1")
        followups = record(self.fx)["state"]["followups"]
        self.assertEqual([f["id"] for f in followups], [f"FX-1-gap-{repo['id']}"])
        [filed] = kinds(self.fx, "followup.filed")
        self.assertEqual((filed["subject"], filed["data"]),
                         ("FX-1", {"finding": f"gap-{repo['id']}", "followup": f"FX-1-gap-{repo['id']}",
                                   "title": "The fixture has no README"}))
        marks = [m["followup"] for m in tracker_publisher.read_flow(self.fx.tmp / "flow-log", "FX-1", tracker_publisher.MARKS)
                 if m.get("followup")]
        self.assertEqual(marks, [{"story": "FX-1", "finding": f"gap-{repo['id']}", "title": "The fixture has no README"}])
        issues = self.backend.read()["issues"]
        created = [issues[call[1]] for call in self.backend.read()["calls"] if call[0] == "create"]
        self.assertEqual([issue["summary"] for issue in created], ["The fixture has no README"])
        self.assertEqual(created[0]["labels"], [tracker_publisher.followup_label("FX-1", f"gap-{repo['id']}")])

    def test_no_gap_note_is_posted_and_no_workflow_or_host_gap_is_filed(self) -> None:
        self.assertIsNone(tracker_publisher.kind_updates()["gap.noted"])
        noted = {e["id"] for e in kinds(self.fx, "gap.noted")}
        ledger = tracker_publisher.read_ledger(self.fx.tmp / "record")
        self.assertTrue(noted)
        self.assertEqual({line["outcome"] for line in ledger if line.get("journal") in noted}, {"skip"})
        said = json.dumps(self.backend.read()["issues"])
        for text in ("review-prepare loses the lenses", "lead-accept forgets the checks", "The terminal drops PATH"):
            self.assertNotIn(text, said)

    def test_the_workflow_report_has_one_heading_per_release_and_counts_occurrences(self) -> None:
        report = Path(f"{self.archive}.workflow-gaps.md")
        self.assertEqual(self.closed["workflowGaps"], str(report))
        text = report.read_text()
        self.assertEqual(re.findall(r"(?m)^## (\S+)", text), ["release-one", "release-two"])
        one = text[text.index("## release-one"):text.index("## release-two")]
        self.assertIn("### review-prepare loses the lenses", one)
        self.assertIn("- Noted: 2 times", one)
        self.assertIn("- Why: a script stood in", one)
        self.assertIn("- Command: `run_delivery.py release-one`", one)
        two = text[text.index("## release-two"):]
        self.assertIn("### lead-accept forgets the checks", two)
        self.assertIn("- Noted: 1 time\n", two)
        self.assertNotIn("README", text)
        self.assertNotIn("PATH", text)

    def test_the_archive_holds_each_evidence_file_matching_its_sha256(self) -> None:
        noted = [e for e in kinds(self.fx, "gap.noted") if "evidence" in e["data"]]
        self.assertEqual(len(noted), 3)
        report = Path(f"{self.archive}.workflow-gaps.md").read_text()
        for entry in noted:
            with self.subTest(entry=entry["id"]):
                copy = self.archive / "record" / entry["data"]["evidence"]["path"]
                self.assertEqual(hashlib.sha256(copy.read_bytes()).hexdigest(), entry["data"]["evidence"]["sha256"])
                if "occurrenceOf" not in entry["data"]:
                    self.assertIn(str(copy), report)

    def test_the_friction_stub_lists_every_gap_under_its_class(self) -> None:
        text = Path(self.closed["friction"]).read_text()
        gaps = text[text.index("## Gaps noted"):text.index("## What the close-out left to you")]
        expected = {"Workflow": ["review-prepare loses the lenses", "lead-accept forgets the checks"],
                    "Repo": ["The fixture has no README"], "Host": ["The terminal drops PATH"]}
        headings = re.findall(r"(?m)^### (\w+)", gaps)
        self.assertEqual(headings, list(expected))
        for n, (heading, texts) in enumerate(expected.items()):
            part = gaps.split("### ")[n + 1]
            self.assertTrue(part.startswith(heading), part)
            for gap_text in texts:
                self.assertIn(f"- {gap_text} (journal entry {self.gaps[gap_text]['id']}", part)
        self.assertIn("noted 2 times", gaps)


if __name__ == "__main__":
    unittest.main()
