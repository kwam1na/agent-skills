"""Pure acquisition admission and projection; hosts realize caller-selected lenses.

Context and attestations must be captured/authenticated upstream, separately from
the untrusted envelope. Consistency checks neither prove their authenticity nor
grant mutation, checkpoint, or finish-line authority.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from .workflow_graph import WorkflowGraphError, graph_sha256, validate_reference, validate_release
from .workflows import ReviewLensResult

BINDINGS = ("release", "graphSha256", "subjectRef", "candidateRef", "round")
RESULT_SHAPE = "review-lens-result/1"
FAILURE_CODES = {"failed": "review-acquisition-failed", "indeterminate": "review-acquisition-indeterminate"}


class ReviewAcquisitionError(ValueError):
    """Stable rejection code, without reflecting untrusted payloads into errors."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ReviewAcquisition:
    """A copied retained envelope and unchanged reducer inputs, not convergence."""

    envelope: dict[str, Any]
    results: tuple[ReviewLensResult, ...]


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ReviewAcquisitionError(code)


def _closed(value: Any, required: set[str], optional: set[str], code: str) -> None:
    _require(isinstance(value, dict) and required <= value.keys()
             and not value.keys() - required - optional, code)


def _text(value: Any, code: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), code)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ReviewAcquisitionError(code) from None
    return value.strip()


def _texts(value: Any, code: str) -> list[str]:
    _require(isinstance(value, (list, tuple)), code)
    return list(dict.fromkeys(_text(item, code) for item in value))


def _lenses(values: Any) -> tuple[str, ...]:
    _require(isinstance(values, (list, tuple)) and bool(values), "invalid-lenses")
    lenses = tuple(_text(value, "invalid-lenses") for value in values)
    _require(all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", lens) for lens in lenses)
             and len(set(lenses)) == len(lenses), "invalid-lenses")
    return lenses


def _context(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "invalid-context")
    try:
        validate_release(value.get("release"))
        validate_reference(value.get("subjectRef"), "subject")
        validate_reference(value.get("candidateRef"), "candidate")
        _require(value.get("graphSha256") == graph_sha256(), "invalid-context")
        _require(type(value.get("round")) is int and value["round"] > 0, "invalid-context")
    except WorkflowGraphError as error:
        raise ReviewAcquisitionError("invalid-context") from error
    return {key: copy.deepcopy(value[key]) for key in BINDINGS}


def _bound(value: dict, context: dict, code: str = "binding-mismatch") -> None:
    # The separately validated context also fixes the shape of every binding.
    _require(all(value.get(key) == context[key] for key in BINDINGS)
             and type(value.get("round")) is int, code)


def prepare_review(lenses: Sequence[str], context: Mapping[str, Any],
                   lens_contexts: Mapping[str, Sequence[str]], *,
                   expected_result_shape: str = RESULT_SHAPE) -> tuple[dict[str, Any], ...]:
    """Return bounded, ordered in-process host inputs; never launch a reviewer.

    Context refs scope each lens without accepting inline transcripts, arbitrary
    instructions, or authority fields. Hosts may realize these inputs sequentially.
    """
    names = _lenses(lenses)
    binding = _context(context)
    _require(expected_result_shape == RESULT_SHAPE, "invalid-result-shape")
    _require(isinstance(lens_contexts, Mapping) and set(lens_contexts) == set(names), "invalid-lens-context")
    return tuple({**copy.deepcopy(binding), "lens": lens,
                  "contextRefs": _texts(lens_contexts[lens], "invalid-lens-context"),
                  "expectedResultShape": RESULT_SHAPE} for lens in names)


def _result(value: Any) -> dict[str, Any]:
    _closed(value, {"outcome", "findings", "evidence"}, set(), "invalid-result")
    outcome = _text(value["outcome"], "invalid-result")
    findings = _texts(value["findings"], "invalid-result")
    evidence = _texts(value["evidence"], "invalid-result")
    _require(outcome in {"aligned", "changes-requested"} and bool(evidence), "invalid-result")
    _require(bool(findings) == (outcome == "changes-requested"), "invalid-result")
    return {"outcome": outcome, "findings": findings, "evidence": evidence}


def _attestations(values: Any, binding: dict) -> dict[str, dict]:
    _require(isinstance(values, (list, tuple)), "invalid-attestation")
    attestations = {}
    realizations = set()
    lenses = set()
    for value in values:
        _closed(value, set(BINDINGS) | {"schemaVersion", "lens", "resultSha256", "realizationRef",
                "attestationRef", "distinctRealization"}, set(), "invalid-attestation")
        _bound(value, binding, "invalid-attestation")
        _require(value["schemaVersion"] == "review-realization-attestation/1"
                 and value["distinctRealization"] is True, "invalid-attestation")
        for key in ("lens", "realizationRef", "attestationRef"):
            _text(value[key], "invalid-attestation")
        digest = value["resultSha256"]
        _require(isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest) is not None, "invalid-attestation")
        _require(value["attestationRef"] not in attestations and value["realizationRef"] not in realizations
                 and value["lens"] not in lenses, "invalid-attestation")
        attestations[value["attestationRef"]] = value
        realizations.add(value["realizationRef"])
        lenses.add(value["lens"])
    return attestations


def _independence(entry: dict, normalized: dict | None, attestations: dict[str, dict],
                  used: set[str]) -> dict:
    value = entry.get("independence", {"status": "unverified"})
    _require(isinstance(value, dict), "invalid-independence")
    if value.get("status") == "unverified":
        _closed(value, {"status"}, set(), "invalid-independence")
        return {"status": "unverified"}
    _closed(value, {"status", "realizationRef", "attestationRef"}, set(), "invalid-independence")
    _require(value["status"] == "attested" and normalized is not None, "invalid-independence")
    for key in ("realizationRef", "attestationRef"):
        _text(value[key], "invalid-independence")
    _require(bool(attestations), "invalid-independence")
    proof = attestations.get(value["attestationRef"])
    _require(proof is not None, "invalid-attestation")
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False).encode("utf-8")).hexdigest()
    _require(proof["lens"] == entry["lens"] and proof["resultSha256"] == digest
             and proof["realizationRef"] == value["realizationRef"], "invalid-attestation")
    used.add(value["attestationRef"])
    return copy.deepcopy(value)


def obtain_review(envelope: dict[str, Any], lenses: Sequence[str], context: Mapping[str, Any], *,
                  attestations: Sequence[dict[str, Any]] = ()) -> ReviewAcquisition:
    """Retain a closed exact round and project it, without reducing or persisting.

    Do not derive `context` or `attestations` from the untrusted envelope. The
    caller retains historical rounds; a changed candidate needs wholly new bound
    entries. This function has no host launcher, retry, repair, or finish policy.
    """
    names = _lenses(lenses)
    binding = _context(context)
    _closed(envelope, set(BINDINGS) | {"schemaVersion", "requestedLenses", "entries"}, set(), "invalid-envelope")
    _require(envelope["schemaVersion"] == "review-acquisition-envelope/1"
             and envelope["requestedLenses"] == list(names), "invalid-envelope")
    _bound(envelope, binding)
    entries = envelope["entries"]
    _require(isinstance(entries, list) and len(entries) == len(names), "invalid-envelope")
    trusted = _attestations(attestations, binding)
    used: set[str] = set()
    retained = []
    results = []
    for lens, entry in zip(names, entries):
        _closed(entry, set(BINDINGS) | {"lens", "state"}, {"result", "independence"}, "invalid-entry")
        _require(entry["lens"] == lens, "invalid-envelope")
        _bound(entry, binding)
        state = entry["state"]
        _require(isinstance(state, str) and state in {"obtained", "failed", "indeterminate", "missing"}, "invalid-entry")
        _require(("result" in entry) == (state == "obtained"), "invalid-entry")
        normalized = _result(entry["result"]) if state == "obtained" else None
        independence = _independence(entry, normalized, trusted, used)
        item = {**copy.deepcopy(binding), "lens": lens, "state": state, "independence": independence}
        if normalized is not None:
            item["result"] = normalized
            results.append(ReviewLensResult(lens, normalized["outcome"], tuple(normalized["findings"]),
                                            tuple(normalized["evidence"])))
        elif state in FAILURE_CODES:
            results.append(ReviewLensResult(lens, "failed", failure=FAILURE_CODES[state]))
        retained.append(item)
    _require(used == set(trusted), "invalid-attestation")
    return ReviewAcquisition({"schemaVersion": "review-acquisition-envelope/1", **copy.deepcopy(binding),
                              "requestedLenses": list(names), "entries": retained}, tuple(results))
