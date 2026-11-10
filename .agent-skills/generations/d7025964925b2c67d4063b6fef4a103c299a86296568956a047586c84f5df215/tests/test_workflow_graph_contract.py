"""Public schema and production conformance, with independently bound context."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_skills.workflow_graph import (
    WorkflowGraphError, apply_overlay, load_graph, validate_edge,
    validate_graph, validate_stage_result,
)

CONSUMER = ROOT / "tests/consumers/workflow_consumer.py"
spec = importlib.util.spec_from_file_location("workflow_consumer", CONSUMER)
consumer_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(consumer_module)
FakeConsumer = consumer_module.FakeConsumer


def ref(kind, value):
    return {"schemaVersion": f"workflow-{kind}-ref/1", "opaque": value}


def context():
    return {
        "release": {"releaseId": "release-test", "profile": "core",
                    "archiveSha256": "a" * 64, "metadataSha256": "b" * 64},
        "graphSha256": hashlib.sha256((ROOT / "workflows/delivery-v1.json").read_bytes()).hexdigest(),
        "subjectRef": ref("subject", "subject-one"),
        "completedStages": {},
    }


def completed(kind):
    return {"status": "succeeded", "outputKind": kind, "evidenceRef": "retained-evidence"}


def result(stage, ctx, status="succeeded", kind=None):
    declaration = next(s for s in load_graph()["stages"] if s["id"] == stage)
    value = {"schemaVersion": "workflow-stage-result/1", "stageId": stage,
             **{k: copy.deepcopy(ctx[k]) for k in ("release", "graphSha256", "subjectRef")},
             "status": status, "evidenceRefs": ["observation-one"], "limitations": []}
    candidate = ctx.get("candidateRef")
    if declaration["candidateBinding"] == "forbidden":
        candidate = None
    if declaration["candidateBinding"] == "produced-on-success" and status == "succeeded":
        candidate = ctx.get("producedCandidateRef")
    if candidate is not None:
        value["candidateRef"] = copy.deepcopy(candidate)
    if status == "succeeded":
        value["output"] = {"kind": kind or declaration["successOutputs"][0], "evidenceRef": "output-evidence"}
        if stage in {"publish.handoff", "feedback.handoff"}:
            value["output"]["adapterRef"] = declaration["evidenceAdapter"]["ref"]
        if stage == "feedback.handoff":
            value["output"]["trust"] = "untrusted"
        if stage == "compound" and value["output"]["kind"] == "learning-required":
            value["output"]["preservationHandoffRef"] = "repository-owned-preservation"
    else:
        value["nextStep"] = "Supply the missing evidence."
    return value


class WorkflowGraphContractTests(unittest.TestCase):
    def setUp(self):
        self.graph = load_graph()
        self.ctx = context()

    def both(self, value, ctx=None, accepted=True, overlay=None):
        ctx = ctx or self.ctx
        if accepted:
            validate_stage_result(value, ctx, graph=self.graph, overlay=overlay)
            FakeConsumer(ROOT, ctx, overlay=overlay).admit(value)
        else:
            with self.assertRaises(WorkflowGraphError):
                validate_stage_result(value, ctx, graph=self.graph, overlay=overlay)
            with self.assertRaises(ValueError):
                FakeConsumer(ROOT, ctx, overlay=overlay).admit(value)

    def test_schema_engine_is_exactly_pinned_and_consumer_does_not_import_runtime(self):
        from importlib.metadata import version
        self.assertEqual(version("jsonschema"), "4.23.0")
        self.assertNotIn("agent_skills", CONSUMER.read_text())

    def test_complete_canonical_graph_and_strict_matrix(self):
        validate_graph(self.graph)
        FakeConsumer(ROOT, self.ctx)
        self.assertEqual([s["id"] for s in self.graph["stages"]], [
            "intake", "diagnose", "plan", "implement", "review.acquire", "review.reduce",
            "publish.handoff", "feedback.handoff", "finish.verify", "compound"])
        for stage in self.graph["stages"]:
            for field in stage:
                bad = copy.deepcopy(self.graph)
                del next(s for s in bad["stages"] if s["id"] == stage["id"])[field]
                with self.assertRaises(WorkflowGraphError):
                    validate_graph(bad)
        bad = copy.deepcopy(self.graph)
        bad["stages"][2]["prerequisites"] = []
        with self.assertRaises(WorkflowGraphError):
            validate_graph(bad)
        bad = copy.deepcopy(self.graph)
        bad["stages"][1]["prerequisites"][0]["allowOmitted"] = 0
        with self.assertRaises(WorkflowGraphError):
            validate_graph(bad)

    def test_intake_diagnosis_plan_returns_to_same_checkpoint(self):
        fake = FakeConsumer(ROOT, self.ctx, checkpoint="checkpoint-one")
        intake = result("intake", self.ctx)
        self.both(intake)
        fake.admit(intake)
        self.ctx["completedStages"]["intake"] = completed("scoped-subject")
        self.ctx["diagnosisFrom"] = "intake"
        fake.set_context(self.ctx)
        diagnosis = result("diagnose", self.ctx)
        self.both(diagnosis)
        fake.admit(diagnosis)
        self.ctx["completedStages"]["diagnose"] = completed("confirmed")
        self.ctx["diagnosisInvoked"] = True
        self.both(result("plan", self.ctx))
        self.assertEqual(fake.checkpoint, "checkpoint-one")
        self.assertNotIn("candidateRef", fake.context)
        validate_edge(diagnosis, "plan", self.ctx)

    def test_unknown_fields_versions_and_exact_binding_reject(self):
        valid = result("intake", self.ctx)
        mutations = [("schemaVersion", "workflow-stage-result/2"), ("stageId", "execute-work"),
                     ("graphSha256", "c" * 64), ("subjectRef", ref("subject", "wrong")),
                     ("approval", True), ("checkpoint", "next"), ("attempt", "one"),
                     ("retry", True), ("permissionGrant", "write"), ("hostTranscript", "secret")]
        for key, value in mutations:
            with self.subTest(key=key):
                bad = copy.deepcopy(valid)
                bad[key] = value
                self.both(bad, accepted=False)
        for field in self.ctx["release"]:
            bad = copy.deepcopy(valid)
            bad["release"][field] = "wrong"
            self.both(bad, accepted=False)
        for key in ("subjectRef",):
            for value in ({"schemaVersion": "workflow-subject-ref/2", "opaque": "x"},
                          ref("subject", " "), {**ref("subject", "x"), "grant": True}):
                bad = copy.deepcopy(valid)
                bad[key] = value
                self.both(bad, accepted=False)

    def test_required_prerequisite_and_conditional_diagnosis(self):
        self.both(result("plan", self.ctx), accepted=False)
        self.ctx["completedStages"]["intake"] = completed("scoped-subject")
        self.both(result("plan", self.ctx))
        self.ctx["diagnosisInvoked"] = True
        self.both(result("plan", self.ctx), accepted=False)
        self.ctx["completedStages"]["diagnose"] = completed("confirmed")
        self.both(result("plan", self.ctx))
        self.ctx["completedStages"]["diagnose"]["status"] = "indeterminate"
        self.both(result("plan", self.ctx), accepted=False)

    def test_digest_shapes_reject_terminal_newline_even_when_context_matches(self):
        from jsonschema import Draft202012Validator
        schema = json.loads((ROOT / "schemas/workflow-stage-result.schema.json").read_text())
        for field in ("archiveSha256", "metadataSha256", "graphSha256"):
            ctx = copy.deepcopy(self.ctx)
            value = result("intake", ctx)
            result_parent = value if field == "graphSha256" else value["release"]
            context_parent = ctx if field == "graphSha256" else ctx["release"]
            result_parent[field] += "\n"
            context_parent[field] += "\n"
            # Matching malformed bindings prevent equality checks from hiding
            # release-digest shape drift. Direct schema checks also expose graph
            # shape drift before the independent artifact-digest check runs.
            with self.subTest(field=field, boundary="both-peers"):
                self.both(value, ctx, accepted=False)
            with self.subTest(field=field, boundary="public-result-schema"):
                self.assertFalse(Draft202012Validator(schema).is_valid(value))
            with self.subTest(field=field, boundary="trusted-context-shape"):
                context_key = "graphSha256" if field == "graphSha256" else "release"
                self.assertFalse(Draft202012Validator(schema["properties"][context_key]).is_valid(ctx[context_key]))

    def test_contextual_diagnosis_and_plan_reject_candidate_b_after_checkpoint_a(self):
        self.ctx["completedStages"]["intake"] = completed("scoped-subject")
        self.ctx["diagnosisFrom"] = "intake"
        self.ctx["candidateRef"] = ref("candidate", "candidate-A")
        for stage in ("diagnose", "plan"):
            with self.subTest(stage=stage):
                value = result(stage, self.ctx)
                value["candidateRef"] = ref("candidate", "candidate-B")
                self.both(value, accepted=False)

    def test_contextual_and_produced_candidate_binding(self):
        self.ctx["completedStages"] = {"intake": completed("scoped-subject"), "plan": completed("bounded-plan")}
        self.ctx["diagnosisFrom"] = "intake"
        self.both(result("diagnose", self.ctx))
        bad = result("diagnose", self.ctx)
        bad["candidateRef"] = ref("candidate", "speculative")
        self.both(bad, accepted=False)
        self.ctx["candidateRef"] = ref("candidate", "candidate-A")
        self.both(result("diagnose", self.ctx))
        bad = result("diagnose", self.ctx)
        del bad["candidateRef"]
        self.both(bad, accepted=False)
        for prior in (None, ref("candidate", "candidate-A")):
            ctx = copy.deepcopy(self.ctx)
            ctx.pop("candidateRef", None)
            if prior:
                ctx["candidateRef"] = prior
            ctx["producedCandidateRef"] = ref("candidate", "candidate-B")
            self.both(result("implement", ctx), ctx)
            self.both(result("implement", ctx, "failed"), ctx)
            failed = result("implement", ctx, "failed")
            failed["candidateRef"] = ref("candidate", "candidate-B")
            self.both(failed, ctx, accepted=False)
            bad = result("implement", ctx)
            bad["candidateRef"] = ref("candidate", "candidate-A")
            self.both(bad, ctx, accepted=False)
            bad.pop("candidateRef")
            self.both(bad, ctx, accepted=False)
            ctx.pop("producedCandidateRef")
            self.both(result("implement", ctx), ctx, accepted=False)

    def test_omission_effective_requiredness_and_closed_overlay(self):
        omitted = {"stageId": "publish.handoff", "disposition": "omitted", "policyRef": "policy-one", "reason": "No publication requested."}
        apply_overlay(self.graph, [omitted])
        for stage in self.graph["stages"]:
            candidate = {**omitted, "stageId": stage["id"]}
            if stage["requiredness"] == "required":
                with self.assertRaises(WorkflowGraphError):
                    apply_overlay(self.graph, [candidate])
            else:
                apply_overlay(self.graph, [candidate])
                with self.assertRaises(WorkflowGraphError):
                    apply_overlay(self.graph, [{**candidate, "requiredness": "required"}])
        for patch_value in ({"reason": ""}, {"policyRef": ""}, {"prerequisites": []}, {"disposition": "succeeded"}):
            with self.assertRaises(WorkflowGraphError):
                apply_overlay(self.graph, [{**omitted, **patch_value}])
        self.ctx["candidateRef"] = ref("candidate", "A")
        self.ctx["completedStages"]["review.reduce"] = completed("review-round-aligned")
        self.both(result("publish.handoff", self.ctx), accepted=False, overlay=[omitted])
        diagnostic_required = [{"stageId": "diagnose", "requiredness": "required"}]
        self.ctx["completedStages"]["intake"] = completed("scoped-subject")
        self.both(result("plan", self.ctx), accepted=False, overlay=diagnostic_required)
        self.ctx["completedStages"]["diagnose"] = completed("confirmed")
        self.both(result("plan", self.ctx), overlay=diagnostic_required)

    def test_all_stages_statuses_and_typed_outputs(self):
        self.ctx["candidateRef"] = ref("candidate", "A")
        self.ctx["producedCandidateRef"] = ref("candidate", "B")
        self.ctx["diagnosisFrom"] = "intake"
        self.ctx["completedStages"] = {s["id"]: completed(s["successOutputs"][0]) for s in self.graph["stages"]}
        for stage in self.graph["stages"]:
            for status in ("succeeded", "blocked", "failed", "indeterminate"):
                value = result(stage["id"], self.ctx, status)
                self.both(value, accepted=status in stage["statuses"])
                if status != "succeeded":
                    value["output"] = {"kind": stage["successOutputs"][0], "evidenceRef": "false-success"}
                    self.both(value, accepted=False)
            for kind in stage["successOutputs"]:
                self.both(result(stage["id"], self.ctx, kind=kind))
            value = result(stage["id"], self.ctx)
            value["output"]["kind"] = "blocked"
            self.both(value, accepted=False)

    def test_unbound_public_templates_admit_only_after_harness_binding(self):
        templates = json.loads((ROOT / "tests/fixtures/workflow-graph/result-templates.json").read_text())
        self.ctx["candidateRef"] = ref("candidate", "A")
        self.ctx["producedCandidateRef"] = ref("candidate", "B")
        self.ctx["diagnosisFrom"] = "intake"
        self.ctx["completedStages"] = {s["id"]: completed(s["successOutputs"][0]) for s in self.graph["stages"]}
        for template in templates["results"]:
            self.assertNotIn("release", template)
            self.assertNotIn("graphSha256", template)
            value = {**result(template["stageId"], self.ctx), **template}
            self.both(value)
        before = copy.deepcopy(self.ctx)
        FakeConsumer(ROOT, self.ctx).admit(result("implement", self.ctx))
        self.assertEqual(self.ctx, before)

    def test_edges_repair_diagnosis_and_required_handoffs(self):
        self.ctx["candidateRef"] = ref("candidate", "A")
        self.ctx["completedStages"] = {s["id"]: completed(s["successOutputs"][0]) for s in self.graph["stages"]}
        review = result("review.reduce", self.ctx)
        validate_edge(review, "publish.handoff", self.ctx)
        with self.assertRaises(WorkflowGraphError):
            validate_edge(review, "implement", self.ctx)
        review["output"]["kind"] = "review-round-changes-requested"
        validate_edge(review, "implement", self.ctx)
        with self.assertRaises(WorkflowGraphError):
            validate_edge(review, "publish.handoff", self.ctx)
        self.ctx["diagnosisFrom"] = "implement"
        self.ctx["invokingStageRef"] = "retained-invocation-evidence"
        diagnosis = result("diagnose", self.ctx)
        self.both(diagnosis)
        validate_edge(diagnosis, "implement", self.ctx)
        with self.assertRaises(WorkflowGraphError):
            validate_edge(diagnosis, "plan", self.ctx)
        self.ctx["completedStages"]["publish.handoff"] = {"status": "blocked", "evidenceRef": "authority-absent"}
        self.both(result("finish.verify", self.ctx), accepted=False)
        omissions = [{"stageId": stage, "disposition": "omitted", "policyRef": "p", "reason": "Not requested"}
                     for stage in ("publish.handoff", "feedback.handoff")]
        self.both(result("finish.verify", self.ctx), overlay=omissions)

    def test_review_acquisition_requires_exact_repair_origin_evidence(self):
        baseline = context()
        baseline["candidateRef"] = ref("candidate", "repaired-candidate")
        baseline["completedStages"]["implement"] = completed("delivery-candidate")
        for origin, kind, wrong_kind in (
            ("review.reduce", "review-round-changes-requested", "review-round-aligned"),
            ("feedback.handoff", "feedback-available", "feedback-empty"),
        ):
            valid = copy.deepcopy(baseline)
            valid["repairFrom"] = origin
            valid["completedStages"][origin] = completed(kind)
            value = result("review.acquire", valid)
            with self.subTest(origin=origin, case="valid"):
                self.both(value, valid)
            invalid_evidence = [
                None,
                *[{**completed(kind), "status": status}
                  for status in ("blocked", "failed", "indeterminate")],
                completed(wrong_kind),
                completed("feedback-available" if origin == "review.reduce"
                          else "review-round-changes-requested"),
                {"status": "succeeded", "outputKind": kind},
                {**completed(kind), "evidenceRef": " "},
            ]
            for evidence in invalid_evidence:
                bad = copy.deepcopy(valid)
                if evidence is None:
                    del bad["completedStages"][origin]
                else:
                    bad["completedStages"][origin] = evidence
                with self.subTest(origin=origin, evidence=evidence):
                    self.both(value, bad, accepted=False)
        for origin in ("intake", "implement", "unknown", "", [], True):
            bad = copy.deepcopy(baseline)
            bad["repairFrom"] = origin
            with self.subTest(invalid_origin=origin):
                self.both(result("review.acquire", bad), bad, accepted=False)

    def test_independent_consumer_catches_planted_validator_and_schema_drift(self):
        bad = result("intake", self.ctx)
        bad["checkpointAdvanced"] = True
        with patch("agent_skills.workflow_graph.validate_stage_result", return_value=None):
            import agent_skills.workflow_graph as runtime
            self.assertIsNone(runtime.validate_stage_result(bad, self.ctx))
            with self.assertRaises(ValueError):
                FakeConsumer(ROOT, self.ctx).admit(bad)
        consumer = FakeConsumer(ROOT, self.ctx)
        weakened = copy.deepcopy(consumer.result_schema)
        weakened["additionalProperties"] = True
        from jsonschema import Draft202012Validator
        self.assertTrue(Draft202012Validator(weakened).is_valid(bad))
        with self.assertRaises(WorkflowGraphError):
            validate_stage_result(bad, self.ctx)

    def test_every_declared_edge_predicate_has_independent_conformance(self):
        self.ctx["candidateRef"] = ref("candidate", "A")
        self.ctx["producedCandidateRef"] = ref("candidate", "B")
        self.ctx["diagnosisFrom"] = "intake"
        self.ctx["completedStages"] = {s["id"]: completed(s["successOutputs"][0]) for s in self.graph["stages"]}
        omissions = [{"stageId": name, "disposition": "omitted", "policyRef": "policy", "reason": "Not requested"}
                     for name in ("publish.handoff", "feedback.handoff")]
        # Expected paths come from the approved matrix, not either validator.
        expected = {
            "scoped-subject": {"diagnose", "plan"},
            "confirmed": {"plan"},  # This vector records the intake caller.
            "bounded-plan": {"diagnose", "implement"},
            "delivery-candidate": {"diagnose", "review.acquire"},
            "review-acquisition-envelope": {"diagnose", "review.reduce"},
            "review-round-aligned": {"publish.handoff", "finish.verify"},
            "review-round-changes-requested": {"diagnose", "implement"},
            "published": {"feedback.handoff", "finish.verify"},
            "feedback-available": {"diagnose", "plan", "implement", "finish.verify"},
            "feedback-empty": {"finish.verify"},
            "finish-evidence-satisfied": {"compound"},
            "learning-required": set(), "no-reusable-learning": set(),
        }
        for stage in self.graph["stages"]:
            for kind in stage["successOutputs"]:
                for target in [s["id"] for s in self.graph["stages"]]:
                    value = result(stage["id"], self.ctx, kind=kind)
                    overlay = omissions if stage["id"] == "review.reduce" and target == "finish.verify" else None
                    accepted = target in expected[kind]
                    with self.subTest(stage=stage["id"], output=kind, target=target, accepted=accepted):
                        consumer = FakeConsumer(ROOT, self.ctx, overlay=overlay)
                        if accepted:
                            validate_edge(value, target, self.ctx, overlay=overlay)
                            self.assertEqual(consumer.handoff(value, target), target)
                        else:
                            with self.assertRaises(WorkflowGraphError):
                                validate_edge(value, target, self.ctx, overlay=overlay)
                            with self.assertRaises(ValueError):
                                consumer.handoff(value, target)

    def test_feedback_cannot_claim_trust_or_learning_written(self):
        self.ctx["candidateRef"] = ref("candidate", "A")
        self.ctx["completedStages"] = {s["id"]: completed(s["successOutputs"][0]) for s in self.graph["stages"]}
        for stage, field, bad_value in [("feedback.handoff", "trust", "trusted"),
                                        ("publish.handoff", "adapterRef", "wrong-adapter"),
                                        ("compound", "written", True)]:
            bad = result(stage, self.ctx)
            bad["output"][field] = bad_value
            self.both(bad, accepted=False)


if __name__ == "__main__":
    unittest.main()
