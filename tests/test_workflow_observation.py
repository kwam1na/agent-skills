from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/execute-work/scripts/report_observation.py"
SPEC = importlib.util.spec_from_file_location("report_observation", SCRIPT)
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)


class WorkflowObservationTests(unittest.TestCase):
    def test_unsupported_runtime_and_legacy_run_write_nothing(self):
        for responses in [
            [(2, {})],
            [(0, {"spec": "run-capabilities/1", "writerVersions": ["run-event/1"], "artifactCapture": False})],
            [(0, self.capabilities()), (0, {"spec": "delivery-run-export/1", "runId": "run-test"})],
        ]:
            with self.subTest(responses=responses), patch.object(reporter, "invoke", side_effect=responses) as invoke:
                result = reporter.report(self.request())
                self.assertEqual(result["status"], "unsupported")
                self.assertTrue(all(call.args[0][2] == "runs" for call in invoke.call_args_list))

    @staticmethod
    def capabilities():
        return {"spec": "run-capabilities/1", "writerVersions": ["run-event/1", "run-event/2"], "artifactCapture": True}

    @staticmethod
    def request():
        return {"runtime": ["python", "runtime.py"], "cwd": "/repo", "runId": "run-test", "operation": "emit",
                "eventId": "attempt-start", "kind": "activity.observed", "payload": {"activityId": "review", "attemptId": "attempt-1",
                "candidateTreeSha": "a" * 40, "roundId": "round-1", "round": 1, "lensId": "correctness", "state": "running", "owner": "reviewer", "phase": "review"}}

    def test_both_hosts_forward_identical_supplied_bindings_without_orchestration(self):
        calls = []
        for host in ("codex", "claude-code"):
            with self.subTest(host=host), patch.object(reporter, "invoke", side_effect=[
                (0, self.capabilities()), (0, {"spec": "delivery-run-export/2", "runId": "run-test"}), (0, {})
            ]) as invoke:
                request = self.request()
                self.assertEqual(reporter.report(request)["status"], "reported")
                calls.append(invoke.call_args_list[-1].args[0])
                self.assertEqual(json.loads(calls[-1][-1]), request["payload"])
        self.assertEqual(calls[0], calls[1])

    def test_capture_uses_selected_request_and_failure_is_explicit(self):
        request = self.request()
        request.update(operation="capture", request={"sourceRoot": "/scratch", "sourcePath": "dissent.json", "eventId": "report-1",
                        "artifact": {"artifactId": "artifact-1"}, "report": {"reportId": "report-1", "role": "review"}})
        with patch.object(reporter, "invoke", side_effect=[(0, self.capabilities()),
                (0, {"spec": "delivery-run-export/2", "runId": "run-test"}), (1, {})]) as invoke:
            self.assertEqual(reporter.report(request)["status"], "unavailable")
            self.assertEqual(invoke.call_args_list[-1].args[0][2:5], ["runs", "capture", "run-test"])
            self.assertEqual(json.loads(invoke.call_args_list[-1].args[0][-1]), request["request"])

    def test_lost_runtime_is_not_an_authority_failure(self):
        with patch.object(reporter, "invoke", side_effect=OSError("unavailable")):
            self.assertEqual(reporter.report(self.request())["status"], "unavailable")

    def test_scenarios_preserve_rounds_partial_results_and_tracking_next_step(self):
        binding = {key: value for key, value in self.request()["payload"].items() if key not in ("owner", "state", "phase")}
        observations = [
            ("review.round.opened", {"round": 4, "roundId": "round-4", "bound": 4, "grace": True, "reopensRoundId": "round-3", "candidateTreeSha": "a" * 40, "lenses": ["correctness"]}),
            *[("activity.observed", {**binding, "state": state, "owner": "reviewer", "phase": "review"}) for state in ("queued", "running", "waiting", "completed", "failed", "interrupted")],
            ("wait.started", {**{key: binding[key] for key in ("activityId", "attemptId", "candidateTreeSha")}, "waitId": "wait-1", "owner": "operator", "waitingOn": "human", "reason": "native signal", "nextAction": "answer native prompt", "scope": "this attempt only"}),
            ("review.round.closed", {"round": 4, "roundId": "round-4", "candidateTreeSha": "a" * 40, "outcome": "unresolved", "findings": {"P0": 0, "P1": 1, "P2": 0, "P3": 0}, "cost": {"unit": "tokens", "total": 123, "coverage": "partial", "reportedBy": "host"}}),
            ("report.referenced", {**binding, "reportId": "partial-1", "role": "partial-output", "availability": "unavailable", "reason": "host returned no selected bytes"}),
            ("activity.observed", {"activityId": "tracking", "attemptId": "tracking-1", "candidateTreeSha": "a" * 40, "state": "running", "owner": "executor", "phase": "tracking", "nextStep": "Verify the filed deferral references; reviewers have completed."}),
        ]
        for host in ("codex", "claude-code"):
            for index, (kind, payload) in enumerate(observations):
                with self.subTest(host=host, kind=kind, index=index), patch.object(reporter, "invoke", side_effect=[
                    (0, self.capabilities()), (0, {"spec": "delivery-run-export/2", "runId": "run-test"}), (0, {})
                ]) as invoke:
                    request = {**self.request(), "eventId": f"event-{index}", "kind": kind, "payload": payload}
                    self.assertEqual(reporter.report(request)["status"], "reported")
                    self.assertEqual(json.loads(invoke.call_args_list[-1].args[0][-1]), payload)

    def test_capture_all_selected_output_roles_without_deleting_source(self):
        with tempfile.TemporaryDirectory() as scratch:
            selected = Path(scratch) / "result.json"
            selected.write_text('{"verdict":"changes-requested","actual":true}')
            for role in ("review", "reduction", "clarification", "partial-output"):
                request = {**self.request(), "operation": "capture", "request": {
                    "sourceRoot": scratch, "sourcePath": "result.json", "eventId": role,
                    "artifact": {"artifactId": role, "producer": "host"},
                    "report": {"reportId": role, "role": role}}}
                with patch.object(reporter, "invoke", side_effect=[(0, self.capabilities()),
                        (0, {"spec": "delivery-run-export/2", "runId": "run-test"}), (0, {})]) as invoke:
                    self.assertEqual(reporter.report(request)["status"], "reported")
                    self.assertEqual(json.loads(invoke.call_args_list[-1].args[0][-1]), request["request"])
                    self.assertTrue(selected.exists())

    def test_shipped_workflows_share_contract_and_do_not_require_python(self):
        contract = (ROOT / "skills/execute-work/references/run-observation-contract.md").read_text()
        for phrase in ("runs capabilities --json", "delivery-run-export/2", "Python is\nnot a prerequisite", "before cleanup", "Late-finding counts", "never reusable grants"):
            self.assertIn(phrase, contract)
        for skill in ("execute-work", "obtain-review", "review-work"):
            self.assertIn("run-observation-contract.md", (ROOT / f"skills/{skill}/SKILL.md").read_text())
