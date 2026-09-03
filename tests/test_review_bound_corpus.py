from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PHRASES: dict[str, tuple[str, ...]] = {
    "skills/review-work/SKILL.md": (
        "The workflow default is four rounds",
        "a repository instruction file may declare a smaller bound; a larger"
        " bound is an operator decision recorded before the first round",
        "every round obtained for the delivery counts against it, across every loop"
        ", except one grace verification round obtained at the bound",
        "typed blocker `review.loop-bound-reached` naming the open findings and does"
        " not run another round",
        "by that lens's closure report, by that lens's withdrawal with its reason,"
        " or, for a finding it filed as a deferral, by that deferral",
        "A deferral's follow-up is a tracked item the executor records before the"
        " delivery reports done",
        "the deferral's evidence names what that item must say, and the lens files"
        " nothing itself",
    ),
    "skills/obtain-review/SKILL.md": (
        "A lens's assessment round is its first result-carrying round for the"
        " delivery",
        "its delta runs from the candidate of that lens's most recent"
        " result-carrying entry",
        "A grace round obtained at the bound is such a verification round, like"
        " any other, with no special kind, scope, or carry-forward",
        "that lens's own retained entry from every prior round of the delivery in"
        " which it was selected",
        "binding tuple with its result-less state where the acquisition failed",
        "and nothing else, nothing narrower",
        "The previous round's `candidateRef` is read from the lens's most recent"
        " entry",
        "An assessment round spans no delta and needs no resolution pair",
        "a pair of the exact `candidateRef` text (its `opaque` member) and the"
        " revision it resolves to",
        "matches each pair's `candidateRef` text by equality, never by parsing",
        "The lens re-derives its carried set from those entries",
        "returns `changes-requested` with a P0 finding naming the defect",
        "when a round is accounted for by neither an obtained entry nor a"
        " result-less one",
        "when a resolution is missing, unpaired, or paired with a reference that is"
        " neither of the two the delta spans",
        "or when any narrower scope is supplied",
        "report each one closed, open, raised with its new severity, withdrawn with"
        " its reason, or still deferred",
        "then to review the delta between the previous round's candidate and this"
        " one",
        "filed severity and in-scope status are a floor: the filing lens may raise"
        " them on re-check and never lower them",
        "A carried P0 or P1 is discharged only by the filing lens's closure report"
        " or by that lens's withdrawal with its reason, never by a later deferral",
        "a finding the lens filed as a deferral is discharged by that deferral",
        "A new finding outside the delta is filed only at P0",
        "recorded as a deferral with a follow-up item",
        "belong in `evidence` as deferrals with a follow-up item",
        "A deferral's follow-up is a tracked item, and the executor records it, not"
        " the lens",
        "the lens records in its evidence what that item must say: the outcome the"
        " item must reach, the deferral's identifier, and the lens that filed it",
        "A lens is read-only and files nothing itself",
    ),
    "skills/execute-work/SKILL.md": (
        "state the bound in effect for the delivery",
        "Never declare a second bound for the same delivery",
        "The lens set is declared before the first round and held unchanged for the"
        " delivery",
        "no lens is added after the first round",
        "dropping a lens disposes of nothing it filed",
        "record a disposition for every finding, each citing the lens",
        "The executor closes, defers, and declines nothing on its own judgement",
        "With dissent open, record the typed blocker `review.loop-bound-reached`"
        " naming the open findings, report `partial`, and stop",
        "record the same blocker naming the failed lens, report `partial`, and stop",
        "When every lens is aligned at the bound and a required change would alter"
        " the candidate, the delivery obtains exactly one grace verification round"
        " for that change",
        "at most once per delivery, declared as the grace round when it is obtained",
        "The grace round is an ordinary verification round under the same lens set"
        " and the same carry-forward rules",
        "If the grace round does not align, or a further candidate change is required"
        " after it, record the typed blocker `review.loop-bound-reached` naming"
        " what is open, report `partial`, and stop",
        "The grace round is not available with dissent open or where the latest"
        " round was blocked by a failed acquisition",
        "Before the delivery reports done, every finding any lens recorded as a"
        " deferral has a tracked follow-up item recorded through the tracker's"
        " `create-work`",
        "one item per shippable outcome, each deferral cited by its identifier and"
        " the lens that filed it, related to the delivering work item",
        "A deferral with no tracked item is an open item for the finish line",
        "record the typed blocker `review.deferral-untracked` naming the deferrals"
        " that lack one, report `partial`, and stop",
        "Where no tracker is configured, those follow-up items are the actionable"
        " tracking handoff in the result rather than a blocker",
    ),
    "skills/compound-delivery-kernel/SKILL.md": (
        "Declare the round bound before the first round; every round of the delivery"
        " counts, except one grace round obtained at the bound; the default is"
        " four rounds",
        "Later rounds verify closure and review the delta",
        "only P0 and P1 findings block",
        "the lens, not the executor, closes or defers a finding",
        "Name the tracked follow-up item recorded for every deferral any lens"
        " filed",
    ),
}


class ReviewBoundCorpusTests(unittest.TestCase):
    """Pin the bounded-convergence rules in the shipped workflow text."""

    def test_bounded_convergence_phrases_are_present(self) -> None:
        for relative, phrases in REQUIRED_PHRASES.items():
            normalized = " ".join(
                (ROOT / relative).read_text(encoding="utf-8").split()
            )
            for phrase in phrases:
                with self.subTest(path=relative, phrase=phrase):
                    self.assertIn(phrase, normalized)

if __name__ == "__main__":
    unittest.main()
