"""A review round in flight: review-prepare journals its start, each lens result that matches its brief is journaled
once as it comes back, by the supervisor's pass or by review-reduce, and the reduced round answers its start. While it
runs, the story waits on it, in one sentence every view prints.

The round's cases drive the installed release's commands on the characterization fixture, as `tests.test_run_delivery`
does. The wait's are derived in-process from the waits fixture, as `tests.test_waits` derives the others.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_run_delivery import (CONTEXT_TODO, POSIX_ONLY, Fixture, bytecode_under, commit, installed_release,
                                     setup_delivery, sh, write_results)
from tests.test_waits import (MARK, PRODUCT_ROOT, WORDS, config, journal, marked, planned, record, render_state,
                              state_tool, views)

RUN_QUIETLY = True


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class RoundInFlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def tearDown(self) -> None:
        self.assertEqual(bytecode_under(self.generation), [], "the case left bytecode in the installed generation")

    def lane(self) -> tuple[Fixture, Path, Path, list]:
        """FX-2's lane, with one commit and its context filled in: (fixture, lane checkout, review folder, lenses)."""
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        setup_delivery(fx)
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        wt = fx.lane("FX-2")
        review = wt / ".orchestration/review/FX-2"
        (wt / "src/one.txt").write_text("one\n")
        commit(wt, "FX-2: one")
        fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)  # seeds the context, and refuses until it is filled
        context = review / "context.md"
        context.write_text(context.read_text().replace(next(line for line in context.read_text().splitlines()
                                                            if line.startswith(CONTEXT_TODO)), "- evidence"))
        return fx, wt, review, fx.lenses(wt)

    def journal(self, fx: Fixture, *kinds: str) -> list[dict]:
        path = fx.tmp / "record/journal.jsonl"
        entries = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
        return [entry for entry in entries if entry["kind"] in kinds]

    def head(self, wt: Path) -> str:
        return sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()

    def test_prepare_journals_each_start_and_the_reduced_round_answers_the_latest(self) -> None:
        fx, wt, review, lenses = self.lane()
        code, out = fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        [first] = self.journal(fx, "review.started")
        # No reason given, and no round before it: neither `why` nor `moved`.
        self.assertEqual((first["subject"], first["lane"], first["data"]),
                         ("FX-2", "lane-1", {"round": 1, "head": self.head(wt), "lenses": [lens for lens, _ in lenses],
                                             "restart": False}))
        # A reason of only spaces is refused, and nothing is journaled or discarded.
        code, out = fx.flow("review-prepare", "FX-2", "--round", "1", "--restart", "--why", "  ", cwd=wt)
        self.assertNotEqual(code, 0, out)
        self.assertIn("--why is empty", out)
        self.assertEqual(len(self.journal(fx, "review.started")), 1)
        code, out = fx.flow("review-prepare", "FX-2", "--round", "1", "--restart", "--why", " Checks the restart ", cwd=wt)
        self.assertEqual(code, 0, out)
        _, again = self.journal(fx, "review.started")
        self.assertEqual(again["data"], {"round": 1, "head": self.head(wt), "lenses": [lens for lens, _ in lenses],
                                         "restart": True, "why": "Checks the restart"})
        short0 = lenses[0][1][0]
        write_results(review, 1, "FX-2", lenses, findings=[{"id": f"{short0}-r1-1", "severity": "P1", "summary": "fix"}])
        code, out = fx.flow("review-reduce", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        # review-reduce journals the lenses no pass has, then the round, which answers the restart's start.
        returned = self.journal(fx, "review.lens.returned")
        self.assertEqual(sorted((e["data"]["lens"], e["data"]["verdict"], e["data"]["findings"]["P1"]) for e in returned),
                         sorted((lens, "changes" if i == 0 else "approve", 1 if i == 0 else 0)
                                for i, (lens, _) in enumerate(lenses)))
        self.assertTrue(all(e["id"] > again["id"] and e["data"]["round"] == 1 for e in returned))
        [reduced] = self.journal(fx, "review.round")
        self.assertEqual(reduced["answers"], [again["id"]])
        # The next round's start counts the commits since the last round's candidate.
        (wt / "src/one.txt").write_text("two\n")
        commit(wt, "FX-2: two")
        (wt / "src/one.txt").write_text("three\n")
        commit(wt, "FX-2: three")
        code, out = fx.flow("review-prepare", "FX-2", "--round", "2", cwd=wt)
        self.assertEqual(code, 0, out)
        latest = self.journal(fx, "review.started")[-1]
        self.assertEqual((latest["data"]["round"], latest["data"]["moved"], latest["data"]["restart"]), (2, 2, False))
        self.assertNotIn("why", latest["data"])
        # The story picked up again after the start: its round is no longer in flight, so a pass journals no result
        # saved for it, and the pass's step still succeeds.
        done = subprocess.run([sys.executable, "-B", str(fx.release / "skills/orchestrate-work/scripts/delivery_state.py"),
                               "--dir", str(fx.tmp / "record"), "journal", "--kind", "lane.assigned", "--subject", "FX-2",
                               "--lane", "lane-1", "--data", json.dumps({"title": "Again"})],
                              cwd=fx.main, env=fx.env, text=True, capture_output=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        write_results(review, 2, "FX-2", lenses)
        code, out = fx.flow("lead-run", "--once", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertEqual([e["data"]["round"] for e in self.journal(fx, "review.lens.returned")], [1, 1])

    def test_each_lens_result_is_journaled_once_whichever_sees_it_first_and_a_mismatched_one_never(self) -> None:
        fx, wt, review, lenses = self.lane()
        code, out = fx.flow("review-prepare", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        (first, _), (second, _) = lenses[0], lenses[1]
        # A result for another candidate fails its brief's identity check: the pass journals nothing for it.
        write_results(review, 1, "FX-2", lenses, candidate="d" * 40, only=0)
        code, out = fx.flow("lead-run", "--once", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.journal(fx, "review.lens.returned"), [])
        # The first lens's file, half saved, is not JSON: the pass skips it and goes on to the second lens, whose own
        # result, saved before the pass, it journals. A pass that let the half-saved file raise would journal nothing.
        short0 = lenses[0][1][0]
        (review / f"result-r1-{short0}.json").write_text('{"schemaVersion": "review-lens-res')
        write_results(review, 1, "FX-2", lenses, only=1)
        code, out = fx.flow("lead-run", "--once", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertEqual([e["data"]["lens"] for e in self.journal(fx, "review.lens.returned")], [second])
        # Saved whole, the first lens's result is journaled by the next pass, and the second's is not again.
        write_results(review, 1, "FX-2", lenses, only=0)
        code, out = fx.flow("lead-run", "--once", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertEqual([e["data"]["lens"] for e in self.journal(fx, "review.lens.returned")], [second, first])
        # A restart's new start journals its lenses afresh: the first lens's result, saved again, is journaled again.
        code, out = fx.flow("review-prepare", "FX-2", "--round", "1", "--restart", cwd=wt)
        self.assertEqual(code, 0, out)
        restart = self.journal(fx, "review.started")[-1]
        self.assertTrue(restart["data"]["restart"])
        write_results(review, 1, "FX-2", lenses, only=0)
        code, out = fx.flow("lead-run", "--once", cwd=fx.main)
        self.assertEqual(code, 0, out)
        again = self.journal(fx, "review.lens.returned")
        self.assertEqual([e["data"]["lens"] for e in again], [second, first, first])
        self.assertGreater(again[2]["id"], restart["id"])
        # The other lens's, saved after it: the next pass journals that one alone, and a pass with nothing new nothing.
        write_results(review, 1, "FX-2", lenses, only=1)
        for _ in range(2):
            code, out = fx.flow("lead-run", "--once", cwd=fx.main)
            self.assertEqual(code, 0, out)
        returned = self.journal(fx, "review.lens.returned")
        self.assertEqual([(e["data"]["lens"], e["data"]["verdict"], e["data"]["round"]) for e in returned],
                         [(second, "approve", 1), (first, "approve", 1), (first, "approve", 1), (second, "approve", 1)])
        self.assertEqual({e["lane"] for e in returned}, {"lane-1"})
        # review-reduce finds both journaled, and journals only the round, which answers the restart's start.
        code, out = fx.flow("review-reduce", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.journal(fx, "review.lens.returned")), 4)
        [reduced] = self.journal(fx, "review.round")
        self.assertEqual(reduced["answers"], [restart["id"]])


OC, AT = "lens.outcome-correctness", "lens.adversarial-testing"
HEAD = "a1b2c3d" + "e" * 33


def started(document: dict, round: int = 3, *, restart: bool = False) -> dict:
    """APP-1's lane starts a round of two lenses at HEAD."""
    return journal(document, "review.started", "APP-1", {"round": round, "head": HEAD, "lenses": [OC, AT],
                                                         "restart": restart}, lane="lane-2")


def returned(document: dict, lens: str, round: int = 3) -> dict:
    return journal(document, "review.lens.returned", "APP-1", {
        "round": round, "lens": lens, "verdict": "approve", "findings": {"P0": 0, "P1": 0, "P2": 0, "P3": 0}},
        lane="lane-2")


def reviewing(document: dict) -> list[dict]:
    return [wait for wait in state_tool.waits(document) if wait["cause"] == "review"]


class ReviewWaitTests(unittest.TestCase):
    """A story in review waits on its round from the round's start until it is reduced, restarted or abandoned."""

    def test_a_round_with_one_of_two_lenses_back_is_one_review_wait_on_the_storys_lane(self) -> None:
        document = record()
        start = started(document)
        returned(document, OC)
        returned(document, OC)
        self.assertEqual(reviewing(document), [{
            "subject": "APP-1", "on": "lane-2", "cause": "review", "stage": "review", "frees": "ended",
            "since": start["at"], "needsSomeone": False,
            "words": "In review: round 3 under way at a1b2c3d, 1 of 2 lenses back."}])
        # The round is the story's own: nothing reads as waited on by it.
        self.assertEqual(state_tool.waited_on(document, state_tool.waits(document)),
                         state_tool.waited_on(record(), state_tool.waits(record())))
        returned(document, AT)
        self.assertEqual(reviewing(document)[0]["words"], "In review: round 3 under way at a1b2c3d, 2 of 2 lenses back.")

    def test_a_lens_counts_only_for_the_round_and_start_it_returned_to(self) -> None:
        document = record()
        returned(document, OC)
        started(document)
        returned(document, OC, round=2)
        journal(document, "review.lens.returned", "APP-1", {"round": 3, "lens": "lens.other", "verdict": "approve",
                                                            "findings": {"P0": 0, "P1": 0, "P2": 0, "P3": 0}})
        journal(document, "review.lens.returned", "APP-2", {"round": 3, "lens": OC, "verdict": "approve",
                                                            "findings": {"P0": 0, "P1": 0, "P2": 0, "P3": 0}})
        self.assertIn("0 of 2 lenses back", reviewing(document)[0]["words"])

    def test_the_wait_ends_when_the_round_is_reduced_restarted_handed_or_reassigned(self) -> None:
        for end in ("answers", "round", "handed", "assigned"):
            with self.subTest(end=end):
                document = record()
                start = started(document)
                returned(document, OC)
                if end == "answers":
                    journal(document, "review.round", "APP-1", {"round": 9})["answers"] = [start["id"]]
                elif end == "round":
                    journal(document, "review.round", "APP-1", {"round": 3})
                elif end == "handed":
                    journal(document, "candidate.handed", "APP-1", {"head": HEAD})
                else:
                    journal(document, "lane.assigned", "APP-1", {"title": "Again"})
                self.assertEqual(reviewing(document), [])
        # Another story's reduction ends nothing.
        document = record()
        started(document)
        journal(document, "review.round", "APP-2", {"round": 3})
        self.assertEqual(len(reviewing(document)), 1)
        # A restart's new start is the round in flight, with no lens back yet.
        document = record()
        started(document)
        returned(document, OC)
        again = started(document, restart=True)
        [wait] = reviewing(document)
        self.assertEqual((wait["since"], wait["words"]),
                         (again["at"], "In review: round 3 under way at a1b2c3d, 0 of 2 lenses back."))

    def test_a_story_settled_or_not_started_has_no_review_wait(self) -> None:
        for stage in ("settled", "planned"):
            with self.subTest(stage=stage):
                document = record()
                started(document)
                planned(document, "APP-1")["stage"] = stage
                self.assertEqual(reviewing(document), [])
        document = record()
        started(document)
        planned(document, "APP-1")["stage"] = "building"
        self.assertEqual(len(reviewing(document)), 1)


class ReviewMarkerTests(unittest.TestCase):
    """With the table marked, the review wait's sentence carries its template's marker on every view, which print it
    and word none of it themselves."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        copied = Path(folder.name) / "wait-words.json"
        copied.write_text(json.dumps(marked(json.loads(WORDS.read_text(encoding="utf-8"))), ensure_ascii=False),
                          encoding="utf-8")
        for module in (state_tool, views.review_state):
            patcher = mock.patch.object(module, "WAIT_WORDS_FILE", copied)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.document = record()
        started(self.document)
        returned(self.document, OC)
        [wait] = [wait for wait in render_state.with_waits(self.document)["waits"] if wait["cause"] == "review"]
        self.words = wait["words"]

    def test_the_sentence_carries_its_own_marker(self) -> None:
        self.assertEqual(MARK.search(self.words).group(1), "waits.review")
        self.assertEqual(self.words, "⟦waits.review⟧ In review: round 3 under way at a1b2c3d, 1 of 2 lenses back.")

    def test_the_markdown_view_shows_it_under_the_story(self) -> None:
        shown = render_state.render_markdown(self.document)
        story = shown[shown.index("\n- APP-1:"):].split("\n- ")[1]
        self.assertIn(f"\n  - {render_state._markdown_text(self.words)}", story)

    def test_the_built_in_page_and_the_live_page_carry_it_and_word_none_of_it(self) -> None:
        page = render_state.render_static_page(self.document)
        raw = re.search(r'<script type="application/json" id="delivery-state">(.*?)</script>', page, re.S).group(1)
        embedded = json.loads(raw)
        self.assertIn(self.words, [wait["words"] for wait in embedded["waits"]])
        data = views.line_map_data(self.document, {}, config(), theme_key="t", history=None)
        self.assertIn(self.words, [wait["words"] for wait in data["document"]["waits"]])
        for script in (page.replace(raw, ""),
                       (PRODUCT_ROOT / "skills/delivery-views/assets/line-map.js").read_text(encoding="utf-8"),
                       (PRODUCT_ROOT / "skills/delivery-views/assets/run-summary.js").read_text(encoding="utf-8")):
            self.assertNotIn("under way at", script)


if __name__ == "__main__":
    unittest.main()
