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
    LIST_WORK = "list-work"


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


# The normalized state types a listed work item carries.
WORK_STATE_TYPES = ("backlog", "started", "completed", "canceled")


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
        if request.operation is TrackerOperation.LIST_WORK:
            problem = _work_list_problem(value.data, request.payload.get("parentReference"))
            if problem:
                return _malformed(request.operation, problem)
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


def _work_list_problem(data: Mapping[str, object], requested: object = None) -> str | None:
    """Why a list-work result is not the normalized shape, or None."""
    if set(data) - {"parentReference", "workItems", "auditReference"}:
        return "Return only the parent reference, the work items and an audit reference."
    if not isinstance(data.get("parentReference"), str) or not data["parentReference"]:
        return "Return the parent reference the work was listed under."
    if requested is not None and data["parentReference"] != requested:
        return "Return the work of the parent that was asked for."
    items = data.get("workItems")
    if not isinstance(items, list):
        return "Return the listed work as a list of work items."
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping) or set(item) - {"workReference", "title", "stateType", "blockedBy"}:
            return "Return each work item with only its reference, title, state type and blockers."
        reference = item.get("workReference")
        if not isinstance(reference, str) or not reference or reference in seen:
            return "Return each work item once, with a reference."
        seen.add(reference)
        if not isinstance(item.get("title"), str):
            return "Return each work item's title."
        if item.get("stateType") not in WORK_STATE_TYPES:
            return "Return each work item's state type as backlog, started, completed or canceled."
        blockers = item.get("blockedBy", [])
        if not isinstance(blockers, list) or not all(isinstance(entry, str) and entry for entry in blockers):
            return "Return each work item's blockers as a list of references."
    problems = dependency_problems(
        [(item["workReference"], item.get("blockedBy", [])) for item in items], data["parentReference"])
    if problems:
        return "Return the work items' blockers as a dependency list: " + "; ".join(problems) + "."
    return None


def dependency_problems(items: list[tuple[str, list[str]]], parent: str | None) -> list[str]:
    """Why work items' blockers are not a dependency list, naming the items; empty when they are.

    ``items`` are (reference, blockers) pairs. An item may not be the parent,
    be blocked by the parent, block itself, name a blocker twice, or wait on
    itself through other items. A blocker outside the items is allowed. Kept
    identical in the product's tracker contract and the delivery state tool.
    """
    problems: list[str] = []
    blockers_of: dict[str, list[str]] = {}
    for reference, blockers in items:
        if parent is not None and reference == parent:
            problems.append(f"{reference} is the parent itself")
        if parent is not None and parent in blockers:
            problems.append(f"{reference} is blocked by its own parent {parent}")
        if reference in blockers:
            problems.append(f"{reference} blocks itself")
        named: set[str] = set()
        for blocker in blockers:
            if blocker in named:
                problems.append(f"{reference} names its blocker {blocker} twice")
            named.add(blocker)
        blockers_of[reference] = [blocker for blocker in dict.fromkeys(blockers) if blocker != reference]
    # A cycle through other items, found without recursion: each is reported
    # once, from the item it is first reached by.
    finished: set[str] = set()
    for start in blockers_of:
        if start in finished:
            continue
        path = [start]
        stack = [iter(blockers_of[start])]
        while stack:
            for blocker in stack[-1]:
                if blocker not in blockers_of or blocker in finished:
                    continue
                if blocker in path:
                    cycle = path[path.index(blocker):] + [blocker]
                    problems.append("the blockers form a cycle, each blocked by the next: " + " -> ".join(cycle))
                    continue
                path.append(blocker)
                stack.append(iter(blockers_of[blocker]))
                break
            else:
                stack.pop()
                finished.add(path.pop())
    return problems


def work_snapshot(data: Mapping[str, object], taken_at: str) -> dict[str, object]:
    """A list-work success written as a work-snapshot/1 document from the tracker."""
    items = []
    for item in data["workItems"]:  # type: ignore[union-attr]
        entry: dict[str, object] = {"subject": item["workReference"], "title": item["title"],
                                    "state": item["stateType"]}
        if item.get("blockedBy"):
            entry["blockedBy"] = list(item["blockedBy"])
        items.append(entry)
    return {"takenAt": taken_at, "source": "tracker", "items": items}


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
    if request.operation is TrackerOperation.LIST_WORK and not (
        isinstance(request.payload.get("parentReference"), str) and request.payload["parentReference"].strip()
    ):
        return _malformed(
            request.operation,
            "Name the parent reference whose work to list.",
        )
    return _normalize_outcome(request, adapter.execute(request))
