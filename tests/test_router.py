from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_skills.router import WorkflowRequest, route_workflow


ROOT = Path(__file__).resolve().parents[1]


class RouterTests(unittest.TestCase):
    def test_pinned_intents_route_deterministically(self) -> None:
        fixture = json.loads(
            (ROOT / "tests/scenarios/core/routing.json").read_text(encoding="utf-8")
        )
        for scenario in fixture["scenarios"]:
            with self.subTest(request=scenario["request"]):
                first = route_workflow(WorkflowRequest(scenario["request"]))
                second = route_workflow(WorkflowRequest(f"  {scenario['request'].upper()}  "))
                self.assertEqual(first, second)
                self.assertEqual(first.entry_point, scenario["expectedEntryPoint"])
                self.assertEqual(first.workflow.value, scenario["expectedWorkflow"])

    def test_optional_absent_capability_uses_complete_core_path(self) -> None:
        decision = route_workflow(
            WorkflowRequest(
                "Implement the bounded parser change",
                optional_capabilities={"tracker": "absent"},
            )
        )
        self.assertEqual(decision.route_kind.value, "degraded")
        self.assertEqual(decision.unavailable_capabilities, ("tracker",))
        self.assertEqual(decision.action, "Continue without optional tracking.")

    def test_required_blocked_capability_returns_named_handoff(self) -> None:
        decision = route_workflow(
            WorkflowRequest(
                "Implement the bounded parser change",
                required_capabilities={"tracker": "blocked"},
            )
        )
        self.assertEqual(decision.route_kind.value, "handoff")
        self.assertEqual(decision.unavailable_capabilities, ("tracker",))
        self.assertEqual(decision.action, "Resolve the tracker blocker, then resume delivery.")

    def test_capability_order_does_not_change_normalized_decision(self) -> None:
        first = route_workflow(
            WorkflowRequest(
                "Fix the parser",
                optional_capabilities={"review": "available", "tracker": "absent"},
            )
        )
        second = route_workflow(
            WorkflowRequest(
                "Fix the parser",
                optional_capabilities={"tracker": "absent", "review": "available"},
            )
        )
        self.assertEqual(first, second)

    def test_repository_workflow_overrides_host_hint_and_request(self) -> None:
        fixture = json.loads(
            (ROOT / "tests/scenarios/core/repository-discovery.json").read_text(
                encoding="utf-8"
            )
        )
        for scenario in fixture["scenarios"]:
            with self.subTest(scenario=scenario):
                decision = route_workflow(
                    WorkflowRequest(
                        scenario["request"],
                        host_workflow_hint=scenario.get("hostWorkflowHint"),
                        repository_workflow=scenario.get("repositoryWorkflow"),
                    )
                )
                self.assertEqual(decision.workflow.value, scenario["expectedWorkflow"])

    def test_invalid_repository_workflow_clarifies_actionably(self) -> None:
        decision = route_workflow(
            WorkflowRequest(
                "Implement the bounded parser change",
                host_workflow_hint="review",
                repository_workflow="invent-new-workflow",
            )
        )
        self.assertEqual(decision.workflow.value, "clarify")
        self.assertEqual(decision.route_kind.value, "handoff")
        self.assertEqual(
            decision.action,
            "Choose a declared repository workflow, then resume delivery.",
        )

    def test_orchestration_routes_to_its_workflow(self) -> None:
        decision = route_workflow(WorkflowRequest("Deliver this epic"))
        self.assertEqual(decision.workflow.value, "orchestrate")
        self.assertEqual(decision.entry_point, "deliver-work")
        self.assertEqual(decision.workflow_skill, "orchestrate-work")

    def test_orchestration_is_a_declared_workflow(self) -> None:
        for request in (
            WorkflowRequest("Implement the change", repository_workflow="orchestrate"),
            WorkflowRequest("Implement the change", host_workflow_hint="orchestrate"),
        ):
            with self.subTest(request=request):
                decision = route_workflow(request)
                self.assertEqual(decision.workflow.value, "orchestrate")
                self.assertEqual(decision.workflow_skill, "orchestrate-work")

    def test_an_unknown_named_workflow_still_clarifies(self) -> None:
        decision = route_workflow(
            WorkflowRequest("Deliver this epic", repository_workflow="orchestration")
        )
        self.assertEqual(decision.workflow.value, "clarify")
        self.assertEqual(decision.route_kind.value, "handoff")

    def test_invalid_host_workflow_hint_clarifies_actionably(self) -> None:
        decision = route_workflow(
            WorkflowRequest(
                "Implement the bounded parser change",
                host_workflow_hint="invent-new-workflow",
            )
        )
        self.assertEqual(decision.workflow.value, "clarify")
        self.assertEqual(decision.route_kind.value, "handoff")
        self.assertEqual(
            decision.action,
            "Choose a declared host workflow hint, then resume delivery.",
        )


if __name__ == "__main__":
    unittest.main()
