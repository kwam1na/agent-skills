"""Reduce diagnostic observations without executing probes or granting fix authority.

The harness retains the returned evidence at its supplied reference and admits
the stage result against independently captured context. These Python inputs
are not serialized execution requests, checkpoints, or durable attempt state.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from typing import Any, Mapping

from agent_skills.workflow_graph import validate_stage_result


@dataclass(frozen=True)
class Prediction:
    statement: str
    observed_result: str | None = None
    outcome: str = "unobserved"
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CausalLink:
    cause: str
    effect: str
    evidence_refs: tuple[str, ...] = ()
    uncertain: bool = False
    prediction: Prediction | None = None


@dataclass(frozen=True)
class Hypothesis:
    statement: str
    outcome: str = "open"
    evidence_refs: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class Assumption:
    statement: str
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiagnosisObservations:
    trigger: str
    symptom: str
    links: tuple[CausalLink, ...] = ()
    reproduction: str = "observed"
    reproduction_evidence_refs: tuple[str, ...] = ()
    missing_conditions: tuple[str, ...] = ()
    hypotheses: tuple[Hypothesis, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    failure: str | None = None


@dataclass(frozen=True)
class DiagnosisResult:
    stage_result: dict[str, Any]
    evidence: DiagnosisObservations


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("diagnostic text must be nonempty")
    return value.strip()


def _items(value: Any, kind: type) -> tuple:
    if not isinstance(value, tuple) or any(not isinstance(item, kind) for item in value):
        raise ValueError(f"diagnostic items must be a tuple of {kind.__name__}")
    return value


def _texts(value: Any, *, opaque: bool = False) -> tuple[str, ...]:
    items = _items(value, str)
    normalized = tuple(_text(item) for item in items)
    # References are opaque identities: validate but never trim into another one.
    return tuple(dict.fromkeys(items if opaque else normalized))


def _choice(value: Any, choices: set[str]) -> None:
    if not isinstance(value, str) or value not in choices:
        raise ValueError("unsupported diagnostic disposition")


def _normalize(observations: DiagnosisObservations) -> DiagnosisObservations:
    if not isinstance(observations, DiagnosisObservations):
        raise ValueError("diagnosis requires normalized diagnostic observations")
    _choice(observations.reproduction, {"observed", "unavailable", "inconclusive"})
    links = []
    for link in _items(observations.links, CausalLink):
        if type(link.uncertain) is not bool:
            raise ValueError("causal-link uncertainty must be boolean")
        prediction = link.prediction
        if prediction is not None:
            if not isinstance(prediction, Prediction):
                raise ValueError("causal-link prediction must be a Prediction")
            _choice(prediction.outcome, {"satisfied", "contradicted", "unobserved"})
            prediction = replace(prediction, statement=_text(prediction.statement),
                                 observed_result=(_text(prediction.observed_result)
                                                  if prediction.observed_result is not None else None),
                                 evidence_refs=_texts(prediction.evidence_refs, opaque=True))
        links.append(replace(link, cause=_text(link.cause), effect=_text(link.effect),
                             evidence_refs=_texts(link.evidence_refs, opaque=True), prediction=prediction))
    hypotheses = []
    for hypothesis in _items(observations.hypotheses, Hypothesis):
        _choice(hypothesis.outcome, {"open", "ruled-out"})
        hypothesis = replace(hypothesis, statement=_text(hypothesis.statement),
                             evidence_refs=_texts(hypothesis.evidence_refs, opaque=True),
                             reason=_text(hypothesis.reason) if hypothesis.reason is not None else None)
        if hypothesis.outcome == "ruled-out" and (not hypothesis.evidence_refs or not hypothesis.reason):
            raise ValueError("ruled-out hypotheses require evidence and a reason")
        hypotheses.append(hypothesis)
    assumptions = tuple(replace(item, statement=_text(item.statement),
                                evidence_refs=_texts(item.evidence_refs, opaque=True))
                        for item in _items(observations.assumptions, Assumption))
    return replace(
        observations, trigger=_text(observations.trigger), symptom=_text(observations.symptom),
        links=tuple(links), hypotheses=tuple(hypotheses), assumptions=assumptions,
        reproduction_evidence_refs=_texts(observations.reproduction_evidence_refs, opaque=True),
        missing_conditions=_texts(observations.missing_conditions),
        evidence_refs=_texts(observations.evidence_refs, opaque=True),
        limitations=_texts(observations.limitations), recommendations=_texts(observations.recommendations),
        failure=_text(observations.failure) if observations.failure is not None else None,
    )


def _causal_gaps(evidence: DiagnosisObservations) -> tuple[str, ...]:
    gaps = []
    if evidence.reproduction != "observed" or not evidence.reproduction_evidence_refs:
        gaps.append("Obtain a conclusive reproduction observation and its evidence reference.")
    if not evidence.links:
        gaps.append("Trace the trigger-to-symptom causal chain with supporting evidence.")
    previous = evidence.trigger
    for index, link in enumerate(evidence.links, 1):
        if link.cause != previous:
            gaps.append(f"Resolve the causal-chain gap at link {index}.")
        if not link.evidence_refs:
            gaps.append(f"Obtain supporting evidence for causal link {index}.")
        prediction = link.prediction
        if prediction is not None and prediction.outcome == "contradicted":
            gaps.append(f"Reconsider causal link {index} after its contradicted prediction.")
        elif link.uncertain or prediction is not None:
            if (prediction is None or prediction.outcome != "satisfied"
                    or not prediction.observed_result or not prediction.evidence_refs):
                gaps.append(f"Test a prediction for causal link {index} and retain the observed result.")
        previous = link.effect
    if evidence.links and previous != evidence.symptom:
        gaps.append("Trace the remaining causal path to the observed symptom.")
    return tuple(gaps)


def diagnose_work(observations: DiagnosisObservations, binding: Mapping[str, Any],
                  context: Mapping[str, Any], *, evidence_ref: str) -> DiagnosisResult:
    """Return retained evidence plus a closed stage result; mutate no caller state.

    `binding` carries release, graphSha256, subjectRef and only the contextual
    candidateRef. `context` is independently supplied graph admission context.
    `evidence_ref` names caller-owned retention, not storage performed here.
    """
    required = {"release", "graphSha256", "subjectRef"}
    if (not isinstance(binding, Mapping) or not required <= binding.keys()
            or binding.keys() - required - {"candidateRef"}):
        raise ValueError("diagnosis binding has missing or unknown fields")
    _text(evidence_ref)
    evidence = _normalize(observations)
    gaps = _causal_gaps(evidence)
    limitations = tuple(dict.fromkeys((
        *evidence.limitations,
        *(f"Unverified assumption: {item.statement}" for item in evidence.assumptions if not item.evidence_refs),
        *gaps,
    )))
    evidence = replace(evidence, limitations=limitations)
    refs = [evidence_ref, *evidence.evidence_refs, *evidence.reproduction_evidence_refs]
    for link in evidence.links:
        refs.extend(link.evidence_refs)
        if link.prediction is not None:
            refs.extend(link.prediction.evidence_refs)
    for item in (*evidence.hypotheses, *evidence.assumptions):
        refs.extend(item.evidence_refs)
    result = {
        "schemaVersion": "workflow-stage-result/1", **copy.deepcopy(dict(binding)),
        "stageId": "diagnose", "evidenceRefs": list(dict.fromkeys(refs)),
        "limitations": list(limitations),
    }
    if evidence.missing_conditions:
        result.update(status="blocked", nextStep=f"Provide the required condition: {evidence.missing_conditions[0]}.")
    elif evidence.failure:
        result.update(status="failed", nextStep=f"Resolve the diagnostic evidence failure: {evidence.failure}.")
    elif gaps:
        result.update(status="indeterminate", nextStep=gaps[0])
    else:
        result.update(status="succeeded", output={"kind": "confirmed", "evidenceRef": evidence_ref})
    validate_stage_result(result, context)
    return DiagnosisResult(stage_result=result, evidence=evidence)
