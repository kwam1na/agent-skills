"""Pure admission checks for workflow semantics, never managed execution state.

The caller supplies independently captured release, subject, candidate and
prerequisite evidence. Acceptance checks consistency; it grants no authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
GRAPH_PATH = "workflows/delivery-v1.json"
RESULT_VERSION = "workflow-stage-result/1"
DIAGNOSIS_CALLERS = frozenset({"intake", "plan", "implement", "review.acquire", "review.reduce", "feedback.handoff"})


class WorkflowGraphError(ValueError):
    """A portable result or graph does not satisfy the public contract."""


def _fail(message: str) -> None:
    raise WorkflowGraphError(message)


def _text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} must be a nonempty string")


def _object(value: Any, required: set[str], optional: set[str], label: str) -> None:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        _fail(f"{label} has missing or unknown fields")


def _digest(value: Any, label: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        _fail(f"{label} must be a lowercase SHA-256")


def validate_reference(value: Any, kind: str) -> None:
    """Validate opacity and version without interpreting the durable identity."""
    _object(value, {"schemaVersion", "opaque"}, set(), f"{kind} reference")
    if value["schemaVersion"] != f"workflow-{kind}-ref/1":
        _fail(f"unsupported {kind} reference version")
    _text(value["opaque"], f"{kind} opacity")


def validate_release(value: Any) -> None:
    _object(value, {"releaseId", "profile", "archiveSha256", "metadataSha256"}, set(), "release")
    _text(value["releaseId"], "releaseId")
    _text(value["profile"], "profile")
    _digest(value["archiveSha256"], "archiveSha256")
    _digest(value["metadataSha256"], "metadataSha256")


def load_graph(root: Path = ROOT) -> dict[str, Any]:
    graph = json.loads((root / GRAPH_PATH).read_text(encoding="utf-8"))
    validate_graph(graph)
    return graph


def graph_sha256(root: Path = ROOT) -> str:
    """Hash exact artifact bytes, not a result or a self-referential release."""
    contents = (root / GRAPH_PATH).read_bytes()
    validate_graph(json.loads(contents))
    return hashlib.sha256(contents).hexdigest()


def validate_graph(graph: Any) -> None:
    # Version 1 freezes the whole semantic matrix, not just its field shapes.
    # The independent consumer validates this same public const with JSON Schema.
    schema = json.loads((ROOT / "schemas/workflow-graph.schema.json").read_text(encoding="utf-8"))
    try:
        matches = json.dumps(graph, sort_keys=True) == json.dumps(schema["const"], sort_keys=True)
    except (TypeError, ValueError) as error:
        raise WorkflowGraphError("graph must contain only JSON values") from error
    if not matches:
        _fail("graph differs from the canonical workflow-graph/1 semantic matrix")


def apply_overlay(graph: dict, overlay: list[dict] | None = None) -> dict:
    """Return a copy with effective requiredness; never delete a declaration."""
    validate_graph(graph)
    if overlay is not None and not isinstance(overlay, list):
        _fail("overlay must be a list of stage overrides")
    effective = copy.deepcopy(graph)
    stages = {stage["id"]: stage for stage in effective["stages"]}
    seen: set[str] = set()
    for override in overlay or []:
        _object(override, {"stageId"}, {"requiredness", "disposition", "policyRef", "reason"}, "stage override")
        stage_id = override["stageId"]
        if not isinstance(stage_id, str) or stage_id not in stages or stage_id in seen:
            _fail("unknown or duplicate overlay stage")
        seen.add(stage_id)
        stage = stages[stage_id]
        if "requiredness" in override and override["requiredness"] != "required":
            _fail("overlay cannot weaken requiredness")
        requiredness = override.get("requiredness", stage["requiredness"])
        disposition = override.get("disposition", "included")
        if not isinstance(disposition, str) or disposition not in {"included", "omitted"}:
            _fail("overlay disposition must be included or omitted")
        for field in ("policyRef", "reason"):
            if field in override:
                _text(override[field], field)
        if disposition == "omitted":
            if stage["requiredness"] != "policy-skippable" or requiredness == "required":
                _fail("an effectively required stage cannot be omitted")
            _text(override.get("policyRef"), "omission policyRef")
            _text(override.get("reason"), "omission reason")
        stage["requiredness"] = requiredness
        stage["disposition"] = disposition
        for field in ("policyRef", "reason"):
            if field in override:
                stage[field] = override[field]
    for stage in stages.values():
        stage.setdefault("disposition", "included")
    return effective


def _list_of_text(value: Any, label: str) -> None:
    if not isinstance(value, list):
        _fail(f"{label} must be a list")
    for item in value:
        _text(item, label)
    if len(value) != len(set(value)):
        _fail(f"{label} must be unique")


def _shape(result: Any, stage: dict) -> None:
    _object(result, {"schemaVersion", "release", "graphSha256", "stageId", "subjectRef", "status", "evidenceRefs", "limitations"},
            {"candidateRef", "output", "nextStep"}, "stage result")
    if result["schemaVersion"] != RESULT_VERSION:
        _fail("unsupported stage result version")
    validate_release(result["release"])
    _digest(result["graphSha256"], "graphSha256")
    validate_reference(result["subjectRef"], "subject")
    if "candidateRef" in result:
        validate_reference(result["candidateRef"], "candidate")
    if result["status"] not in stage["statuses"]:
        _fail("status is not permitted for this stage")
    _list_of_text(result["evidenceRefs"], "evidenceRefs")
    _list_of_text(result["limitations"], "limitations")
    if result["status"] != "succeeded":
        if "output" in result:
            _fail("non-success cannot carry a success output")
        _text(result.get("nextStep"), "non-success nextStep")
        return
    if "nextStep" in result:
        _fail("success output cannot masquerade as a non-success next step")
    output = result.get("output")
    required = {"kind", "evidenceRef"}
    if stage["id"] in {"publish.handoff", "feedback.handoff"}:
        required.add("adapterRef")
    if stage["id"] == "feedback.handoff":
        required.add("trust")
    if isinstance(output, dict) and output.get("kind") == "learning-required":
        required.add("preservationHandoffRef")
    _object(output, required, set(), "success output")
    if output["kind"] not in stage["successOutputs"]:
        _fail("unexpected success output kind")
    for name in required - {"kind", "trust"}:
        _text(output[name], name)
    if "adapterRef" in output and output["adapterRef"] != stage["evidenceAdapter"]["ref"]:
        _fail("wrong evidence adapter reference")
    if "trust" in output and output["trust"] != "untrusted":
        _fail("external feedback must remain untrusted")


def _bindings(result: dict, context: Mapping[str, Any], stage: dict) -> None:
    validate_release(context.get("release"))
    validate_reference(context.get("subjectRef"), "subject")
    _digest(context.get("graphSha256"), "context graphSha256")
    if context["graphSha256"] != graph_sha256():
        _fail("context must name the exact canonical graph bytes")
    for key in ("release", "graphSha256", "subjectRef"):
        if result[key] != context[key]:
            _fail(f"exact {key} binding mismatch")
    for key in ("candidateRef", "producedCandidateRef"):
        if key in context:
            validate_reference(context[key], "candidate")
    policy = stage["candidateBinding"]
    expected = context.get("candidateRef")
    if policy == "forbidden":
        expected = None
    elif policy == "required" and expected is None:
        _fail("stage requires an independently captured checkpoint candidate")
    elif policy == "produced-on-success" and result["status"] == "succeeded":
        expected = context.get("producedCandidateRef")
        if expected is None:
            _fail("implementation success requires an independently captured produced candidate")
    if expected is None:
        if "candidateRef" in result:
            _fail("candidate reference forbidden before a candidate exists")
    elif result.get("candidateRef") != expected:
        _fail("exact candidate binding mismatch")


def _prerequisites(stage: dict, context: Mapping[str, Any], stages: dict[str, dict]) -> None:
    evidence = context.get("completedStages", {})
    if not isinstance(evidence, dict):
        _fail("completedStages must be harness-retained evidence")
    if "diagnosisInvoked" in context and type(context["diagnosisInvoked"]) is not bool:
        _fail("diagnosisInvoked must be boolean")
    if stage["id"] == "diagnose" and (
        not isinstance(context.get("diagnosisFrom"), str) or context["diagnosisFrom"] not in DIAGNOSIS_CALLERS
    ):
        _fail("diagnosis requires its recorded invoking stage")
    for prerequisite in stage["prerequisites"]:
        stage_id, when = prerequisite["stageId"], prerequisite["when"]
        if (when == "diagnosis-invoked" and not context.get("diagnosisInvoked", False)
                and stages["diagnose"]["requiredness"] != "required"):
            continue
        if when == "diagnostic-subflow":
            if context["diagnosisFrom"] != "intake":
                _text(context.get("invokingStageRef"), "diagnostic invoking-stage evidence")
            continue
        if when == "repair-loop":
            stage_id = context.get("repairFrom")
            if stage_id is None:
                continue
            if not isinstance(stage_id, str) or stage_id not in {"review.reduce", "feedback.handoff"}:
                _fail("invalid repair-loop source")
        if stages[stage_id]["disposition"] == "omitted":
            if prerequisite["allowOmitted"]:
                continue
            _fail(f"prerequisite {stage_id} does not permit omission")
        previous = evidence.get(stage_id)
        if not isinstance(previous, dict) or previous.get("status") != "succeeded":
            _fail(f"prerequisite not satisfied: {stage_id}")
        _text(previous.get("evidenceRef"), f"{stage_id} prerequisite evidenceRef")
        if prerequisite["outputs"] and previous.get("outputKind") not in prerequisite["outputs"]:
            _fail(f"wrong prerequisite output: {stage_id}")
        if previous.get("outputKind") not in stages[stage_id]["successOutputs"]:
            _fail(f"prerequisite output is not canonical for {stage_id}")


def validate_stage_result(result: dict, context: Mapping[str, Any], *, graph: dict | None = None,
                          overlay: list[dict] | None = None) -> None:
    """Validate one result using trusted harness context; mutate nothing.

    Context is not a portable execution request. The harness owns admission,
    checkpoint/candidate capture, prerequisite evidence and any durable state.
    """
    if not isinstance(context, Mapping):
        _fail("admission context must be a harness-supplied mapping")
    effective = apply_overlay(load_graph() if graph is None else graph, overlay)
    stages = {stage["id"]: stage for stage in effective["stages"]}
    if not isinstance(result, dict) or not isinstance(result.get("stageId"), str) or result["stageId"] not in stages:
        _fail("unknown canonical stage")
    stage = stages[result["stageId"]]
    if stage["disposition"] == "omitted":
        _fail("an omitted stage cannot report a result")
    _shape(result, stage)
    _bindings(result, context, stage)
    _prerequisites(stage, context, stages)


def validate_edge(result: dict, next_stage: str, context: Mapping[str, Any], *,
                  graph: dict | None = None, overlay: list[dict] | None = None) -> None:
    """Check a declared successful handoff, without advancing any checkpoint."""
    graph = load_graph() if graph is None else graph
    validate_stage_result(result, context, graph=graph, overlay=overlay)
    if result["status"] != "succeeded":
        _fail("non-success has a next step, not a successful graph edge")
    stages = {stage["id"]: stage for stage in apply_overlay(graph, overlay)["stages"]}
    if next_stage not in stages or stages[next_stage]["disposition"] == "omitted":
        _fail("edge target unavailable")
    edge = next((edge for edge in stages[result["stageId"]]["edges"] if edge["to"] == next_stage), None)
    if edge is None:
        _fail("edge is not declared by the canonical graph")
    condition, output = edge["when"], result["output"]["kind"]
    if condition == "diagnosis-return":
        target = "plan" if context["diagnosisFrom"] == "intake" else context["diagnosisFrom"]
        if next_stage != target:
            _fail("diagnosis must return to the same recorded invoking stage")
    elif condition == "aligned-publication-omitted":
        if output != "review-round-aligned" or stages["publish.handoff"]["disposition"] != "omitted":
            _fail("finish edge requires alignment and valid publication omission")
    elif condition != "success" and condition != output:
        _fail("output does not permit this edge")
    if next_stage == "finish.verify":
        # A validated source result can provide evidence for its own edge;
        # retained prerequisite records still belong to the caller.
        retained = copy.deepcopy(context.get("completedStages", {}))
        retained[result["stageId"]] = {"status": "succeeded", "outputKind": output,
                                       "evidenceRef": result["output"]["evidenceRef"]}
        _prerequisites(stages[next_stage], {**context, "completedStages": retained}, stages)
