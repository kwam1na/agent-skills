from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()
