from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from agent_skills.capabilities import CapabilityState


DECLARED_CAPABILITIES = frozenset({"tracker"})


class WorkflowKind(str, Enum):
    CLARIFY = "clarify"
    IMPLEMENT = "implement"
    DIAGNOSE = "diagnose"
    PLAN = "plan"
    REVIEW = "review"
    COMPOUND = "compound"


class RouteKind(str, Enum):
    DIRECT = "direct"
    DEGRADED = "degraded"
    HANDOFF = "handoff"


@dataclass(frozen=True)
class WorkflowRequest:
    intent: str
    optional_capabilities: Mapping[str, str] = field(default_factory=dict)
    required_capabilities: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteDecision:
    workflow: WorkflowKind
    entry_point: str
    route_kind: RouteKind
    unavailable_capabilities: tuple[str, ...] = ()
    action: str = ""


ROUTES = (
    (WorkflowKind.COMPOUND, re.compile(r"\b(?:compound|reusable learning)\b")),
    (WorkflowKind.REVIEW, re.compile(r"\b(?:review|audit)\b")),
    (WorkflowKind.DIAGNOSE, re.compile(r"\b(?:diagnose|investigate|root cause)\b")),
    (WorkflowKind.PLAN, re.compile(r"\b(?:plan|design)\b")),
    (WorkflowKind.IMPLEMENT, re.compile(r"\b(?:build|fix|implement|modify|refactor|ship)\b")),
)


def _normalized_intent(intent: str) -> str:
    normalized = unicodedata.normalize("NFKC", intent).casefold()
    return " ".join(normalized.split())


def _workflow(intent: str) -> WorkflowKind:
    normalized = _normalized_intent(intent)
    for workflow, pattern in ROUTES:
        if pattern.search(normalized):
            return workflow
    return WorkflowKind.CLARIFY


def _state(value: str) -> CapabilityState:
    try:
        return CapabilityState(value)
    except ValueError:
        return CapabilityState.BLOCKED


def _unavailable(capabilities: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            name
            for name, state in capabilities.items()
            if name not in DECLARED_CAPABILITIES
            or _state(state) is not CapabilityState.CONFIGURED
        )
    )


def route_workflow(request: WorkflowRequest) -> RouteDecision:
    workflow = _workflow(request.intent)
    required = _unavailable(request.required_capabilities)
    if required:
        capability = required[0]
        state = _state(request.required_capabilities[capability])
        if capability not in DECLARED_CAPABILITIES:
            action = f"Provide the declared {capability} capability, then resume delivery."
        elif state is CapabilityState.BLOCKED:
            action = f"Resolve the {capability} blocker, then resume delivery."
        elif state is CapabilityState.AVAILABLE:
            action = f"Configure the {capability} capability, then resume delivery."
        else:
            action = f"Provide the {capability} capability, then resume delivery."
        return RouteDecision(
            workflow=workflow,
            entry_point="deliver-work",
            route_kind=RouteKind.HANDOFF,
            unavailable_capabilities=required,
            action=action,
        )
    optional = _unavailable(request.optional_capabilities)
    if optional:
        return RouteDecision(
            workflow=workflow,
            entry_point="deliver-work",
            route_kind=RouteKind.DEGRADED,
            unavailable_capabilities=optional,
            action="Continue without optional tracking.",
        )
    return RouteDecision(
        workflow=workflow,
        entry_point="deliver-work",
        route_kind=RouteKind.DIRECT,
    )
