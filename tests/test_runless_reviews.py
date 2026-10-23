"""Captured native reviews are observations, never fabricated product runs."""
import base64
import hashlib
import json
import unittest
from pathlib import Path

from tests import test_orchestration_state as fixtures
from tests.delivery_views_fixtures import render

state_tool = fixtures.state_tool


def outcome(number, candidate, *, finding=False, resolved=False):
    entries = []
    for lens in ("lens.outcome-correctness", "lens.adversarial-testing"):
        raised = [{"id": "F1", "severity": "P1", "summary": "<img src=x onerror=alert(1)>", "state": "open"}] if finding and lens.endswith("correctness") else []
        closures = [{"id": "F1", "state": "closed", "originatingLens": lens, "fixCommit": fixtures.HEAD_B,
                     "resolution": "Original reviewer confirmed the fix."}] if resolved and lens.endswith("correctness") else []
        binding = {"subjectRef": "work-a", "candidateRef": candidate, "round": number}
        entries.append({"lens": lens, "state": "obtained", "binding": binding, "result": {
            "schemaVersion": "review-lens-result/1", "lensId": lens, **binding,
            "outcome": "changes-requested" if raised else "aligned", "findings": raised,
            "resolvedFindings": closures, "evidence": {"startCommit": candidate}}})
    return {"round": number, "outcome": "unresolved" if finding else "aligned", "entries": entries}


class RunlessReviewTests(unittest.TestCase):
    setUp = fixtures.ReconcileTests.setUp
    lead = fixtures.ReconcileTests.lead

    def files(self):
        paths = []
        for number, data in enumerate([outcome(1, fixtures.HEAD_A, finding=True), outcome(2, fixtures.HEAD_B, resolved=True)], 1):
            path = self.root / f"round{number}.json"
            path.write_text(json.dumps(data, indent=2) + "\n")
            paths.append(path)
        return paths

    def capture(self, paths=None):
        return state_tool.capture_review(self.directory, subject="work-a", candidate=fixtures.HEAD_B,
                                         outcomes=paths or self.files())

    def test_capture_preserves_bytes_round_candidates_and_replays_without_new_event(self):
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_B)]})
        files = self.files()
        captured = self.capture(files)
        before = (self.directory / state_tool.JOURNAL_FILE).read_bytes()
        self.assertEqual(self.capture(files), captured)
        self.assertEqual((self.directory / state_tool.JOURNAL_FILE).read_bytes(), before)
        event = state_tool.read_journal(self.directory)[-1]
        self.assertEqual(event["kind"], "review.evidence")
        artifact = self.directory / "review-artifacts" / (event["reviewArtifact"]["digest"] + ".json")
        raw = artifact.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), event["reviewArtifact"]["digest"])
        self.assertEqual([base64.b64decode(row) for row in json.loads(raw)["outcomes"]], [p.read_bytes() for p in files])
        shown = state_tool.show(self.directory)
        self.assertIsNone(shown["state"]["queue"][0]["runId"])
        observation = shown["reviewEvidence"]["work-a"]
        self.assertEqual([r["entries"][0]["binding"]["candidateRef"] for r in observation["outcomes"]], [fixtures.HEAD_A, fixtures.HEAD_B])
        summary = render.native_review_summaries(shown)["work-a"]
        self.assertEqual(summary["roundDetail"][-1]["outcome"], "approve")
        self.assertEqual(summary["roundDetail"][0]["raised"][0]["state"], "resolved")
        self.assertIn("Original reviewer", summary["roundDetail"][-1]["settled"][0]["resolution"])

    def test_missing_tampered_oversized_and_candidate_mismatch_are_unavailable(self):
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_B)]})
        self.capture()
        ref = state_tool.read_journal(self.directory)[-1]["reviewArtifact"]
        path = self.directory / "review-artifacts" / (ref["digest"] + ".json")
        original = path.read_bytes()
        for data in (None, b"{}", b"x" * (state_tool.REVIEW_ARTIFACT_LIMIT + 1)):
            with self.subTest(data=None if data is None else len(data)):
                if data is None:
                    path.unlink()
                else:
                    path.write_bytes(data)
                self.assertTrue(state_tool.show(self.directory)["reviewEvidence"]["work-a"]["unavailable"])
                path.write_bytes(original)
        state_tool.patch(self.directory, writer="runner", version=state_tool.read_state(self.directory)["version"],
                         selector="queue[subject=work-a]", changes={"syncedHead": fixtures.HEAD_A})
        self.assertTrue(state_tool.show(self.directory)["reviewEvidence"]["work-a"]["unavailable"])

    def test_invalid_binding_or_foreign_closure_refuses_before_capture(self):
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_B)]})
        for mutation in ("subject", "candidate", "closure"):
            paths = self.files()
            data = json.loads(paths[1].read_text())
            if mutation == "subject": data["entries"][0]["result"]["subjectRef"] = "other"
            if mutation == "candidate": data["entries"][0]["binding"]["candidateRef"] = fixtures.HEAD_A
            if mutation == "closure": data["entries"][0]["result"]["resolvedFindings"][0]["originatingLens"] = "lens.other"
            paths[1].write_text(json.dumps(data))
            with self.subTest(mutation=mutation), self.assertRaises(state_tool.StateError): self.capture(paths)
            self.assertEqual(state_tool.read_journal(self.directory), [])

    def test_oversized_source_and_symlink_storage_are_refused(self):
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_B)]})
        paths = self.files()
        paths[0].write_bytes(b"x" * (state_tool.REVIEW_ARTIFACT_LIMIT + 1))
        with self.assertRaises(state_tool.StateError): self.capture(paths)
        (self.directory / "review-artifacts").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(state_tool.StateError): self.capture()
        self.assertEqual(state_tool.read_journal(self.directory), [])

    def test_approval_requires_actual_reports_and_originating_closure_not_zero_counts(self):
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_B)]})
        for mutation in ("missing-result", "missing-closure", "zero-counts"):
            paths = self.files()
            data = json.loads(paths[1].read_text())
            if mutation == "missing-result":
                data["entries"][0]["state"] = "missing"
                del data["entries"][0]["result"]
            elif mutation == "missing-closure": data["entries"][0]["result"]["resolvedFindings"] = []
            else:
                data["entries"] = []
                data["findings"] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
            paths[1].write_text(json.dumps(data))
            with self.subTest(mutation=mutation), self.assertRaises(state_tool.StateError): self.capture(paths)
            self.assertEqual(state_tool.read_journal(self.directory), [])

    def test_native_resolution_alias_preserves_reason_and_closure_without_rewriting_source(self):
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_B)]})
        paths = self.files()
        data = json.loads(paths[1].read_text())
        result = data["entries"][0]["result"]
        result["resolutions"] = result.pop("resolvedFindings")
        result["resolutions"][0]["reason"] = result["resolutions"][0].pop("resolution")
        paths[1].write_text(json.dumps(data))
        self.capture(paths)
        summary = render.native_review_summaries(state_tool.show(self.directory))["work-a"]
        self.assertEqual(summary["roundDetail"][0]["raised"][0]["state"], "resolved")
        self.assertIn("Original reviewer", summary["roundDetail"][1]["settled"][0]["resolution"])

    def test_malformed_json_and_unpaired_unicode_refuse_cleanly(self):
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_B)]})
        for text in ('{"round":1,"round":2}', '{"round":', '{"x":"\\ud800"}'):
            paths = self.files()
            paths[0].write_text(text)
            with self.subTest(text=text), self.assertRaises(state_tool.StateError): self.capture(paths)
            self.assertEqual(state_tool.read_journal(self.directory), [])
