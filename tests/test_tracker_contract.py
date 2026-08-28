from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_skills.capabilities import (
    AdapterOutcome,
    CapabilityState,
    OutcomeKind,
    TrackerRequest,
    TrackerOperation,
    execute_tracker_operation,
)
from agent_skills.fake_tracker import InMemoryTrackerAdapter


ROOT = Path(__file__).resolve().parents[1]


class TrackerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = json.loads(
            (ROOT / "tests/scenarios/core/tracker-capability.json").read_text(
                encoding="utf-8"
            )
        )
        self.fixture = fixture

    def test_fixture_covers_every_operation_state_and_outcome(self) -> None:
        self.assertEqual(
            set(self.fixture["operations"]), {item.value for item in TrackerOperation}
        )
        self.assertEqual(
            set(self.fixture["states"]), {item.value for item in CapabilityState}
        )
        self.assertEqual(
            set(self.fixture["outcomes"]), {item.value for item in OutcomeKind}
        )

    def test_fake_adapter_executes_every_operation(self) -> None:
        adapter = InMemoryTrackerAdapter()
        for index, operation in enumerate(TrackerOperation):
            with self.subTest(operation=operation):
                request = TrackerRequest(
                    operation=operation,
                    payload={"sequence": index},
                    idempotency_key=f"request-{index}",
                )
                outcome = execute_tracker_operation(
                    CapabilityState.CONFIGURED, request, adapter
                )
                self.assertEqual(outcome.kind, OutcomeKind.SUCCESS)
                self.assertEqual(outcome.operation, operation)

    def test_missing_capability_is_typed_and_actionable(self) -> None:
        request = TrackerRequest(TrackerOperation.RESOLVE_CONTEXT, {})
        for state in (
            CapabilityState.ABSENT,
            CapabilityState.AVAILABLE,
            CapabilityState.BLOCKED,
        ):
            with self.subTest(state=state):
                outcome = execute_tracker_operation(
                    state, request, InMemoryTrackerAdapter()
                )
                self.assertEqual(outcome.kind, OutcomeKind.UNAVAILABLE)
                self.assertTrue(outcome.action)

    def test_scripted_failure_paths_remain_typed_and_actionable(self) -> None:
        request = TrackerRequest(
            TrackerOperation.CREATE_WORK,
            {"title": "Bounded work"},
            idempotency_key="create-one",
        )
        cases = (
            AdapterOutcome.unsupported(request.operation, "Choose a supported operation."),
            AdapterOutcome.auth_required(request.operation, "Restore access, then retry."),
            AdapterOutcome.retry(request.operation, "Retry later.", retry_after_seconds=2),
            AdapterOutcome.reconciliation_required(
                request.operation,
                "Reconcile the prior attempt before retrying.",
                reconciliation_key="attempt-one",
            ),
        )
        for expected in cases:
            with self.subTest(kind=expected.kind):
                adapter = InMemoryTrackerAdapter(scripted={request.operation: [expected]})
                self.assertEqual(
                    execute_tracker_operation(CapabilityState.CONFIGURED, request, adapter),
                    expected,
                )

    def test_malformed_adapter_result_is_normalized(self) -> None:
        request = TrackerRequest(TrackerOperation.RESOLVE_CONTEXT, {})
        adapter = InMemoryTrackerAdapter(
            scripted={request.operation: [{"unexpected": "shape"}]}
        )
        outcome = execute_tracker_operation(CapabilityState.CONFIGURED, request, adapter)
        self.assertEqual(outcome.kind, OutcomeKind.MALFORMED)
        self.assertTrue(outcome.action)

    def test_incomplete_typed_outcomes_are_normalized(self) -> None:
        request = TrackerRequest(TrackerOperation.RESOLVE_CONTEXT, {})
        cases = (
            AdapterOutcome(
                operation=TrackerOperation.CREATE_WORK,
                kind=OutcomeKind.SUCCESS,
            ),
            AdapterOutcome(
                operation=request.operation,
                kind="unknown",  # type: ignore[arg-type]
                action="Do something.",
            ),
            AdapterOutcome(
                operation=request.operation,
                kind=OutcomeKind.RETRY,
                action="Retry later.",
            ),
            AdapterOutcome(
                operation=request.operation,
                kind=OutcomeKind.UNSUPPORTED,
                action=None,  # type: ignore[arg-type]
            ),
            AdapterOutcome(
                operation=request.operation,
                kind=OutcomeKind.RECONCILIATION_REQUIRED,
                action="Reconcile first.",
            ),
        )
        for scripted in cases:
            with self.subTest(scripted=scripted):
                adapter = InMemoryTrackerAdapter(
                    scripted={request.operation: [scripted]}
                )
                outcome = execute_tracker_operation(
                    CapabilityState.CONFIGURED, request, adapter
                )
                self.assertEqual(outcome.kind, OutcomeKind.MALFORMED)

    def test_mutation_requires_idempotency_key(self) -> None:
        request = TrackerRequest(TrackerOperation.UPDATE_STATUS, {"state": "done"})
        outcome = execute_tracker_operation(
            CapabilityState.CONFIGURED, request, InMemoryTrackerAdapter()
        )
        self.assertEqual(outcome.kind, OutcomeKind.MALFORMED)
        self.assertIn("idempotency", outcome.action.lower())

    def test_retry_with_same_key_does_not_duplicate_mutation(self) -> None:
        request = TrackerRequest(
            TrackerOperation.CREATE_WORK,
            {"title": "Bounded work"},
            idempotency_key="create-one",
        )
        adapter = InMemoryTrackerAdapter()
        first = execute_tracker_operation(CapabilityState.CONFIGURED, request, adapter)
        second = execute_tracker_operation(CapabilityState.CONFIGURED, request, adapter)
        self.assertEqual(first, second)
        self.assertEqual(len(adapter.mutations), 1)

    def test_scripted_success_with_same_key_does_not_duplicate_mutation(self) -> None:
        request = TrackerRequest(
            TrackerOperation.CREATE_WORK,
            {"title": "Bounded work"},
            idempotency_key="scripted-create-one",
        )
        expected = AdapterOutcome.success(
            request.operation,
            {"operation": request.operation.value, "reference": "work-one"},
        )
        adapter = InMemoryTrackerAdapter(
            scripted={request.operation: [expected]}
        )
        first = execute_tracker_operation(CapabilityState.CONFIGURED, request, adapter)
        second = execute_tracker_operation(CapabilityState.CONFIGURED, request, adapter)
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertEqual(adapter.mutations, [request])

    def test_invalid_scripted_success_is_not_recorded_or_cached(self) -> None:
        request = TrackerRequest(
            TrackerOperation.CREATE_WORK,
            {"title": "Bounded work"},
            idempotency_key="invalid-scripted-create",
        )
        cases = (
            AdapterOutcome.success(
                TrackerOperation.RESOLVE_CONTEXT,
                {"reference": "wrong-operation"},
            ),
            AdapterOutcome.success(
                request.operation,
                ["invalid-data"],  # type: ignore[arg-type]
            ),
        )
        for invalid in cases:
            with self.subTest(invalid=invalid):
                adapter = InMemoryTrackerAdapter(
                    scripted={request.operation: [invalid, invalid]}
                )
                first = execute_tracker_operation(
                    CapabilityState.CONFIGURED, request, adapter
                )
                second = execute_tracker_operation(
                    CapabilityState.CONFIGURED, request, adapter
                )
                self.assertEqual(first.kind, OutcomeKind.MALFORMED)
                self.assertEqual(second.kind, OutcomeKind.MALFORMED)
                self.assertEqual(adapter.mutations, [])
                self.assertEqual(adapter.scripted[request.operation], [])


if __name__ == "__main__":
    unittest.main()
