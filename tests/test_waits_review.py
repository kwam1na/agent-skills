"""A review round in flight: review-prepare journals its start, each lens result that matches its brief is journaled
once as it comes back, by the supervisor's pass or by review-reduce, and the reduced round answers its start.

The cases drive the installed release's commands on the characterization fixture, as `tests.test_run_delivery` does.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from tests.test_run_delivery import (CONTEXT_TODO, POSIX_ONLY, Fixture, bytecode_under, commit, installed_release,
                                     setup_delivery, sh, write_results)

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
        # The lens's own result, saved before a pass: that pass journals it.
        write_results(review, 1, "FX-2", lenses, only=0)
        code, out = fx.flow("lead-run", "--once", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertEqual([e["data"]["lens"] for e in self.journal(fx, "review.lens.returned")], [first])
        # The other lens's, saved after it: the next pass journals that one alone, and a pass with nothing new nothing.
        write_results(review, 1, "FX-2", lenses, only=1)
        for _ in range(2):
            code, out = fx.flow("lead-run", "--once", cwd=fx.main)
            self.assertEqual(code, 0, out)
        returned = self.journal(fx, "review.lens.returned")
        self.assertEqual([(e["data"]["lens"], e["data"]["verdict"], e["data"]["round"]) for e in returned],
                         [(first, "approve", 1), (second, "approve", 1)])
        self.assertEqual({e["lane"] for e in returned}, {"lane-1"})
        # review-reduce finds both journaled, and journals only the round.
        code, out = fx.flow("review-reduce", "FX-2", "--round", "1", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.journal(fx, "review.lens.returned")), 2)
        [start] = self.journal(fx, "review.started")
        [reduced] = self.journal(fx, "review.round")
        self.assertEqual(reduced["answers"], [start["id"]])


if __name__ == "__main__":
    unittest.main()
