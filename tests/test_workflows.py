from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_skills.capabilities import CapabilityState, OutcomeKind, TrackerOperation
from agent_skills.fake_tracker import InMemoryTrackerAdapter
from agent_skills.router import WorkflowRequest, route_workflow
from agent_skills.workflows import (
    ExecutionRequest,
    PlanningRequest,
    Sensor,
    execute_work,
    plan_work,
)


ROOT = Path(__file__).resolve().parents[1]


def _plan_fixture() -> dict[str, object]:
    return json.loads(
        (ROOT / "tests/scenarios/core/plan.json").read_text(encoding="utf-8")
    )


def _plan_from_fixture() -> object:
    request = _plan_fixture()["request"]
    assert isinstance(request, dict)
    return plan_work(
        PlanningRequest(
            outcome=request["outcome"],
            scope=tuple(request["scope"]),
            out_of_scope=tuple(request["outOfScope"]),
            finish_line=tuple(request["finishLine"]),
            dependencies=tuple(request["dependencies"]),
            test_scenarios=tuple(request["testScenarios"]),
            repository_sensors=tuple(
                Sensor(
                    name=sensor["name"],
                    required=sensor["required"],
                    available=sensor["available"],
                )
                for sensor in request["repositorySensors"]
            ),
            generic_sensors=tuple(request["genericSensors"]),
            approval_handoffs=tuple(request["approvalHandoffs"]),
            existing_behavior=request["existingBehavior"],
        )
    )


class PlanningWorkflowTests(unittest.TestCase):
    def test_characterized_plan_preserves_scope_posture_and_repository_sensors(self) -> None:
        fixture = _plan_fixture()
        expected = fixture["expected"]
        plan = _plan_from_fixture()

        self.assertEqual(plan.posture, expected["posture"])
        self.assertEqual(plan.out_of_scope, tuple(expected["outOfScope"]))
        self.assertEqual(plan.finish_line, tuple(expected["finishLine"]))
        self.assertEqual(plan.dependencies, tuple(expected["dependencies"]))
        self.assertEqual(plan.test_scenarios, tuple(expected["testScenarios"]))
        self.assertEqual(
            tuple(sensor.name for sensor in plan.sensors),
            tuple(expected["sensors"]),
        )
        self.assertEqual(plan.approval_handoffs, tuple(expected["approvalHandoffs"]))

    def test_same_facts_produce_the_same_normalized_plan(self) -> None:
        self.assertEqual(_plan_from_fixture(), _plan_from_fixture())

    def test_diagnosis_only_routes_without_execution_mutation(self) -> None:
        adapter = InMemoryTrackerAdapter()

        decision = route_workflow(WorkflowRequest("Diagnose the parser failure"))

        self.assertEqual(decision.workflow.value, "diagnose")
        self.assertFalse(adapter.mutations)

    def test_plan_only_returns_a_plan_without_tracker_mutation(self) -> None:
        adapter = InMemoryTrackerAdapter()
        plan = plan_work(
            PlanningRequest(
                outcome="bound the parser change",
                scope=("parser",),
                finish_line=("focused sensor passes",),
                test_scenarios=("bounded input is accepted",),
                repository_sensors=(Sensor("focused sensor"),),
                tracker_state=CapabilityState.CONFIGURED,
                tracker_adapter=adapter,
            )
        )

        self.assertFalse(plan.tracker_outcomes)
        self.assertFalse(adapter.mutations)

    def test_plan_without_tracker_completes_and_reports_selected_tracking_handoff(self) -> None:
        request = PlanningRequest(
            outcome="bound the parser change",
            scope=("parser",),
            finish_line=("focused sensor passes",),
            test_scenarios=("bounded input is accepted",),
            repository_sensors=(Sensor("focused sensor"),),
            tracking_selected=True,
            tracker_state=CapabilityState.ABSENT,
        )

        plan = plan_work(request)

        self.assertEqual(plan.outcome, request.outcome)
        self.assertEqual(len(plan.tracker_outcomes), 1)
        self.assertEqual(plan.tracker_outcomes[0].kind, OutcomeKind.UNAVAILABLE)
        self.assertIn("Continue without tracking", plan.tracker_outcomes[0].action)
        self.assertEqual(plan.handoffs, (plan.tracker_outcomes[0].action,))

    def test_configured_planning_uses_only_normalized_tracker_operations(self) -> None:
        adapter = InMemoryTrackerAdapter()
        plan = plan_work(
            PlanningRequest(
                outcome="bound the parser change",
                scope=("parser",),
                out_of_scope=("new parser framework",),
                finish_line=("focused sensor passes",),
                test_scenarios=("bounded input is accepted",),
                repository_sensors=(
                    Sensor("focused sensor"),
                    Sensor("optional review", required=False, available=False),
                ),
                dependencies=("characterization",),
                approval_handoffs=("maintainer approval",),
                tracking_selected=True,
                tracker_state=CapabilityState.CONFIGURED,
                tracker_adapter=adapter,
                tracking_key="parser-plan",
            )
        )

        self.assertTrue(plan.tracker_outcomes)
        self.assertTrue(
            all(outcome.kind is OutcomeKind.SUCCESS for outcome in plan.tracker_outcomes)
        )
        self.assertEqual(len(adapter.mutations), 2)
        self.assertEqual(
            tuple(request.operation for request in adapter.mutations),
            (
                TrackerOperation.CREATE_WORK,
                TrackerOperation.LINK_DEPENDENCIES,
            ),
        )
        self.assertEqual(
            adapter.mutations[0].payload,
            {
                "approvalHandoffs": ("maintainer approval",),
                "finishLine": ("focused sensor passes",),
                "outOfScope": ("new parser framework",),
                "outcome": "bound the parser change",
                "posture": "test-first",
                "scope": ("parser",),
                "sensors": (
                    {
                        "available": True,
                        "name": "focused sensor",
                        "required": True,
                    },
                    {
                        "available": False,
                        "name": "optional review",
                        "required": False,
                    },
                ),
                "testScenarios": ("bounded input is accepted",),
            },
        )
        self.assertEqual(
            adapter.mutations[1].payload,
            {"dependencies": ("characterization",)},
        )

    def test_unavailable_optional_sensor_is_reported_without_generic_replacement(self) -> None:
        plan = plan_work(
            PlanningRequest(
                outcome="bound the parser change",
                scope=("parser",),
                finish_line=("review evidence is retained",),
                test_scenarios=("review evidence remains inspectable",),
                repository_sensors=(
                    Sensor("repository review", required=False, available=False),
                ),
                generic_sensors=("generic review",),
            )
        )

        self.assertEqual(tuple(sensor.name for sensor in plan.sensors), ("repository review",))
        self.assertEqual(
            plan.handoffs,
            ("optional sensor unavailable: repository review",),
        )


class ExecutionWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = _plan_from_fixture()
        self.fixture = json.loads(
            (ROOT / "tests/scenarios/core/execute.json").read_text(encoding="utf-8")
        )

    def _execute(self, scenario_name: str):
        scenario = self.fixture[scenario_name]
        return execute_work(
            ExecutionRequest(
                plan=self.plan,
                completed_work=tuple(scenario["completedWork"]),
                completed_finish_line=tuple(scenario["completedFinishLine"]),
                completed_dependencies=tuple(scenario.get("completedDependencies", [])),
                sensor_results=scenario["sensors"],
                approved_handoffs=tuple(scenario["approvedHandoffs"]),
                evidence=tuple(scenario["evidence"]),
            )
        )

    def test_incomplete_dependency_blocks_execution(self) -> None:
        result = self._execute("blocked")
        self.assertEqual(result.status, self.fixture["blocked"]["expectedStatus"])
        self.assertEqual(result.blockers, tuple(self.fixture["blocked"]["expectedBlockers"]))

    def test_partial_progress_is_preserved_with_approval_handoff(self) -> None:
        result = self._execute("partial")
        self.assertEqual(result.status, self.fixture["partial"]["expectedStatus"])
        self.assertEqual(result.completed_work, ("bounded parser behavior",))
        self.assertEqual(result.evidence, ("focused parser test passed",))
        self.assertEqual(result.handoffs, tuple(self.fixture["partial"]["expectedHandoffs"]))

    def test_completion_requires_finish_line_sensors_and_approvals(self) -> None:
        result = self._execute("complete")
        self.assertEqual(result.status, self.fixture["complete"]["expectedStatus"])
        self.assertFalse(result.blockers)
        self.assertFalse(result.handoffs)

    def test_out_of_scope_completed_work_blocks_completion(self) -> None:
        complete = self.fixture["complete"]
        result = execute_work(
            ExecutionRequest(
                plan=self.plan,
                completed_work=(
                    *complete["completedWork"],
                    "new parser framework",
                ),
                completed_finish_line=tuple(complete["completedFinishLine"]),
                completed_dependencies=tuple(complete["completedDependencies"]),
                sensor_results=complete["sensors"],
                approved_handoffs=tuple(complete["approvedHandoffs"]),
                evidence=tuple(complete["evidence"]),
            )
        )

        self.assertEqual(result.status, "blocked")
        self.assertIn(
            "out-of-scope work reported complete: new parser framework",
            result.blockers,
        )

    def test_missing_required_sensor_prevents_completion(self) -> None:
        result = execute_work(
            ExecutionRequest(
                plan=self.plan,
                completed_work=self.plan.scope,
                completed_finish_line=self.plan.finish_line,
                completed_dependencies=self.plan.dependencies,
                sensor_results={"focused parser test": True},
                approved_handoffs=self.plan.approval_handoffs,
            )
        )
        self.assertEqual(result.status, "blocked")
        self.assertIn("required sensor incomplete: repository validation", result.blockers)

    def test_failed_required_sensor_blocks_partial_progress(self) -> None:
        result = execute_work(
            ExecutionRequest(
                plan=self.plan,
                completed_dependencies=self.plan.dependencies,
                completed_work=("bounded parser behavior",),
                sensor_results={"focused parser test": False},
                evidence=("focused parser test failed",),
            )
        )

        self.assertEqual(result.status, "blocked")
        self.assertIn("required sensor failed: focused parser test", result.blockers)

    def test_configured_execution_records_normalized_progress_and_evidence(self) -> None:
        adapter = InMemoryTrackerAdapter()
        complete = self.fixture["complete"]
        result = execute_work(
            ExecutionRequest(
                plan=self.plan,
                completed_work=tuple(complete["completedWork"]),
                completed_finish_line=tuple(complete["completedFinishLine"]),
                completed_dependencies=tuple(complete["completedDependencies"]),
                sensor_results=complete["sensors"],
                approved_handoffs=tuple(complete["approvedHandoffs"]),
                evidence=tuple(complete["evidence"]),
                tracking_selected=True,
                tracker_state=CapabilityState.CONFIGURED,
                tracker_adapter=adapter,
                tracking_key="parser-execution",
            )
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(
            result.sensor_results,
            (
                ("focused parser test", True),
                ("repository validation", True),
            ),
        )
        self.assertTrue(result.tracker_outcomes)
        self.assertTrue(
            all(outcome.kind is OutcomeKind.SUCCESS for outcome in result.tracker_outcomes)
        )
        self.assertEqual(len(adapter.mutations), 3)

    def test_execution_without_tracker_preserves_core_result_without_mutation(self) -> None:
        complete = self.fixture["complete"]
        result = execute_work(
            ExecutionRequest(
                plan=self.plan,
                completed_work=tuple(complete["completedWork"]),
                completed_finish_line=tuple(complete["completedFinishLine"]),
                completed_dependencies=tuple(complete["completedDependencies"]),
                sensor_results=complete["sensors"],
                approved_handoffs=tuple(complete["approvedHandoffs"]),
                evidence=tuple(complete["evidence"]),
                tracking_selected=True,
                tracker_state=CapabilityState.ABSENT,
            )
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(len(result.tracker_outcomes), 1)
        self.assertEqual(result.tracker_outcomes[0].kind, OutcomeKind.UNAVAILABLE)
        self.assertEqual(result.handoffs, (result.tracker_outcomes[0].action,))


if __name__ == "__main__":
    unittest.main()
