from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from agent_skills.capabilities import (
    AdapterOutcome,
    CapabilityState,
    MUTATING_OPERATIONS,
    OutcomeKind,
    TrackerRequest,
    TrackerOperation,
    execute_tracker_operation,
    work_snapshot,
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

    def test_configured_state_without_adapter_is_malformed_not_an_exception(self) -> None:
        outcome = execute_tracker_operation(
            CapabilityState.CONFIGURED,
            TrackerRequest(TrackerOperation.RESOLVE_CONTEXT, {}),
            None,
        )

        self.assertEqual(outcome.kind, OutcomeKind.MALFORMED)
        self.assertEqual(
            outcome.action,
            "Provide the configured tracker adapter, then retry.",
        )

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

    def test_non_mutating_idempotency_key_does_not_cache_result(self) -> None:
        request = TrackerRequest(
            TrackerOperation.RESOLVE_CONTEXT,
            {"query": "current context"},
            idempotency_key="ignored-read-key",
        )
        first_expected = AdapterOutcome.success(
            request.operation,
            {"reference": "context-one"},
        )
        second_expected = AdapterOutcome.success(
            request.operation,
            {"reference": "context-two"},
        )
        adapter = InMemoryTrackerAdapter(
            scripted={request.operation: [first_expected, second_expected]}
        )
        first = execute_tracker_operation(CapabilityState.CONFIGURED, request, adapter)
        second = execute_tracker_operation(CapabilityState.CONFIGURED, request, adapter)
        self.assertEqual(first, first_expected)
        self.assertEqual(second, second_expected)
        self.assertEqual(adapter.mutations, [])
        self.assertEqual(adapter.scripted[request.operation], [])



CHILDREN = [
    {"workReference": "work-1", "title": "Plan the work", "stateType": "completed", "blockedBy": []},
    {"workReference": "work-2", "title": "Build it", "stateType": "started", "blockedBy": ["work-1"]},
    {"workReference": "work-3", "title": "Later work", "stateType": "backlog", "blockedBy": []},
    {"workReference": "work-4", "title": "Dropped work", "stateType": "canceled", "blockedBy": []},
]


class ListWorkTests(unittest.TestCase):
    """list-work reads a parent's child items, normalized so they write out as a tracker snapshot."""

    def request(self, parent: str = "epic-1") -> TrackerRequest:
        return TrackerRequest(TrackerOperation.LIST_WORK, {"parentReference": parent})

    def test_list_work_is_read_only_and_needs_no_idempotency_key(self) -> None:
        self.assertNotIn(TrackerOperation.LIST_WORK, MUTATING_OPERATIONS)
        adapter = InMemoryTrackerAdapter(work={"epic-1": CHILDREN})
        outcome = execute_tracker_operation(CapabilityState.CONFIGURED, self.request(), adapter)
        self.assertEqual(outcome.kind, OutcomeKind.SUCCESS)
        self.assertEqual(outcome.data, {"parentReference": "epic-1", "workItems": CHILDREN})
        self.assertEqual(adapter.mutations, [])

    def test_its_outcome_writes_out_as_a_valid_tracker_snapshot(self) -> None:
        adapter = InMemoryTrackerAdapter(work={"epic-1": CHILDREN})
        outcome = execute_tracker_operation(CapabilityState.CONFIGURED, self.request(), adapter)
        snapshot = work_snapshot(outcome.data, "2026-10-21T05:00:00Z")
        schemas = json.loads((ROOT / "skills/orchestrate-work/references/schemas.json").read_text(encoding="utf-8"))
        Draft202012Validator(schemas["work-snapshot/1"]).validate(snapshot)
        self.assertEqual(snapshot["source"], "tracker")
        self.assertEqual([(item["subject"], item["state"]) for item in snapshot["items"]], [
            ("work-1", "completed"), ("work-2", "started"), ("work-3", "backlog"), ("work-4", "canceled")])
        self.assertEqual(snapshot["items"][1]["blockedBy"], ["work-1"])
        self.assertNotIn("blockedBy", snapshot["items"][0], "an item nothing blocks carries no blockers")

    def test_a_result_of_any_other_shape_is_malformed(self) -> None:
        good = {"parentReference": "epic-1", "workItems": CHILDREN}
        item = CHILDREN[0]
        cases = {
            "no parent": {"workItems": CHILDREN},
            "items not a list": {**good, "workItems": {"work-1": item}},
            "an unknown state type": {**good, "workItems": [{**item, "stateType": "triage"}]},
            "an extra field": {**good, "workItems": [{**item, "rawHostPayload": {}}]},
            "a missing reference": {**good, "workItems": [{**item, "workReference": ""}]},
            "a reference twice": {**good, "workItems": [item, item]},
            "no title": {**good, "workItems": [{key: value for key, value in item.items() if key != "title"}]},
            "blockers not references": {**good, "workItems": [{**item, "blockedBy": [3]}]},
            "an empty blocker": {**good, "workItems": [{**item, "blockedBy": [""]}]},
            "a field beside the work": {**good, "rawHostPayload": {}},
            "another parent's work": {**good, "parentReference": "epic-2"},
        }
        for name, data in cases.items():
            with self.subTest(name):
                adapter = InMemoryTrackerAdapter(
                    scripted={TrackerOperation.LIST_WORK: [AdapterOutcome.success(TrackerOperation.LIST_WORK, data)]})
                outcome = execute_tracker_operation(CapabilityState.CONFIGURED, self.request(), adapter)
                self.assertEqual(outcome.kind, OutcomeKind.MALFORMED)
                self.assertTrue(outcome.action)

    def test_without_the_capability_the_listing_is_unavailable_or_unsupported(self) -> None:
        for state in (CapabilityState.ABSENT, CapabilityState.AVAILABLE, CapabilityState.BLOCKED):
            with self.subTest(state=state):
                outcome = execute_tracker_operation(state, self.request(), InMemoryTrackerAdapter())
                self.assertEqual(outcome.kind, OutcomeKind.UNAVAILABLE)
                self.assertTrue(outcome.action)
        adapter = InMemoryTrackerAdapter(supported_operations=set(TrackerOperation) - {TrackerOperation.LIST_WORK})
        outcome = execute_tracker_operation(CapabilityState.CONFIGURED, self.request(), adapter)
        self.assertEqual(outcome.kind, OutcomeKind.UNSUPPORTED)

    def test_an_audit_reference_may_accompany_the_work(self) -> None:
        data = {"parentReference": "epic-1", "workItems": CHILDREN, "auditReference": "audit-1"}
        adapter = InMemoryTrackerAdapter(
            scripted={TrackerOperation.LIST_WORK: [AdapterOutcome.success(TrackerOperation.LIST_WORK, data)]})
        outcome = execute_tracker_operation(CapabilityState.CONFIGURED, self.request(), adapter)
        self.assertEqual(outcome.kind, OutcomeKind.SUCCESS)

    def test_each_parent_lists_its_own_children(self) -> None:
        adapter = InMemoryTrackerAdapter(work={"epic-1": CHILDREN[:1], "epic-2": CHILDREN[1:]})
        first = execute_tracker_operation(CapabilityState.CONFIGURED, self.request("epic-1"), adapter)
        second = execute_tracker_operation(CapabilityState.CONFIGURED, self.request("epic-2"), adapter)
        self.assertEqual([item["workReference"] for item in first.data["workItems"]], ["work-1"])
        self.assertEqual([item["workReference"] for item in second.data["workItems"]], ["work-2", "work-3", "work-4"])

    def test_a_parent_with_no_children_lists_nothing(self) -> None:
        outcome = execute_tracker_operation(CapabilityState.CONFIGURED, self.request("epic-2"), InMemoryTrackerAdapter())
        self.assertEqual((outcome.kind, outcome.data["workItems"]), (OutcomeKind.SUCCESS, []))
        self.assertEqual(work_snapshot(outcome.data, "2026-10-21T05:00:00Z")["items"], [])

if __name__ == "__main__":
    unittest.main()
