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
    host_workflow_hint: str | None = None
    repository_workflow: str | None = None


@dataclass(frozen=True)
class RouteDecision:
    workflow: WorkflowKind
    entry_point: str
    route_kind: RouteKind
    unavailable_capabilities: tuple[str, ...] = ()
    action: str = ""

    @property
    def workflow_skill(self) -> str | None:
        """Released downstream workflow, never an authorization to execute it."""
        if self.route_kind is RouteKind.HANDOFF:
            return None
        return {
            WorkflowKind.IMPLEMENT: "execute-work",
            WorkflowKind.DIAGNOSE: "diagnose-work",
            WorkflowKind.PLAN: "plan-work",
            WorkflowKind.REVIEW: "review-work",
            WorkflowKind.COMPOUND: "compound-learning",
        }.get(self.workflow)


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


def _declared_workflow(value: str) -> WorkflowKind | None:
    try:
        return WorkflowKind(_normalized_intent(value))
    except ValueError:
        return None


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
    if request.repository_workflow is not None:
        repository_workflow = _declared_workflow(request.repository_workflow)
        if repository_workflow is None:
            return RouteDecision(
                workflow=WorkflowKind.CLARIFY,
                entry_point="deliver-work",
                route_kind=RouteKind.HANDOFF,
                action="Choose a declared repository workflow, then resume delivery.",
            )
        workflow = repository_workflow
    elif request.host_workflow_hint is not None:
        host_workflow = _declared_workflow(request.host_workflow_hint)
        if host_workflow is None:
            return RouteDecision(
                workflow=WorkflowKind.CLARIFY,
                entry_point="deliver-work",
                route_kind=RouteKind.HANDOFF,
                action="Choose a declared host workflow hint, then resume delivery.",
            )
        workflow = host_workflow
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
