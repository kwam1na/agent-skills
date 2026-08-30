"""Test-first review acquisition and independent repair-round conformance."""
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

from agent_skills.review_orchestration import (
    ReviewAcquisitionError, obtain_review, prepare_review,
)
from agent_skills.workflows import ReviewRequest, review_work

spec = importlib.util.spec_from_file_location("review_consumer", ROOT / "tests/consumers/workflow_consumer.py")
consumer_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(consumer_module)
FakeConsumer = consumer_module.FakeConsumer
FIXTURE = json.loads((ROOT / "tests/scenarios/core/obtain-review.json").read_text())
BINDINGS = ("release", "graphSha256", "subjectRef", "candidateRef", "round")


def context(candidate="candidate-A", round_number=1):
    return {
        "release": {"releaseId": "release-one", "profile": "core", "archiveSha256": "a" * 64,
                    "metadataSha256": "b" * 64},
        "graphSha256": hashlib.sha256((ROOT / "workflows/delivery-v1.json").read_bytes()).hexdigest(),
        "subjectRef": {"schemaVersion": "workflow-subject-ref/1", "opaque": "subject-one"},
        "candidateRef": {"schemaVersion": "workflow-candidate-ref/1", "opaque": candidate},
        "round": round_number,
    }


def entry(lens, ctx, state="obtained", disposition="aligned"):
    value = {"lens": lens, "state": state, **copy.deepcopy(ctx)}
    if state == "obtained":
        value["result"] = {"outcome": disposition, "findings": [], "evidence": ["retained-evidence"]}
        if disposition == "changes-requested":
            value["result"]["findings"] = ["Correct the boundary check."]
    return value


def envelope(lenses, ctx, dispositions=None):
    dispositions = dispositions or ["aligned"] * len(lenses)
    return {"schemaVersion": "review-acquisition-envelope/1", **copy.deepcopy(ctx),
            "requestedLenses": list(lenses),
            "entries": [entry(lens, ctx, disposition=outcome) for lens, outcome in zip(lenses, dispositions)]}


def attested(value, index=0, realization="separate-host-realization"):
    item = value["entries"][index]
    attestation = {"schemaVersion": "review-realization-attestation/1",
                   **{key: copy.deepcopy(item[key]) for key in BINDINGS},
                   "lens": item["lens"], "realizationRef": realization,
                   "attestationRef": f"attestation-{item['lens']}", "distinctRealization": True,
                   "resultSha256": hashlib.sha256(json.dumps(item["result"], sort_keys=True,
                       separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()}
    item["independence"] = {"status": "attested", "realizationRef": realization,
                            "attestationRef": attestation["attestationRef"]}
    return attestation


class ReviewAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.ctx = context()
        self.lenses = tuple(FIXTURE["requestedLenses"])
        self.value = envelope(self.lenses, self.ctx)

    def reject(self, value, code, *, ctx=None, lenses=None, attestations=()):
        with self.assertRaises(ReviewAcquisitionError) as raised:
            obtain_review(value, self.lenses if lenses is None else lenses,
                          self.ctx if ctx is None else ctx, attestations=attestations)
        self.assertEqual(raised.exception.code, code)

    def test_lenses_reject_before_realization_inputs_exist(self):
        for lenses in ([], (), "correctness", None, [""], [" "], [1], [None], [{}],
                       ["a", " a "], ["has space"], ["a\nb"], ["a/../../b"]):
            with self.subTest(lenses=lenses), self.assertRaises(ReviewAcquisitionError) as raised:
                prepare_review(lenses, self.ctx, {})
            self.assertEqual(raised.exception.code, "invalid-lenses")

    def test_prepared_inputs_are_bounded_ordered_and_copied(self):
        lens_contexts = {"security": ["security-scope"], "testing": ["test-scope"]}
        before = copy.deepcopy((self.ctx, lens_contexts))
        prepared = prepare_review([" security ", "testing"], self.ctx, lens_contexts)
        self.assertEqual([item["lens"] for item in prepared], ["security", "testing"])
        self.assertEqual(prepared[0]["contextRefs"], ["security-scope"])
        self.assertEqual(prepared[0]["expectedResultShape"], "review-lens-result/1")
        self.assertEqual(set(prepared[0]), set(BINDINGS) | {"lens", "contextRefs", "expectedResultShape"})
        prepared[0]["candidateRef"]["opaque"] = "cannot-mutate-input"
        self.assertEqual((self.ctx, lens_contexts), before)
        for contexts in ({}, {"testing": []}, {"testing": [" "], "security": []},
                         {"testing": [], "security": [], "extra": []},
                         {"testing": {"instructions": "write"}, "security": []}):
            with self.assertRaises(ReviewAcquisitionError) as raised:
                prepare_review(["testing", "security"], self.ctx, contexts)
            self.assertEqual(raised.exception.code, "invalid-lens-context")
        with self.assertRaises(ReviewAcquisitionError):
            prepare_review(["testing"], self.ctx, {"testing": []}, expected_result_shape="arbitrary-payload")

    def test_all_projection_states_and_complete_retained_evidence(self):
        cases = FIXTURE["projection"]
        lenses = tuple(f"lens-{number}" for number in range(len(cases)))
        value = envelope(lenses, self.ctx)
        value["entries"] = [entry(lens, self.ctx, case["state"], case.get("outcome", "aligned"))
                            for lens, case in zip(lenses, cases)]
        acquired = obtain_review(value, lenses, self.ctx)
        self.assertEqual([item["state"] for item in acquired.envelope["entries"]],
                         [case["state"] for case in cases])
        self.assertEqual([item.lens for item in acquired.results], list(lenses[:-1]))
        self.assertEqual([item.outcome for item in acquired.results],
                         ["aligned", "changes-requested", "failed", "failed"])
        self.assertEqual([item.failure for item in acquired.results[-2:]],
                         ["review-acquisition-failed", "review-acquisition-indeterminate"])
        reduced = review_work(ReviewRequest(lenses, (acquired.results,), 2))
        self.assertIn("required review lens missing: lens-4 in round 1", reduced.blockers)
        self.assertEqual(reduced.status, "blocked")

    def test_normalization_default_independence_and_no_aliasing(self):
        self.value["entries"][0]["result"]["evidence"] = [" proof ", "proof"]
        before = copy.deepcopy((self.value, self.ctx))
        acquired = obtain_review(self.value, self.lenses, self.ctx)
        self.assertEqual(acquired.results[0].evidence, ("proof",))
        self.assertTrue(all(item["independence"] == {"status": "unverified"}
                            for item in acquired.envelope["entries"]))
        acquired.envelope["candidateRef"]["opaque"] = "changed-copy"
        self.assertEqual((self.value, self.ctx), before)
        self.assertEqual(acquired.results[0].outcome, "aligned")
        self.assertFalse(hasattr(acquired, "status"))

    def test_envelope_entries_preserve_exact_caller_order(self):
        for key in ("requestedLenses", "entries"):
            value = copy.deepcopy(self.value)
            value[key].reverse()
            self.reject(value, "invalid-envelope")
        value = copy.deepcopy(self.value)
        value["entries"][1] = copy.deepcopy(value["entries"][0])
        self.reject(value, "invalid-envelope")

    def test_all_binding_components_reject_at_envelope_and_lens_boundary(self):
        replacements = {"release": {**self.ctx["release"], "releaseId": "other-release"},
                        "graphSha256": "c" * 64,
                        "subjectRef": {**self.ctx["subjectRef"], "opaque": "other-subject"},
                        "candidateRef": {**self.ctx["candidateRef"], "opaque": "candidate-B"}, "round": 2}
        for key, replacement in replacements.items():
            for nested in (False, True):
                with self.subTest(key=key, nested=nested):
                    value = copy.deepcopy(self.value)
                    (value["entries"][0] if nested else value)[key] = replacement
                    self.reject(value, "binding-mismatch")
        for key in self.ctx["release"]:
            value = copy.deepcopy(self.value)
            value["release"][key] = "c" * 64 if key.endswith("Sha256") else "other"
            self.reject(value, "binding-mismatch")

    def test_context_is_separate_and_malformed_context_fails_closed(self):
        for key, replacement in (("round", True), ("round", 0), ("round", "1"),
                                 ("candidateRef", None), ("graphSha256", "c" * 64),
                                 ("subjectRef", {"schemaVersion": "workflow-subject-ref/2", "opaque": "x"})):
            ctx = {**self.ctx, key: replacement}
            self.reject(self.value, "invalid-context", ctx=ctx)
        for ctx in (None, [], {}, "claimed-context"):
            with self.assertRaises(ReviewAcquisitionError):
                obtain_review(self.value, self.lenses, ctx)
        self.reject({**self.value, "trustedContext": self.ctx}, "invalid-envelope")

    def test_closed_malformed_result_states_and_authority_claims_reject(self):
        for field in ("status", "aligned", "converged", "mutation", "remediation", "permission",
                      "checkpoint", "hostTranscript", "attestations"):
            self.reject({**self.value, field: True}, "invalid-envelope")
        for bad in (None, [], "result", {}):
            self.reject(bad, "invalid-envelope")
        for state in ("aligned", "converged", "succeeded", [], None):
            value = copy.deepcopy(self.value)
            value["entries"][0]["state"] = state
            self.reject(value, "invalid-entry")
        for result in ({"outcome": "aligned", "findings": [], "evidence": []},
                       {"outcome": "aligned", "findings": ["dissent"], "evidence": ["proof"]},
                       {"outcome": "changes-requested", "findings": [], "evidence": ["proof"]},
                       {"outcome": "aligned", "findings": [], "evidence": ["proof"], "arbitrary": {}},
                       {"outcome": [], "findings": [], "evidence": ["proof"]},
                       {"outcome": "failed", "findings": [], "evidence": ["proof"]}):
            value = copy.deepcopy(self.value)
            value["entries"][0]["result"] = result
            self.reject(value, "invalid-result")
        value = copy.deepcopy(self.value)
        value["entries"][0]["state"] = "missing"
        self.reject(value, "invalid-entry")

    def test_attestation_binds_exact_result_and_distinct_realization(self):
        proof = attested(self.value)
        acquired = obtain_review(self.value, self.lenses, self.ctx, attestations=(proof,))
        self.assertEqual(acquired.envelope["entries"][0]["independence"]["status"], "attested")
        self.reject(self.value, "invalid-independence")
        for key, value in (("resultSha256", "c" * 64), ("lens", "testing"), ("round", 2),
                           ("distinctRealization", False), ("realizationRef", "wrong"),
                           ("attestationRef", "wrong"), ("candidateRef", context("B")["candidateRef"])):
            with self.subTest(key=key):
                bad = {**proof, key: value}
                self.reject(self.value, "invalid-attestation", attestations=(bad,))
        changed = copy.deepcopy(self.value)
        changed["entries"][0]["result"]["evidence"] = ["substituted-evidence"]
        self.reject(changed, "invalid-attestation", attestations=(proof,))
        second = attested(self.value, 1)
        self.reject(self.value, "invalid-attestation", attestations=(proof, second))
        second = attested(self.value, 1, "another-distinct-realization")
        obtain_review(self.value, self.lenses, self.ctx, attestations=(proof, second))

    def test_unsupported_self_claims_and_nonobtained_attestation_reject(self):
        for independence in ("independent", {"status": "independent"}, {"status": "attested"},
                             {"status": "unverified", "persona": "security expert"}):
            value = copy.deepcopy(self.value)
            value["entries"][0]["independence"] = independence
            self.reject(value, "invalid-independence")
        proof = attested(self.value)
        self.value["entries"][0]["state"] = "failed"
        del self.value["entries"][0]["result"]
        self.reject(self.value, "invalid-independence", attestations=(proof,))

    def test_attestation_checks_every_binding_and_normalized_result_field(self):
        self.value["entries"][0]["result"] = {
            "outcome": "changes-requested", "findings": ["repair"], "evidence": ["proof"]}
        proof = attested(self.value)
        self.value["entries"][0]["result"]["findings"] = [" repair ", "repair"]
        self.value["entries"][0]["result"]["evidence"] = [" proof ", "proof"]
        acquired = obtain_review(self.value, self.lenses, self.ctx, attestations=(proof,))
        self.assertEqual(acquired.results[0].findings, ("repair",))
        for key in ("release", "graphSha256", "subjectRef", "candidateRef", "round"):
            bad = copy.deepcopy(proof)
            if key == "release":
                bad[key]["archiveSha256"] = "c" * 64
            elif key.endswith("Ref"):
                bad[key]["opaque"] = "different"
            elif key == "round":
                bad[key] = 2
            else:
                bad[key] = "c" * 64
            self.reject(self.value, "invalid-attestation", attestations=(bad,))
        for replacement in ({"outcome": "aligned", "findings": [], "evidence": ["proof"]},
                            {"outcome": "changes-requested", "findings": ["different"], "evidence": ["proof"]}):
            value = copy.deepcopy(self.value)
            value["entries"][0]["result"] = replacement
            self.reject(value, "invalid-attestation", attestations=(proof,))

    def test_lone_surrogate_review_text_rejects_with_and_without_attestation(self):
        for field in ("findings", "evidence"):
            for encoded in ('"\\ud800"', '"\\udfff"'):
                for with_attestation in (False, True):
                    with self.subTest(field=field, encoded=encoded, attested=with_attestation):
                        value = copy.deepcopy(self.value)
                        value["entries"][0]["result"] = {
                            "outcome": "changes-requested", "findings": ["repair"], "evidence": ["proof"]}
                        proofs = (attested(value),) if with_attestation else ()
                        value["entries"][0]["result"][field] = [json.loads(encoded)]
                        before = copy.deepcopy(value)
                        self.reject(value, "invalid-result", attestations=proofs)
                        self.assertEqual(value, before)

    def test_utf8_review_text_retains_exact_valid_attestation(self):
        text = json.loads('"caf\\u00e9 \\ud83d\\udd0d"')
        self.value["entries"][0]["result"] = {
            "outcome": "changes-requested", "findings": [text], "evidence": [text]}
        proof = attested(self.value)
        acquired = obtain_review(self.value, self.lenses, self.ctx, attestations=(proof,))
        self.assertEqual(acquired.results[0].findings, (text,))
        self.assertEqual(acquired.results[0].evidence, (text,))
        self.assertEqual(acquired.envelope["entries"][0]["independence"]["status"], "attested")

    def test_nested_malformed_values_have_stable_rejection_without_payload_echo(self):
        for path in (("round",), ("entries", 0, "round")):
            value = copy.deepcopy(self.value)
            target = value
            for component in path[:-1]:
                target = target[component]
            target[path[-1]] = True  # Python equality must not treat this as round 1.
            self.reject(value, "binding-mismatch")
        for field in ("permission", "status", "arbitraryPayload"):
            value = copy.deepcopy(self.value)
            value["entries"][0][field] = {"secret": "must-not-echo"}
            with self.assertRaises(ReviewAcquisitionError) as raised:
                obtain_review(value, self.lenses, self.ctx)
            self.assertEqual(str(raised.exception), "invalid-entry")
        proof = attested(self.value)
        for field in ("schemaVersion", "resultSha256", "lens", "realizationRef", "attestationRef"):
            self.reject(self.value, "invalid-attestation", attestations=({**proof, field: []},))

    def test_sequential_realization_and_acquisition_never_call_reducer(self):
        prepared = prepare_review(self.lenses, self.ctx, {lens: [f"scope-{lens}"] for lens in self.lenses})
        value = envelope(self.lenses, self.ctx)
        value["entries"] = []
        for request in prepared:
            value["entries"].append(entry(request["lens"], {key: request[key] for key in BINDINGS}))
        before = copy.deepcopy(value)
        with patch("agent_skills.workflows.review_work", side_effect=AssertionError("acquisition reduced")):
            acquired = obtain_review(value, self.lenses, self.ctx)
        self.assertEqual(value, before)
        self.assertEqual([result.lens for result in acquired.results], list(self.lenses))

    def test_independent_consumer_rejects_stale_a_and_accepts_complete_fresh_b(self):
        ctx = {**self.ctx, "completedStages": {
            "implement": {"status": "succeeded", "outputKind": "delivery-candidate", "evidenceRef": "A"}}}
        fake = FakeConsumer(ROOT, ctx)
        fake.set_review_context(self.lenses, self.ctx["round"])
        candidate_a = envelope(self.lenses, self.ctx, FIXTURE["candidateA"]["dispositions"])
        obtained_a = obtain_review(candidate_a, self.lenses, self.ctx)
        self.assertEqual(fake.review_round(candidate_a), FIXTURE["candidateA"]["expectedReduction"])
        self.assertEqual(review_work(ReviewRequest(self.lenses, (obtained_a.results,), 3)).status, "unresolved")
        next_context = context("candidate-B", 2)
        fake.set_context({**ctx, **next_context})
        fake.set_review_context(self.lenses, 2)
        for stale in (candidate_a, {**candidate_a, **next_context}):
            self.reject(stale, "binding-mismatch", ctx=next_context)
            with self.assertRaises(ValueError):
                fake.review_round(stale)
        candidate_b = envelope(self.lenses, next_context, FIXTURE["candidateB"]["dispositions"])
        acquired_b = obtain_review(candidate_b, self.lenses, next_context)
        with patch("agent_skills.workflows.review_work", side_effect=AssertionError("consumer used reducer")), \
             patch("agent_skills.review_orchestration.obtain_review", side_effect=AssertionError("consumer used validator")):
            self.assertEqual(fake.review_round(candidate_b), FIXTURE["candidateB"]["expectedReduction"])
        self.assertEqual(review_work(ReviewRequest(self.lenses, (obtained_a.results, acquired_b.results), 3)).status, "aligned")
        self.assertEqual(fake.checkpoint, "checkpoint-one")
        self.assertEqual(fake.context["candidateRef"], next_context["candidateRef"])
        self.assertEqual(fake.review_history[0]["candidateRef"], self.ctx["candidateRef"])


if __name__ == "__main__":
    unittest.main()
