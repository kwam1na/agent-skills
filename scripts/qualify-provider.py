#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


DRIVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DRIVER_ROOT))

from agent_skills.fs import sha256_file
from agent_skills.lifecycle import Lifecycle
from agent_skills.release import VerifiedRelease, verify_release


PROTOCOL_VERSION = "delivery-provider-rails/1"
OPERATIONS = ("create", "read", "update", "search", "relations", "reconciliation")
SCENARIOS = (
    "fresh-install-and-startup",
    "success-and-native-operations",
    "blocked",
    "failed",
    "cancelled",
    "interrupted",
    "duplicate-and-reconciliation",
    "release-mismatch",
    "unsupported-version",
    "evidence-completeness",
    "base-move-reopening",
)
TREE = "b" * 40
DIGEST = "a" * 64
RAW_REFERENCE = "raw-provider-reference"


class QualificationFailure(AssertionError):
    pass


class Checkpoint:
    def __init__(self, scenario_id: str) -> None:
        self.scenario_id = scenario_id
        self.assertion_count = 0
        self.evidence: list[str] = []

    def check(self, condition: bool, evidence: str) -> None:
        self.assertion_count += 1
        if not condition:
            raise QualificationFailure(evidence)
        self.evidence.append(evidence)

    def record(self) -> dict[str, object]:
        return {
            "assertionCount": self.assertion_count,
            "evidence": self.evidence,
            "id": self.scenario_id,
            "result": "passed",
        }


def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


@contextmanager
def isolated_host_environment(root: Path):
    injected = {
        "AGENT_SKILLS_EXECUTION_CONTEXT": "maintainer",
        "APPDATA": str(root / "config"),
        "CLAUDE_CONFIG_DIR": str(root / "hosts" / "claude"),
        "CODEX_HOME": str(root / "hosts" / "codex"),
        "HOME": str(root / "home"),
        "USERPROFILE": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "config"),
    }
    saved = {name: os.environ.get(name) for name in (*injected, "GITHUB_EVENT_NAME")}
    try:
        os.environ.pop("GITHUB_EVENT_NAME", None)
        os.environ.update(injected)
        for value in injected.values():
            if value != "maintainer":
                Path(value).mkdir(parents=True, exist_ok=True)
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class InstalledProvider:
    def __init__(self, generation: Path, repository: Path) -> None:
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        self.process = subprocess.Popen(
            [sys.executable, "-B", "-m", "agent_skills.provider", "--root", str(repository)],
            cwd=generation,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.closed = False

    def _read(self) -> dict[str, object]:
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if not line:
            assert self.process.stderr is not None
            raise QualificationFailure(self.process.stderr.read() or "installed provider stopped")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise QualificationFailure("installed provider emitted a non-object")
        return value

    def exchange(self, message: dict[str, object], *, deferred: bool = False) -> list[dict[str, object]]:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message, sort_keys=True, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        if message["kind"] in {"negotiate", "cancel"} or deferred:
            return [self._read()]
        events = []
        while True:
            event = self._read()
            events.append(event)
            if event.get("kind") == "terminal":
                return events

    def interrupt(self) -> list[dict[str, object]]:
        try:
            assert self.process.stdin is not None
            self.process.stdin.close()
            assert self.process.stdout is not None
            return [json.loads(line) for line in self.process.stdout if line.strip()]
        finally:
            self.close()

    def reject(self, message: dict[str, object]) -> tuple[int | None, dict[str, object] | None, str]:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message, sort_keys=True, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if line:
            return None, json.loads(line), ""
        return_code = self.process.wait(timeout=10)
        assert self.process.stderr is not None
        return return_code, None, self.process.stderr.read()

    def close(self) -> None:
        if self.closed:
            return
        try:
            if self.process.poll() is None:
                assert self.process.stdin is not None
                if not self.process.stdin.closed:
                    try:
                        self.process.stdin.close()
                    except OSError:
                        pass
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=10)
        finally:
            for stream in (
                self.process.stdin,
                self.process.stdout,
                self.process.stderr,
            ):
                if stream is not None and not stream.closed:
                    stream.close()
            self.closed = True


def negotiate(provider: InstalledProvider, *versions: str) -> dict[str, object]:
    return provider.exchange({"kind": "negotiate", "supportedVersions": list(versions)})[0]


def aligned_review() -> dict[str, object]:
    return {
        "findings": [],
        "maxRounds": 2,
        "requiredLenses": ["correctness", "testing"],
        "rounds": [
            {
                "preparedTreeSha": TREE,
                "results": [
                    {
                        "evidence": ["source inspection"],
                        "findings": [],
                        "lens": "correctness",
                        "outcome": "aligned",
                    },
                    {
                        "evidence": ["focused sensor"],
                        "findings": [],
                        "lens": "testing",
                        "outcome": "aligned",
                    },
                ],
            }
        ],
    }


def provider_request(
    release: VerifiedRelease,
    run_root: Path,
    request_id: str,
    *,
    host_events: list[dict[str, object]] | None = None,
    review: dict[str, object] | None = None,
    deferred: bool = False,
    expected_release: dict[str, str] | None = None,
) -> dict[str, object]:
    binding = expected_release or {
        "archiveSha256": release.archive_sha256,
        "metadataSha256": release.metadata_sha256,
        "profile": release.profile_id,
        "releaseId": release.release_id,
    }
    return {
        "idempotencyKey": f"attempt-{request_id}",
        "kind": "request",
        "payload": {
            "candidate": {
                "base": {
                    "mergeBaseSha": "c" * 40,
                    "ref": "origin/main",
                    "tipSha": "d" * 40,
                },
                "deliverable": {"digest": DIGEST, "identity": "deliverable-tree/v1"},
                "headSha": "e" * 40,
                "treeSha": TREE,
                "vcs": "git",
                "workspaceId": "qualification-workspace",
            },
            "gateId": "delivery.gate",
            "obligationIds": ["review.green"],
            "providerId": "agent-skills.review",
            "runId": request_id,
            "runRoot": str(run_root),
            "agentSkills": {
                "defer": deferred,
                "hostEvents": host_events or [],
                "recordedAt": "2026-08-28T12:00:00Z",
                "release": binding,
                "review": review,
                "workflowId": "review-work",
            },
        },
        "requestId": request_id,
        "version": PROTOCOL_VERSION,
    }


def terminal(events: list[dict[str, object]]) -> dict[str, object]:
    if not events or events[-1].get("kind") != "terminal":
        raise QualificationFailure("provider attempt did not terminate")
    return events[-1]


def run_scenario(scenario_id: str, operation) -> dict[str, object]:
    checkpoint = Checkpoint(scenario_id)
    operation(checkpoint)
    return checkpoint.record()


def qualify(archive: Path, metadata: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        extracted = root / "candidate"
        release = verify_release(archive, metadata, extract_to=extracted)
        repository = root / "repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True, capture_output=True)
        with isolated_host_environment(root / "isolated"):
            Lifecycle(repository).install(archive, metadata, maintainer=True)
        generation = repository / ".agent-skills" / "generations" / release.archive_sha256
        provider_path = generation / "agent_skills" / "provider.py"
        graph_path = generation / "workflows" / "delivery-v1.json"

        def session() -> InstalledProvider:
            return InstalledProvider(generation, repository)

        def fresh(checkpoint: Checkpoint) -> None:
            status = Lifecycle(repository).status()
            checkpoint.check(status.lifecycle == "current", "exact candidate installation is current")
            checkpoint.check(
                {item["host"] for item in status.exposures} == {"codex", "claude-code"},
                "qualification-owned repository exposes both fixed hosts",
            )
            checkpoint.check(
                all(
                    (generation / relative).read_bytes() == (extracted / relative).read_bytes()
                    for relative in (
                        "workflows/delivery-v1.json",
                        "skills/diagnose-work/SKILL.md",
                        "skills/obtain-review/SKILL.md",
                    )
                ),
                "installed workflow graph and both new skills match the exact candidate",
            )
            provider = session()
            try:
                response = negotiate(provider, PROTOCOL_VERSION)
                checkpoint.check(response.get("outcome") == "supported", "installed module starts with isolated Python module search")
                checkpoint.check(response.get("selectedVersion") == PROTOCOL_VERSION, "installed module negotiates the exact protocol")
            finally:
                provider.close()

        def success(checkpoint: Checkpoint) -> None:
            run_root = root / "success"
            run_root.mkdir()
            provider = session()
            try:
                negotiate(provider, PROTOCOL_VERSION)
                events = provider.exchange(
                    provider_request(
                        release,
                        run_root,
                        "success-attempt",
                        review=aligned_review(),
                        host_events=[
                            {
                                "kind": "progress",
                                "operation": operation,
                                "reference": RAW_REFERENCE if index == 0 else f"{RAW_REFERENCE}-{operation}",
                            }
                            for index, operation in enumerate(OPERATIONS)
                        ],
                    )
                )
                checkpoint.check(terminal(events).get("outcome") == "success", "aligned installed workflow reaches success")
                observed = [
                    event["details"]["operation"]
                    for event in events
                    if event.get("kind") == "progress" and "operation" in event.get("details", {})
                ]
                checkpoint.check(observed == list(OPERATIONS), "all native operation names survive provider mapping")
                references = [
                    event["details"]["reference"]
                    for event in events
                    if isinstance(event.get("details"), dict) and "reference" in event["details"]
                ]
                checkpoint.check(len(references) == len(set(references)) == len(OPERATIONS), "host references become distinct session-random opaque values")
                provider.close()
                repeated_root = root / "success-repeat"
                repeated_root.mkdir()
                repeated_provider = session()
                try:
                    negotiate(repeated_provider, PROTOCOL_VERSION)
                    repeated_events = repeated_provider.exchange(
                        provider_request(
                            release,
                            repeated_root,
                            "success-repeat-attempt",
                            review=aligned_review(),
                            host_events=[
                                {
                                    "kind": "progress",
                                    "operation": "read",
                                    "reference": RAW_REFERENCE,
                                }
                            ],
                        )
                    )
                    repeated_reference = next(
                        event["details"]["reference"]
                        for event in repeated_events
                        if event.get("details", {}).get("operation") == "read"
                    )
                finally:
                    repeated_provider.close()
                checkpoint.check(
                    terminal(repeated_events).get("outcome") == "success",
                    "fresh comparison session completes the installed workflow",
                )
                checkpoint.check(
                    references[0] != repeated_reference,
                    "the same low-entropy host reference maps differently across fresh sessions",
                )
                predictable = f"opaque:{hashlib.sha256(RAW_REFERENCE.encode()).hexdigest()[:32]}"
                checkpoint.check(
                    predictable not in {references[0], repeated_reference},
                    "opaque references resist practical low-entropy dictionary recovery",
                )
                manifest_path = Path(terminal(events)["result"]["manifestPath"])
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                checkpoint.check(manifest["spec"] == "delivery-evidence/1", "installed provider writes the declared evidence manifest")
                checkpoint.check(manifest["candidate"]["treeSha"] == TREE, "evidence binds the requested candidate tree")
                checkpoint.check(manifest["claims"][0]["payloadSpec"] == "review.green/1", "evidence contains the declared non-empty claim")
                checkpoint.check(
                    manifest["claims"][0]["payload"]["reviewers"]["completed"]
                    == ["correctness", "testing"],
                    "evidence names every completed required lens",
                )
                checkpoint.check(len(manifest["artifacts"]) == 2, "evidence contains one approval per required lens")
                checkpoint.check(
                    all(
                        sha256_file(run_root / artifact["path"]) == artifact["sha256"]
                        for artifact in manifest["artifacts"]
                    ),
                    "all retained approval bytes match their digests",
                )
                retained = canonical_json(events) + canonical_json(manifest) + "".join(
                    (run_root / artifact["path"]).read_text(encoding="utf-8")
                    for artifact in manifest["artifacts"]
                )
                checkpoint.check(RAW_REFERENCE not in retained, "raw host references are absent from retained provider evidence")
            finally:
                provider.close()

        def degraded(checkpoint: Checkpoint, kind: str, outcome: str) -> None:
            run_root = root / outcome
            run_root.mkdir()
            provider = session()
            try:
                negotiate(provider, PROTOCOL_VERSION)
                events = provider.exchange(
                    provider_request(
                        release,
                        run_root,
                        f"{outcome}-attempt",
                        review=aligned_review(),
                        host_events=[{"kind": kind, "operation": "search", "reference": RAW_REFERENCE}],
                    )
                )
                checkpoint.check(terminal(events).get("outcome") == outcome, f"host {kind} maps to terminal {outcome}")
                checkpoint.check(any(event.get("kind") == "blocker" for event in events), f"{outcome} retains an actionable blocker")
                checkpoint.check("retry" in str(terminal(events).get("action", "")), f"{outcome} retains explicit retry guidance")
                checkpoint.check(not (run_root / "manifest.json").exists(), f"{outcome} cannot emit green evidence")
                checkpoint.check(RAW_REFERENCE not in canonical_json(events), f"{outcome} retains no raw host reference")
            finally:
                provider.close()

        def cancelled(checkpoint: Checkpoint) -> None:
            run_root = root / "cancelled"
            run_root.mkdir()
            provider = session()
            try:
                negotiate(provider, PROTOCOL_VERSION)
                started = provider.exchange(
                    provider_request(release, run_root, "cancel-attempt", deferred=True),
                    deferred=True,
                )
                checkpoint.check(started[0].get("kind") == "progress", "deferred attempt is active before cancellation")
                message = {
                    "cancellationId": "cancel-qualification",
                    "kind": "cancel",
                    "requestId": "cancel-attempt",
                    "version": PROTOCOL_VERSION,
                }
                first = provider.exchange(message)
                second = provider.exchange(message)
                checkpoint.check(terminal(first).get("outcome") == "cancelled", "active deferred attempt reaches cancelled")
                checkpoint.check(first == second, "duplicate cancellation is correlated and idempotent")
                checkpoint.check(not (run_root / "manifest.json").exists(), "cancelled attempt emits no green evidence")
            finally:
                provider.close()

        def interrupted(checkpoint: Checkpoint) -> None:
            run_root = root / "interrupted"
            run_root.mkdir()
            provider = session()
            try:
                negotiate(provider, PROTOCOL_VERSION)
                provider.exchange(
                    provider_request(release, run_root, "interrupt-attempt", deferred=True),
                    deferred=True,
                )
                events = provider.interrupt()
                checkpoint.check(terminal(events).get("outcome") == "indeterminate", "host loss reaches indeterminate")
                checkpoint.check(terminal(events).get("requestId") == "interrupt-attempt", "interruption stays correlated to its attempt")
                checkpoint.check(not (run_root / "manifest.json").exists(), "interrupted attempt emits no green evidence")
            finally:
                provider.close()

        def duplicate(checkpoint: Checkpoint) -> None:
            run_root = root / "duplicate"
            run_root.mkdir()
            provider = session()
            try:
                negotiate(provider, PROTOCOL_VERSION)
                message = provider_request(
                    release,
                    run_root,
                    "duplicate-attempt",
                    review=aligned_review(),
                    host_events=[{"kind": "evidence", "operation": "reconciliation", "reference": RAW_REFERENCE}],
                )
                first = provider.exchange(message)
                before = (run_root / "manifest.json").read_bytes()
                second = provider.exchange(message)
                checkpoint.check(first == second, "identical request replays the same accepted attempt")
                checkpoint.check(before == (run_root / "manifest.json").read_bytes(), "duplicate request preserves exact evidence bytes")
                checkpoint.check(
                    sum(
                        event.get("details", {}).get("operation") == "reconciliation"
                        for event in first
                    ) == 1,
                    "caller-owned reconciliation is preserved without an internal retry",
                )
            finally:
                provider.close()

        def release_mismatch(checkpoint: Checkpoint) -> None:
            run_root = root / "release-mismatch"
            run_root.mkdir()
            provider = session()
            try:
                negotiate(provider, PROTOCOL_VERSION)
                wrong_release = {
                    "archiveSha256": "0" * 64,
                    "metadataSha256": release.metadata_sha256,
                    "profile": release.profile_id,
                    "releaseId": release.release_id,
                }
                events = provider.exchange(
                    provider_request(
                        release,
                        run_root,
                        "release-mismatch-attempt",
                        review=aligned_review(),
                        expected_release=wrong_release,
                    )
                )
                checkpoint.check(
                    terminal(events).get("outcome") == "blocked",
                    "wrong expected release fails closed with a blocked terminal",
                )
                checkpoint.check(
                    any(
                        event.get("kind") == "blocker"
                        and event.get("blockerId") == "release-mismatch"
                        for event in events
                    ),
                    "release mismatch retains its typed blocker",
                )
                checkpoint.check(
                    not (run_root / "manifest.json").exists(),
                    "release mismatch emits no green evidence",
                )
            finally:
                provider.close()

        def unsupported(checkpoint: Checkpoint) -> None:
            provider = session()
            try:
                response = negotiate(provider, "future-provider-rails/2")
                checkpoint.check(response.get("outcome") == "unsupported", "unsupported version fails negotiation closed")
                checkpoint.check(response.get("selectedVersion") is None, "unsupported version selects no protocol")
                checkpoint.check(response.get("supportedVersions") == [PROTOCOL_VERSION], "provider reports only its exact supported protocol")
                run_root = root / "unsupported"
                run_root.mkdir()
                return_code, event, detail = provider.reject(
                    provider_request(
                        release,
                        run_root,
                        "unsupported-attempt",
                        review=aligned_review(),
                    )
                )
                checkpoint.check(
                    return_code == 2 and event is None and "request-malformed" in detail,
                    "unsupported negotiation closes the session to later work",
                )
            finally:
                provider.close()

        def evidence(checkpoint: Checkpoint) -> None:
            run_root = root / "empty-evidence"
            run_root.mkdir()
            provider = session()
            try:
                negotiate(provider, PROTOCOL_VERSION)
                empty_review = aligned_review()
                empty_review["requiredLenses"] = []
                empty_review["rounds"] = []
                events = provider.exchange(
                    provider_request(release, run_root, "empty-evidence-attempt", review=empty_review)
                )
                checkpoint.check(terminal(events).get("outcome") == "failed", "empty review evidence fails anti-vacuity checks")
                checkpoint.check(not (run_root / "manifest.json").exists(), "incomplete evidence cannot emit a manifest")
            finally:
                provider.close()

        def base_move_reopening(checkpoint: Checkpoint) -> None:
            # These inputs are independent of the source reducer. Every case is
            # sent to the provider freshly installed from the supplied archive.
            for variant in ("valid", "changed-diff", "unchanged-base", "missing-lens"):
                run_root = root / f"base-reopening-{variant}"
                run_root.mkdir()
                request = provider_request(release, run_root, f"base-reopening-{variant}")
                current = request["payload"]["candidate"]
                previous = deepcopy(current)
                previous["treeSha"] = "f" * 40
                previous["headSha"] = "f" * 40
                previous["base"]["tipSha"] = "1" * 40
                previous["base"]["mergeBaseSha"] = "2" * 40
                first = deepcopy(previous)
                first["treeSha"] = "9" * 40
                first["headSha"] = "9" * 40
                results = aligned_review()["rounds"][0]["results"]
                content = "diff --git a/feature.txt b/feature.txt\n+same delivered bytes\n"
                entry = {"path": "feature.txt", "content": content,
                         "sha256": hashlib.sha256(content.encode()).hexdigest()}
                comparison = {
                    "spec": "delivered-diff-comparison/1",
                    "previousCandidateRef": previous["treeSha"], "candidateRef": current["treeSha"],
                    "previousBaseRef": previous["base"]["ref"], "baseRef": current["base"]["ref"],
                    "previousBaseTipSha": previous["base"]["tipSha"], "baseTipSha": current["base"]["tipSha"],
                    "previousMergeBaseSha": previous["base"]["mergeBaseSha"], "mergeBaseSha": current["base"]["mergeBaseSha"],
                    "previousDiff": [deepcopy(entry)], "diff": [deepcopy(entry)],
                    "evidenceRef": "artifact:executed-diff-comparison",
                }
                rounds = [
                    {"candidate": first, "passId": "pass-1", "round": 1, "results": deepcopy(results)},
                    {"candidate": previous, "passId": "pass-2", "round": 2, "results": deepcopy(results)},
                    {"candidate": deepcopy(current), "passId": "pass-3", "round": 2,
                     "supersedesPassId": "pass-2", "results": deepcopy(results)},
                ]
                review = {"findings": [], "maxRounds": 2, "requiredLenses": ["correctness", "testing"],
                          "rounds": rounds, "baseMoveReopenings": [{"previousPassId": "pass-2",
                          "passId": "pass-3", "comparison": comparison}]}
                if variant == "changed-diff":
                    comparison["diff"][0]["content"] = "changed delivered bytes"
                    comparison["diff"][0]["sha256"] = hashlib.sha256(b"changed delivered bytes").hexdigest()
                elif variant == "unchanged-base":
                    previous["base"] = deepcopy(current["base"])
                    comparison["previousBaseTipSha"] = current["base"]["tipSha"]
                    comparison["previousMergeBaseSha"] = current["base"]["mergeBaseSha"]
                elif variant == "missing-lens":
                    rounds[-1]["results"].pop()
                request["payload"]["agentSkills"]["review"] = review
                request["payload"]["acceptedPayloadSpecs"] = ["review.green/2", "review.green/1"]
                provider = session()
                try:
                    negotiate(provider, PROTOCOL_VERSION)
                    observed = terminal(provider.exchange(request))
                    if variant == "valid":
                        checkpoint.check(observed.get("outcome") == "success", "installed provider reopens at the original bound")
                        manifest = json.loads(Path(observed["result"]["manifestPath"]).read_text())
                        checkpoint.check(manifest["candidate"] == current, "replay evidence keeps the exact final candidate and base")
                        checkpoint.check([item["evaluatedInPassId"] for item in manifest["runHistory"]]
                                         == ["pass-1", "pass-2", "pass-3"], "replay preserves every obtained pass")
                        checkpoint.check(manifest["claims"][0]["payload"]["telemetry"]["iterationCount"] == 3,
                                         "iteration telemetry counts retained passes without charging the reopening to the bound")
                        checkpoint.check(manifest["claims"][0]["payloadSpec"] == "review.green/2", "installed provider selects explicitly accepted v2")
                    else:
                        checkpoint.check(observed.get("outcome") in {"blocked", "failed"}, f"{variant} cannot claim a base-move exclusion")
                        checkpoint.check(not (run_root / "manifest.json").exists(), f"{variant} produces no approval manifest")
                finally:
                    provider.close()

        operations = {
            "fresh-install-and-startup": fresh,
            "success-and-native-operations": success,
            "blocked": lambda checkpoint: degraded(checkpoint, "blocker", "blocked"),
            "failed": lambda checkpoint: degraded(checkpoint, "failure", "failed"),
            "cancelled": cancelled,
            "interrupted": interrupted,
            "duplicate-and-reconciliation": duplicate,
            "release-mismatch": release_mismatch,
            "unsupported-version": unsupported,
            "evidence-completeness": evidence,
            "base-move-reopening": base_move_reopening,
        }
        if len(SCENARIOS) != len(operations) or set(SCENARIOS) != set(operations):
            raise QualificationFailure("required provider scenarios are missing or duplicated")
        scenarios = [run_scenario(name, operations[name]) for name in SCENARIOS]
        if any(
            item["id"] != name
            or item["result"] != "passed"
            or type(item["assertionCount"]) is not int
            or item["assertionCount"] < 1
            or item["assertionCount"] != len(item["evidence"])
            or any(not isinstance(value, str) or not value.strip() for value in item["evidence"])
            for name, item in zip(SCENARIOS, scenarios)
        ):
            raise QualificationFailure("provider scenario evidence is incomplete")
        assertion_count = sum(int(item["assertionCount"]) for item in scenarios)
        return {
            "installedProviderSha256": sha256_file(provider_path),
            "workflowGraphSha256": sha256_file(graph_path),
            "proofBoundary": {
                "external": "Host and connector provenance remains owned by the protected external run that supplies host events.",
                "offline": "This record proves exact installed provider execution, retained evidence behavior, and deterministic replay; controlled host events do not independently prove external calls.",
            },
            "protocolVersion": PROTOCOL_VERSION,
            "redaction": {
                "environmentCaptured": False,
                "localPathsCaptured": False,
                "opaqueReferencesCaptured": False,
                "rawPayloadsCaptured": False,
            },
            "release": {
                "archiveSha256": release.archive_sha256,
                "metadataSha256": release.metadata_sha256,
                "profile": release.profile_id,
                "releaseId": release.release_id,
            },
            "replay": "python3 scripts/qualify-provider.py --archive <archive> --metadata <metadata> --record <record>",
            "scenarios": scenarios,
            "schemaVersion": "agent-skills-provider-qualification/1",
            "summary": {
                "assertionCount": assertion_count,
                "failedScenarioCount": 0,
                "passedScenarioCount": len(scenarios),
                "requiredScenarioCount": len(SCENARIOS),
                "result": "passed",
            },
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qualify one exact installed agent-skills provider release.")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if any(arguments.record.resolve() == path.resolve() for path in (arguments.archive, arguments.metadata)):
        print("provider qualification failed: record must not overwrite an input", file=sys.stderr)
        return 1
    try:
        arguments.record.unlink(missing_ok=True)
        record = qualify(arguments.archive, arguments.metadata)
    except (OSError, ValueError, subprocess.SubprocessError, QualificationFailure) as error:
        print(f"provider qualification failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    arguments.record.parent.mkdir(parents=True, exist_ok=True)
    arguments.record.write_bytes(canonical_json(record).encode("utf-8"))
    print(canonical_json(record), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
