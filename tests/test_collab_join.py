"""Joining a shared delivery, feedback on its plan, and the shared watch stream.

`collab join` on a fresh clone builds the local record from the admitted seats' partitions, so the first seat's
`record.started` is its entry 1, then gives the clone a seat that asks to be admitted; every join prints the story,
the plan's change since the seat last read it, open feedback and the scopes it holds, and a join run again prints only
what is new. Feedback is a journal fact both seats see, settled only by the holder of the section it is on. `collab
watch` prints one line per ingested peer entry. The narrator attributes a shared record's entries to their seats'
labels.

The commands run through run_delivery.py's own command line in the run-delivery fixture repository, whose first
checkout is seat a; b is a second clone of its remote. With no supervisor running, the sync loop's steps are taken
here: each seat's partition pushed, its peers' fetched and ingested.
"""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import time
import unittest
from pathlib import Path

from tests.quiet_git import quieten
from tests.test_planning_record import KEY, PlanningRecordCase, generation_tools, journal, record
from tests.test_run_delivery import POSIX_ONLY, ROOT, commit, sh

RUN_QUIETLY = True  # every case runs the tool's command line and real git, several times

SCRIPTS = ROOT / "skills/orchestrate-work/scripts"
sys.path.insert(0, str(SCRIPTS))
import narrator  # noqa: E402  (the corpus's own narrator)


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class JoinCase(PlanningRecordCase):
    """a opens the planning record of the fixture's plan; b is a fresh clone of the same remote."""

    def shared(self, *, poll: float | None = None):
        fx = self.fixture()
        if poll is not None:
            binding = fx.main / ".agents/run-delivery.json"
            raw = json.loads(binding.read_text())
            raw["collab"] = {"pollSeconds": poll}
            binding.write_text(json.dumps(raw, indent=2) + "\n")
            commit(fx.main, "a short collab poll")
        self.delivered(fx)
        sh("git", "switch", "-q", "-c", "lead/FX-1", cwd=fx.main)
        sh("git", "push", "-q", "-u", "origin", "lead/FX-1", cwd=fx.main)
        a = self.opened(fx)
        tool, link = generation_tools(self.generation)
        link.push(a, remote="origin")
        b_repo = fx.tmp / "b"
        sh("git", "clone", "-q", "-b", "lead/FX-1", fx.origin, b_repo)
        quieten(b_repo)
        sh("git", "config", "user.name", "Ben", cwd=b_repo)
        b = b_repo / a.relative_to(fx.main.resolve())
        self.tool, self.link = tool, link
        return fx, a, b_repo, b

    def exchange(self, *folders: Path) -> None:
        """The sync loop's pass on each folder in turn: push its seat's partition, fetch and ingest its peers'."""
        for folder in folders:
            seat = self.tool.local_seat(folder, KEY)
            if seat is not None:
                self.link.push(folder, remote="origin")
            self.link.fetch(folder, remote="origin", key=KEY)
            self.link.ingest(folder, key=KEY)

    def ok(self, fx, *argv, cwd: Path) -> str:
        code, out = fx.flow(*argv, cwd=cwd)
        self.assertEqual(code, 0, out)
        return out

    def woken(self, fx, after: int) -> dict:
        """a's lead-wait started after entry `after`: what it woke on."""
        output = fx.tmp / f"wait-{after}.json"
        code, out = fx.flow("--delivery", KEY, "lead-wait", "--after", after, "--timeout", 30, "--output", output,
                            cwd=fx.main)
        self.assertEqual(code, 0, out)
        return json.loads(output.read_text())


class JoinTests(JoinCase):
    def test_a_fresh_clone_joins_with_the_full_catch_up_then_only_what_is_new(self) -> None:
        fx, a, b_repo, b = self.shared()
        out = self.ok(fx, "collab", "join", "--key", KEY, cwd=b_repo)
        seat_b = self.tool.local_seat(b, KEY)
        self.assertRegex(seat_b, r"^s-[0-9a-f]{12}$")
        # Entry 1 on the fresh clone is a's record.started, at a's own instant: the tracker's init id agrees.
        first_a, first_b = journal(a)[0], journal(b)[0]
        self.assertEqual((first_b["kind"], first_b["at"], first_b["origin"]["seat"]),
                         ("record.started", first_a["at"], first_a["origin"]["seat"]))
        publisher = self.publisher()
        self.assertEqual(publisher.init_id(journal(b)), publisher.init_id(journal(a)))
        # The seat is made after the ingest, then asks to be admitted, naming what it has seen.
        requested = [entry for entry in journal(b) if entry["kind"] == "seat.requested"]
        self.assertEqual([entry["data"]["seat"] for entry in requested], [seat_b])
        self.assertEqual(requested[0]["data"]["label"], "Ben")
        self.assertEqual(requested[0]["origin"]["seen"], {first_a["origin"]["seat"]: journal(a)[-1]["origin"]["seq"]})
        # The full catch-up, in its order: the story, the plan, open feedback, the scopes held.
        sections = [out.index(word) for word in ("story:", "plan:", "open feedback:", "scopes held:")]
        self.assertEqual(sections, sorted(sections), out)
        self.assertIn("The delivery started", out.split("plan:")[0])
        self.assertIn("docs/plan.md", out.split("plan:")[1].split("open feedback:")[0])
        self.assertIn("open feedback: none", out)
        self.assertIn("scopes held: none", out)
        self.assertIn(f"collab admit --seat {seat_b}", out)
        # b's join wakes a's lead-wait with the admission request.
        last = journal(a)[-1]["id"]
        self.exchange(b, a)
        woke = self.woken(fx, last)
        self.assertIn("seat.requested", [event["kind"] for event in woke["events"]])
        # a admits b; b's join again prints only what is new: the admission, no record start, the plan unchanged.
        self.ok(fx, "collab", "admit", "--key", KEY, "--seat", seat_b, cwd=fx.main)
        self.exchange(a, b)
        again = self.ok(fx, "collab", "join", "--key", KEY, cwd=b_repo)
        story = again.split("plan:")[0]
        self.assertIn("Ben joined the delivery", story)
        self.assertNotIn("The delivery started", story)
        self.assertRegex(again, r"plan: docs/plan\.md unchanged since [0-9a-f]{12}")
        self.assertEqual(len([entry for entry in journal(b) if entry["kind"] == "seat.requested"]), 1)
        seen = [entry for entry in journal(b) if entry["kind"] == "plan.seen"]
        self.assertEqual(len(seen), 1, "an admitted seat journals what it read")
        # Run a third time, nothing is new, and the plan read is not journalled again.
        third = self.ok(fx, "collab", "join", "--key", KEY, cwd=b_repo)
        self.assertIn("story: nothing new", third)
        self.assertIn("open feedback: none", third)
        self.assertEqual(len([entry for entry in journal(b) if entry["kind"] == "plan.seen"]), 1)
        # a changes the plan and raises feedback: b's next join shows the plan's diff and the feedback as new.
        plan = fx.main / "docs/plan.md"
        plan.write_text(plan.read_text().replace("- Scenarios: it loads; it fails.", "- Scenarios: it loads, a says."))
        self.ok(fx, "plan-sync", "--plan", "docs/plan.md", "--message", "a's edit", cwd=fx.main)
        self.ok(fx, "collab", "feedback", "add", "--key", KEY, "--anchor", "## Stories", "--text", "Split S2?",
                cwd=fx.main)
        self.exchange(a, b)
        fourth = self.ok(fx, "collab", "join", "--key", KEY, cwd=b_repo)
        self.assertRegex(fourth, r"plan: docs/plan\.md changed since [0-9a-f]{12}, now at [0-9a-f]{12}:")
        self.assertIn("+- Scenarios: it loads, a says.", fourth)
        self.assertRegex(fourth, r"open feedback:\n  #\d+ on ## Stories, by [^:]+: Split S2\?")
        self.assertEqual(len([entry for entry in journal(b) if entry["kind"] == "plan.seen"]), 2)
        # Once more, the diff and the feedback are no longer new: the feedback stays open, unrepeated.
        fifth = self.ok(fx, "collab", "join", "--key", KEY, cwd=b_repo)
        self.assertRegex(fifth, r"plan: docs/plan\.md unchanged since [0-9a-f]{12}")
        self.assertIn("open feedback: none new (1 still open)", fifth)
        self.assertNotIn("Split S2?", fifth)
        self.assertIn("story: nothing new", fifth)
        self.assertEqual(len([entry for entry in journal(b) if entry["kind"] == "plan.seen"]), 2)
        # A relabel reaches the peer's derived seats.
        self.ok(fx, "collab", "label", "--key", KEY, "--label", "Benedict", cwd=b_repo)
        self.exchange(b, a)
        self.assertEqual(self.tool.derived_seats(journal(a))[seat_b]["label"], "Benedict")
        self.assertEqual(record(a)["meta"]["collab"]["seats"][seat_b]["label"], "Benedict")

    def publisher(self):
        import importlib.util
        path = self.generation / "skills/run-delivery/scripts/tracker_publisher.py"
        spec = importlib.util.spec_from_file_location("tracker_publisher_of_the_generation", path)
        module = importlib.util.module_from_spec(spec)
        previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = previous
        return module


class FeedbackTests(JoinCase):
    def needs(self, fx, folder: Path) -> list[dict]:
        """What one supervisor pass over a's record found needs a person, as its pass log records it."""
        self.ok(fx, "--delivery", KEY, "lead-run", "--once", cwd=fx.main)
        log = folder.parent / "flow-log" / f"{KEY}.supervisor.jsonl"
        return json.loads(log.read_text().splitlines()[-1])["needsYou"]

    def test_feedback_wakes_the_peer_holds_approval_and_only_the_anchor_s_holder_settles_it(self) -> None:
        fx, a, b_repo, b = self.shared()
        self.ok(fx, "collab", "join", "--key", KEY, cwd=b_repo)
        self.exchange(b, a)
        seat_b = self.tool.local_seat(b, KEY)
        self.ok(fx, "collab", "admit", "--key", KEY, "--seat", seat_b, cwd=fx.main)
        self.exchange(a, b)
        # An anchor the plan has no section for is refused.
        code, out = fx.flow("collab", "feedback", "add", "--key", KEY, "--anchor", "## Nowhere", "--text", "Hm.",
                            cwd=b_repo)
        self.assertEqual(code, 2, out)
        self.assertIn("feedback.anchor_unknown", out)
        self.assertIn("## Decisions", out)
        # b's blocking feedback wakes a's lead-wait.
        last = journal(a)[-1]["id"]
        out = self.ok(fx, "collab", "feedback", "add", "--key", KEY, "--anchor", "## Decisions", "--text",
                      "The copy needs a second look.", "--blocking", cwd=b_repo)
        self.exchange(b, a)
        woke = self.woken(fx, last)
        self.assertIn("feedback.raised", [event["kind"] for event in woke["events"]])
        raised = next(entry for entry in journal(a) if entry["kind"] == "feedback.raised")
        self.assertEqual(raised["data"], {"anchor": "## Decisions", "text": "The copy needs a second look.",
                                          "blocking": True})
        # Feedback that doesn't stop approval is open too, and holds nothing.
        self.ok(fx, "collab", "feedback", "add", "--key", KEY, "--anchor", "## Stories", "--text", "A thought.",
                cwd=fx.main)
        # The blocking one shows in Needs you on a's record, and a's join lists both as open.
        needs = self.needs(fx, a)
        [item] = [item for item in needs if item.get("kind") == "feedback"]
        self.assertTrue(item["holding"])
        self.assertIn("## Decisions", item["text"])
        self.assertNotIn("A thought.", json.dumps(needs))
        listed = self.ok(fx, "collab", "join", "--key", KEY, cwd=fx.main)
        self.assertRegex(listed, rf"open feedback:\n  #{raised['id']} on ## Decisions, stopping approval, by Ben: "
                                 r"The copy needs a second look\.\n  #\d+ on ## Stories, by [^:]+: A thought\.")
        # An id that is no feedback is refused.
        code, out = fx.flow("collab", "feedback", "resolve", "--key", KEY, "--id", 1, "--outcome", "answered",
                            "--reason", "Not feedback.", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("feedback.unknown", out)
        # b doesn't hold the section it is on, so b can't settle it yet.
        on_b = next(entry for entry in journal(b) if entry["kind"] == "feedback.raised")
        code, out = fx.flow("collab", "feedback", "resolve", "--key", KEY, "--id", on_b["id"], "--outcome", "declined",
                            "--reason", "Mine to settle.", cwd=b_repo)
        self.assertEqual(code, 2, out)
        self.assertIn("scope.not_held", out)
        self.assertIn("plan:## Decisions", out)
        self.assertFalse([entry for entry in journal(b) if entry["kind"] == "feedback.resolved"])
        # b asks for the section and a passes it on: then b, its holder, settles the feedback.
        self.ok(fx, "collab", "request", "--key", KEY, "--scope", "plan:## Decisions", "--reason", "To settle it.",
                cwd=b_repo)
        self.exchange(b, a)
        self.ok(fx, "collab", "give", "--key", KEY, "--scope", "plan:## Decisions", "--to", seat_b, cwd=fx.main)
        self.exchange(a, b)
        self.assertEqual(self.tool.holder(journal(b), "plan:## Decisions"), seat_b)
        self.ok(fx, "collab", "feedback", "resolve", "--key", KEY, "--id", on_b["id"], "--outcome", "accepted",
                "--reason", "Reworded.", cwd=b_repo)
        self.exchange(b, a)
        [resolved] = [entry for entry in journal(a) if entry["kind"] == "feedback.resolved"]
        self.assertEqual((resolved["answers"], resolved["data"]), ([raised["id"]], {"outcome": "accepted",
                                                                                     "reason": "Reworded."}))
        # Settled once: a second resolution is refused.
        code, out = fx.flow("collab", "feedback", "resolve", "--key", KEY, "--id", raised["id"], "--outcome",
                            "answered", "--reason", "Again.", cwd=fx.main)
        self.assertEqual(code, 2, out)
        self.assertIn("feedback.settled", out)
        # Released from Needs you once settled.
        needs = self.needs(fx, a)
        self.assertFalse([item for item in needs if item.get("kind") == "feedback"])


class WatchTests(JoinCase):
    """`collab watch` reads the local record, which the sync loop keeps current: one line per ingested peer entry."""

    def watching(self, fx, cwd: Path, *argv) -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-B", str(fx.tool), "collab", "watch", "--key", KEY, *map(str, argv)],
                                cwd=cwd, env=fx.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def line(self, proc: subprocess.Popen, within: float) -> tuple[str, float]:
        started = time.monotonic()
        ready, _, _ = select.select([proc.stdout], [], [], within)
        self.assertTrue(ready, f"no line within {within:g}s")
        return proc.stdout.readline(), time.monotonic() - started

    def test_one_flushed_line_per_peer_entry_and_its_three_exits(self) -> None:
        fx, a, b_repo, b = self.shared(poll=2)
        self.ok(fx, "collab", "join", "--key", KEY, cwd=b_repo)
        self.exchange(b, a)
        seat_b = self.tool.local_seat(b, KEY)
        self.ok(fx, "collab", "admit", "--key", KEY, "--seat", seat_b, cwd=fx.main)
        self.exchange(a, b)
        # With no --until it prints each peer entry ingested while it runs, and exits 0 at --timeout.
        proc = self.watching(fx, b_repo, "--timeout", 12)
        time.sleep(1)
        # b's own entry is not a peer's: it prints nothing.
        self.ok(fx, "collab", "label", "--key", KEY, "--label", "Benedict", cwd=b_repo)
        self.ok(fx, "collab", "feedback", "add", "--key", KEY, "--anchor", "## Stories", "--text", "Split S2?",
                cwd=fx.main)
        self.exchange(a, b)
        line, took = self.line(proc, 4.5)  # within two ticks of the ingest
        self.assertLessEqual(took, 4.5)
        self.assertRegex(line, r"^#\d+ feedback\.raised by [^:]+: Feedback on ## Stories\. Split S2\?\n$")
        rest, err = proc.communicate(timeout=30)
        self.assertEqual((proc.returncode, rest), (0, ""), err)
        # With --until it exits 0 on the first matching line; one never matching exits 3 at --timeout.
        proc = self.watching(fx, b_repo, "--timeout", 20, "--until", "Split S3")
        time.sleep(1)
        self.ok(fx, "collab", "feedback", "add", "--key", KEY, "--anchor", "## Stories", "--text", "Split S3?",
                cwd=fx.main)
        self.exchange(a, b)
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(len(out.splitlines()), 1, out)
        self.assertIn("Split S3?", out)
        proc = self.watching(fx, b_repo, "--timeout", 3, "--until", "never")
        out, err = proc.communicate(timeout=30)
        self.assertEqual((proc.returncode, out), (3, ""), err)


class NarratorAttributionTests(unittest.TestCase):
    """A shared record's entries are told as their seats' labels; an unshared record's story is as it was."""

    SEAT_A, SEAT_B = "s-aaaaaaaaaaaa", "s-bbbbbbbbbbbb"

    def entry(self, number: int, seat: str, seq: int, kind: str, data: dict, **more) -> dict:
        return {"id": number, "at": f"2026-11-24T10:00:{number:02d}Z", "kind": kind, "writer": "lead", "data": data,
                "origin": {"seat": seat, "seq": seq}, **more}

    def test_entries_name_their_seat_s_label_and_the_seats_they_name(self) -> None:
        a, b = self.SEAT_A, self.SEAT_B
        entries = [
            self.entry(1, a, 1, "record.started", {"base": {"ref": "origin/main", "head": "a" * 40}}),
            self.entry(2, a, 2, "seat.admitted", {"seat": a, "label": "Ama", "approver": True}),
            self.entry(3, b, 1, "seat.requested", {"seat": b, "label": "Ben"}),
            self.entry(4, a, 3, "seat.admitted", {"seat": b, "label": "Ben", "approver": True}, answers=[3]),
            self.entry(5, b, 2, "feedback.raised", {"anchor": "## Decisions", "text": "Not yet.", "blocking": True}),
            self.entry(6, a, 4, "scope.transferred", {"scope": "plan:## Decisions", "from": a, "to": b}),
            self.entry(7, b, 3, "seat.labeled", {"seat": b, "label": "Benedict"}),
            self.entry(8, b, 4, "plan.endorsed", {"commit": "c" * 40}),
        ]
        story = narrator.narrate(entries, {"meta": {"collab": {"key": "FX-1", "seats": {}}}})
        said = {entry: (beat if beat["form"] == "entry" else item)["words"]
                for beat in story["beats"] for entry in beat["entries"]
                for item in ([beat] if beat["form"] == "entry" else beat["items"]) if
                beat["form"] == "entry" or item["entry"] == entry}
        self.assertIn("By Benedict", said[5]["line"])
        self.assertIn("Not yet.", said[5]["line"])
        # The seats an entry names are named by their latest labels, its writer among them.
        self.assertEqual(said[6]["head"], "plan:## Decisions passed to Benedict")
        self.assertEqual(said[6]["line"], "From Ama")
        self.assertEqual(said[8]["head"], "Benedict endorsed the plan")
        self.assertNotIn(b, json.dumps(said[8]))
        # A seat entry already names its seat by label; its writer is named when another seat wrote it.
        self.assertEqual(said[3]["head"], "Ben asked to join the delivery")
        self.assertNotIn("By ", said[3]["line"])
        self.assertIn("By Ama", said[4]["line"])

    def test_a_short_label_inside_a_word_is_still_attributed(self) -> None:
        a = self.SEAT_A
        entries = [self.entry(1, a, 1, "record.started", {"base": {"ref": "origin/main", "head": "a" * 40}}),
                   self.entry(2, a, 2, "seat.admitted", {"seat": a, "label": "Al", "approver": True}),
                   self.entry(3, a, 3, "feedback.raised", {"anchor": "## Stories", "text": "Also this.",
                                                           "blocking": False})]
        story = narrator.narrate(entries, {"meta": {"collab": {"key": "FX-1", "seats": {}}}})
        [beat] = [beat for beat in story["beats"] if beat["entries"] == [3]]
        self.assertEqual(beat["words"]["line"], "By Al. Also this.")

    def test_an_unshared_record_s_story_has_no_attribution(self) -> None:
        entries = [{"id": 1, "at": "2026-11-24T10:00:01Z", "kind": "record.started", "writer": "lead",
                    "data": {"base": {"ref": "origin/main", "head": "a" * 40}}}]
        story = narrator.narrate(entries, {"meta": {}})
        self.assertNotIn("By ", json.dumps(story["beats"]))


if __name__ == "__main__":
    unittest.main()
