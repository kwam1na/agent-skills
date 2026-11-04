from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import unittest

from agent_skills.diagnosis import (
    Assumption, CausalLink, DiagnosisObservations, Hypothesis, Prediction,
    diagnose_work,
)
from agent_skills.workflow_graph import (
    WorkflowGraphError, graph_sha256, validate_edge, validate_stage_result,
)
from agent_skills.workflows import ExecutionRequest, PlanningRequest, execute_work, plan_work

ROOT = Path(__file__).resolve().parents[1]


class DiagnosisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads((ROOT / "tests/scenarios/core/diagnose.json").read_text())
        self.binding = {
            "release": {"releaseId": "test-release", "profile": "core",
                        "archiveSha256": "a" * 64, "metadataSha256": "b" * 64},
            "graphSha256": graph_sha256(),
            "subjectRef": {"schemaVersion": "workflow-subject-ref/1", "opaque": "subject"},
        }
        self.context = {
            **copy.deepcopy(self.binding), "diagnosisFrom": "intake",
            "completedStages": {"intake": {"status": "succeeded", "outputKind": "scoped-subject",
                                            "evidenceRef": "retained:intake"}},
        }
        self.observations = DiagnosisObservations(
            trigger=self.fixture["trigger"], symptom=self.fixture["symptom"],
            links=tuple(CausalLink(link["cause"], link["effect"], tuple(link["evidenceRefs"]))
                        for link in self.fixture["links"]),
            reproduction_evidence_refs=tuple(self.fixture["reproductionEvidenceRefs"]),
            limitations=(self.fixture["limitation"],),
            recommendations=(self.fixture["recommendation"],),
        )

    def run_diagnosis(self, observations=None):
        return diagnose_work(observations or self.observations, self.binding, self.context,
                             evidence_ref="retained:diagnosis")

    def prediction(self, **changes):
        raw = self.fixture["prediction"]
        return replace(Prediction(raw["statement"], raw["observedResult"], raw["outcome"],
                                  tuple(raw["evidenceRefs"])), **changes)

    def with_prediction(self, prediction):
        return replace(self.observations, links=(
            replace(self.observations.links[0], uncertain=True, prediction=prediction),
            self.observations.links[1],
        ))

    def assert_non_success(self, result, status):
        self.assertEqual(result.stage_result["status"], status)
        self.assertNotIn("output", result.stage_result)
        self.assertIsInstance(result.stage_result["nextStep"], str)
        self.assertTrue(result.stage_result["nextStep"].strip())
        validate_stage_result(result.stage_result, self.context)
        with self.assertRaises(WorkflowGraphError):
            validate_edge(result.stage_result, "plan", self.context)

    def test_complete_evidence_backed_chain_confirms(self):
        result = self.run_diagnosis()
        self.assertEqual(result.stage_result["status"], "succeeded")
        self.assertEqual(result.stage_result["output"],
                         {"kind": "confirmed", "evidenceRef": "retained:diagnosis"})
        self.assertNotIn("candidateRef", result.stage_result)
        self.assertNotIn("nextStep", result.stage_result)
        self.assertEqual(result.evidence.links, self.observations.links)
        self.assertTrue(set(self.fixture["reproductionEvidenceRefs"]) <=
                        set(result.stage_result["evidenceRefs"]))
        validate_edge(result.stage_result, "plan", self.context)

    def test_uncertain_link_needs_observed_satisfied_prediction(self):
        self.assert_non_success(self.run_diagnosis(self.with_prediction(None)), "indeterminate")
        prediction = self.prediction()
        result = self.run_diagnosis(self.with_prediction(prediction))
        self.assertEqual(result.stage_result["status"], "succeeded")
        self.assertEqual(result.evidence.links[0].prediction, prediction)
        for changes in ({"observed_result": None}, {"evidence_refs": ()},
                        {"outcome": "unobserved"}):
            with self.subTest(changes=changes):
                self.assert_non_success(self.run_diagnosis(self.with_prediction(
                    self.prediction(**changes))), "indeterminate")

    def test_contradiction_cannot_confirm_even_if_link_is_marked_certain(self):
        observations = self.with_prediction(self.prediction(outcome="contradicted"))
        for uncertain in (True, False):
            with self.subTest(uncertain=uncertain):
                changed = replace(observations, links=(replace(observations.links[0], uncertain=uncertain),
                                                        observations.links[1]))
                result = self.run_diagnosis(changed)
                self.assert_non_success(result, "indeterminate")
                self.assertEqual(result.evidence.links[0].prediction.outcome, "contradicted")

    def test_missing_chain_evidence_or_continuity_is_inconclusive(self):
        changes = [(), (self.observations.links[0],),
                   (replace(self.observations.links[0], evidence_refs=()), self.observations.links[1]),
                   (self.observations.links[0], replace(self.observations.links[1], cause="other cause")),
                   (replace(self.observations.links[0], cause="other trigger"), self.observations.links[1])]
        for links in changes:
            with self.subTest(links=links):
                self.assert_non_success(self.run_diagnosis(replace(self.observations, links=links)),
                                        "indeterminate")

    def test_unavailable_reproduction_and_missing_observation_are_inconclusive(self):
        for change in ({"reproduction": "unavailable"}, {"reproduction": "inconclusive"},
                       {"reproduction_evidence_refs": ()}):
            with self.subTest(change=change):
                self.assert_non_success(self.run_diagnosis(replace(self.observations, **change)),
                                        "indeterminate")

    def test_missing_required_condition_blocks_with_action(self):
        result = self.run_diagnosis(replace(self.observations, missing_conditions=("read access to trace",)))
        self.assert_non_success(result, "blocked")
        self.assertIn("read access to trace", result.stage_result["nextStep"])

    def test_diagnostic_failure_does_not_become_causal_confirmation(self):
        result = self.run_diagnosis(replace(self.observations, failure="evidence decoder failed"))
        self.assert_non_success(result, "failed")
        self.assertIn("evidence decoder failed", result.stage_result["nextStep"])

    def test_ruled_out_hypotheses_assumptions_and_limits_are_retained(self):
        raw = self.fixture["ruledOut"]
        hypothesis = Hypothesis(raw["statement"], "ruled-out", tuple(raw["evidenceRefs"]), raw["reason"])
        assumption = Assumption(self.fixture["assumption"])
        observations = replace(self.observations, hypotheses=(hypothesis,), assumptions=(assumption,))
        result = self.run_diagnosis(observations)
        self.assertEqual(result.evidence.hypotheses, (hypothesis,))
        self.assertEqual(result.evidence.assumptions, (assumption,))
        self.assertIn(raw["evidenceRefs"][0], result.stage_result["evidenceRefs"])
        self.assertIn(self.fixture["limitation"], result.stage_result["limitations"])
        self.assertTrue(any(assumption.statement in value for value in result.stage_result["limitations"]))
        self.assertEqual(result.evidence.recommendations, observations.recommendations)
        with self.assertRaises(ValueError):
            self.run_diagnosis(replace(observations, hypotheses=(replace(hypothesis, evidence_refs=()),)))

    def test_subflows_return_to_same_context_without_mutating_or_aliasing(self):
        for caller in ("plan", "implement", "review.acquire", "review.reduce", "feedback.handoff"):
            with self.subTest(caller=caller):
                self.context["diagnosisFrom"] = caller
                self.context["invokingStageRef"] = "retained:invocation"
                self.context["candidateRef"] = {"schemaVersion": "workflow-candidate-ref/1", "opaque": "candidate-A"}
                self.binding["candidateRef"] = copy.deepcopy(self.context["candidateRef"])
                original = copy.deepcopy((self.observations, self.binding, self.context))
                result = self.run_diagnosis()
                validate_edge(result.stage_result, caller, self.context)
                wrong = "implement" if caller != "implement" else "plan"
                with self.assertRaises(WorkflowGraphError):
                    validate_edge(result.stage_result, wrong, self.context)
                self.assertEqual((self.observations, self.binding, self.context), original)
                result.stage_result["release"]["releaseId"] = "changed"
                result.stage_result["candidateRef"]["opaque"] = "changed"
                self.assertEqual((self.observations, self.binding, self.context), original)
                with self.assertRaises(FrozenInstanceError):
                    result.evidence.trigger = "changed"

    def test_binding_rejects_stale_context_and_injected_authority(self):
        for field, value in (("release", {**self.binding["release"], "archiveSha256": "c" * 64}),
                             ("graphSha256", "c" * 64),
                             ("subjectRef", {"schemaVersion": "workflow-subject-ref/1", "opaque": "other"}),
                             ("candidateRef", {"schemaVersion": "workflow-candidate-ref/1", "opaque": "speculative"}),
                             ("fixAuthorized", True), ("checkpoint", "new-checkpoint")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                diagnose_work(self.observations, {**self.binding, field: value}, self.context,
                              evidence_ref="retained:diagnosis")
        self.context["candidateRef"] = {"schemaVersion": "workflow-candidate-ref/1", "opaque": "current"}
        with self.assertRaises(WorkflowGraphError):
            self.run_diagnosis()
        self.binding["candidateRef"] = {"schemaVersion": "workflow-candidate-ref/1", "opaque": "stale"}
        with self.assertRaises(WorkflowGraphError):
            self.run_diagnosis()

    def test_confirmation_does_not_satisfy_implementation_approval(self):
        diagnosis = self.run_diagnosis()
        plan = plan_work(PlanningRequest(
            outcome="repair diagnosed cause", scope=("parser",), finish_line=("sensor passes",),
            test_scenarios=("empty input remains bounded",), generic_sensors=("sensor",),
            approval_handoffs=("implementation authorization",),
        ))
        request = ExecutionRequest(plan=plan, completed_work=("parser",),
                                   completed_finish_line=("sensor passes",), sensor_results={"sensor": True},
                                   evidence=(diagnosis.stage_result["output"]["evidenceRef"],))
        blocked = execute_work(request)
        self.assertEqual(blocked.status, "blocked")
        authorized = execute_work(replace(request, approved_handoffs=("implementation authorization",)))
        self.assertEqual(authorized.status, "complete")
        self.assertNotIn("fixAuthorized", diagnosis.stage_result)

    def test_each_release_field_reference_version_and_prerequisite_remains_exact(self):
        for field in ("releaseId", "profile", "archiveSha256", "metadataSha256"):
            altered = copy.deepcopy(self.binding)
            altered["release"][field] = "c" * 64 if field.endswith("Sha256") else "other"
            with self.subTest(field=field), self.assertRaises(WorkflowGraphError):
                diagnose_work(self.observations, altered, self.context, evidence_ref="retained:diagnosis")
        changed = copy.deepcopy(self.binding)
        changed["subjectRef"]["schemaVersion"] = "workflow-subject-ref/2"
        with self.assertRaises(WorkflowGraphError):
            diagnose_work(self.observations, changed, self.context, evidence_ref="retained:diagnosis")
        for changed_context in ({**self.context, "completedStages": {}},
                                {**self.context, "diagnosisFrom": "implement"}):
            with self.subTest(context=changed_context), self.assertRaises(WorkflowGraphError):
                diagnose_work(self.observations, self.binding, changed_context, evidence_ref="retained:diagnosis")

    def test_normalization_preserves_opaque_evidence_identity_and_does_not_alias_lists(self):
        observations = replace(self.observations, trigger="  " + self.observations.trigger + "  ",
                               evidence_refs=(" opaque ref ", " opaque ref "),
                               limitations=(" bounded context ", "bounded context"))
        result = self.run_diagnosis(observations)
        self.assertEqual(result.stage_result["status"], "succeeded")
        self.assertEqual(result.evidence.trigger, self.observations.trigger)
        self.assertEqual(result.evidence.evidence_refs, (" opaque ref ",))
        self.assertEqual(result.evidence.limitations, ("bounded context",))
        result.stage_result["evidenceRefs"].append("new evidence")
        result.stage_result["limitations"].append("new limit")
        self.assertEqual(result.evidence.evidence_refs, (" opaque ref ",))
        self.assertEqual(result.evidence.limitations, ("bounded context",))

    def test_non_success_retains_ruled_out_hypotheses_and_recommendations(self):
        raw = self.fixture["ruledOut"]
        hypothesis = Hypothesis(raw["statement"], "ruled-out", tuple(raw["evidenceRefs"]), raw["reason"])
        observations = replace(self.observations, hypotheses=(hypothesis,), reproduction="unavailable")
        result = self.run_diagnosis(observations)
        self.assert_non_success(result, "indeterminate")
        self.assertEqual(result.evidence.hypotheses, (hypothesis,))
        self.assertEqual(result.evidence.recommendations, observations.recommendations)
        self.assertIn(raw["evidenceRefs"][0], result.stage_result["evidenceRefs"])

    def test_malformed_observations_reject_instead_of_becoming_findings(self):
        for changed in (replace(self.observations, trigger=" "),
                        replace(self.observations, reproduction="success"),
                        self.with_prediction(self.prediction(outcome="probably")),
                        replace(self.observations, evidence_refs=("",))):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.run_diagnosis(changed)


if __name__ == "__main__":
    unittest.main()
