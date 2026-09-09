"""Base-move replay preserves history without spending the declared round bound."""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agent_skills import workflows
from agent_skills.provider import DeliveryRailsProvider, ProviderInputError


ROOT = Path(__file__).resolve().parents[1]


LENSES = ("correctness", "testing")


def aligned(evidence: str) -> tuple[workflows.ReviewLensResult, ...]:
    return tuple(
        workflows.ReviewLensResult(lens, "aligned", evidence=(evidence,))
        for lens in LENSES
    )


def candidate(
    marker: str,
    *,
    digest: str = "d" * 64,
    base_tip: str = "a" * 40,
    merge_base: str = "a" * 40,
) -> workflows.ReviewCandidateBinding:
    return workflows.ReviewCandidateBinding(
        candidate_ref=marker * 40,
        head_ref=marker * 40,
        deliverable_identity="deliverable-tree/v1",
        deliverable_digest=digest,
        base_ref="origin/main",
        base_tip_ref=base_tip,
        merge_base_ref=merge_base,
        workspace_id="workspace-one",
    )


def comparison(
    previous: workflows.ReviewCandidateBinding,
    current: workflows.ReviewCandidateBinding,
) -> workflows.DeliveredDiffComparison:
    contents = "diff --git a/feature.txt b/feature.txt\n+review reopening\n"
    delivered_diff = (
        workflows.DeliveredDiffEntry(
            "feature.txt",
            contents,
            __import__("hashlib").sha256(contents.encode()).hexdigest(),
        ),
    )
    return workflows.DeliveredDiffComparison(
        spec="delivered-diff-comparison/1",
        previous_candidate_ref=previous.candidate_ref,
        candidate_ref=current.candidate_ref,
        previous_base_ref=previous.base_ref,
        base_ref=current.base_ref,
        previous_base_tip_ref=previous.base_tip_ref,
        base_tip_ref=current.base_tip_ref,
        previous_merge_base_ref=previous.merge_base_ref,
        merge_base_ref=current.merge_base_ref,
        previous_diff=delivered_diff,
        diff=delivered_diff,
        evidence_ref="artifact:delivered-diff-comparison-round-2",
    )


def request() -> workflows.ReviewRequest:
    first, old, replay = candidate("1"), candidate("2"), candidate(
        "3", base_tip="b" * 40, merge_base="b" * 40
    )
    round_one, round_two_old, round_two_replay = (
        aligned("round-one"), aligned("round-two-old"), aligned("round-two-replay")
    )
    history = (
        workflows.ReviewRoundPass("pass-1", 1, first, round_one),
        workflows.ReviewRoundPass("pass-2", 2, old, round_two_old),
        workflows.ReviewRoundPass("pass-3", 2, replay, round_two_replay, "pass-2"),
    )
    reopening = workflows.BaseMoveReopening(
        previous_pass_id="pass-2",
        pass_id="pass-3",
        comparison=comparison(old, replay),
    )
    return workflows.ReviewRequest(
        LENSES,
        (round_one, round_two_replay),
        2,
        round_history=history,
        base_move_reopenings=(reopening,),
    )


class ReviewBaseReopeningTests(unittest.TestCase):
    def test_valid_replay_keeps_original_bound_active_rounds_and_full_history(self):
        supplied = request()
        result = workflows.review_work(supplied)

        self.assertEqual(result.status, "aligned")
        self.assertEqual(supplied.max_rounds, 2)
        self.assertEqual(result.counted_rounds, 2)
        self.assertEqual(result.rounds, supplied.rounds)
        self.assertEqual(result.round_history, supplied.round_history)
        self.assertEqual(result.base_move_reopenings, supplied.base_move_reopenings)
        self.assertEqual([item.pass_id for item in result.round_history], ["pass-1", "pass-2", "pass-3"])

    def test_equal_deliverable_digest_without_executed_comparison_cannot_reopen(self):
        supplied = request()
        with self.assertRaisesRegex(ValueError, "comparison"):
            workflows.review_work(replace(supplied, base_move_reopenings=()))

    def test_changed_delivered_bytes_or_paths_cannot_claim_reopening(self):
        supplied = request()
        original = supplied.base_move_reopenings[0]
        for change in (
            {"diff": (workflows.DeliveredDiffEntry("other.txt", "same", __import__("hashlib").sha256(b"same").hexdigest()),)},
            {"diff": (workflows.DeliveredDiffEntry("feature.txt", "different", __import__("hashlib").sha256(b"different").hexdigest()),)},
        ):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "byte-identical"):
                reopening = replace(original, comparison=replace(original.comparison, **change))
                workflows.review_work(replace(supplied, base_move_reopenings=(reopening,)))

    def test_changed_deliverable_binding_unchanged_base_and_stale_comparison_refuse(self):
        supplied = request()
        previous = supplied.round_history[-2]
        current = supplied.round_history[-1]
        cases = []
        cases.append(replace(current, candidate=replace(current.candidate, deliverable_digest="4" * 64)))
        cases.append(replace(current, candidate=replace(current.candidate,
                                                        base_tip_ref=previous.candidate.base_tip_ref,
                                                        merge_base_ref=previous.candidate.merge_base_ref)))
        cases.append(current)
        for index, changed in enumerate(cases):
            history = supplied.round_history[:-1] + (changed,)
            reopening = supplied.base_move_reopenings[0]
            if index == 2:
                reopening = replace(
                    reopening,
                    comparison=replace(reopening.comparison, candidate_ref="9" * 40),
                )
            with self.subTest(index=index), self.assertRaises(ValueError):
                workflows.review_work(replace(supplied, round_history=history,
                                              base_move_reopenings=(reopening,)))

    def test_missing_history_relabel_and_nonadjacent_supersession_refuse(self):
        supplied = request()
        mutations = (
            supplied.round_history[1:],
            supplied.round_history[:-1] + (replace(supplied.round_history[-1], round_number=3),),
            supplied.round_history[:-1] + (replace(supplied.round_history[-1], supersedes_pass_id="pass-1"),),
        )
        for history in mutations:
            with self.subTest(history=history), self.assertRaises(ValueError):
                workflows.review_work(replace(supplied, round_history=history))

    def test_replay_keeps_required_lenses_and_dissent_visible(self):
        supplied = request()
        replay = supplied.round_history[-1]
        invalid = (
            replay.results[:1],
            (replay.results[0], workflows.ReviewLensResult(
                "testing", "changes-requested", ("repair",), ("proof",)
            )),
        )
        for results in invalid:
            history = supplied.round_history[:-1] + (replace(replay, results=results),)
            active = supplied.rounds[:-1] + (results,)
            outcome = workflows.review_work(replace(supplied, rounds=active, round_history=history))
            with self.subTest(results=results):
                self.assertEqual(outcome.status, "blocked")


class ProviderBaseReopeningTests(unittest.TestCase):
    @staticmethod
    def raw_candidate(binding: workflows.ReviewCandidateBinding) -> dict[str, object]:
        return {
            "vcs": "git",
            "treeSha": binding.candidate_ref,
            "headSha": binding.head_ref,
            "deliverable": {
                "identity": binding.deliverable_identity,
                "digest": binding.deliverable_digest,
            },
            "base": {
                "ref": binding.base_ref,
                "tipSha": binding.base_tip_ref,
                "mergeBaseSha": binding.merge_base_ref,
            },
            "workspaceId": binding.workspace_id,
        }

    def document(self, supplied: workflows.ReviewRequest | None = None) -> dict[str, object]:
        supplied = supplied or request()
        rounds = []
        for item in supplied.round_history:
            value = {
                "passId": item.pass_id,
                "round": item.round_number,
                "candidate": self.raw_candidate(item.candidate),
                "results": [
                    {
                        "lens": result.lens,
                        "outcome": result.outcome,
                        "findings": list(result.findings),
                        "evidence": list(result.evidence),
                    }
                    for result in item.results
                ],
            }
            if item.supersedes_pass_id is not None:
                value["supersedesPassId"] = item.supersedes_pass_id
            rounds.append(value)
        reopening = supplied.base_move_reopenings[0]
        comparison_document = {
            "spec": reopening.comparison.spec,
            "previousCandidateRef": reopening.comparison.previous_candidate_ref,
            "candidateRef": reopening.comparison.candidate_ref,
            "previousBaseRef": reopening.comparison.previous_base_ref,
            "baseRef": reopening.comparison.base_ref,
            "previousBaseTipSha": reopening.comparison.previous_base_tip_ref,
            "baseTipSha": reopening.comparison.base_tip_ref,
            "previousMergeBaseSha": reopening.comparison.previous_merge_base_ref,
            "mergeBaseSha": reopening.comparison.merge_base_ref,
            "previousDiff": [asdict(entry) for entry in reopening.comparison.previous_diff],
            "diff": [asdict(entry) for entry in reopening.comparison.diff],
            "evidenceRef": reopening.comparison.evidence_ref,
        }
        return {
            "requiredLenses": list(LENSES),
            "maxRounds": 2,
            "findings": [],
            "rounds": rounds,
            "baseMoveReopenings": [{
                "previousPassId": reopening.previous_pass_id,
                "passId": reopening.pass_id,
                "comparison": comparison_document,
            }],
        }

    def test_provider_parses_closed_history_and_preserves_every_pass(self):
        result, trees, findings = DeliveryRailsProvider._review(self.document())
        self.assertEqual(result.status, "aligned")
        self.assertEqual(result.counted_rounds, 2)
        self.assertEqual(len(result.round_history), 3)
        self.assertEqual(trees, ("1" * 40, "2" * 40, "3" * 40))
        self.assertEqual(findings, ())

    def test_provider_manifest_retains_bound_history_and_reopening_audit(self):
        document = self.document()
        result, trees, findings = DeliveryRailsProvider._review(document)
        with tempfile.TemporaryDirectory() as directory:
            payload = {
                "providerId": "agent-skills.review",
                "runId": "request-one",
                "runRoot": directory,
                "obligationIds": ["review.green"],
                "candidate": self.raw_candidate(request().round_history[-1].candidate),
            }
            provider = object.__new__(DeliveryRailsProvider)
            manifest_path = provider._write_manifest(
                payload,
                "request-one",
                {"releaseId": "release-one", "archiveSha256": "a" * 64},
                document,
                result,
                trees,
                findings,
                "2026-09-09T00:00:00Z",
            )
            manifest = json.loads(manifest_path.read_text())
            retained = next(
                artifact
                for artifact in manifest["artifacts"]
                if artifact["role"] == "review-history"
            )
            history_path = Path(directory) / retained["path"]
            history_bytes = history_path.read_bytes()
            history = json.loads(history_bytes)

            self.assertEqual(
                hashlib.sha256(history_bytes).hexdigest(), retained["sha256"]
            )
            self.assertEqual(history["request"]["maxRounds"], 2)
            self.assertEqual(history["request"]["requiredLenses"], list(LENSES))
            self.assertEqual(
                [item["passId"] for item in history["request"]["rounds"]],
                ["pass-1", "pass-2", "pass-3"],
            )
            self.assertEqual(
                history["request"]["rounds"][-1]["supersedesPassId"], "pass-2"
            )
            self.assertEqual(history["result"]["counted_rounds"], 2)
            self.assertEqual(
                history["result"]["base_move_reopenings"][0]["comparison"][
                    "evidence_ref"
                ],
                "artifact:delivered-diff-comparison-round-2",
            )

    def test_provider_binds_replayed_grace_to_active_logical_rounds(self):
        original = request()
        first, grace, replay = original.round_history
        bound = workflows.ReviewRoundPass("pass-2", 2, candidate("4"), aligned("bound"))
        history = (
            first,
            bound,
            replace(grace, pass_id="pass-3", round_number=3),
            replace(replay, pass_id="pass-4", round_number=3, supersedes_pass_id="pass-3"),
        )
        supplied = replace(
            original,
            rounds=(first.results, bound.results, replay.results),
            round_history=history,
            base_move_reopenings=(replace(
                original.base_move_reopenings[0], previous_pass_id="pass-3", pass_id="pass-4",
            ),),
        )
        document = self.document(supplied)
        document["graceVerification"] = {
            "previousCandidateRef": bound.candidate.candidate_ref,
            "candidateRef": replay.candidate.candidate_ref,
            "requiredChange": "Required candidate correction after bound-round alignment.",
        }
        result, trees, findings = DeliveryRailsProvider._review(document)
        self.assertEqual(result.status, "aligned")
        self.assertEqual(result.counted_rounds, 2)
        self.assertEqual([item.pass_id for item in result.round_history],
                         ["pass-1", "pass-2", "pass-3", "pass-4"])
        self.assertEqual(trees, tuple(item.candidate.candidate_ref for item in history))
        self.assertEqual(findings, ())

        mutations = [
            {**document, "graceVerification": {
                **document["graceVerification"],
                "previousCandidateRef": grace.candidate.candidate_ref,
            }},
            {**document, "rounds": document["rounds"][:2] + document["rounds"][3:]},
            {**document, "baseMoveReopenings": []},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ProviderInputError) as raised:
                DeliveryRailsProvider._review(mutation)
            self.assertEqual(raised.exception.blocker_id, "review-malformed")

    def test_provider_rejects_unknown_malformed_and_digest_only_declarations(self):
        document = self.document()
        cases = [
            {**document, "baseMoveReopenings": True},
            {**document, "baseMoveReopenings": [{
                **document["baseMoveReopenings"][0], "extra": "ignored"
            }]},
            {**document, "baseMoveReopenings": [{
                "previousPassId": "pass-2", "passId": "pass-3",
                "deliverableDigest": "d" * 64,
            }]},
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ProviderInputError) as raised:
                DeliveryRailsProvider._review(case)
            self.assertEqual(raised.exception.blocker_id, "review-malformed")

    def test_provider_rejects_outer_candidate_that_is_not_the_exact_final_binding(self):
        document = self.document()
        result, trees, findings = DeliveryRailsProvider._review(document)
        payload = {
            "providerId": "agent-skills.review",
            "runId": "request-one",
            "runRoot": "/tmp",
            "obligationIds": ["review.green"],
            "candidate": self.raw_candidate(request().round_history[-1].candidate),
        }
        provider = object.__new__(DeliveryRailsProvider)
        payload["candidate"]["base"]["tipSha"] = "9" * 40
        with self.assertRaises(ProviderInputError) as raised:
            provider._write_manifest(payload, "request-one", {
                "releaseId": "release-one", "archiveSha256": "a" * 64,
            }, document, result, trees, findings, "2026-09-09T00:00:00Z")
        self.assertEqual(raised.exception.blocker_id, "candidate-mismatch")


if __name__ == "__main__":
    unittest.main()
