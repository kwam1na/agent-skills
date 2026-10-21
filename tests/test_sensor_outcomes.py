"""Credential-gated delivery evidence must remain honest through review."""
from dataclasses import replace
import unittest
from unittest.mock import Mock

from agent_skills import workflows as workflow


class SensorOutcomeTests(unittest.TestCase):
    def plan(self, sensor):
        return workflow.plan_work(workflow.PlanningRequest(
            outcome="repair", scope=("repair",), finish_line=("repair delivered",),
            test_scenarios=("repair works",), repository_sensors=(sensor,)))

    def execute(self, sensor, results=None):
        return workflow.execute_work(workflow.ExecutionRequest(
            plan=self.plan(sensor), completed_work=("repair",),
            completed_finish_line=("repair delivered",), sensor_results=results or {}))

    def test_unavailable_credential_sensor_completes_with_explicit_gap(self):
        sensor = workflow.Sensor("integration", needs_credentials=True)
        run = Mock(return_value=True)
        outcome = workflow.run_sensor(sensor, run)
        run.assert_not_called()
        self.assertEqual(outcome, workflow.SensorResult("skipped", "needs credentials"))
        result = self.execute(sensor)
        self.assertEqual(result.status, "complete")
        self.assertEqual(dict(result.sensor_results)["integration"], outcome)
        self.assertIn("not verified: integration (needs credentials)", result.handoffs)

    def test_declared_credential_runs_and_preserves_pass_or_fail(self):
        sensor = workflow.Sensor("integration", needs_credentials=True, credential_available=True)
        for passed in (True, False):
            with self.subTest(passed=passed):
                run = Mock(return_value=passed)
                self.assertIs(workflow.run_sensor(sensor, run), passed)
                run.assert_called_once_with()
                self.assertEqual(self.execute(sensor, {sensor.name: passed}).status,
                                 "complete" if passed else "blocked")

    def test_unmarked_environmental_failure_remains_failed(self):
        sensor = workflow.Sensor("integration")
        self.assertEqual(self.execute(sensor, {sensor.name: False}).status, "blocked")
        with self.assertRaisesRegex(ValueError, "explicit"):
            self.execute(sensor, {sensor.name: workflow.SensorResult("skipped", "needs credentials")})

    def test_declared_service_and_time_budget_skips_retain_reasons(self):
        for reason in ("needs external service", "exceeds time budget"):
            sensor = workflow.Sensor("integration", skip_reason=reason)
            run = Mock()
            self.assertEqual(workflow.run_sensor(sensor, run).reason, reason)
            run.assert_not_called()
            self.assertIn(reason, self.execute(sensor).handoffs[-1])

    def test_unavailable_credential_cannot_be_reported_passed(self):
        with self.assertRaisesRegex(ValueError, "cannot report"):
            self.execute(workflow.Sensor("integration", needs_credentials=True), {"integration": True})

    def test_review_exposes_missing_evidence_and_detects_relabel(self):
        delivery = self.execute(workflow.Sensor("integration", needs_credentials=True))
        aligned = workflow.ReviewLensResult("correctness", "aligned", evidence=("diff inspected",))
        review = workflow.review_work(workflow.ReviewRequest(
            required_lenses=("correctness",), rounds=((aligned,),), max_rounds=2,
            sensor_evidence=delivery.sensor_results))
        self.assertEqual(review.status, "blocked")
        self.assertIn("sensor evidence missing: integration (needs credentials)", review.blockers)
        with self.assertRaisesRegex(ValueError, "mismatch"):
            workflow.review_work(workflow.ReviewRequest(
                required_lenses=("correctness",), rounds=((aligned,),), max_rounds=2,
                sensor_evidence=delivery.sensor_results, reported_sensor_results={"integration": True}))

    def test_skip_does_not_satisfy_an_unmet_finish_line_or_approval(self):
        plan = self.plan(workflow.Sensor("integration", needs_credentials=True))
        incomplete = workflow.execute_work(workflow.ExecutionRequest(
            plan=plan, completed_work=("repair",)))
        self.assertEqual(incomplete.status, "partial")
        approval = workflow.execute_work(workflow.ExecutionRequest(
            plan=replace(plan, approval_handoffs=("operator",)),
            completed_work=("repair",), completed_finish_line=plan.finish_line))
        self.assertEqual(approval.status, "blocked")

    def test_review_without_rounds_still_names_skipped_evidence(self):
        delivery = self.execute(workflow.Sensor("integration", needs_credentials=True))
        result = workflow.review_work(workflow.ReviewRequest(
            required_lenses=("correctness",), rounds=(), max_rounds=2,
            sensor_evidence=delivery.sensor_results))
        self.assertIn("sensor evidence missing: integration (needs credentials)", result.blockers)
