"""Exact-release adapter from host workflow results to neutral provider rails.

The adapter owns no agents, tracker client, transport, or delivery policy. It
verifies the active agent-skills installation, reduces caller-supplied review
results through the existing portable workflow, and maps the result to the
published provider-rail documents. Host references are represented only by
session-scoped opaque values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

from . import workflows as workflow_runtime
from .fs import atomic_write, sha256_file
from .lifecycle import Lifecycle
from .locking import RepositoryLock
from .receipt import load_receipt
from .validate import canonical_json
from .workflows import ReviewLensResult, ReviewRequest, ReviewResult, review_work


PROTOCOL_VERSION = "delivery-provider-rails/1"
HOST_OPERATIONS = frozenset(
    {"create", "read", "update", "search", "relations", "reconciliation"}
)
HOST_EVENT_KINDS = frozenset({"progress", "evidence", "blocker", "failure"})
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
PROVIDER_ID = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"
)
FINDING_SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})
FINDING_SCOPES = frozenset({"in_contract", "adjacent", "expansion"})
FINDING_DISPOSITIONS = frozenset(
    {"resolved", "advisory", "pre_existing", "deferred", "unresolved", "ignored"}
)


class ProviderInputError(ValueError):
    def __init__(self, outcome: str, blocker_id: str, summary: str, action: str) -> None:
        super().__init__(summary)
        self.outcome = outcome
        self.blocker_id = blocker_id
        self.summary = summary
        self.action = action


@dataclass(frozen=True)
class Attempt:
    encoded_request: str
    idempotency_key: str
    events: tuple[dict[str, object], ...]
    evidence_files: tuple[tuple[str, str], ...]
    deferred: bool


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ProviderInputError(
            "failed",
            "input-malformed",
            f"The {name} input is malformed.",
            "Provide the declared provider input, then retry with a new attempt.",
        )
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProviderInputError(
            "failed",
            "input-malformed",
            f"The {name} input is malformed.",
            "Provide the declared provider input, then retry with a new attempt.",
        )
    return value


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ProviderInputError(
            "failed",
            "input-malformed",
            f"The {name} input is malformed.",
            "Provide the declared provider input, then retry with a new attempt.",
        )
    return value


def _git_oid(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{40}", value) is not None


def _sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _event(
    request_id: str,
    sequence: int,
    kind: str,
    summary: str,
    **fields: object,
) -> dict[str, object]:
    return {
        "kind": kind,
        "requestId": request_id,
        "sequence": sequence,
        "summary": summary,
        "version": PROTOCOL_VERSION,
        **fields,
    }


def _failure_events(
    request_id: str,
    sequence: int,
    error: ProviderInputError,
) -> list[dict[str, object]]:
    blocker = _event(
        request_id,
        sequence,
        "blocker",
        error.summary,
        blockerId=error.blocker_id,
        action=error.action,
    )
    terminal = _event(
        request_id,
        sequence + 1,
        "terminal",
        error.summary,
        outcome=error.outcome,
        action=error.action,
    )
    return [blocker, terminal]


class DeliveryRailsProvider:
    """One in-memory protocol session over a verified installed corpus."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve(strict=True)
        self.lifecycle = Lifecycle(self.repository_root)
        self.lock = RepositoryLock(self.lifecycle.lock_path, shared=True)
        self.lock.__enter__()
        self.locked = True
        try:
            self.receipt = self._runtime_snapshot()
        except Exception:
            self.close()
            raise
        self.negotiated = False
        self.closed = False
        self.attempts: dict[str, Attempt] = {}
        self.request_ids_by_key: dict[str, str] = {}
        self.active_request_id: str | None = None
        self.cancellations: dict[str, tuple[str, tuple[dict[str, object], ...]]] = {}
        self.opaque_references: dict[str, str] = {}

    def close(self) -> None:
        if getattr(self, "locked", False):
            self.lock.__exit__(None, None, None)
            self.locked = False

    def _runtime_snapshot(self) -> dict:
        status = self.lifecycle.status()
        receipt = load_receipt(self.repository_root / ".agent-skills" / "active.json")
        if status.lifecycle != "current" or status.blockers:
            raise ProviderInputError(
                "blocked",
                "installation-unavailable",
                "The active workflow installation is not current.",
                "Restore a verified active agent-skills installation.",
            )
        generation = self.lifecycle.store.generation_path(receipt["generation"])
        records = {record["path"]: record["sha256"] for record in receipt["files"]}
        runtime_paths = {
            "agent_skills/provider.py": Path(__file__).resolve(),
            "agent_skills/workflows.py": Path(workflow_runtime.__file__).resolve(),
        }
        for relative, runtime_path in runtime_paths.items():
            expected = (generation / relative).resolve()
            if (
                runtime_path != expected
                or relative not in records
                or sha256_file(runtime_path) != records[relative]
            ):
                raise ProviderInputError(
                    "blocked",
                    "runtime-mismatch",
                    "The executing provider runtime is not the active installed generation.",
                    "Start the provider from the verified active generation.",
                )
        return receipt

    def _opaque_reference(self, value: str) -> str:
        reference = self.opaque_references.get(value)
        if reference is None:
            reference = f"opaque:{secrets.token_hex(16)}"
            self.opaque_references[value] = reference
        return reference

    def handle(self, message: object) -> list[dict[str, object]]:
        document = _object(message, "protocol message")
        kind = document.get("kind")
        if kind == "negotiate":
            return self._negotiate(document)
        if kind == "request":
            return self._request(document)
        if kind == "cancel":
            return self._cancel(document)
        raise ProviderInputError(
            "failed",
            "message-unsupported",
            "The provider received an unsupported message.",
            "Send a declared provider-rail message.",
        )

    def _negotiate(self, message: dict[str, object]) -> list[dict[str, object]]:
        if self.closed or self.attempts or set(message) != {"kind", "supportedVersions"}:
            raise ProviderInputError(
                "failed",
                "negotiation-malformed",
                "Provider negotiation is malformed.",
                "Start a fresh provider session and negotiate once.",
            )
        versions = _list(message["supportedVersions"], "supported versions")
        supported = (
            len(versions) <= 8
            and len(versions) == len(set(item for item in versions if isinstance(item, str)))
            and all(isinstance(item, str) and item for item in versions)
            and PROTOCOL_VERSION in versions
        )
        self.negotiated = supported
        self.closed = not supported
        return [
            {
                "kind": "negotiation",
                "outcome": "supported" if supported else "unsupported",
                "selectedVersion": PROTOCOL_VERSION if supported else None,
                "supportedVersions": [PROTOCOL_VERSION],
            }
        ]

    def _request(self, message: dict[str, object]) -> list[dict[str, object]]:
        request_id = message.get("requestId")
        idempotency_key = message.get("idempotencyKey")
        if not (
            self.negotiated
            and not self.closed
            and set(message) == {
                "idempotencyKey",
                "kind",
                "payload",
                "requestId",
                "version",
            }
            and message.get("version") == PROTOCOL_VERSION
            and isinstance(request_id, str)
            and IDENTIFIER.fullmatch(request_id)
            and isinstance(idempotency_key, str)
            and IDENTIFIER.fullmatch(idempotency_key)
            and isinstance(message.get("payload"), dict)
        ):
            raise ProviderInputError(
                "failed",
                "request-malformed",
                "The provider request is malformed or was not negotiated.",
                "Start a fresh negotiated attempt with a valid request.",
            )

        encoded = canonical_json(message)
        prior = self.attempts.get(request_id)
        prior_request_id = self.request_ids_by_key.get(idempotency_key)
        if self.active_request_id is not None and request_id != self.active_request_id:
            raise ProviderInputError(
                "failed",
                "attempt-active",
                "Another accepted provider attempt is still active.",
                "Terminate the active attempt before starting another request.",
            )
        if prior is not None:
            if prior.encoded_request == encoded and prior.idempotency_key == idempotency_key:
                self._revalidate_evidence(prior)
                return [dict(event) for event in prior.events]
            raise ProviderInputError(
                "failed",
                "request-conflict",
                "A request identity was reused with different content.",
                "Use a new request and idempotency identity.",
            )
        if prior_request_id is not None and prior_request_id != request_id:
            raise ProviderInputError(
                "failed",
                "request-conflict",
                "An idempotency identity was reused for another request.",
                "Use a new request and idempotency identity.",
            )

        try:
            events, deferred = self._execute(request_id, _object(message["payload"], "payload"))
            evidence_files = self._evidence_snapshot(events)
        except ProviderInputError as error:
            events = _failure_events(request_id, 1, error)
            evidence_files = ()
            deferred = False
        attempt = Attempt(
            encoded,
            idempotency_key,
            tuple(events),
            evidence_files,
            deferred,
        )
        self.attempts[request_id] = attempt
        self.request_ids_by_key[idempotency_key] = request_id
        self.active_request_id = request_id if deferred else None
        return [dict(event) for event in events]

    def _execute(
        self,
        request_id: str,
        payload: dict[str, object],
    ) -> tuple[list[dict[str, object]], bool]:
        provider = _object(payload.get("agentSkills"), "agent-skills")
        workflow_id = _string(provider.get("workflowId"), "workflow id")
        binding = self._binding(workflow_id, _object(provider.get("release"), "release"))
        if provider.get("defer") is True:
            return (
                [
                    _event(
                        request_id,
                        1,
                        "progress",
                        "The installed workflow is bound and awaiting host output.",
                        details={"release": binding, "workflowId": workflow_id},
                    )
                ],
                True,
            )

        sequence = 1
        events: list[dict[str, object]] = [
            _event(
                request_id,
                sequence,
                "progress",
                "The request is bound to the installed workflow.",
                details={"release": binding, "workflowId": workflow_id},
            )
        ]
        sequence += 1
        host_failed = False
        host_blocked = False
        for raw in _list(provider.get("hostEvents"), "host events"):
            mapped, is_blocked, is_failed = self._host_event(request_id, sequence, raw)
            events.append(mapped)
            sequence += 1
            host_blocked = host_blocked or is_blocked
            host_failed = host_failed or is_failed
        if host_failed or host_blocked:
            outcome = "failed" if host_failed else "blocked"
            events.append(
                _event(
                    request_id,
                    sequence,
                    "terminal",
                    "A host-native operation did not complete successfully.",
                    outcome=outcome,
                    action="Resolve the reported host-native operation before retrying.",
                )
            )
            return events, False

        if workflow_id != "review-work":
            raise ProviderInputError(
                "blocked",
                "workflow-unsupported",
                "The installed workflow does not emit retained review evidence.",
                "Select review-work for a review evidence request.",
            )
        review_document = _object(provider.get("review"), "review")
        result, round_trees, findings = self._review(review_document)
        if result.status != "aligned":
            failure = bool(result.failures)
            error = ProviderInputError(
                "failed" if failure else "blocked",
                "review-failed" if failure else "review-incomplete",
                "The host review did not establish an aligned final pass.",
                result.action,
            )
            events.extend(_failure_events(request_id, sequence, error))
            return events, False

        recorded_at = _string(provider.get("recordedAt"), "recorded timestamp")
        if TIMESTAMP.fullmatch(recorded_at) is None:
            raise ProviderInputError(
                "failed",
                "timestamp-malformed",
                "The review timestamp is malformed.",
                "Provide a UTC RFC 3339 timestamp.",
            )
        try:
            manifest_path = self._write_manifest(
                payload,
                request_id,
                binding,
                result,
                round_trees,
                findings,
                recorded_at,
            )
        except OSError as error:
            raise ProviderInputError(
                "failed",
                "evidence-write-failed",
                "The review evidence could not be written to the allocated run root.",
                "Restore the allocated run root and start a new attempt.",
            ) from error
        manifest_reference = self._opaque_reference(manifest_path.as_posix())
        events.append(
            _event(
                request_id,
                sequence,
                "evidence",
                "A complete aligned review manifest was produced.",
                evidenceId=f"review-{hashlib.sha256(request_id.encode()).hexdigest()[:24]}",
                details={
                    "evidenceReference": manifest_reference,
                    "payloadSpec": "review.green/1",
                    "release": binding,
                },
            )
        )
        sequence += 1
        events.append(
            _event(
                request_id,
                sequence,
                "terminal",
                "The installed review workflow completed successfully.",
                outcome="success",
                result={"manifestPath": manifest_path.as_posix()},
            )
        )
        return events, False

    def _binding(
        self,
        workflow_id: str,
        expected_release: dict[str, object],
    ) -> dict[str, str]:
        release = self.receipt["release"]
        if expected_release != release:
            raise ProviderInputError(
                "blocked",
                "release-mismatch",
                "The request does not name the exact active release.",
                "Refresh the request from the active release identity.",
            )
        workflow_path = f"skills/{workflow_id}/SKILL.md"
        record = next(
            (item for item in self.receipt["files"] if item["path"] == workflow_path),
            None,
        )
        if record is None:
            raise ProviderInputError(
                "blocked",
                "workflow-unsupported",
                "The requested workflow is not installed in the active release.",
                "Select a workflow present in the active profile.",
            )
        return {
            "archiveSha256": release["archiveSha256"],
            "metadataSha256": release["metadataSha256"],
            "profile": release["profile"],
            "releaseId": release["releaseId"],
            "workflowSha256": record["sha256"],
        }

    def _host_event(
        self,
        request_id: str,
        sequence: int,
        value: object,
    ) -> tuple[dict[str, object], bool, bool]:
        document = _object(value, "host event")
        kind = document.get("kind")
        operation = document.get("operation")
        reference = document.get("reference")
        if (
            kind not in HOST_EVENT_KINDS
            or operation not in HOST_OPERATIONS
            or not isinstance(reference, str)
            or not reference
        ):
            raise ProviderInputError(
                "failed",
                "host-event-malformed",
                "A host-native event is malformed or unsupported.",
                "Map the host output to a declared operation and event kind.",
            )
        details = {
            "operation": operation,
            "reference": self._opaque_reference(reference),
        }
        if kind == "progress":
            return (
                _event(
                    request_id,
                    sequence,
                    "progress",
                    f"Host-native {operation} progress was observed.",
                    details=details,
                ),
                False,
                False,
            )
        if kind == "evidence":
            return (
                _event(
                    request_id,
                    sequence,
                    "evidence",
                    f"Host-native {operation} evidence was retained.",
                    evidenceId=f"host-{sequence}",
                    details=details,
                ),
                False,
                False,
            )
        failed = kind == "failure"
        return (
            _event(
                request_id,
                sequence,
                "blocker",
                f"Host-native {operation} {'failed' if failed else 'is blocked'}.",
                blockerId=f"host-{sequence}",
                action="Inspect the host-native operation and retry safely.",
                details=details,
            ),
            not failed,
            failed,
        )

    @staticmethod
    def _review(
        document: dict[str, object],
    ) -> tuple[ReviewResult, tuple[str, ...], tuple[dict[str, object], ...]]:
        required = tuple(
            _string(item, "review lens")
            for item in _list(document.get("requiredLenses"), "required lenses")
        )
        raw_rounds = _list(document.get("rounds"), "review rounds")
        round_trees: list[str] = []
        rounds: list[tuple[ReviewLensResult, ...]] = []
        for raw_round in raw_rounds:
            round_document = _object(raw_round, "review round")
            tree = _string(round_document.get("preparedTreeSha"), "prepared tree")
            if re.fullmatch(r"[a-f0-9]{40}", tree) is None:
                raise ProviderInputError(
                    "failed",
                    "review-malformed",
                    "A review round has an invalid prepared tree.",
                    "Bind every review round to a Git tree identity.",
                )
            round_trees.append(tree)
            results = []
            for raw_result in _list(round_document.get("results"), "review results"):
                result = _object(raw_result, "review result")
                results.append(
                    ReviewLensResult(
                        lens=_string(result.get("lens"), "review lens"),
                        outcome=_string(result.get("outcome"), "review outcome"),
                        findings=tuple(
                            _string(item, "review finding")
                            for item in _list(result.get("findings"), "review findings")
                        ),
                        evidence=tuple(
                            _string(item, "review evidence")
                            for item in _list(result.get("evidence"), "review evidence")
                        ),
                        failure=(
                            _string(result["failure"], "review failure")
                            if result.get("failure") is not None
                            else None
                        ),
                    )
                )
            rounds.append(tuple(results))
        max_rounds = document.get("maxRounds")
        if type(max_rounds) is not int:
            raise ProviderInputError(
                "failed",
                "review-malformed",
                "The review round bound is malformed.",
                "Provide a positive integer review round bound.",
            )
        try:
            result = review_work(
                ReviewRequest(
                    required_lenses=required,
                    rounds=tuple(rounds),
                    max_rounds=max_rounds,
                )
            )
        except ValueError as error:
            raise ProviderInputError(
                "failed",
                "review-malformed",
                "The review output failed the portable workflow contract.",
                "Correct the host review output, then retry with a new attempt.",
            ) from error
        findings = DeliveryRailsProvider._findings(
            _list(document.get("findings"), "typed findings"), result
        )
        return result, tuple(round_trees), findings

    @staticmethod
    def _findings(
        raw_findings: list[object],
        result: ReviewResult,
    ) -> tuple[dict[str, object], ...]:
        findings: list[dict[str, object]] = []
        sources: list[str] = []
        ids: set[str] = set()
        for raw in raw_findings:
            document = _object(raw, "typed finding")
            finding_id = document.get("id")
            severity = document.get("severity")
            scope = document.get("scope")
            disposition = document.get("disposition")
            source = document.get("source")
            actionable = document.get("actionable")
            blocking = document.get("blocking")
            deferred = document.get("deferredIssueId")
            if (
                not isinstance(finding_id, str)
                or not finding_id
                or finding_id in ids
                or severity not in FINDING_SEVERITIES
                or scope not in FINDING_SCOPES
                or disposition not in FINDING_DISPOSITIONS
                or type(actionable) is not bool
                or type(blocking) is not bool
                or not isinstance(source, str)
                or not source
            ):
                raise ProviderInputError(
                    "failed",
                    "finding-malformed",
                    "A typed review finding is malformed.",
                    "Provide complete unique finding evidence.",
                )
            if blocking or (actionable and disposition not in {"resolved", "pre_existing", "deferred"}):
                raise ProviderInputError(
                    "blocked",
                    "finding-unresolved",
                    "A review finding is not eligible for green evidence.",
                    "Resolve or validly defer every actionable finding.",
                )
            if disposition == "deferred":
                if not (
                    actionable
                    and not blocking
                    and severity in {"P2", "P3"}
                    and scope == "expansion"
                    and isinstance(deferred, str)
                    and re.fullmatch(r"[A-Z][A-Z0-9]*-[0-9]+", deferred)
                ):
                    raise ProviderInputError(
                        "failed",
                        "finding-malformed",
                        "A deferred review finding is malformed.",
                        "Use the declared low-severity expansion deferral shape.",
                    )
            elif deferred is not None:
                raise ProviderInputError(
                    "failed",
                    "finding-malformed",
                    "A non-deferred finding names a deferred work item.",
                    "Remove the deferred work reference or use a valid deferral.",
                )
            ids.add(finding_id)
            sources.append(source)
            retained = {
                "actionable": actionable,
                "blocking": blocking,
                "disposition": disposition,
                "id": f"finding-{len(findings) + 1}",
                "scope": scope,
                "severity": severity,
            }
            if deferred is not None:
                retained["deferredIssueId"] = deferred
            findings.append(retained)
        reviewed_sources = [finding for dissent in result.dissent for finding in dissent.findings]
        if set(sources) != set(reviewed_sources):
            raise ProviderInputError(
                "failed",
                "finding-incomplete",
                "Typed review findings do not cover the complete review history.",
                "Provide one typed finding for every surfaced review finding.",
            )
        return tuple(findings)

    @staticmethod
    def _candidate(payload: dict[str, object]) -> dict[str, object]:
        candidate = _object(payload.get("candidate"), "candidate")
        required = {"base", "deliverable", "treeSha", "vcs", "workspaceId"}
        if not required <= set(candidate) or not set(candidate) <= required | {"headSha"}:
            raise ProviderInputError(
                "failed",
                "candidate-malformed",
                "The candidate binding is incomplete.",
                "Provide the consumer-owned candidate binding.",
            )
        tree = candidate.get("treeSha")
        head = candidate.get("headSha")
        base = _object(candidate.get("base"), "candidate base")
        deliverable = _object(candidate.get("deliverable"), "candidate deliverable")
        if (
            candidate.get("vcs") != "git"
            or not _git_oid(tree)
            or (head is not None and not _git_oid(head))
            or set(base) != {"mergeBaseSha", "ref", "tipSha"}
            or not _git_oid(base.get("mergeBaseSha"))
            or not _git_oid(base.get("tipSha"))
            or not isinstance(base.get("ref"), str)
            or not base.get("ref")
            or set(deliverable) != {"digest", "identity"}
            or not _sha256(deliverable.get("digest"))
            or not isinstance(deliverable.get("identity"), str)
            or not deliverable.get("identity")
            or not isinstance(candidate.get("workspaceId"), str)
            or not candidate.get("workspaceId")
        ):
            raise ProviderInputError(
                "failed",
                "candidate-malformed",
                "The candidate binding is malformed.",
                "Provide a valid Git candidate binding.",
            )
        return candidate

    def _write_manifest(
        self,
        payload: dict[str, object],
        request_id: str,
        binding: dict[str, str],
        result: ReviewResult,
        round_trees: tuple[str, ...],
        findings: tuple[dict[str, object], ...],
        recorded_at: str,
    ) -> Path:
        provider_id = _string(payload.get("providerId"), "provider id")
        run_id = _string(payload.get("runId"), "run id")
        if PROVIDER_ID.fullmatch(provider_id) is None or run_id != request_id:
            raise ProviderInputError(
                "failed",
                "attempt-binding-malformed",
                "The provider attempt identity is malformed.",
                "Use the negotiated request identity for this provider attempt.",
            )
        obligations = _list(payload.get("obligationIds"), "obligations")
        if obligations != ["review.green"]:
            raise ProviderInputError(
                "blocked",
                "obligation-unsupported",
                "This provider can emit only the aligned review obligation.",
                "Invoke a provider that covers the requested obligations.",
            )
        candidate = self._candidate(payload)
        if not round_trees or round_trees[-1] != candidate["treeSha"]:
            raise ProviderInputError(
                "blocked",
                "candidate-mismatch",
                "The final review pass does not match the requested candidate.",
                "Review the exact requested candidate before emitting evidence.",
            )
        run_root = Path(_string(payload.get("runRoot"), "run root"))
        if not run_root.is_absolute() or run_root.is_symlink() or not run_root.is_dir():
            raise ProviderInputError(
                "failed",
                "run-root-malformed",
                "The consumer-allocated run root is unavailable.",
                "Provide an existing non-symlink run root.",
            )
        final_pass_id = f"pass-{len(result.rounds)}"
        provider = {
            "finalPassId": final_pass_id,
            "id": provider_id,
            "runId": request_id,
            "version": f"{binding['releaseId']}+sha256.{binding['archiveSha256']}",
        }
        artifacts: list[dict[str, str]] = []
        reviewers_root = run_root / "reviewers"
        reviewers_root.mkdir(exist_ok=True)
        if reviewers_root.is_symlink() or not reviewers_root.is_dir():
            raise ProviderInputError(
                "failed",
                "run-root-malformed",
                "The reviewer artifact root is unsafe.",
                "Restore the consumer-allocated run root.",
            )
        for lens in result.rounds[-1]:
            name = hashlib.sha256(lens.lens.encode()).hexdigest()[:24] + ".json"
            relative = f"reviewers/{name}"
            approval = {
                "candidate": candidate,
                "provider": {
                    "finalPassId": final_pass_id,
                    "id": provider_id,
                    "runId": request_id,
                },
                "result": "approved",
                "reviewerId": lens.lens,
                "schemaVersion": 1,
                "workspaceId": candidate["workspaceId"],
            }
            contents = canonical_json(approval).encode()
            atomic_write(run_root / relative, contents)
            artifacts.append(
                {
                    "path": relative,
                    "role": "reviewer-approval",
                    "sha256": hashlib.sha256(contents).hexdigest(),
                }
            )
        finding_counts = {
            severity: sum(finding["severity"] == severity for finding in findings)
            for severity in ("P0", "P1", "P2", "P3")
        }
        deferred_ids = sorted(
            {
                str(finding["deferredIssueId"])
                for finding in findings
                if "deferredIssueId" in finding
            }
        )
        deferred_count = sum(
            finding["disposition"] == "deferred" for finding in findings
        )
        review_payload = {
            "editedAfterFinalPass": False,
            "finalized": True,
            "findings": list(findings),
            "reviewers": {
                "completed": [lens.lens for lens in result.rounds[-1]],
                "failed": [],
                "selected": [lens.lens for lens in result.rounds[-1]],
                "timedOut": [],
            },
            "telemetry": {
                "deferredExpansionCount": deferred_count,
                "deferredIssueIds": deferred_ids,
                "findingCounts": finding_counts,
                "iterationCount": len(result.rounds),
            },
            "verdict": "green",
        }
        manifest = {
            "artifacts": artifacts,
            "attestation": {"level": "self", "signatures": []},
            "candidate": candidate,
            "claims": [
                {
                    "obligation": "review.green",
                    "payload": review_payload,
                    "payloadSpec": "review.green/1",
                }
            ],
            "provider": provider,
            "recordedAt": recorded_at,
            "repository": None,
            "runHistory": [
                {"evaluatedInPassId": f"pass-{index}", "preparedTreeSha": tree}
                for index, tree in enumerate(round_trees, start=1)
            ],
            "spec": "delivery-evidence/1",
        }
        path = run_root / "manifest.json"
        atomic_write(path, canonical_json(manifest).encode())
        return path

    @staticmethod
    def _evidence_snapshot(
        events: list[dict[str, object]],
    ) -> tuple[tuple[str, str], ...]:
        terminal = events[-1]
        if terminal.get("kind") != "terminal" or terminal.get("outcome") != "success":
            return ()
        result = _object(terminal.get("result"), "terminal result")
        manifest_path = Path(_string(result.get("manifestPath"), "manifest path"))
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            document = _object(manifest, "evidence manifest")
            artifacts = _list(document.get("artifacts"), "evidence artifacts")
            files = [(manifest_path, sha256_file(manifest_path))]
            for raw in artifacts:
                artifact = _object(raw, "evidence artifact")
                relative = Path(_string(artifact.get("path"), "artifact path"))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("unsafe artifact path")
                path = manifest_path.parent / relative
                digest = _string(artifact.get("sha256"), "artifact digest")
                if sha256_file(path) != digest:
                    raise ValueError("artifact digest mismatch")
                files.append((path, digest))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ProviderInputError(
                "failed",
                "evidence-incomplete",
                "The produced review evidence is incomplete or unreadable.",
                "Rebuild complete review evidence in the allocated run root.",
            ) from error
        return tuple((path.as_posix(), digest) for path, digest in files)

    @staticmethod
    def _revalidate_evidence(attempt: Attempt) -> None:
        try:
            valid = all(
                not Path(path).is_symlink()
                and Path(path).is_file()
                and sha256_file(Path(path)) == digest
                for path, digest in attempt.evidence_files
            )
        except OSError:
            valid = False
        if not valid:
            raise ProviderInputError(
                "failed",
                "evidence-replay-invalid",
                "Cached success evidence no longer matches the completed attempt.",
                "Start a new attempt and rebuild the review evidence.",
            )

    def _cancel(self, message: dict[str, object]) -> list[dict[str, object]]:
        request_id = message.get("requestId")
        cancellation_id = message.get("cancellationId")
        if not (
            set(message) <= {"cancellationId", "kind", "reason", "requestId", "version"}
            and {"cancellationId", "kind", "requestId", "version"} <= set(message)
            and message.get("version") == PROTOCOL_VERSION
            and isinstance(request_id, str)
            and isinstance(cancellation_id, str)
            and IDENTIFIER.fullmatch(cancellation_id)
            and (
                message.get("reason") is None
                or (
                    isinstance(message.get("reason"), str)
                    and 0 < len(message["reason"]) <= 1024
                )
            )
        ):
            raise ProviderInputError(
                "failed",
                "cancellation-malformed",
                "The cancellation request is malformed.",
                "Send a cancellation for an accepted attempt.",
            )
        prior = self.cancellations.get(cancellation_id)
        if prior is not None:
            if prior[0] != request_id:
                raise ProviderInputError(
                    "failed",
                    "cancellation-conflict",
                    "A cancellation identity was reused for another attempt.",
                    "Use a new cancellation identity.",
                )
            return [dict(event) for event in prior[1]]
        attempt = self.attempts.get(request_id)
        if attempt is None or not attempt.deferred or self.active_request_id != request_id:
            raise ProviderInputError(
                "failed",
                "cancellation-unavailable",
                "The cancellation does not name an active accepted attempt.",
                "Cancel only an active accepted attempt.",
            )
        events = (
            _event(
                request_id,
                len(attempt.events) + 1,
                "terminal",
                "The provider attempt was cancelled.",
                outcome="cancelled",
                action="Start a new attempt if the review is still required.",
            ),
        )
        self.cancellations[cancellation_id] = (request_id, events)
        self.attempts[request_id] = Attempt(
            attempt.encoded_request,
            attempt.idempotency_key,
            (*attempt.events, *events),
            attempt.evidence_files,
            False,
        )
        self.active_request_id = None
        return [dict(event) for event in events]

    def interrupt(self) -> list[dict[str, object]]:
        request_id = self.active_request_id
        if request_id is None:
            return []
        attempt = self.attempts[request_id]
        event = _event(
            request_id,
            len(attempt.events) + 1,
            "terminal",
            "The provider lost the host workflow before a terminal outcome.",
            outcome="indeterminate",
            action="Inspect retained host evidence and start a new attempt.",
        )
        self.attempts[request_id] = Attempt(
            attempt.encoded_request,
            attempt.idempotency_key,
            (*attempt.events, event),
            attempt.evidence_files,
            False,
        )
        self.active_request_id = None
        return [event]


def _write_documents(documents: list[dict[str, object]]) -> None:
    for document in documents:
        sys.stdout.write(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the agent-skills delivery provider.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    provider: DeliveryRailsProvider | None = None
    try:
        provider = DeliveryRailsProvider(arguments.root)
        for line in sys.stdin:
            if not line.strip():
                continue
            _write_documents(provider.handle(json.loads(line)))
        _write_documents(provider.interrupt())
    except (json.JSONDecodeError, OSError, ValueError) as error:
        if provider is not None:
            _write_documents(provider.interrupt())
        detail = (
            f"{error.blocker_id}: {error.summary}"
            if isinstance(error, ProviderInputError)
            else type(error).__name__
        )
        sys.stderr.write(f"provider input rejected: {detail}\n")
        return 2
    finally:
        if provider is not None:
            provider.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
