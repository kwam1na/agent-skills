#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


DRIVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DRIVER_ROOT))

from agent_skills.lifecycle import Lifecycle
from agent_skills.release import RUNTIME_FILES, VerifiedRelease, build_release, verify_release


CONTRACT_PATH = "skills/linear-tracker-adapter/references/adapter-contract.json"
ADAPTER_ROOT = "skills/linear-tracker-adapter"
GRAPH_PATH = "workflows/delivery-v1.json"
TRUSTED_CONTRACT_SHA256 = "82b9bd2de7a3a38b866dbdccca66cc0b37fd253b58a816412984f67484b94f45"
REQUIRED_SCENARIOS = (
    "core-qualification-binding",
    "collector-candidate-binding",
    "linear-install-and-host-discovery",
    "live-host-attestation",
    "live-reconcile-before-retry",
    "controlled-degraded-outcomes",
    "forbidden-alternate-transport",
)
CONTROLLED_BOUNDARY_OUTCOMES = {
    "auth": "auth-required",
    "malformed": "malformed",
    "missing": "unavailable",
    "mutation-ambiguous": "reconciliation-required",
    "mutation-timeout": "reconciliation-required",
    "read-ambiguous": "retry",
    "read-timeout": "retry",
    "reconciliation-ambiguous": "reconciliation-required",
    "reconciliation-auth": "reconciliation-required",
    "reconciliation-malformed": "reconciliation-required",
    "reconciliation-miss": "reconciliation-required",
    "reconciliation-timeout": "reconciliation-required",
    "reconciliation-unavailable": "reconciliation-required",
    "unsupported": "unsupported",
}
EXPECTED_LIVE_EVENTS = (
    ("search", "miss", ("primary",)),
    ("search", "miss", ("dependency",)),
    ("create", "success", ("primary",)),
    ("create", "success", ("dependency",)),
    ("read", "success", ("primary",)),
    ("read", "success", ("dependency",)),
    ("update", "success", ("primary",)),
    ("update", "success", ("dependency",)),
    ("relations", "success", ("primary", "dependency")),
    ("read", "relation-observed", ("primary", "dependency")),
    ("reconciliation", "unique-match", ("primary",)),
    ("update", "canceled", ("primary",)),
    ("update", "canceled", ("dependency",)),
    ("read", "cleanup-confirmed", ("primary",)),
    ("read", "cleanup-confirmed", ("dependency",)),
)
DECLARED_LIVE_OPERATIONS = {
    "create",
    "read",
    "reconciliation",
    "relations",
    "search",
    "update",
}
REFERENCE_PATTERNS = {
    "audit": re.compile(r"linear-audit:[a-f0-9]{32}"),
    "event": re.compile(r"linear-event:[a-f0-9]{32}"),
    "operation": re.compile(r"linear-operation:[a-f0-9]{32}"),
    "receipt": re.compile(r"linear-receipt:[a-f0-9]{32}"),
    "session": re.compile(r"linear-session:[a-f0-9]{32}"),
}
TICKET_ID = re.compile(r"\b[A-Z]\d{2,}-\d+\b")
EXECUTABLE_SUFFIXES = {".cjs", ".js", ".mjs", ".py", ".sh", ".ts", ".tsx"}
DIRECT_LINEAR_ENDPOINTS = ("api.linear.app", "linear.app/api", "linear.app/graphql")
LINEAR_SDK_ACTIONS = (
    "@linear/sdk",
    "from linear import",
    "import linear",
    "linear_client",
    "linear_sdk",
    "linearclient",
)
COMMAND_TRANSPORT = re.compile(r"(?m)^\s*(?:curl|wget)\s+https?://")
HTTP_CLIENT_ACTION = re.compile(
    r"\b(?:aiohttp|httpx|requests|urllib3)\.(?:delete|get|patch|post|put|request)\s*\(|"
    r"\b(?:axios\.(?:delete|get|patch|post|put)|fetch|urlopen)\s*\("
)
LINEAR_SDK_IMPORT = re.compile(
    r"(?m)^\s*(?:from\s+linear(?:\.\w+)*\s+import\b|"
    r"import\s+linear(?:\.\w+)*(?:\s|$)|"
    r"import\b[^\n]*\bfrom\s+['\"]@linear/sdk['\"]|"
    r"(?:import|require)\s*\(\s*['\"]@linear/sdk['\"]\s*\)|"
    r"new\s+linearclient\s*\()"
)


class QualificationFailure(AssertionError):
    pass


class Checkpoint:
    def __init__(self, scenario_id: str, evidence_source: str) -> None:
        self.scenario_id = scenario_id
        self.evidence_source = evidence_source
        self.assertion_count = 0
        self.evidence: list[str] = []

    def check(self, condition: bool, evidence: str, failure: str | None = None) -> None:
        self.assertion_count += 1
        if not condition:
            raise QualificationFailure(failure or evidence)
        self.evidence.append(evidence)

    def retain(self, evidence: list[str]) -> None:
        self.assertion_count += len(evidence)
        self.evidence.extend(evidence)


@contextmanager
def _qualification_host_environment(root: Path):
    injected = {
        "AGENT_SKILLS_EXECUTION_CONTEXT": "maintainer",
        "APPDATA": str(root / "config"),
        "CLAUDE_CONFIG_DIR": str(root / "hosts" / "claude"),
        "CODEX_HOME": str(root / "hosts" / "codex"),
        "HOME": str(root / "home"),
        "USERPROFILE": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "config"),
    }
    saved = {
        name: os.environ.get(name)
        for name in (*injected, "GITHUB_EVENT_NAME")
    }
    try:
        os.environ.pop("GITHUB_EVENT_NAME", None)
        os.environ.update(injected)
        for path in {Path(value) for value in injected.values() if value != "maintainer"}:
            path.mkdir(parents=True, exist_ok=True)
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def digest_bytes(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _digest(value: object) -> str:
    return digest_bytes(canonical_json(value).encode("utf-8"))


def _load_document(path: Path, name: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"linear_qualification.{name}: invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"linear_qualification.{name}: expected an object")
    return value


def _fullmatch(reference: object, kind: str) -> bool:
    return isinstance(reference, str) and REFERENCE_PATTERNS[kind].fullmatch(reference) is not None


def validate_collector_binding(value: object) -> dict:
    prefix = "linear_qualification.collector_binding"
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion",
        "releases",
        "graphSha256",
        "subjectRef",
        "candidateRef",
    }:
        raise ValueError(f"{prefix}: unexpected document shape")
    if value["schemaVersion"] != "agent-skills-linear-collector-binding/1":
        raise ValueError(f"{prefix}: unsupported schema")
    releases = value["releases"]
    if not isinstance(releases, dict) or set(releases) != {"core", "linear"}:
        raise ValueError(f"{prefix}: exact release pair is required")
    digests = [value["graphSha256"]]
    for profile, release in releases.items():
        if not isinstance(release, dict) or set(release) != {
            "archiveSha256",
            "metadataSha256",
            "profile",
            "releaseId",
        }:
            raise ValueError(f"{prefix}: release shape is invalid")
        if (
            release["profile"] != profile
            or not isinstance(release["releaseId"], str)
            or not release["releaseId"].strip()
        ):
            raise ValueError(f"{prefix}: release identity is invalid")
        digests.extend((release["archiveSha256"], release["metadataSha256"]))
    if any(
        not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None
        for digest in digests
    ):
        raise ValueError(f"{prefix}: invalid digest")
    for kind in ("subject", "candidate"):
        reference = value[f"{kind}Ref"]
        if (
            not isinstance(reference, dict)
            or set(reference) != {"schemaVersion", "opaque"}
            or reference["schemaVersion"] != f"workflow-{kind}-ref/1"
            or not isinstance(reference["opaque"], str)
            or not reference["opaque"].strip()
        ):
            raise ValueError(f"{prefix}: invalid versioned opaque reference")
    return value


def load_collector_binding(path: Path) -> dict:
    return validate_collector_binding(_load_canonical(path, "collector_binding"))


def validate_attestation(value: object, expected_binding: dict) -> dict:
    validate_collector_binding(expected_binding)
    if not isinstance(value, dict) or set(value) != {
        "auditReferences",
        "collector",
        "collectorBinding",
        "entities",
        "events",
        "receipt",
        "schemaVersion",
        "sessionReference",
    }:
        raise ValueError("linear_qualification.attestation: unexpected document shape")
    if value["schemaVersion"] != "agent-skills-linear-host-attestation/2":
        raise ValueError("linear_qualification.attestation: unsupported schema")
    validate_collector_binding(value["collectorBinding"])
    if value["collectorBinding"] != expected_binding:
        raise ValueError("linear_qualification.attestation: collector binding mismatch")
    if value["collector"] != {
        "kind": "trusted-host-side",
        "transport": "host-native-linear-mcp",
    }:
        raise ValueError("linear_qualification.attestation: collector is not trusted host MCP")
    references = value["auditReferences"]
    if (
        not isinstance(references, list)
        or len(references) != 2
        or any(not _fullmatch(reference, "audit") for reference in references)
        or len(set(references)) != len(references)
    ):
        raise ValueError("linear_qualification.attestation: audit references are invalid")
    entities = value["entities"]
    if not isinstance(entities, list) or len(entities) != 2:
        raise ValueError("linear_qualification.attestation: entities are invalid")
    expected_roles = ("primary", "dependency")
    operation_markers = []
    entity_references = []
    for entity, role in zip(entities, expected_roles, strict=True):
        if not isinstance(entity, dict) or set(entity) != {
            "auditReference",
            "finalStatus",
            "operationMarker",
            "role",
        }:
            raise ValueError("linear_qualification.attestation: entity shape is invalid")
        if (
            entity["role"] != role
            or entity["finalStatus"] != "canceled"
            or not _fullmatch(entity["auditReference"], "audit")
            or not _fullmatch(entity["operationMarker"], "operation")
        ):
            raise ValueError("linear_qualification.attestation: entity evidence is invalid")
        entity_references.append(entity["auditReference"])
        operation_markers.append(entity["operationMarker"])
    if entity_references != references or len(set(operation_markers)) != 2:
        raise ValueError("linear_qualification.attestation: entity references are inconsistent")
    receipt = value["receipt"]
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {"externalSystem", "reference"}
        or receipt["externalSystem"] != "linear"
        or not _fullmatch(receipt["reference"], "receipt")
    ):
        raise ValueError("linear_qualification.attestation: receipt is invalid")
    if not _fullmatch(value["sessionReference"], "session"):
        raise ValueError("linear_qualification.attestation: session reference is invalid")
    events = value["events"]
    if not isinstance(events, list) or len(events) != len(EXPECTED_LIVE_EVENTS):
        raise ValueError("linear_qualification.attestation: event count is invalid")
    event_references = []
    observed_operations = set()
    for sequence, (event, expected) in enumerate(
        zip(events, EXPECTED_LIVE_EVENTS, strict=True),
        start=1,
    ):
        if not isinstance(event, dict) or set(event) != {
            "eventReference",
            "operation",
            "outcome",
            "roles",
            "sequence",
        }:
            raise ValueError("linear_qualification.attestation: event shape is invalid")
        operation, outcome, roles = expected
        if (
            type(event["sequence"]) is not int
            or event["sequence"] != sequence
            or event["operation"] != operation
            or event["outcome"] != outcome
            or not isinstance(event["roles"], list)
            or tuple(event["roles"]) != roles
            or not _fullmatch(event["eventReference"], "event")
        ):
            raise ValueError("linear_qualification.attestation: event causality is invalid")
        event_references.append(event["eventReference"])
        observed_operations.add(event["operation"])
    if len(set(event_references)) != len(event_references):
        raise ValueError("linear_qualification.attestation: event references are duplicated")
    if observed_operations != DECLARED_LIVE_OPERATIONS:
        raise ValueError("linear_qualification.attestation: operation coverage is incomplete")
    all_references = [
        *references,
        *operation_markers,
        *event_references,
        receipt["reference"],
        value["sessionReference"],
    ]
    if len(set(all_references)) != len(all_references):
        raise ValueError("linear_qualification.attestation: references are duplicated")
    serialized = canonical_json(value)
    if (
        TICKET_ID.search(serialized)
        or "/Users/" in serialized
        or "rawHostPayload" in serialized
        or "rawIdempotencyKey" in serialized
    ):
        raise ValueError("linear_qualification.attestation: forbidden retained data")
    return {
        "liveEvidence": [
            "retained host attestation matches the independently frozen collector binding",
            "retained host attestation covers all six declared operations",
            "retained host attestation records canceled cleanup for both entities",
            "retained attestation names one external Linear receipt reference",
        ],
        "receiptReference": receipt["reference"],
        "reconciliationEvidence": [
            "primary create follows an exact-marker miss",
            "dependency create follows an exact-marker miss",
            "unique reconciliation suppresses any repeat create",
        ],
    }


def _load_canonical(path: Path, name: str) -> dict:
    try:
        contents = path.read_text(encoding="utf-8")
        value = json.loads(contents)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"linear_qualification.{name}: invalid JSON or unavailable input") from error
    if canonical_json(value) != contents:
        raise ValueError(f"linear_qualification.{name}: canonical JSON is required")
    return value


def load_attestation(path: Path, expected_binding: dict) -> dict:
    return validate_attestation(_load_canonical(path, "attestation"), expected_binding)


def controlled_boundary_matrix(contract: dict) -> list[dict]:
    if contract.get("failureOutcomes") != CONTROLLED_BOUNDARY_OUTCOMES:
        raise ValueError("linear_qualification.contract: degraded outcomes changed")
    matrix = []
    for input_name, outcome in sorted(CONTROLLED_BOUNDARY_OUTCOMES.items()):
        mutation_uncertain = input_name.startswith("mutation-")
        action = {
            "auth-required": "restore host authorization before retrying",
            "malformed": "correct the named host contract field before retrying",
            "reconciliation-required": "reconcile the prior mutation and do not repeat it",
            "retry": "retry the read after the declared positive delay",
            "unavailable": "configure the host capability or continue the optional core path",
            "unsupported": "use a supported neutral operation or continue the optional core path",
        }.get(outcome)
        if not isinstance(action, str):
            raise ValueError("linear_qualification.contract: unknown normalized outcome")
        matrix.append(
            {
                "action": action,
                "input": input_name,
                "outcome": outcome,
                "repeatMutation": False if mutation_uncertain else None,
            }
        )
    return matrix


def source_tree_sha256(root: Path) -> str:
    files = [
        (path.relative_to(root).as_posix(), path)
        for path in root.rglob("*")
        if path.is_file() and path.name != "release-manifest.json"
    ]
    records = [
        {
            "path": relative,
            "sha256": digest_bytes(path.read_bytes()),
        }
        for relative, path in sorted(files, key=lambda item: item[0])
    ]
    return _digest(records)


def _candidate_delta(linear_candidate: Path, core_candidate: Path) -> list[Path]:
    changed = []
    for path in sorted(item for item in linear_candidate.rglob("*") if item.is_file()):
        relative = path.relative_to(linear_candidate)
        core_path = core_candidate / relative
        if not core_path.is_file() or path.read_bytes() != core_path.read_bytes():
            changed.append(path)
    return changed


def _actionable_instruction(contents: str, marker: str) -> bool:
    return any(
        marker in line
        and re.match(
            r"\s*(?:[-*]\s*)?(?:call|execute|fetch|import|install|invoke|run|send|use)\b",
            line,
        )
        for line in contents.splitlines()
    )


def _node_http_action(contents: str) -> bool:
    identifier = r"[a-z_$][a-z0-9_$]*"
    module = r"(?:node:)?https?"
    module_aliases = {
        match.group(1)
        for match in re.finditer(
            rf"\b(?:const|let|var)\s+({identifier})\s*=\s*"
            rf"require\(\s*['\"]{module}['\"]\s*\)",
            contents,
        )
    }
    module_aliases.update(
        match.group(1)
        for match in re.finditer(
            rf"\bimport\s+(?:\*\s+as\s+)?({identifier})"
            rf"(?:\s*,\s*\{{[^}}]+\}})?\s+from\s+"
            rf"['\"]{module}['\"]",
            contents,
        )
    )
    function_aliases = {
        match.group(1)
        for match in re.finditer(
            rf"\b(?:const|let|var)\s+({identifier})\s*=\s*"
            rf"require\(\s*['\"]{module}['\"]\s*\)\.(?:get|request)\b",
            contents,
        )
    }
    function_aliases.update(
        match.group(1)
        for module_alias in module_aliases
        for match in re.finditer(
            rf"\b(?:const|let|var)\s+({identifier})\s*=\s*"
            rf"{re.escape(module_alias)}\.(?:get|request)\b",
            contents,
        )
    )
    bindings = [
        match.group(1)
        for pattern in (
            rf"\bimport\s*(?:{identifier}\s*,\s*)?\{{([^}}]+)\}}\s*"
            rf"from\s*['\"]{module}['\"]",
            rf"\b(?:const|let|var)\s*\{{([^}}]+)\}}\s*=\s*"
            rf"require\(\s*['\"]{module}['\"]\s*\)",
        )
        for match in re.finditer(pattern, contents)
    ]
    for binding_list in bindings:
        for binding in binding_list.split(","):
            match = re.fullmatch(
                rf"\s*(get|request)\s*(?:(?:as|:)\s*({identifier}))?\s*",
                binding,
            )
            if match:
                function_aliases.add(match.group(2) or match.group(1))

    endpoint = r"(?:api\.linear\.app|linear\.app/(?:api|graphql))"
    call_suffix = rf"\s*\([^;]{{0,512}}?{endpoint}"
    if re.search(
        rf"require\(\s*['\"]{module}['\"]\s*\)\.(?:get|request){call_suffix}",
        contents,
    ):
        return True
    if any(
        re.search(rf"\b{re.escape(alias)}\.(?:get|request){call_suffix}", contents)
        for alias in module_aliases
    ):
        return True
    return any(
        re.search(rf"\b{re.escape(alias)}{call_suffix}", contents)
        for alias in function_aliases
    )


def inspect_candidate_transport_boundary(
    linear_candidate: Path,
    core_candidate: Path,
    contract: dict,
) -> list[str]:
    contract_path = linear_candidate / CONTRACT_PATH
    contract_bytes = contract_path.read_bytes()
    if digest_bytes(contract_bytes) != TRUSTED_CONTRACT_SHA256:
        raise QualificationFailure("candidate contract differs from the trusted contract")
    candidate_contract = json.loads(contract_bytes)
    if candidate_contract != contract:
        raise QualificationFailure("candidate contract differs from the verified input")
    if contract.get("transportBoundary", {}).get("mode") != "host-native-mcp-only":
        raise QualificationFailure("candidate transport mode is not host-native MCP only")
    if set(contract.get("allowedHostOperations", ())) != DECLARED_LIVE_OPERATIONS:
        raise QualificationFailure("host-native MCP capability set is incomplete")

    for path in _candidate_delta(linear_candidate, core_candidate):
        relative = path.relative_to(linear_candidate).as_posix()
        if relative == CONTRACT_PATH:
            continue
        # The boundary guards how the workflow's tracker operations reach Linear; the delivery views are a
        # separate configured skill whose pages and snapshot commands talk to the providers they are set up for.
        if relative.startswith("skills/delivery-views/"):
            continue
        executable = path.suffix in EXECUTABLE_SUFFIXES
        actionable_document = relative == f"{ADAPTER_ROOT}/SKILL.md"
        if not executable and not actionable_document:
            continue
        contents = path.read_text(encoding="utf-8").casefold()
        command_action = COMMAND_TRANSPORT.search(contents) or re.search(
            r"(?:os\.system|subprocess\.(?:call|run|popen))\s*\([^\n]*(?:curl|wget)",
            contents,
        )
        http_action = HTTP_CLIENT_ACTION.search(contents)
        node_http_action = _node_http_action(contents)
        if any(
            marker in contents
            and (
                http_action
                or node_http_action
                or command_action
                or _actionable_instruction(contents, marker)
            )
            for marker in DIRECT_LINEAR_ENDPOINTS
        ):
            raise QualificationFailure("candidate alternate transport: direct-linear-endpoint")
        if LINEAR_SDK_IMPORT.search(contents) or any(
            _actionable_instruction(contents, marker) for marker in LINEAR_SDK_ACTIONS
        ):
            raise QualificationFailure("candidate alternate transport: linear-sdk")
        if command_action:
            raise QualificationFailure("candidate alternate transport: command-transport")
        if executable and http_action:
            raise QualificationFailure("candidate alternate transport: http-client-action")
    return [
        "supplied Linear candidate delta contains no concrete alternate transport action",
        "host-native MCP capability set remains complete",
    ]


def _record(checkpoint: Checkpoint, result: str, failure: str | None = None) -> dict:
    return {
        "assertionCount": checkpoint.assertion_count,
        "evidence": checkpoint.evidence,
        "evidenceSource": checkpoint.evidence_source,
        "id": checkpoint.scenario_id,
        "result": result,
        **({"failure": failure} if failure else {}),
    }


def _run(scenario_id: str, evidence_source: str, operation) -> dict:
    checkpoint = Checkpoint(scenario_id, evidence_source)
    try:
        operation(checkpoint)
    except QualificationFailure as error:
        return _record(checkpoint, "failed", str(error))
    except Exception as error:
        return _record(checkpoint, "failed", f"unexpected:{type(error).__name__}")
    return _record(checkpoint, "passed")


def _release_view(release: VerifiedRelease, source_tree: str) -> dict[str, str]:
    return {
        "archiveSha256": release.archive_sha256,
        "metadataSha256": release.metadata_sha256,
        "profile": release.profile_id,
        "releaseId": release.release_id,
        "sourceTreeSha256": source_tree,
    }


def validate_candidate_binding(
    binding: dict,
    core: VerifiedRelease,
    linear: VerifiedRelease,
    core_candidate: Path,
    linear_candidate: Path,
) -> list[str]:
    validate_collector_binding(binding)
    for release in (core, linear):
        if binding["releases"][release.profile_id] != {
            "archiveSha256": release.archive_sha256,
            "metadataSha256": release.metadata_sha256,
            "profile": release.profile_id,
            "releaseId": release.release_id,
        }:
            raise ValueError("linear_qualification.collector_binding: verified release mismatch")
    for candidate in (core_candidate, linear_candidate):
        try:
            graph_bytes = (candidate / GRAPH_PATH).read_bytes()
        except OSError as error:
            raise ValueError("linear_qualification.collector_binding: released graph unavailable") from error
        if digest_bytes(graph_bytes) != binding["graphSha256"]:
            raise ValueError("linear_qualification.collector_binding: released graph mismatch")
    return [
        "frozen collector input names both verified four-field release identities",
        "both exact archives contain the independently frozen graph bytes",
        "subject and candidate references match independently frozen versioned opaque input",
    ]


def _core_binding(
    checkpoint: Checkpoint,
    core: VerifiedRelease,
    core_record: dict,
    graph_sha256: str,
) -> None:
    checkpoint.check(core.profile_id == "core", "exact base profile identity verified")
    checkpoint.check(
        core_record.get("release")
        == {
            "archiveSha256": core.archive_sha256,
            "metadataSha256": core.metadata_sha256,
            "profile": core.profile_id,
            "releaseId": core.release_id,
        },
        "base qualification names the exact base archive and metadata",
    )
    checkpoint.check(
        core_record.get("workflowGraphSha256") == graph_sha256,
        "base qualification binds the exact released workflow graph",
    )
    checkpoint.check(
        core_record.get("summary", {}).get("result") == "passed",
        "base qualification is passing",
    )


def _install_linear(
    checkpoint: Checkpoint,
    linear_archive: Path,
    linear_metadata: Path,
) -> None:
    with tempfile.TemporaryDirectory(prefix="agent-skills-linear-install-") as temporary:
        qualification_root = Path(temporary)
        repository = qualification_root / "repository"
        repository.mkdir()
        with _qualification_host_environment(qualification_root):
            subprocess.run(
                ["git", "init", "-q"],
                cwd=repository,
                check=True,
                capture_output=True,
            )
            result = Lifecycle(repository).install(
                linear_archive,
                linear_metadata,
                maintainer=True,
            )
        checkpoint.check(
            result["status"]["lifecycle"] == "current",
            "exact optional profile installs as the current generation",
        )
        checkpoint.check(
            result["status"]["active"]["profile"] == "linear",
            "installed generation retains the optional profile identity",
        )
        checkpoint.check(
            {exposure["host"] for exposure in result["status"]["exposures"]}
            == {"codex", "claude-code"}
            and all(
                exposure["state"] == "current"
                for exposure in result["status"]["exposures"]
            ),
            "both fixed hosts expose the current optional generation",
        )
        host_roots = (
            repository / ".agents" / "skills",
            repository / ".claude" / "skills",
        )
        checkpoint.check(
            all(
                root.is_dir() and root.is_relative_to(qualification_root)
                for root in host_roots
            ),
            "qualification-owned roots isolate both host projections",
        )
        for host in (".agents", ".claude"):
            checkpoint.check(
                (
                    repository
                    / host
                    / "skills"
                    / "linear-tracker-adapter"
                    / "SKILL.md"
                ).is_file(),
                f"{host} discovers the installed optional adapter",
            )


def _controlled_evidence(matrix: list[dict]) -> list[str]:
    return [
        "controlled boundary matrix covers every declared degraded input",
        *(
            f"controlled boundary normalized: {case['input']} -> {case['outcome']}"
            for case in matrix
        ),
        "controlled ambiguous mutations never repeat before reconciliation",
    ]


def _finalize_record(
    candidate: dict,
    core_record_sha256: str,
    attestation_sha256: str,
    receipt_reference: str,
    scenarios: list[dict],
    collector_binding: dict,
    collector_binding_sha256: str,
) -> dict:
    if not isinstance(scenarios, list) or any(
        not isinstance(scenario, dict) for scenario in scenarios
    ):
        raise ValueError("linear_qualification.anti_vacuity: scenarios are invalid")
    ids = tuple(scenario.get("id") for scenario in scenarios)
    if ids != REQUIRED_SCENARIOS:
        raise ValueError("linear_qualification.anti_vacuity: scenarios are missing or reordered")
    for scenario in scenarios:
        evidence = scenario.get("evidence")
        count = scenario.get("assertionCount")
        if (
            scenario.get("result") not in ("passed", "failed")
            or type(count) is not int
            or count < 0
            or not isinstance(evidence, list)
            or any(not isinstance(item, str) or not item.strip() for item in evidence)
            or not isinstance(scenario.get("evidenceSource"), str)
            or not scenario["evidenceSource"].strip()
            or (scenario["result"] == "passed" and (count < 1 or count != len(evidence)))
        ):
            raise ValueError("linear_qualification.anti_vacuity: passing evidence is empty or invalid")
    failed = sum(scenario["result"] != "passed" for scenario in scenarios)
    return {
        "attestationSha256": attestation_sha256,
        "candidate": candidate,
        "collectorBinding": collector_binding,
        "collectorBindingSha256": collector_binding_sha256,
        "coreQualificationSha256": core_record_sha256,
        "proofBoundary": {
            "live": "external MCP provenance is owned by the protected host-native run",
            "offline": (
                "binds the retained attestation bytes to the retained receipt reference "
                "and independently frozen release, graph, subject, and candidate input; "
                "it does not independently prove external calls or collector authorization"
            ),
        },
        "receiptReference": receipt_reference,
        "redaction": {
            "environmentCaptured": False,
            "hostPayloadsCaptured": False,
            "localPathsCaptured": False,
            "retainedEvidence": "canonical redacted host attestation and opaque references",
        },
        "scenarios": scenarios,
        "schemaVersion": "agent-skills-linear-qualification/2",
        "summary": {
            "assertionCount": sum(scenario["assertionCount"] for scenario in scenarios),
            "failedScenarioCount": failed,
            "passedScenarioCount": len(scenarios) - failed,
            "requiredScenarioCount": len(REQUIRED_SCENARIOS),
            "result": "failed" if failed else "passed",
        },
    }


def qualify(
    core_archive: Path,
    core_metadata: Path,
    core_record_path: Path,
    linear_archive: Path,
    linear_metadata: Path,
    attestation_path: Path,
    collector_binding_path: Path,
) -> dict:
    core = verify_release(core_archive, core_metadata)
    linear = verify_release(linear_archive, linear_metadata)
    if core.profile_id != "core" or linear.profile_id != "linear":
        raise ValueError("linear_qualification.profile: exact core and linear profiles are required")
    core_record = _load_document(core_record_path, "core_record")
    binding = load_collector_binding(collector_binding_path)
    attested = load_attestation(attestation_path, binding)
    attestation_bytes = attestation_path.read_bytes()
    with tempfile.TemporaryDirectory(prefix="agent-skills-linear-candidates-") as temporary:
        candidate_root = Path(temporary)
        core_candidate = candidate_root / "core"
        linear_candidate = candidate_root / "linear"
        verify_release(core_archive, core_metadata, extract_to=core_candidate)
        verify_release(linear_archive, linear_metadata, extract_to=linear_candidate)
        binding_evidence = validate_candidate_binding(
            binding,
            core,
            linear,
            core_candidate,
            linear_candidate,
        )
        # Product profiles share runtime bytes, while each verified descriptor
        # binds its own workflow projection. Compare the full component closure
        # before using the core descriptor for the exact core reconstruction.
        core_runtime = core_candidate / "runtime"
        linear_runtime = linear_candidate / "runtime"
        if core_runtime.exists() != linear_runtime.exists():
            raise ValueError("linear_qualification.core_binding: Linear contains different core bytes")
        if core_runtime.exists():
            descriptors = [_load_document(runtime / "runtime.json", "runtime") for runtime in (core_runtime, linear_runtime)]
            for descriptor in descriptors:
                descriptor.pop("workflowContentSha256")
            if descriptors[0] != descriptors[1] or any(
                (core_runtime / name).read_bytes() != (linear_runtime / name).read_bytes()
                for name in RUNTIME_FILES - {"runtime.json"}
            ):
                raise ValueError("linear_qualification.core_binding: Linear contains different core bytes")
        projected_core = build_release(
            linear_candidate,
            candidate_root / "projected-core.zip",
            candidate_root / "projected-core.release.json",
            release_id=core.release_id,
            profile_id="core",
            runtime_directory=core_runtime if core_runtime.exists() else None,
        )
        if projected_core != core:
            raise ValueError("linear_qualification.core_binding: Linear contains different core bytes")
        binding_evidence.append("Linear's core projection exactly matches the qualified core archive and metadata")
        contract = _load_document(linear_candidate / CONTRACT_PATH, "contract")
        scenarios = [
            _run(
                "core-qualification-binding",
                "trusted-checked-qualification",
                lambda checkpoint: _core_binding(
                    checkpoint, core, core_record, binding["graphSha256"],
                ),
            ),
            _run(
                "collector-candidate-binding",
                "independently-frozen-collector-input",
                lambda checkpoint: checkpoint.retain(binding_evidence),
            ),
            _run(
                "linear-install-and-host-discovery",
                "exact-release-runtime",
                lambda checkpoint: _install_linear(
                    checkpoint,
                    linear_archive,
                    linear_metadata,
                ),
            ),
            _run(
                "live-host-attestation",
                "protected-external-live-mcp-sensor",
                lambda checkpoint: checkpoint.retain(attested["liveEvidence"]),
            ),
            _run(
                "live-reconcile-before-retry",
                "protected-external-live-mcp-sensor",
                lambda checkpoint: checkpoint.retain(
                    attested["reconciliationEvidence"]
                ),
            ),
            _run(
                "controlled-degraded-outcomes",
                "deterministic-controlled-boundary-double",
                lambda checkpoint: checkpoint.retain(
                    _controlled_evidence(controlled_boundary_matrix(contract))
                ),
            ),
            _run(
                "forbidden-alternate-transport",
                "supplied-extracted-candidate-sensor",
                lambda checkpoint: checkpoint.retain(
                    inspect_candidate_transport_boundary(
                        linear_candidate,
                        core_candidate,
                        contract,
                    )
                ),
            ),
        ]
        candidate = {
            "core": _release_view(core, source_tree_sha256(core_candidate)),
            "linear": _release_view(linear, source_tree_sha256(linear_candidate)),
        }
    return _finalize_record(
        candidate,
        digest_bytes(core_record_path.read_bytes()),
        digest_bytes(attestation_bytes),
        attested["receiptReference"],
        scenarios,
        binding,
        digest_bytes(collector_binding_path.read_bytes()),
    )


def validate_checked_record(
    record: dict,
    core_archive: Path,
    core_metadata: Path,
    core_record_path: Path,
    linear_archive: Path,
    linear_metadata: Path,
    attestation_path: Path,
    collector_binding_path: Path,
) -> None:
    binding = load_collector_binding(collector_binding_path)
    attested = load_attestation(attestation_path, binding)
    if record.get("attestationSha256") != digest_bytes(attestation_path.read_bytes()):
        raise ValueError("linear_qualification.record: attestation mismatch")
    if record.get("receiptReference") != attested["receiptReference"]:
        raise ValueError("linear_qualification.record: attestation receipt mismatch")
    expected = qualify(
        core_archive,
        core_metadata,
        core_record_path,
        linear_archive,
        linear_metadata,
        attestation_path,
        collector_binding_path,
    )
    if record != expected or expected["summary"]["result"] != "passed":
        raise ValueError("linear_qualification.record: exact record mismatch")


def _atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            target.write(contents)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-archive", type=Path, required=True)
    parser.add_argument("--core-metadata", type=Path, required=True)
    parser.add_argument("--core-record", type=Path, required=True)
    parser.add_argument("--linear-archive", type=Path, required=True)
    parser.add_argument("--linear-metadata", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--collector-binding", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args(arguments)
    inputs = (
        args.core_archive,
        args.core_metadata,
        args.core_record,
        args.linear_archive,
        args.linear_metadata,
        args.attestation,
        args.collector_binding,
    )
    if any(args.record.resolve() == path.resolve() for path in inputs):
        print("qualification failed: record must not overwrite an input", file=sys.stderr)
        return 1
    try:
        args.record.unlink(missing_ok=True)
        record = qualify(
            args.core_archive,
            args.core_metadata,
            args.core_record,
            args.linear_archive,
            args.linear_metadata,
            args.attestation,
            args.collector_binding,
        )
        _atomic_write(args.record, canonical_json(record))
    except Exception as error:
        print(f"qualification failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(canonical_json(record), end="")
    return 0 if record["summary"]["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
