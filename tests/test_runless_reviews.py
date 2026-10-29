"""Captured native reviews are observations, never fabricated product runs."""
import base64
import hashlib
import json
import re
import unittest
from unittest.mock import patch
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
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_A)]})
        self.assertTrue(state_tool.show(self.directory)["reviewEvidence"]["work-a"]["unavailable"])

    def test_runner_sync_onto_a_moved_base_keeps_evidence_for_the_reviewed_head(self):
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_B)]})
        self.capture()
        synced = "c" * 40
        state_tool.patch(self.directory, writer="runner", version=state_tool.read_state(self.directory)["version"],
                         selector="queue[subject=work-a]", changes={"syncedHead": synced})
        observation = state_tool.show(self.directory)["reviewEvidence"]["work-a"]
        self.assertNotIn("unavailable", observation)
        self.assertEqual(observation["candidateRef"], fixtures.HEAD_B)
        state_tool.patch(self.directory, writer="runner", version=state_tool.read_state(self.directory)["version"],
                         selector="queue[subject=work-a]", changes={"stage": "merged"})
        self.assertNotIn("unavailable", state_tool.show(self.directory)["reviewEvidence"]["work-a"])

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

    def test_reopened_or_unsubstantiated_deferred_findings_cannot_approve(self):
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_B)]})
        for state in ("open", "deferred"):
            paths = self.files()
            third = outcome(3, fixtures.HEAD_B, resolved=True)
            third["entries"][0]["result"]["resolvedFindings"][0]["state"] = state
            path = self.root / "round3.json"
            path.write_text(json.dumps(third))
            with self.subTest(state=state), self.assertRaises(state_tool.StateError): self.capture([*paths, path])
        paths = self.files()[:1]
        first = outcome(1, fixtures.HEAD_B, finding=True)
        first["outcome"] = first["entries"][0]["result"]["outcome"] = "aligned"
        first["entries"][0]["result"]["findings"][0]["state"] = "deferred"
        paths[0].write_text(json.dumps(first))
        with self.assertRaises(state_tool.StateError): self.capture(paths)
        first["entries"][0]["result"]["findings"][0].update(severity="P2", deferredIssue="FOLLOW-1")
        paths[0].write_text(json.dumps(first))
        self.capture(paths)
        self.assertEqual(render.native_review_summaries(state_tool.show(self.directory))["work-a"]["roundDetail"][0]["outcome"], "approve")

    def test_old_prefix_replay_cannot_replace_extended_history(self):
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_B)]})
        paths = self.files()
        paths[0].write_text(json.dumps(outcome(1, fixtures.HEAD_A)))
        state_tool.capture_review(self.directory, subject="work-a", candidate=fixtures.HEAD_A, outcomes=paths[:1])
        paths[1].write_text(json.dumps(outcome(2, fixtures.HEAD_B, finding=True)))
        extended = self.capture(paths)
        replay = state_tool.capture_review(self.directory, subject="work-a", candidate=fixtures.HEAD_A, outcomes=paths[:1])
        self.assertEqual(replay, extended)
        self.assertEqual(len(state_tool.read_journal(self.directory)), 2)
        self.assertEqual(len(state_tool.show(self.directory)["reviewEvidence"]["work-a"]["outcomes"]), 2)
        paths[0].write_text(json.dumps(outcome(1, fixtures.HEAD_A), indent=2))
        with self.assertRaises(state_tool.StateError): self.capture(paths)

    def test_failed_reads_count_toward_shared_budget(self):
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_B)]})
        self.capture()
        ref = state_tool.read_journal(self.directory)[-1]["reviewArtifact"]
        path = self.directory / "review-artifacts" / (ref["digest"] + ".json")
        valid_bytes = path.read_bytes()
        path.write_bytes(b"x" * (state_tool.REVIEW_ARTIFACT_LIMIT + 1))
        state = {"queue": [fixtures.queue_item(s, head=fixtures.HEAD_B) for s in ("a", "b", "c", "d")]}
        journal = [{"kind":"review.evidence", "subject":s, "id":i+1, "reviewArtifact":ref} for i,s in enumerate(("a", "b", "c", "d"))]
        reads, original = [], Path.open
        class Meter:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def read(self, count):
                data = self.stream.read(count); reads.append(len(data)); return data
        with patch.object(Path, "open", lambda p,*a,**kw: Meter(original(p,*a,**kw))):
            observed = state_tool.review_observations(self.directory, state, journal)
        self.assertLessEqual(sum(reads), state_tool.REVIEW_READ_BUDGET)
        self.assertTrue(all(row["unavailable"] for row in observed.values()))
        bad_ref = {**ref, "digest":"0" * 64}
        bad_path = path.parent / (bad_ref["digest"] + ".json")
        bad_path.write_bytes(path.read_bytes())
        path.write_bytes(valid_bytes)
        state["queue"].insert(0, fixtures.queue_item("work-a", head=fixtures.HEAD_B))
        for entry in journal: entry["reviewArtifact"] = bad_ref
        journal.insert(0, {"kind":"review.evidence","subject":"work-a","id":5,"reviewArtifact":ref})
        reads.clear()
        with patch.object(Path, "open", lambda p,*a,**kw: Meter(original(p,*a,**kw))):
            observed = state_tool.review_observations(self.directory, state, journal)
        self.assertLessEqual(sum(reads), state_tool.REVIEW_READ_BUDGET)
        self.assertIn("outcomes", observed["work-a"])
        self.assertTrue(all(observed[s]["unavailable"] for s in ("a", "b", "c", "d")))

    def test_later_journal_review_invalidates_captured_observation(self):
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_B)]})
        self.capture()
        journal = state_tool.read_journal(self.directory)
        journal.append({"id":journal[-1]["id"]+1,"kind":"review.round","subject":"work-a"})
        result = state_tool.review_observations(self.directory, state_tool.read_state(self.directory), journal)
        self.assertTrue(result["work-a"]["unavailable"])

    def test_malformed_renderer_observation_is_contained_per_subject(self):
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_B)]})
        self.capture()
        shown = state_tool.show(self.directory)
        valid = shown["reviewEvidence"]["work-a"]
        for broken in (None, {}, {**valid,"outcomes":None}, {**valid,"outcomes":[{}]},
                       {**valid,"digest":[]}, {**valid,"candidateRef":{}}, {**valid,"outcomes":[]}):
            result = render.native_review_summaries({**shown,"reviewEvidence":{"bad":broken,"work-a":valid}})
            self.assertTrue(result["bad"]["unavailable"])
            self.assertEqual(result["work-a"]["roundDetail"][-1]["outcome"], "approve")

    def test_native_and_product_cards_share_one_page_budget(self):
        self.lead({"queue": [fixtures.queue_item("work-a", head=fixtures.HEAD_B)]})
        self.capture()
        shown = state_tool.show(self.directory)
        shown["state"]["queue"].insert(0, fixtures.queue_item("product", runId="run-1"))
        runs = {"run-1":{"runId":"run-1", "roundDetail":[], "description":"x" * 8000}}
        config = render.views_common.validate_config({"spec":"delivery-views-config/1"})
        combined_size = len(render.embed(runs).encode()) + len(render.embed(render.native_review_summaries(shown)).encode())
        low_budget = combined_size - 100
        for budget in (low_budget, combined_size + 100):
            with patch.object(render, "PAGE_BUDGET_BYTES", budget):
                page = render.render_line_map(shown, runs, config, theme_key="test", history=None)
            blocks = [re.search(f'id="delivery-{name}">(.*?)</script>', page).group(1) for name in ("runs", "reviews")]
            self.assertLessEqual(sum(len(block.encode()) for block in blocks), budget)
            product, native = [json.loads(block) for block in blocks]
            self.assertEqual(bool(product["run-1"].get("unavailable")), budget == low_budget)
            self.assertEqual(native["work-a"]["roundDetail"][-1]["outcome"], "approve")
