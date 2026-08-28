from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from agent_skills.capabilities import (
    AdapterOutcome,
    CapabilityState,
    OutcomeKind,
    TrackerAdapter,
    TrackerOperation,
    TrackerRequest,
    execute_tracker_operation,
)


POSTURES = frozenset({"test-first", "characterization-first", "sensor-only"})


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


@dataclass(frozen=True)
class Sensor:
    name: str
    required: bool = True
    available: bool = True


@dataclass(frozen=True)
class PlanningRequest:
    outcome: str
    scope: tuple[str, ...]
    finish_line: tuple[str, ...]
    test_scenarios: tuple[str, ...]
    out_of_scope: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    repository_sensors: tuple[Sensor, ...] = ()
    generic_sensors: tuple[str, ...] = ()
    approval_handoffs: tuple[str, ...] = ()
    existing_behavior: bool = False
    behavior_change: bool = True
    posture: str | None = None
    tracking_selected: bool = False
    tracker_state: CapabilityState = CapabilityState.ABSENT
    tracker_adapter: TrackerAdapter | None = None
    tracking_key: str | None = None


@dataclass(frozen=True)
class WorkPlan:
    outcome: str
    scope: tuple[str, ...]
    out_of_scope: tuple[str, ...]
    finish_line: tuple[str, ...]
    dependencies: tuple[str, ...]
    test_scenarios: tuple[str, ...]
    posture: str
    sensors: tuple[Sensor, ...]
    approval_handoffs: tuple[str, ...]
    handoffs: tuple[str, ...] = ()
    tracker_outcomes: tuple[AdapterOutcome, ...] = ()


def _posture(request: PlanningRequest) -> str:
    if request.posture is not None:
        if request.posture not in POSTURES:
            raise ValueError(f"unsupported execution posture: {request.posture}")
        return request.posture
    if not request.behavior_change:
        return "sensor-only"
    if request.existing_behavior:
        return "characterization-first"
    return "test-first"


def _sensors(request: PlanningRequest) -> tuple[Sensor, ...]:
    if request.repository_sensors:
        sensors: list[Sensor] = []
        seen: set[str] = set()
        for sensor in request.repository_sensors:
            name = sensor.name.strip()
            if name and name not in seen:
                seen.add(name)
                sensors.append(Sensor(name, sensor.required, sensor.available))
        return tuple(sensors)
    return tuple(Sensor(name) for name in _unique(request.generic_sensors))


def _tracking_outcome(
    state: CapabilityState,
    operation: TrackerOperation,
    payload: Mapping[str, object],
    adapter: TrackerAdapter | None,
    key: str | None,
) -> AdapterOutcome:
    return execute_tracker_operation(
        state,
        TrackerRequest(operation=operation, payload=payload, idempotency_key=key),
        adapter,
    )


def plan_work(request: PlanningRequest) -> WorkPlan:
    outcome = request.outcome.strip()
    scope = _unique(request.scope)
    finish_line = _unique(request.finish_line)
    test_scenarios = _unique(request.test_scenarios)
    if not outcome or not scope or not finish_line or not test_scenarios:
        raise ValueError(
            "planning requires an outcome, scope, finish line, and test scenarios"
        )

    sensors = _sensors(request)
    if not sensors:
        raise ValueError("planning requires at least one verification sensor")
    out_of_scope = _unique(request.out_of_scope)
    approval_handoffs = _unique(request.approval_handoffs)
    posture = _posture(request)
    handoffs = [
        f"optional sensor unavailable: {sensor.name}"
        for sensor in sensors
        if not sensor.available and not sensor.required
    ]
    tracker_outcomes: tuple[AdapterOutcome, ...] = ()
    dependencies = _unique(request.dependencies)
    if request.tracking_selected:
        outcomes = [
            _tracking_outcome(
                request.tracker_state,
                TrackerOperation.CREATE_WORK,
                {
                    "approvalHandoffs": approval_handoffs,
                    "finishLine": finish_line,
                    "outOfScope": out_of_scope,
                    "outcome": outcome,
                    "posture": posture,
                    "scope": scope,
                    "sensors": tuple(
                        {
                            "available": sensor.available,
                            "name": sensor.name,
                            "required": sensor.required,
                        }
                        for sensor in sensors
                    ),
                    "testScenarios": test_scenarios,
                },
                request.tracker_adapter,
                f"{request.tracking_key}:create" if request.tracking_key else None,
            )
        ]
        if dependencies and outcomes[-1].kind is OutcomeKind.SUCCESS:
            outcomes.append(
                _tracking_outcome(
                    request.tracker_state,
                    TrackerOperation.LINK_DEPENDENCIES,
                    {"dependencies": dependencies},
                    request.tracker_adapter,
                    f"{request.tracking_key}:dependencies"
                    if request.tracking_key
                    else None,
                )
            )
        tracker_outcomes = tuple(outcomes)
        handoffs.extend(
            outcome.action
            for outcome in tracker_outcomes
            if outcome.kind is not OutcomeKind.SUCCESS
        )

    return WorkPlan(
        outcome=outcome,
        scope=scope,
        out_of_scope=out_of_scope,
        finish_line=finish_line,
        dependencies=dependencies,
        test_scenarios=test_scenarios,
        posture=posture,
        sensors=sensors,
        approval_handoffs=approval_handoffs,
        handoffs=_unique(tuple(handoffs)),
        tracker_outcomes=tracker_outcomes,
    )


@dataclass(frozen=True)
class ExecutionRequest:
    plan: WorkPlan
    completed_work: tuple[str, ...] = ()
    completed_finish_line: tuple[str, ...] = ()
    completed_dependencies: tuple[str, ...] = ()
    sensor_results: Mapping[str, bool] = field(default_factory=dict)
    approved_handoffs: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    tracking_selected: bool = False
    tracker_state: CapabilityState = CapabilityState.ABSENT
    tracker_adapter: TrackerAdapter | None = None
    tracking_key: str | None = None


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    posture: str
    completed_work: tuple[str, ...]
    completed_finish_line: tuple[str, ...]
    sensor_results: tuple[tuple[str, bool], ...]
    evidence: tuple[str, ...]
    blockers: tuple[str, ...]
    handoffs: tuple[str, ...]
    tracker_outcomes: tuple[AdapterOutcome, ...] = ()


def execute_work(request: ExecutionRequest) -> ExecutionResult:
    completed_work = _unique(request.completed_work)
    completed_finish_line = _unique(request.completed_finish_line)
    completed_dependencies = set(_unique(request.completed_dependencies))
    approved_handoffs = set(_unique(request.approved_handoffs))
    evidence = _unique(request.evidence)

    blockers = list(_unique(request.blockers))
    blockers.extend(
        f"out-of-scope work reported complete: {item}"
        for item in request.plan.out_of_scope
        if item in completed_work
    )
    blockers.extend(
        f"dependency incomplete: {dependency}"
        for dependency in request.plan.dependencies
        if dependency not in completed_dependencies
    )
    blockers.extend(
        f"required sensor unavailable: {sensor.name}"
        for sensor in request.plan.sensors
        if sensor.required and not sensor.available
    )
    blockers.extend(
        f"required sensor failed: {sensor.name}"
        for sensor in request.plan.sensors
        if sensor.required
        and sensor.available
        and request.sensor_results.get(sensor.name) is False
    )

    finish_line_complete = set(request.plan.finish_line) <= set(
        completed_finish_line
    )
    scope_complete = set(request.plan.scope) <= set(completed_work)
    missing_required_sensors = tuple(
        sensor.name
        for sensor in request.plan.sensors
        if sensor.required
        and sensor.available
        and sensor.name not in request.sensor_results
    )
    missing_approvals = tuple(
        handoff
        for handoff in request.plan.approval_handoffs
        if handoff not in approved_handoffs
    )
    if finish_line_complete:
        blockers.extend(
            f"required sensor incomplete: {sensor}"
            for sensor in missing_required_sensors
        )
        blockers.extend(
            f"approval incomplete: {handoff}" for handoff in missing_approvals
        )

    handoffs = list(request.plan.handoffs)
    handoffs.extend(f"approval required: {handoff}" for handoff in missing_approvals)
    if blockers:
        status = "blocked"
    elif (
        finish_line_complete
        and scope_complete
        and not missing_required_sensors
        and not missing_approvals
    ):
        status = "complete"
    elif completed_work or completed_finish_line or evidence:
        status = "partial"
    else:
        status = "in-progress"

    tracker_outcomes: tuple[AdapterOutcome, ...] = ()
    if request.tracking_selected:
        outcomes = [
            _tracking_outcome(
                request.tracker_state,
                TrackerOperation.UPDATE_STATUS,
                {"status": status},
                request.tracker_adapter,
                f"{request.tracking_key}:status" if request.tracking_key else None,
            )
        ]
        if evidence and outcomes[-1].kind is OutcomeKind.SUCCESS:
            outcomes.append(
                _tracking_outcome(
                    request.tracker_state,
                    TrackerOperation.ATTACH_EVIDENCE,
                    {"evidence": evidence},
                    request.tracker_adapter,
                    f"{request.tracking_key}:evidence"
                    if request.tracking_key
                    else None,
                )
            )
        if (
            status in {"blocked", "complete"}
            and outcomes[-1].kind is OutcomeKind.SUCCESS
        ):
            outcomes.append(
                _tracking_outcome(
                    request.tracker_state,
                    TrackerOperation.CLOSE_OR_HANDOFF,
                    {"status": status, "blockers": tuple(blockers)},
                    request.tracker_adapter,
                    f"{request.tracking_key}:close-or-handoff"
                    if request.tracking_key
                    else None,
                )
            )
        tracker_outcomes = tuple(outcomes)
        handoffs.extend(
            outcome.action
            for outcome in tracker_outcomes
            if outcome.kind is not OutcomeKind.SUCCESS
        )

    return ExecutionResult(
        status=status,
        posture=request.plan.posture,
        completed_work=completed_work,
        completed_finish_line=completed_finish_line,
        sensor_results=tuple(sorted(request.sensor_results.items())),
        evidence=evidence,
        blockers=tuple(blockers),
        handoffs=_unique(tuple(handoffs)),
        tracker_outcomes=tracker_outcomes,
    )
