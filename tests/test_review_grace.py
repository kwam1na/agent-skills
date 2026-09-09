"""The documented one-off grace round preserves the declared bound and history."""
from __future__ import annotations

from dataclasses import asdict, replace
import json
import tempfile
import unittest

from agent_skills import workflows
from agent_skills.provider import DeliveryRailsProvider, ProviderInputError
from test_review_orchestration import context, envelope
from agent_skills.review_orchestration import obtain_review, ReviewAcquisitionError


LENSES = ("correctness", "testing")


def aligned(evidence: str = "observed") -> tuple[workflows.ReviewLensResult, ...]:
    return tuple(workflows.ReviewLensResult(lens, "aligned", evidence=(evidence,)) for lens in LENSES)


def declaration():
    return workflows.GraceVerification("candidate-four", "candidate-five", "Required correction: evidence/ref-1")


def history():
    dissent = workflows.ReviewLensResult("testing", "changes-requested", ("repair",), ("observed failure",), late_findings=("repair",))
    return ((aligned()[0], dissent), aligned("round2"), aligned("round3"), aligned("round4"), aligned("round5"))


class ReviewGraceTests(unittest.TestCase):
    def test_explicit_grace_preserves_bound_full_history_and_declaration(self):
        rounds = history()
        request = workflows.ReviewRequest(LENSES, rounds, 4, grace_verification=declaration())
        result = workflows.review_work(request)
        self.assertEqual(result.status, "aligned")
        self.assertEqual(request.max_rounds, 4)
        self.assertEqual(result.rounds, rounds)
        self.assertEqual(result.dissent, (rounds[0][1],))
        self.assertEqual(result.late_findings[0].round_number, 1)
        self.assertEqual(result.counted_rounds, 4)
        self.assertEqual(asdict(result)["grace_verification"], asdict(declaration()))

    def test_fifth_round_without_declaration_still_exceeds_bound(self):
        result = workflows.review_work(workflows.ReviewRequest(LENSES, history(), 4))
        self.assertEqual(result.status, "blocked")
        self.assertIn("review loop exceeded 4 rounds", result.blockers)

    def test_ineligible_bound_round_cannot_be_bypassed_by_aligned_grace(self):
        good = aligned()
        invalid = [
            (good[0], workflows.ReviewLensResult("testing", "changes-requested", ("repair",), ("proof",))),
            (good[0], workflows.ReviewLensResult("testing", "failed", failure="unavailable")),
            (good[0], workflows.ReviewLensResult("testing", "aligned")),
            good[:1], good + (good[0],),
            good + (workflows.ReviewLensResult("extra", "aligned", evidence=("proof",)),),
        ]
        for bound in invalid:
            with self.subTest(bound=bound):
                rounds = history()[:3] + (bound, good)
                result = workflows.review_work(workflows.ReviewRequest(LENSES, rounds, 4, declaration()))
                self.assertEqual(result.status, "blocked")
                self.assertEqual(result.rounds, rounds)

    def test_failed_earlier_round_remains_visible_after_valid_bound_and_grace(self):
        rounds = ((aligned()[0], workflows.ReviewLensResult("testing", "failed", failure="offline")),) + history()[1:]
        result = workflows.review_work(workflows.ReviewRequest(LENSES, rounds, 4, declaration()))
        self.assertEqual(result.status, "aligned")
        self.assertEqual(result.failures[0].reason, "offline")
        self.assertEqual(len(result.rounds), 5)

    def test_grace_dissent_failure_and_incomplete_acquisition_are_terminal(self):
        good = aligned()
        invalid = [
            (good[0], workflows.ReviewLensResult("testing", "changes-requested", ("repair",), ("proof",), late_findings=("repair",))),
            (good[0], workflows.ReviewLensResult("testing", "failed", failure="timeout")),
            (good[0], workflows.ReviewLensResult("testing", "aligned")),
            good[:1], good + (good[0],),
            good + (workflows.ReviewLensResult("extra", "aligned", evidence=("proof",)),),
        ]
        for final in invalid:
            with self.subTest(final=final):
                result = workflows.review_work(workflows.ReviewRequest(LENSES, history()[:4] + (final,), 4, declaration()))
                self.assertEqual(result.status, "blocked")
                self.assertTrue(result.blockers)

    def test_invalid_or_unchanged_candidate_declaration_refuses(self):
        for fields in [
            {"previous_candidate_ref": "candidate-five"},
            {"candidate_ref": ""}, {"previous_candidate_ref": " "},
            {"required_change": " "}, {"candidate_ref": None},
            {"required_change": 1},
        ]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                workflows.review_work(workflows.ReviewRequest(LENSES, history(), 4, replace(declaration(), **fields)))
        with self.assertRaises(ValueError):
            workflows.review_work(workflows.ReviewRequest(LENSES, history(), 4, True))

    def test_declaration_before_bound_or_second_extra_round_refuses(self):
        for count in (0, 3, 4, 6):
            with self.subTest(count=count), self.assertRaises(ValueError):
                workflows.review_work(workflows.ReviewRequest(LENSES, (aligned(),) * count, 4, declaration()))
        result = workflows.review_work(workflows.ReviewRequest(LENSES, (aligned(),) * 6, 4))
        self.assertEqual(result.status, "blocked")

    def test_ordinary_candidate_bound_acquisition_feeds_grace_without_schema_extension(self):
        acquired = []
        for number in range(1, 6):
            ctx = context(f"candidate-{number}", number)
            acquired.append(obtain_review(envelope(LENSES, ctx), LENSES, ctx))
        grace = workflows.GraceVerification(
            acquired[-2].envelope["candidateRef"]["opaque"],
            acquired[-1].envelope["candidateRef"]["opaque"], "Required correction: ref-1",
        )
        result = workflows.review_work(workflows.ReviewRequest(LENSES, tuple(item.results for item in acquired), 4, grace))
        self.assertEqual(result.status, "aligned")
        self.assertEqual(len(result.rounds), 5)
        ctx = context("candidate-5", 5)
        raw = envelope(LENSES, ctx)
        raw["graceVerification"] = asdict(grace)
        with self.assertRaises(ReviewAcquisitionError):
            obtain_review(raw, LENSES, ctx)


class ProviderGraceTests(unittest.TestCase):
    def document(self):
        return {
            "requiredLenses": list(LENSES), "maxRounds": 4, "findings": [],
            "rounds": [{"preparedTreeSha": str(number) * 40,
                        "results": [{"lens": lens, "outcome": "aligned", "findings": [], "evidence": [f"round{number}"]} for lens in LENSES]}
                       for number in range(1, 6)],
            "graceVerification": {"previousCandidateRef": "4" * 40, "candidateRef": "5" * 40, "requiredChange": "Required change ref-1"},
        }

    def test_provider_validates_grace_against_adjacent_actual_bindings(self):
        document = self.document()
        result, trees, findings = DeliveryRailsProvider._review(document)
        self.assertEqual(result.status, "aligned")
        self.assertEqual(result.grace_verification.previous_candidate_ref, trees[-2])
        self.assertEqual(result.grace_verification.candidate_ref, trees[-1])
        self.assertEqual(len(result.rounds), 5)
        self.assertEqual(document["maxRounds"], 4)
        self.assertEqual(findings, ())

    def test_provider_does_not_silently_ignore_unknown_or_mismatched_grace(self):
        for grace in [None, True, {},
                      {**self.document()["graceVerification"], "extra": "override"},
                      {**self.document()["graceVerification"], "previousCandidateRef": "3" * 40},
                      {**self.document()["graceVerification"], "candidateRef": "6" * 40},
                      {**self.document()["graceVerification"], "requiredChange": " "}]:
            with self.subTest(grace=grace), self.assertRaises(ProviderInputError) as raised:
                DeliveryRailsProvider._review({**self.document(), "graceVerification": grace})
            self.assertEqual(raised.exception.blocker_id, "review-malformed")

    def test_provider_unchanged_bindings_cannot_claim_required_change(self):
        document = self.document()
        document["rounds"][-1]["preparedTreeSha"] = "4" * 40
        document["graceVerification"]["candidateRef"] = "4" * 40
        with self.assertRaises(ProviderInputError) as raised:
            DeliveryRailsProvider._review(document)
        self.assertEqual(raised.exception.blocker_id, "review-malformed")

    def test_provider_refuses_reusing_grace_or_declaring_it_before_bound(self):
        for count in (0, 3, 4, 6):
            with self.subTest(count=count):
                document = self.document()
                document["rounds"] = (document["rounds"] + [document["rounds"][-1]])[:count]
                if count >= 2:
                    document["graceVerification"]["previousCandidateRef"] = document["rounds"][-2]["preparedTreeSha"]
                    document["graceVerification"]["candidateRef"] = document["rounds"][-1]["preparedTreeSha"]
                with self.assertRaises(ProviderInputError) as raised:
                    DeliveryRailsProvider._review(document)
                self.assertEqual(raised.exception.blocker_id, "review-malformed")

    def test_provider_ordinary_input_keeps_original_bound(self):
        document = self.document()
        del document["graceVerification"]
        result, _, _ = DeliveryRailsProvider._review(document)
        self.assertEqual(result.status, "blocked")
        document["rounds"].pop()
        result, _, _ = DeliveryRailsProvider._review(document)
        self.assertEqual(result.status, "aligned")

    def test_manifest_counts_all_five_passes_and_keeps_tree_history(self):
        document = self.document()
        result, trees, findings = DeliveryRailsProvider._review(document)
        self.assertEqual(result.status, "aligned")
        with tempfile.TemporaryDirectory() as directory:
            payload = {
                "providerId": "agent-skills.review", "runId": "request-1", "runRoot": directory,
                "obligationIds": ["review.green"],
                "candidate": {"vcs": "git", "treeSha": trees[-1], "workspaceId": "workspace",
                              "base": {"ref": "main", "tipSha": "a" * 40, "mergeBaseSha": "a" * 40},
                              "deliverable": {"identity": "deliverable-tree/v1", "digest": "a" * 64}},
            }
            # Manifest rendering does not access installation state or acquire reviews.
            provider = object.__new__(DeliveryRailsProvider)
            path = provider._write_manifest(payload, "request-1", {"releaseId": "release-1", "archiveSha256": "a" * 64}, result, trees, findings, "2026-09-07T00:00:00Z")
            manifest = json.loads(path.read_text())
            self.assertEqual(manifest["provider"]["finalPassId"], "pass-5")
            self.assertEqual(manifest["claims"][0]["payload"]["telemetry"]["iterationCount"], 5)
            self.assertEqual([r["preparedTreeSha"] for r in manifest["runHistory"]], list(trees))


if __name__ == "__main__":
    unittest.main()
