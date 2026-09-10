from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PHRASES: dict[str, tuple[str, ...]] = {
    "skills/review-work/SKILL.md": (
        "The workflow default is four rounds",
        # Pinned by operator decision on the item reconciling these pins.
        "a repository instruction file may declare a smaller bound; a larger"
        " bound is an operator decision recorded before the first round",
        "every round obtained for the delivery counts against it, across every loop"
        ", except one grace verification round obtained at the bound and a round"
        " reopened because the base moved under an unchanged deliverable"
        " identity",
        "typed blocker `review.loop-bound-reached` naming the open findings and does"
        " not run another round",
        "by that lens's closure report, by that lens's withdrawal with its reason,"
        " or, for a finding it filed as a deferral, by that deferral",
        "A deferral's follow-up is a tracked item the executor records before the"
        " delivery reports done",
        "the deferral's evidence names what that item must say, and the lens files"
        " nothing itself",
        "One tracked item may carry several deferrals, one item per shippable"
        " outcome",
        "Where no tracker is configured, those follow-up items are the actionable"
        " tracking handoff in the result rather than a blocker",
        "The review is bound to the release installed when the delivery's first"
        " round is acquired: a release installed mid-review is recorded and does"
        " not invalidate rounds already carried, and the delivery's closing record"
        " names the bound release's `releaseId` and `archiveSha256`",
        "A lens result may mark some of its own findings as late: findings that"
        " lens raised against lines unchanged since its own most recent"
        " result-carrying round before this one",
        "A late finding is still a finding and is reduced exactly like any other,"
        " so the mark never changes the outcome of a round",
        "The mark is visible dissent about the lens rather than about the"
        " candidate, and the returned value carries every marked finding with the"
        " lens and the round that filed it",
        "A late finding a lens recorded as a deferral rather than as a finding is"
        " marked late in that deferral, and neither the returned value nor the"
        " count below carries it",
        "P0 and P1 findings are blocking and are never deferred",
        "Under `review.green/1` that exception is limited to `expansion` scope",
        "An `in_contract` P2 or P3 deferral requires the consumer to declare"
        " `review.green/2` in the review obligation's accepted payload specs",
        "That version admits `in_contract` and `expansion` deferrals under the"
        " same rule, never `adjacent` findings",
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
        "One tracked item may carry several deferrals, one item per shippable"
        " outcome",
        "Where no tracker is configured, those follow-up items are the actionable"
        " tracking handoff in the result rather than a blocker",
        "A round obtained only because the base moved, under an unchanged"
        " deliverable identity, does not count against the bound",
        "Within that case the round counter advances only when the candidate's"
        " deliverable identity differs from the round before",
        "A round obtained for any other reason counts against the bound even"
        " where the identity is unchanged",
        "a round obtained to dispute a finding, to carry an authorized repair,"
        " or to retry an acquisition that failed, on an unchanged candidate"
        " spends the bound like any other",
        "The bound measures dissent, not the merge traffic a delivery happens to"
        " sit behind",
        "A reopened round is realized like the round it reopens, keeping that"
        " round's kind, its lens set, and its carry-forward rules",
        "The portable Python reducer receives every obtained pass in"
        " `round_history`",
        "The reducer verifies every content digest and requires the two entry"
        " lists to be byte-identical",
        "Equal caller summary digests, an unchanged deliverable token alone, or"
        " a missing comparison cannot reopen a round",
        "The provider rejects mixed legacy and history shapes, unknown members,"
        " incomplete history, stale comparison bindings, and an outer candidate"
        " that is not the exact final active candidate",
        "where it is unchanged the replay reopens the round the previous"
        " candidate was reviewed under, whether that round is still open or"
        " already closed, carrying its number, its declared lens set, and its"
        " carried findings",
        "Every later result-carrying round of that lens that advances the round"
        " counter is a verification round",
        "identity is unchanged only when the diff of the new candidate against"
        " its base is byte-identical to the diff of the previous candidate"
        " against its base, over the same set of paths, with no path added,"
        " removed, or renamed",
        "The executor computes that comparison and retains its output as"
        " evidence; a comparison that was not run, or that reports any"
        " difference at all, is a differing identity",
        "a rebase whose conflict resolution altered one delivered byte spends a"
        " round like any other change",
        "not the reason the base moved, not the disjointness of the upstream"
        " commits, and not a lens's agreement that nothing changed",
        "the lens's retained entry for that round number is the replay's entry",
        "the superseded entry stays in the delivery's history but is supplied"
        " neither as carry-forward nor as that round's entry to the reduction",
        "the entries a later round carries stay one per round, the reduction"
        " receives one entry per lens per round, and its delta runs from the"
        " replayed candidate",
        "A result may also carry `lateFindings`, each naming one of that same"
        " result's own `findings` that the lens raised late; a lens carries none"
        " on its assessment round, and acquisition rejects the member outright in"
        " a first round, where no lens has a round behind it",
        "Realize every lens with a brief that states where in the loop its round"
        " sits",
        "The brief names the round number and the declared bound as round k of N,"
        " and says whether this round is the grace round obtained at the bound",
        "Where the round belongs to a loop that declares no bound of its own, the"
        " brief names the round number and says the loop is unbounded",
        "The review of a plan, which `$plan-work` leaves boundless, is such a loop:"
        " its rounds are realized outside the bound-and-reduction machinery"
        " `$review-work` describes, count against no bound that workflow reduces"
        " against, and reach no `review.loop-bound-reached`",
        "every reviewer, in plan review and delivery review alike, optimizes for"
        " the lowest number of rounds, and a finding the lens can raise now is"
        " raised now",
        "asks the lens for a conscious effort to find everything it can within"
        " the allocation, not across it",
        "every finding the lens can raise against lines unchanged since its own"
        " most recent result-carrying round was expected in that round",
        "A finding the lens raises after its assessment round against lines"
        " unchanged since its own most recent result-carrying round is still a"
        " finding, judged on its merits like any other, and the lens marks it late"
        " in its own report",
        "Such a finding falls outside that round's delta, so the out-of-delta rule"
        " above still decides where the lens puts it: `lateFindings` names only"
        " findings the result filed, and a late finding that rule keeps out of"
        " `findings` is marked late in the deferral it records instead",
        "The mark is dissent about the lens, not about the candidate, and it"
        " never changes what the round reduces to",
        "The last round is judged exactly as any other, and the brief says so",
        "A lens neither aligns because no repair room remains nor blocks to force"
        " an escalation; the grace round and the deferral rule handle a real"
        " finding raised at the bound",
    ),
    "skills/execute-work/SKILL.md": (
        "state the bound in effect for the delivery",
        "The mandated lenses are the two the delivery product ships, correctness"
        " and testing, under the names the adopting repository declares for them",
        "Every other lens is the executor's own selection, chosen by the depth of"
        " the delivery, and the declaration names the mandated pair, the full"
        " selected set, and the reason for the selection",
        "A repository cannot widen what counts as mandated",
        "Never declare a second bound for the same delivery",
        "The review is bound to the release installed when the delivery's first"
        " round is acquired: record a release installed mid-review, keep every"
        " round already carried valid, and name the bound release's `releaseId`"
        " and `archiveSha256` in the delivery's closing record",
        "The lens set is declared before the first round and held unchanged for the"
        " delivery",
        "no lens is added after the first round",
        "dropping a lens disposes of nothing it filed",
        "record a disposition for every finding, each citing the lens",
        "The executor closes, defers, and declines nothing on its own judgement",
        "Reaching the bound with dissent open, or with a failed acquisition and"
        " no round left, is terminal. With dissent open, record the typed blocker"
        " `review.loop-bound-reached` naming the open findings, report `partial`,"
        " and stop",
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
        "against it, and record a disposition for every finding, each citing the"
        " lens; - name the release the review is bound to by its `releaseId` and"
        " `archiveSha256`, and any release installed mid-review; this list is the"
        " delivery's closing record for that identity;",
        "Report the result category, execution posture, completed scope and"
        " finish-line items, sensor results, retained evidence, review provenance"
        " and outcome, the bound review release's `releaseId` and `archiveSha256`,"
        " any release installed mid-review, compounding outcome",
    ),
    "skills/plan-work/SKILL.md": (
        "Plan review carries no round bound",
        "Planning is the most important point in delivery, and a plan is cheap to"
        " revise and expensive to get wrong",
        "This loop is the review of the plan, and it is not the delivery review"
        " that `$review-work` bounds",
        "A plan review that is not converging ends by decision rather than by"
        " count",
        "Each plan-review lens carries this mandate in its own round brief",
        "the plan review loop is left boundless: it runs until its lenses align,"
        " and no round count ends it",
        "This is a recorded decision rather than an omission, so a later plan"
        " review does not re-derive the question",
        "every lens reviewing a plan optimizes for the lowest number of rounds",
        "every finding a lens can raise against the plan as it stands is raised in"
        " the current round, not held for a later one",
        "The operator ends it on that judgement, and one sufficient signal is a"
        " round after the first that raises a finding not raised before while"
        " leaving the number of open findings no lower than the round before it",
        # The ruling reads off the signal it qualifies: "that signal" names the
        # sentence before it, so the pin carries that sentence's tail with it.
        "no lower than the round before it. Where that signal reaches the"
        " operator is the caller's responsibility and outside this section's"
        " scope",
        "a plan-review round is realized by subagents, and this workflow neither"
        " carries a channel to the operator nor assumes one",
        "The caller running the loop is what puts each round in front of the"
        " operator who may end it, and where the caller surfaces nothing the loop"
        " still runs to alignment with no one positioned to end it early",
        "The mandate answers what two plans landed on the same day cost. One"
        " reached alignment only at review round fourteen, with one lens filing a"
        " new finding in each of rounds one through thirteen and closing it in the"
        " round after. The other ran forty-one six-lens rounds. Neither loop was"
        " long because its plan was unusually hard; both were long because findings"
        " arrived one round at a time",
    ),
    "skills/compound-delivery-kernel/SKILL.md": (
        "Declare the round bound before the first round; every round of the delivery"
        " counts, except one grace round obtained at the bound and a round"
        " reopened because the base moved under an unchanged deliverable"
        " identity; the default is four rounds",
        "Later rounds verify closure and review the delta",
        "only P0 and P1 findings block",
        "the lens, not the executor, closes or defers a finding",
        "Name the tracked follow-up item recorded for every deferral any lens"
        " filed",
        "One tracked item may carry several deferrals, one item per shippable"
        " outcome",
        "Where no tracker is configured, those follow-up items are the actionable"
        " tracking handoff in the result rather than a blocker",
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
