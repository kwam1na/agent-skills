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


if __name__ == "__main__":
    unittest.main()
