from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from agent_skills.workflows import (
    CompoundingRequest,
    ReviewLensResult,
    ReviewRequest,
    compound_learning,
    review_work,
)


ROOT = Path(__file__).resolve().parents[1]


def _fixture(name: str) -> dict[str, object]:
    return json.loads(
        (ROOT / "tests" / "scenarios" / "core" / name).read_text(encoding="utf-8")
    )


def _review_result(scenario: dict[str, object]):
    rounds = tuple(
        tuple(
            ReviewLensResult(
                lens=item["lens"],
                outcome=item["outcome"],
                findings=tuple(item["findings"]),
                evidence=tuple(item["evidence"]),
                failure=item.get("failure"),
                late_findings=tuple(item.get("lateFindings", ())),
            )
            for item in round_results
        )
        for round_results in scenario["rounds"]
    )
    return review_work(
        ReviewRequest(
            required_lenses=tuple(scenario["requiredLenses"]),
            rounds=rounds,
            max_rounds=scenario["maxRounds"],
        )
    )


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _fixture("review.json")

    def test_unanimous_alignment_is_explicit_and_preserves_lens_evidence(self) -> None:
        scenario = self.fixture["unanimousAlignment"]

        result = _review_result(scenario)

        self.assertEqual(result.status, scenario["expectedStatus"])
        self.assertEqual(
            tuple(item.lens for item in result.rounds[-1]),
            tuple(scenario["expectedLenses"]),
        )
        self.assertTrue(all(item.evidence for item in result.rounds[-1]))

    def test_unresolved_disagreement_remains_visible_for_another_round(self) -> None:
        scenario = self.fixture["unresolvedDisagreement"]

        result = _review_result(scenario)

        self.assertEqual(result.status, scenario["expectedStatus"])
        self.assertEqual(result.dissent[0].findings, tuple(scenario["expectedDissent"]))
        self.assertEqual(result.action, "Resolve the visible findings, then review again.")

    def test_resolved_dissent_converges_without_erasing_review_history(self) -> None:
        scenario = self.fixture["resolvedDissent"]

        result = _review_result(scenario)

        self.assertEqual(result.status, scenario["expectedStatus"])
        self.assertEqual(len(result.dissent), scenario["expectedDissentCount"])
        self.assertEqual(len(result.rounds), 2)

    def test_no_actionable_findings_align_without_inventing_work(self) -> None:
        scenario = self.fixture["noActionableFindings"]

        result = _review_result(scenario)

        self.assertEqual(result.status, scenario["expectedStatus"])
        self.assertEqual(len(result.dissent), scenario["expectedDissentCount"])
        self.assertEqual(result.blockers, ())

    def test_unresolved_findings_at_loop_bound_block_honestly(self) -> None:
        scenario = self.fixture["loopBound"]

        result = _review_result(scenario)

        self.assertEqual(result.status, scenario["expectedStatus"])
        self.assertIn(scenario["expectedBlocker"], result.blockers)
        self.assertTrue(result.dissent)

    def test_reviewer_failure_is_a_typed_blocked_outcome(self) -> None:
        scenario = self.fixture["reviewerFailure"]

        result = _review_result(scenario)

        self.assertEqual(result.status, scenario["expectedStatus"])
        self.assertEqual(result.failures[0].lens, scenario["expectedFailure"]["lens"])
        self.assertEqual(result.failures[0].reason, scenario["expectedFailure"]["reason"])
        self.assertEqual(result.blockers, ("reviewer failed: testing in round 1",))

    def test_late_findings_stay_visible_without_changing_convergence(self) -> None:
        scenario = self.fixture["lateFinding"]
        unmarked = json.loads(json.dumps(scenario))
        for round_results in unmarked["rounds"]:
            for item in round_results:
                item.pop("lateFindings", None)

        result = _review_result(scenario)
        without_marks = _review_result(unmarked)

        self.assertEqual(result.status, scenario["expectedStatus"])
        self.assertEqual(result.status, without_marks.status)
        self.assertEqual(
            [item.findings for item in result.dissent],
            [item.findings for item in without_marks.dissent],
        )
        self.assertEqual(without_marks.late_findings, ())
        self.assertEqual(
            [
                {"finding": item.finding, "lens": item.lens, "round": item.round_number}
                for item in result.late_findings
            ],
            scenario["expectedLateFindings"],
        )

    def test_a_late_p1_a_lens_defers_leaves_its_round_aligned_and_stays_visible(self) -> None:
        """obtain-review's reducer: a verification round's late P1 the lens defers is kept out of `findings` and marked
        late in its deferral. It never reaches the convergence decision, so the lens's aligned round converges with the
        finding still shown. Filed in `findings` instead, it blocks an aligned round."""
        late = "P1: the retry path swallows the error (deferred, follow-up filed)"
        first = (ReviewLensResult("correctness", "aligned", evidence=("retry.py:1-90",)),)
        deferred = ReviewLensResult("correctness", "aligned", evidence=("retry.py:88",), late_findings=(late,))
        result = review_work(ReviewRequest(required_lenses=("correctness",), rounds=(first, (deferred,)), max_rounds=4))
        self.assertEqual(result.status, "aligned")
        self.assertEqual([(item.finding, item.lens, item.round_number) for item in result.late_findings],
                         [(late, "correctness", 2)])
        filed = ReviewLensResult("correctness", "aligned", findings=(late,), evidence=("retry.py:88",),
                                 late_findings=(late,))
        result = review_work(ReviewRequest(required_lenses=("correctness",), rounds=(first, (filed,)), max_rounds=4))
        self.assertEqual((result.status, result.blockers), ("blocked", ("aligned review has findings: correctness in round 2",)))

    def test_missing_evidence_blocks_alignment(self) -> None:
        result = review_work(
            ReviewRequest(
                required_lenses=("correctness",),
                rounds=((ReviewLensResult("correctness", "aligned"),),),
                max_rounds=1,
            )
        )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(
            result.blockers,
            ("review evidence missing: correctness in round 1",),
        )


class CompoundingWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _fixture("compound.json")

    def test_reusable_learning_requires_caller_owned_compounding(self) -> None:
        scenario = self.fixture["required"]

        decision = compound_learning(
            CompoundingRequest(
                learning=scenario["learning"],
                reusable_reasons=tuple(scenario["reusableReasons"]),
                evidence=tuple(scenario["evidence"]),
            )
        )

        self.assertEqual(decision.status, scenario["expectedStatus"])
        self.assertEqual(decision.action, scenario["expectedAction"])
        self.assertEqual(decision.learning, scenario["learning"])

    def test_local_obvious_change_does_not_require_durable_knowledge(self) -> None:
        scenario = self.fixture["notRequired"]

        decision = compound_learning(
            CompoundingRequest(no_learning_reason=scenario["noLearningReason"])
        )

        self.assertEqual(decision.status, scenario["expectedStatus"])
        self.assertEqual(decision.action, scenario["expectedAction"])
        self.assertEqual(decision.no_learning_reason, scenario["noLearningReason"])

    def test_reusable_learning_without_evidence_is_blocked(self) -> None:
        decision = compound_learning(
            CompoundingRequest(
                learning="Validate the boundary.",
                reusable_reasons=("The failure can recur.",),
            )
        )

        self.assertEqual(decision.status, "blocked")
        self.assertEqual(decision.blockers, ("compounding evidence is required",))



ROOT = Path(__file__).resolve().parents[1]
# Every place a skill states which findings defer says it as run-delivery's reducers and the round briefs enforce it:
# a P0 never defers, P2 and P3 do, and a P1 only when a story review's verification round files it new outside the
# delta, listed late and with its follow-up.
LATE_P1_DEFERRAL = {
    "skills/orchestrate-work/references/orchestration-guide.md": (
        "A P0 is never deferred, and a P2 or P3 can be. A P1 can be deferred only when a verification round files it"
        " new against lines outside the delta: its id is listed in `lateFindings` and it carries `deferredIssue`."
        " Closing a carried finding as `deferred` stays a P2 or P3 move.",
    ),
    "skills/run-delivery/templates/lane-brief.md": (
        "Triage: blocking findings, every P0 and every P1 a lens files open, are always fixed in the lane. Lenses file"
        " P2/P3 as deferred, with what the follow-up must say, and a P1 too when a verification round files it new"
        " outside the delta, listing its id in `lateFindings`; a P0 is never deferred.",
    ),
    "skills/review-work/SKILL.md": (
        "P0 and P1 findings are blocking. A P0 is never deferred, and neither is a P1, except one a delivery review's"
        " verification round files first outside the delta: it is recorded as a deferral with its tracked follow-up"
        " item, as obtain-review's out-of-delta rule says. A plan review defers no P1.",
    ),
}
# The old rule, its phrasings and their synonyms: only P2 and P3 defer, or a P1 never does.
OLD_DEFERRAL_RULE = (
    r"only (a |the )?P2 ?(or|and|/) ?P3( findings?)? (can|may|is|are)",
    r"P2/P3 only",
    r"P0 (and|or|/) ?P1 (findings )?are (blocking and are )?never deferred",
    r"P1s? (findings? )?(is|are) never deferred",
    r"\(P0/P1\) findings are always fixed",
    r"(blocking|P0 and P1) findings are always fixed in the lane\. Lenses file P2/P3 as deferred, with what the"
    r" follow-up must say:",
)


def normalized(relative: str) -> str:
    return " ".join((ROOT / relative).read_text(encoding="utf-8").split())


class LateP1DeferralWordingTests(unittest.TestCase):
    """Each skill that says which findings defer states the late-P1 deferral, and none keeps the old rule."""

    def test_each_file_states_the_late_p1_deferral(self) -> None:
        for relative, phrases in LATE_P1_DEFERRAL.items():
            text = normalized(relative)
            for phrase in phrases:
                with self.subTest(path=relative, phrase=phrase[:60]):
                    self.assertIn(" ".join(phrase.split()), text)

    def test_no_file_keeps_the_old_rule_or_a_synonym_of_it(self) -> None:
        for relative in LATE_P1_DEFERRAL:
            text = normalized(relative)
            for pattern in OLD_DEFERRAL_RULE:
                with self.subTest(path=relative, pattern=pattern):
                    self.assertIsNone(re.search(pattern, text, re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
