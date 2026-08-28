from __future__ import annotations

from typing import Iterable, Mapping

from agent_skills.capabilities import (
    AdapterOutcome,
    MUTATING_OPERATIONS,
    OutcomeKind,
    TrackerOperation,
    TrackerRequest,
)


class InMemoryTrackerAdapter:
    def __init__(
        self,
        *,
        supported_operations: Iterable[TrackerOperation] = tuple(TrackerOperation),
        scripted: Mapping[TrackerOperation, list[object]] | None = None,
    ) -> None:
        self.supported_operations = frozenset(supported_operations)
        self.scripted = {
            operation: list(outcomes)
            for operation, outcomes in (scripted or {}).items()
        }
        self.mutations: list[TrackerRequest] = []
        self._completed: dict[tuple[TrackerOperation, str], AdapterOutcome] = {}

    def _complete(
        self,
        request: TrackerRequest,
        key: tuple[TrackerOperation, str] | None,
        outcome: AdapterOutcome,
    ) -> AdapterOutcome:
        if request.operation in MUTATING_OPERATIONS:
            self.mutations.append(request)
        if key is not None:
            self._completed[key] = outcome
        return outcome

    def execute(self, request: TrackerRequest) -> object:
        if request.operation not in self.supported_operations:
            return AdapterOutcome.unsupported(
                request.operation,
                "Choose an operation supported by the configured tracker capability.",
            )
        key = (
            (request.operation, request.idempotency_key)
            if request.operation in MUTATING_OPERATIONS
            and request.idempotency_key is not None
            else None
        )
        if key is not None and key in self._completed:
            return self._completed[key]
        scripted = self.scripted.get(request.operation, [])
        if scripted:
            outcome = scripted.pop(0)
            if (
                isinstance(outcome, AdapterOutcome)
                and outcome.kind is OutcomeKind.SUCCESS
                and outcome.operation is request.operation
                and isinstance(outcome.data, Mapping)
            ):
                return self._complete(request, key, outcome)
            return outcome
        sequence = len(self.mutations) + 1
        outcome = AdapterOutcome.success(
            request.operation,
            {"operation": request.operation.value, "sequence": sequence},
        )
        return self._complete(request, key, outcome)
