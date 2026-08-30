"""Independent harness-shaped conformance peer using only public artifacts.

This fixture is not an execution engine. Checkpoint and captured candidate state
are owned by the test harness, never changed by an admitted result.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator


class FakeConsumer:
    def __init__(self, root: Path, context: dict, *, checkpoint="checkpoint-one", overlay=None):
        self.graph_schema = json.loads((root / "schemas/workflow-graph.schema.json").read_text())
        self.result_schema = json.loads((root / "schemas/workflow-stage-result.schema.json").read_text())
        graph_bytes = (root / "workflows/delivery-v1.json").read_bytes()
        self.graph = json.loads(graph_bytes)
        Draft202012Validator.check_schema(self.graph_schema)
        Draft202012Validator.check_schema(self.result_schema)
        Draft202012Validator(self.graph_schema).validate(self.graph)
        self.digest = hashlib.sha256(graph_bytes).hexdigest()
        self.checkpoint = checkpoint
        self.accepted = []
        self.stages = {item["id"]: copy.deepcopy(item) for item in self.graph["stages"]}
        for item in self.stages.values():
            item["disposition"] = "included"
        overlay_schema = {"$defs": self.graph_schema["$defs"], "$ref": "#/$defs/overlay"}
        Draft202012Validator(overlay_schema).validate([] if overlay is None else overlay)
        used = set()
        for override in overlay or []:
            name = override["stageId"]
            if name in used:
                raise ValueError("duplicate overlay entry")
            used.add(name)
            item = self.stages[name]
            if override.get("disposition") == "omitted" and item["requiredness"] == "required":
                raise ValueError("required stages are not omittable")
            item.update({key: value for key, value in override.items() if key != "stageId"})
        self.set_context(context)

    def set_context(self, context):
        self.context = copy.deepcopy(context)

    def set_review_context(self, lenses, round_number):
        """Test harness captures selection/round separately from any result."""
        selection = {"type": "array", "minItems": 1, "uniqueItems": True,
                     "items": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9._-]*(?![\s\S])"}}
        Draft202012Validator(selection).validate(list(lenses))
        Draft202012Validator({"type": "integer", "minimum": 1}).validate(round_number)
        self.review_lenses = list(lenses)
        self.review_round_number = round_number
        if not hasattr(self, "review_history"):
            self.review_history = []

    def review_round(self, envelope):
        """Independent unverified-lens scenario peer, not a host or scheduler.

        The public stage schema supplies binding shapes; this consumer separately
        checks retained lens evidence and computes the scenario's expected review
        disposition. It never delegates validation/reduction to a production peer.
        Harness attestation transport is deliberately outside this fixture path.
        """
        try:
            keys = ("release", "graphSha256", "subjectRef", "candidateRef")
            shapes = {key: self.result_schema["properties"][key] for key in keys}
            for key in keys:
                Draft202012Validator(shapes[key]).validate(self.context.get(key))
            if self.context["graphSha256"] != self.digest:
                raise ValueError("wrong graph artifact")
            self._evidence(self.stages["review.acquire"])
            bindings = {key: {"allOf": [shapes[key], {"const": self.context[key]}]} for key in keys}
            bindings["round"] = {"type": "integer", "const": self.review_round_number}
            texts = {"type": "array", "items": {"type": "string", "pattern": r"\S"}}
            lens_result = {"type": "object", "additionalProperties": False,
                           "required": ["outcome", "findings", "evidence"],
                           "properties": {"outcome": {"enum": ["aligned", "changes-requested"]},
                                          "findings": texts, "evidence": {**texts, "minItems": 1}}}
            lens_entries = []
            for lens in self.review_lenses:
                lens_entries.append({"type": "object", "additionalProperties": False,
                    "required": list(bindings) + ["lens", "state"],
                    "properties": {**bindings, "lens": {"const": lens},
                        "state": {"enum": ["obtained", "failed", "indeterminate", "missing"]},
                        "result": lens_result, "independence": {"const": {"status": "unverified"}}},
                    "if": {"properties": {"state": {"const": "obtained"}}},
                    "then": {"required": ["result"]}, "else": {"not": {"required": ["result"]}}})
            schema = {"type": "object", "additionalProperties": False,
                      "required": list(bindings) + ["schemaVersion", "requestedLenses", "entries"],
                      "properties": {**bindings,
                          "schemaVersion": {"const": "review-acquisition-envelope/1"},
                          "requestedLenses": {"const": self.review_lenses},
                          "entries": {"type": "array", "prefixItems": lens_entries,
                                      "minItems": len(lens_entries), "maxItems": len(lens_entries)}}}
            Draft202012Validator(schema).validate(envelope)
            disposition = "aligned"
            for item in envelope["entries"]:
                if item["state"] != "obtained":
                    disposition = "blocked"
                    continue
                result = item["result"]
                findings = list(dict.fromkeys(text.strip() for text in result["findings"]))
                if bool(findings) != (result["outcome"] == "changes-requested"):
                    raise ValueError("lens disposition conflicts with findings")
                if findings and disposition != "blocked":
                    disposition = "unresolved"
        except Exception as error:
            raise ValueError("review round did not satisfy independent expectations") from error
        self.review_history.append(copy.deepcopy(envelope))
        return disposition

    def _expectations(self, result, declaration):
        context = self.context
        for key in ("release", "graphSha256", "subjectRef"):
            Draft202012Validator(self.result_schema["properties"][key]).validate(context.get(key))
        if context["graphSha256"] != self.digest:
            raise ValueError("harness graph digest is not the public artifact")
        candidate_shape = self.result_schema["properties"]["candidateRef"]
        for key in ("candidateRef", "producedCandidateRef"):
            if key in context:
                Draft202012Validator(candidate_shape).validate(context[key])
        binding = {"properties": {key: {"const": context[key]}
                   for key in ("release", "graphSha256", "subjectRef")}}
        mode = declaration["candidateBinding"]
        capture = context.get("candidateRef")
        if mode == "forbidden":
            capture = None
        if mode == "required" and capture is None:
            raise ValueError("candidate absent from harness checkpoint")
        if mode == "produced-on-success" and result["status"] == "succeeded":
            capture = context.get("producedCandidateRef")
            if capture is None:
                raise ValueError("produced candidate not independently captured")
        if capture is None:
            binding["not"] = {"required": ["candidateRef"]}
        else:
            binding["required"] = ["candidateRef"]
            binding["properties"]["candidateRef"] = {"const": capture}
        if declaration["id"] in ("publish.handoff", "feedback.handoff"):
            binding["properties"]["output"] = {"properties": {
                "adapterRef": {"const": declaration["evidenceAdapter"]["ref"]}}}
        return binding

    def _evidence(self, declaration, context=None):
        context = self.context if context is None else context
        recorded = context.get("completedStages", {})
        if not isinstance(recorded, dict):
            raise ValueError("invalid retained evidence")
        if "diagnosisInvoked" in context and type(context["diagnosisInvoked"]) is not bool:
            raise ValueError("invalid diagnostic condition")
        callers = {"intake", "plan", "implement", "review.acquire", "review.reduce", "feedback.handoff"}
        if declaration["id"] == "diagnose" and (
            not isinstance(context.get("diagnosisFrom"), str) or context["diagnosisFrom"] not in callers
        ):
            raise ValueError("missing diagnosis caller")
        needed = []
        for rule in declaration["prerequisites"]:
            condition = rule["when"]
            if (condition == "diagnosis-invoked" and not context.get("diagnosisInvoked", False)
                    and self.stages["diagnose"]["requiredness"] != "required"):
                continue
            if condition == "diagnostic-subflow":
                if context["diagnosisFrom"] != "intake":
                    Draft202012Validator({"type": "string", "pattern": "\\S"}).validate(context.get("invokingStageRef"))
                continue
            target = rule["stageId"]
            if condition == "repair-loop":
                target = context.get("repairFrom")
                if target is None:
                    continue
                if not isinstance(target, str) or target not in {"review.reduce", "feedback.handoff"}:
                    raise ValueError("undeclared repair origin")
            omitted = self.stages[target]["disposition"] == "omitted"
            if omitted and rule["allowOmitted"]:
                continue
            if omitted:
                raise ValueError("edge cannot use omitted prerequisite")
            needed.append((target, rule["outputs"]))
        # Schema-engine validation of the harness-retained prerequisite summary
        # is independent from the production procedural admission checks.
        for target, kinds in needed:
            properties = {"status": {"const": "succeeded"},
                          "evidenceRef": {"type": "string", "pattern": "\\S"}}
            if kinds:
                properties["outputKind"] = {"enum": [kind for kind in kinds if kind in self.stages[target]["successOutputs"]]}
            Draft202012Validator({"type": "object", "required": list(properties),
                                  "properties": properties}).validate(recorded.get(target))

    def admit(self, result):
        try:
            Draft202012Validator(self.result_schema).validate(result)
            declaration = self.stages[result["stageId"]]
            if declaration["disposition"] == "omitted":
                raise ValueError("omission cannot report execution")
            Draft202012Validator(self._expectations(result, declaration)).validate(result)
            self._evidence(declaration)
        except (KeyError, TypeError) as error:
            raise ValueError("malformed workflow result") from error
        except Exception as error:
            # jsonschema.ValidationError is deliberately normalized at this
            # test-consumer boundary, without consulting the production peer.
            raise ValueError(str(error)) from error
        self.accepted.append(copy.deepcopy(result))
        return copy.deepcopy(result)

    def handoff(self, result, target):
        self.admit(result)
        if result["status"] != "succeeded" or target not in self.stages or self.stages[target]["disposition"] == "omitted":
            raise ValueError("no successful target")
        possible = {edge["to"]: edge["when"] for edge in self.stages[result["stageId"]]["edges"]}
        if target not in possible:
            raise ValueError("undeclared edge")
        predicate = possible[target]
        kind = result["output"]["kind"]
        permitted = predicate == "success" or predicate == kind
        if predicate == "diagnosis-return":
            origin = self.context["diagnosisFrom"]
            permitted = target == ("plan" if origin == "intake" else origin)
        if predicate == "aligned-publication-omitted":
            permitted = kind == "review-round-aligned" and self.stages["publish.handoff"]["disposition"] == "omitted"
        if not permitted:
            raise ValueError("success output does not satisfy edge condition")
        if target == "finish.verify":
            retained = copy.deepcopy(self.context.get("completedStages", {}))
            retained[result["stageId"]] = {"status": "succeeded", "outputKind": kind,
                                           "evidenceRef": result["output"]["evidenceRef"]}
            try:
                self._evidence(self.stages[target], {**self.context, "completedStages": retained})
            except Exception as error:
                raise ValueError(str(error)) from error
        return target
