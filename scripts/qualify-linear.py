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
    "linear-install-and-host-discovery",
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
DECLARED_HOST_OPERATIONS = {
    "create",
    "read",
    "reconciliation",
    "relations",
    "search",
    "update",
}
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


def _candidate_delta(linear_candidate: Path, base_candidate: Path) -> list[Path]:
    changed = []
    for path in sorted(item for item in linear_candidate.rglob("*") if item.is_file()):
        relative = path.relative_to(linear_candidate)
        base_path = base_candidate / relative
        if not base_path.is_file() or path.read_bytes() != base_path.read_bytes():
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
    base_candidate: Path,
    contract: dict,
) -> list[str]:
    """That what the Linear profile adds to its base reaches Linear only through the host's connector.

    The base is the run-delivery product, which holds the delivery views and forge adapters that reach their own
    providers. A file differing from it is in the delta, wherever it sits, so no folder is set aside.
    """
    contract_path = linear_candidate / CONTRACT_PATH
    contract_bytes = contract_path.read_bytes()
    if digest_bytes(contract_bytes) != TRUSTED_CONTRACT_SHA256:
        raise QualificationFailure("candidate contract differs from the trusted contract")
    candidate_contract = json.loads(contract_bytes)
    if candidate_contract != contract:
        raise QualificationFailure("candidate contract differs from the verified input")
    if contract.get("transportBoundary", {}).get("mode") != "host-native-mcp-only":
        raise QualificationFailure("candidate transport mode is not host-native MCP only")
    if set(contract.get("allowedHostOperations", ())) != DECLARED_HOST_OPERATIONS:
        raise QualificationFailure("host-native MCP capability set is incomplete")

    for path in _candidate_delta(linear_candidate, base_candidate):
        relative = path.relative_to(linear_candidate).as_posix()
        if relative == CONTRACT_PATH:
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


def _candidate_graph_sha256(core_candidate: Path, linear_candidate: Path) -> str:
    digests = set()
    for candidate in (core_candidate, linear_candidate):
        try:
            digests.add(digest_bytes((candidate / GRAPH_PATH).read_bytes()))
        except OSError as error:
            raise ValueError("linear_qualification.core_binding: released graph unavailable") from error
    if len(digests) != 1:
        raise ValueError("linear_qualification.core_binding: released graph mismatch")
    return digests.pop()


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


def _core_binding_with_projection(
    checkpoint: Checkpoint,
    core: VerifiedRelease,
    core_record: dict,
    graph_sha256: str,
) -> None:
    _core_binding(checkpoint, core, core_record, graph_sha256)
    # qualify() has already refused any other projection before running a scenario.
    checkpoint.retain([
        "both exact archives contain the same workflow graph bytes",
        "Linear's core projection exactly matches the qualified core archive and metadata",
    ])


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
    scenarios: list[dict],
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
        "candidate": candidate,
        "coreQualificationSha256": core_record_sha256,
        "proofBoundary": {
            "offline": (
                "qualifies the exact archives alone: core binding, install and host discovery, "
                "declared degraded outcomes, and the candidate's transport boundary; "
                "it makes no call to Linear or a host connector"
            ),
        },
        "redaction": {
            "environmentCaptured": False,
            "hostPayloadsCaptured": False,
            "localPathsCaptured": False,
        },
        "scenarios": scenarios,
        "schemaVersion": "agent-skills-linear-qualification/3",
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
    run_delivery_archive: Path,
    run_delivery_metadata: Path,
) -> dict:
    core = verify_release(core_archive, core_metadata)
    linear = verify_release(linear_archive, linear_metadata)
    run_delivery = verify_release(run_delivery_archive, run_delivery_metadata)
    if core.profile_id != "core" or linear.profile_id != "linear" or run_delivery.profile_id != "run-delivery":
        raise ValueError("linear_qualification.profile: exact core, linear and run-delivery profiles are required")
    core_record = _load_document(core_record_path, "core_record")
    with tempfile.TemporaryDirectory(prefix="agent-skills-linear-candidates-") as temporary:
        candidate_root = Path(temporary)
        core_candidate = candidate_root / "core"
        linear_candidate = candidate_root / "linear"
        run_delivery_candidate = candidate_root / "run-delivery"
        verify_release(core_archive, core_metadata, extract_to=core_candidate)
        verify_release(linear_archive, linear_metadata, extract_to=linear_candidate)
        verify_release(run_delivery_archive, run_delivery_metadata, extract_to=run_delivery_candidate)
        graph_sha256 = _candidate_graph_sha256(core_candidate, linear_candidate)
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
        # The transport sensor's base: anything it holds leaves the delta, so it must be exactly what Linear builds on.
        base_runtime = run_delivery_candidate / "runtime"
        if base_runtime.exists() != linear_runtime.exists():
            raise ValueError("linear_qualification.run_delivery_binding: Linear contains different run-delivery bytes")
        projected_run_delivery = build_release(
            linear_candidate,
            candidate_root / "projected-run-delivery.zip",
            candidate_root / "projected-run-delivery.release.json",
            release_id=run_delivery.release_id,
            profile_id="run-delivery",
            runtime_directory=base_runtime if base_runtime.exists() else None,
        )
        if projected_run_delivery != run_delivery:
            raise ValueError("linear_qualification.run_delivery_binding: Linear contains different run-delivery bytes")
        contract = _load_document(linear_candidate / CONTRACT_PATH, "contract")
        scenarios = [
            _run(
                "core-qualification-binding",
                "separately-qualified-core-record",
                lambda checkpoint: _core_binding_with_projection(
                    checkpoint, core, core_record, graph_sha256,
                ),
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
                        run_delivery_candidate,
                        contract,
                    )
                ),
            ),
        ]
        candidate = {
            "core": _release_view(core, source_tree_sha256(core_candidate)),
            "linear": _release_view(linear, source_tree_sha256(linear_candidate)),
            "runDelivery": _release_view(run_delivery, source_tree_sha256(run_delivery_candidate)),
        }
    return _finalize_record(
        candidate,
        digest_bytes(core_record_path.read_bytes()),
        scenarios,
    )


def validate_checked_record(
    record: dict,
    core_archive: Path,
    core_metadata: Path,
    core_record_path: Path,
    linear_archive: Path,
    linear_metadata: Path,
    run_delivery_archive: Path,
    run_delivery_metadata: Path,
) -> None:
    expected = qualify(
        core_archive,
        core_metadata,
        core_record_path,
        linear_archive,
        linear_metadata,
        run_delivery_archive,
        run_delivery_metadata,
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
    parser.add_argument("--run-delivery-archive", type=Path, required=True)
    parser.add_argument("--run-delivery-metadata", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args(arguments)
    inputs = (
        args.core_archive,
        args.core_metadata,
        args.core_record,
        args.linear_archive,
        args.linear_metadata,
        args.run_delivery_archive,
        args.run_delivery_metadata,
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
            args.run_delivery_archive,
            args.run_delivery_metadata,
        )
        _atomic_write(args.record, canonical_json(record))
    except Exception as error:
        print(f"qualification failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(canonical_json(record), end="")
    return 0 if record["summary"]["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
