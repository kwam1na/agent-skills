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


REVIEW_OUTCOMES = frozenset({"aligned", "changes-requested", "failed"})


@dataclass(frozen=True)
class ReviewLensResult:
    lens: str
    outcome: str
    findings: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    failure: str | None = None


@dataclass(frozen=True)
class ReviewFailure:
    lens: str
    reason: str


@dataclass(frozen=True)
class ReviewRequest:
    required_lenses: tuple[str, ...]
    rounds: tuple[tuple[ReviewLensResult, ...], ...]
    max_rounds: int


@dataclass(frozen=True)
class ReviewResult:
    status: str
    rounds: tuple[tuple[ReviewLensResult, ...], ...]
    dissent: tuple[ReviewLensResult, ...]
    failures: tuple[ReviewFailure, ...]
    blockers: tuple[str, ...]
    action: str


def _normalized_lens_result(result: ReviewLensResult) -> ReviewLensResult:
    failure = result.failure.strip() if result.failure and result.failure.strip() else None
    return ReviewLensResult(
        lens=result.lens.strip(),
        outcome=result.outcome.strip(),
        findings=_unique(result.findings),
        evidence=_unique(result.evidence),
        failure=failure,
    )


def _round_blockers(
    results: tuple[ReviewLensResult, ...],
    required_lenses: tuple[str, ...],
    round_number: int,
) -> tuple[str, ...]:
    blockers: list[str] = []
    lens_names = tuple(result.lens for result in results)
    for lens in required_lenses:
        count = lens_names.count(lens)
        if count == 0:
            blockers.append(f"required review lens missing: {lens} in round {round_number}")
        elif count > 1:
            blockers.append(f"review lens duplicated: {lens} in round {round_number}")
    blockers.extend(
        f"undeclared review lens: {lens} in round {round_number}"
        for lens in lens_names
        if lens not in required_lenses
    )
    for result in results:
        lens = result.lens or "unnamed"
        if result.outcome not in REVIEW_OUTCOMES:
            blockers.append(
                f"unsupported review outcome: {lens} in round {round_number}"
            )
        elif result.outcome == "aligned":
            if result.findings:
                blockers.append(
                    f"aligned review has findings: {lens} in round {round_number}"
                )
            if result.failure:
                blockers.append(
                    f"aligned review has failure: {lens} in round {round_number}"
                )
            if not result.evidence:
                blockers.append(
                    f"review evidence missing: {lens} in round {round_number}"
                )
        elif result.outcome == "changes-requested":
            if not result.findings:
                blockers.append(
                    f"review findings missing: {lens} in round {round_number}"
                )
            if not result.evidence:
                blockers.append(
                    f"review evidence missing: {lens} in round {round_number}"
                )
            if result.failure:
                blockers.append(
                    f"finding review has failure: {lens} in round {round_number}"
                )
        elif result.failure:
            blockers.append(f"reviewer failed: {lens} in round {round_number}")
        else:
            blockers.append(f"review failure missing: {lens} in round {round_number}")
    return _unique(tuple(blockers))


def review_work(request: ReviewRequest) -> ReviewResult:
    required_lenses = _unique(request.required_lenses)
    if not required_lenses:
        raise ValueError("review requires at least one lens")
    if len(required_lenses) != len(request.required_lenses):
        raise ValueError("review lenses must be non-empty and unique")
    if request.max_rounds < 1:
        raise ValueError("review max rounds must be positive")

    rounds = tuple(
        tuple(_normalized_lens_result(result) for result in results)
        for results in request.rounds
    )
    dissent = tuple(
        result
        for results in rounds
        for result in results
        if result.outcome == "changes-requested"
    )
    failures = tuple(
        ReviewFailure(result.lens, result.failure)
        for results in rounds
        for result in results
        if result.outcome == "failed" and result.failure is not None
    )

    if not rounds:
        return ReviewResult(
            status="unresolved",
            rounds=(),
            dissent=(),
            failures=(),
            blockers=(),
            action="Run the required review lenses.",
        )

    latest = rounds[-1]
    blockers = list(_round_blockers(latest, required_lenses, len(rounds)))
    if len(rounds) > request.max_rounds:
        blockers.append(f"review loop exceeded {request.max_rounds} rounds")

    active_dissent = tuple(
        result for result in latest if result.outcome == "changes-requested"
    )
    active_failures = tuple(result for result in latest if result.outcome == "failed")
    if blockers or active_failures:
        status = "blocked"
        action = "Resolve the named review blockers before continuing."
    elif active_dissent and len(rounds) >= request.max_rounds:
        status = "blocked"
        blockers.append(f"review loop exhausted after {request.max_rounds} rounds")
        action = "Resolve the named review blockers before continuing."
    elif active_dissent:
        status = "unresolved"
        action = "Resolve the visible findings, then review again."
    else:
        status = "aligned"
        action = "Continue with the caller-owned delivery workflow."

    return ReviewResult(
        status=status,
        rounds=rounds,
        dissent=dissent,
        failures=failures,
        blockers=_unique(tuple(blockers)),
        action=action,
    )


@dataclass(frozen=True)
class CompoundingRequest:
    learning: str | None = None
    reusable_reasons: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    no_learning_reason: str | None = None


@dataclass(frozen=True)
class CompoundingDecision:
    status: str
    learning: str | None
    reusable_reasons: tuple[str, ...]
    evidence: tuple[str, ...]
    no_learning_reason: str | None
    blockers: tuple[str, ...]
    action: str


def compound_learning(request: CompoundingRequest) -> CompoundingDecision:
    learning = request.learning.strip() if request.learning and request.learning.strip() else None
    reusable_reasons = _unique(request.reusable_reasons)
    evidence = _unique(request.evidence)
    no_learning_reason = (
        request.no_learning_reason.strip()
        if request.no_learning_reason and request.no_learning_reason.strip()
        else None
    )
    blockers: list[str] = []

    if reusable_reasons:
        if no_learning_reason:
            blockers.append("compounding decision has conflicting reusable outcomes")
        if not learning:
            blockers.append("reusable learning is required")
        if not evidence:
            blockers.append("compounding evidence is required")
        status = "blocked" if blockers else "required"
        action = (
            "Resolve the named compounding blockers before continuing."
            if blockers
            else "Preserve the reusable learning using repository-owned guidance."
        )
    else:
        if learning or evidence:
            blockers.append("reusable reason is required for durable learning")
        if not no_learning_reason:
            blockers.append("no-learning reason is required")
        status = "blocked" if blockers else "not-required"
        action = (
            "Resolve the named compounding blockers before continuing."
            if blockers
            else "Record no durable learning in the caller-owned handoff."
        )

    return CompoundingDecision(
        status=status,
        learning=learning,
        reusable_reasons=reusable_reasons,
        evidence=evidence,
        no_learning_reason=no_learning_reason,
        blockers=_unique(tuple(blockers)),
        action=action,
    )
