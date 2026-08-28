from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Protocol


class CapabilityState(str, Enum):
    ABSENT = "absent"
    AVAILABLE = "available"
    CONFIGURED = "configured"
    BLOCKED = "blocked"


class TrackerOperation(str, Enum):
    RESOLVE_CONTEXT = "resolve-context"
    CREATE_WORK = "create-work"
    LINK_DEPENDENCIES = "link-dependencies"
    UPDATE_STATUS = "update-status"
    ATTACH_EVIDENCE = "attach-evidence"
    CLOSE_OR_HANDOFF = "close-or-handoff"


class OutcomeKind(str, Enum):
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    MALFORMED = "malformed"
    AUTH_REQUIRED = "auth-required"
    RETRY = "retry"
    RECONCILIATION_REQUIRED = "reconciliation-required"


MUTATING_OPERATIONS = frozenset(
    {
        TrackerOperation.CREATE_WORK,
        TrackerOperation.LINK_DEPENDENCIES,
        TrackerOperation.UPDATE_STATUS,
        TrackerOperation.ATTACH_EVIDENCE,
        TrackerOperation.CLOSE_OR_HANDOFF,
    }
)


@dataclass(frozen=True)
class TrackerRequest:
    operation: TrackerOperation
    payload: Mapping[str, object]
    idempotency_key: str | None = None


@dataclass(frozen=True)
class AdapterOutcome:
    operation: TrackerOperation
    kind: OutcomeKind
    action: str = ""
    data: Mapping[str, object] = field(default_factory=dict)
    retry_after_seconds: int | None = None
    reconciliation_key: str | None = None

    @classmethod
    def success(
        cls, operation: TrackerOperation, data: Mapping[str, object]
    ) -> AdapterOutcome:
        return cls(operation=operation, kind=OutcomeKind.SUCCESS, data=data)

    @classmethod
    def unsupported(cls, operation: TrackerOperation, action: str) -> AdapterOutcome:
        return cls(operation=operation, kind=OutcomeKind.UNSUPPORTED, action=action)

    @classmethod
    def auth_required(cls, operation: TrackerOperation, action: str) -> AdapterOutcome:
        return cls(operation=operation, kind=OutcomeKind.AUTH_REQUIRED, action=action)

    @classmethod
    def retry(
        cls,
        operation: TrackerOperation,
        action: str,
        *,
        retry_after_seconds: int,
    ) -> AdapterOutcome:
        return cls(
            operation=operation,
            kind=OutcomeKind.RETRY,
            action=action,
            retry_after_seconds=retry_after_seconds,
        )

    @classmethod
    def reconciliation_required(
        cls,
        operation: TrackerOperation,
        action: str,
        *,
        reconciliation_key: str,
    ) -> AdapterOutcome:
        return cls(
            operation=operation,
            kind=OutcomeKind.RECONCILIATION_REQUIRED,
            action=action,
            reconciliation_key=reconciliation_key,
        )


class TrackerAdapter(Protocol):
    def execute(self, request: TrackerRequest) -> object: ...


def _unavailable(state: CapabilityState, operation: TrackerOperation) -> AdapterOutcome:
    actions = {
        CapabilityState.ABSENT: "Continue without tracking or provide a tracker capability.",
        CapabilityState.AVAILABLE: "Configure the tracker capability, then retry.",
        CapabilityState.BLOCKED: "Resolve the tracker capability blocker, then retry.",
    }
    return AdapterOutcome(
        operation=operation,
        kind=OutcomeKind.UNAVAILABLE,
        action=actions[state],
    )


def _malformed(operation: TrackerOperation, action: str) -> AdapterOutcome:
    return AdapterOutcome(
        operation=operation,
        kind=OutcomeKind.MALFORMED,
        action=action,
    )


def _normalize_outcome(request: TrackerRequest, value: object) -> AdapterOutcome:
    if not isinstance(value, AdapterOutcome):
        return _malformed(
            request.operation,
            "Inspect the adapter result and return a declared outcome.",
        )
    if value.operation is not request.operation:
        return _malformed(
            request.operation,
            "Return an outcome for the requested operation.",
        )
    if not isinstance(value.kind, OutcomeKind):
        return _malformed(
            request.operation,
            "Return one of the declared outcome kinds.",
        )
    if value.kind is OutcomeKind.SUCCESS:
        if not isinstance(value.data, Mapping):
            return _malformed(
                request.operation,
                "Return success data as a mapping.",
            )
        return value
    if not isinstance(value.action, str) or not value.action.strip():
        return _malformed(
            request.operation,
            "Return an actionable instruction with a non-success outcome.",
        )
    if value.kind is OutcomeKind.RETRY and (
        type(value.retry_after_seconds) is not int
        or value.retry_after_seconds <= 0
    ):
        return _malformed(
            request.operation,
            "Return a positive retry delay with a retry outcome.",
        )
    if value.kind is OutcomeKind.RECONCILIATION_REQUIRED and not (
        isinstance(value.reconciliation_key, str)
        and value.reconciliation_key.strip()
    ):
        return _malformed(
            request.operation,
            "Return a reconciliation key before another mutation attempt.",
        )
    return value


def execute_tracker_operation(
    state: CapabilityState,
    request: TrackerRequest,
    adapter: TrackerAdapter | None,
) -> AdapterOutcome:
    if state is not CapabilityState.CONFIGURED:
        return _unavailable(state, request.operation)
    if adapter is None:
        return _malformed(
            request.operation,
            "Provide the configured tracker adapter, then retry.",
        )
    if request.operation in MUTATING_OPERATIONS and not (
        isinstance(request.idempotency_key, str) and request.idempotency_key.strip()
    ):
        return _malformed(
            request.operation,
            "Provide a stable idempotency key before the mutation.",
        )
    return _normalize_outcome(request, adapter.execute(request))
