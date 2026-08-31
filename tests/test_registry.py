#!/usr/bin/env python3
"""Mutation sensors for the v1 metadata-only registry."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate-registry.py"


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def with_copy(mutator) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temporary:
        copied = Path(temporary) / "corpus"
        shutil.copytree(ROOT, copied, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        mutator(copied)
        return run(copied)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_missing_provenance_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "provenance.lock.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["entries"] = document["entries"][1:]
        write_json(path, document)

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("missing provenance record for skill: bootstrap-agent-harness" in result.stdout, result.stdout)
    print("verified rejection: missing provenance")


def test_unknown_profile_member_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "profiles" / "core.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["members"].append("not-a-shipped-skill")
        write_json(path, document)

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("unknown profile member: core -> not-a-shipped-skill" in result.stdout, result.stdout)
    print("verified rejection: unknown profile member")


def test_missing_ownership_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "provenance.lock.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["entries"][0].pop("owner", None)
        write_json(path, document)

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("missing ownership: bootstrap-agent-harness" in result.stdout, result.stdout)
    print("verified rejection: missing ownership")


def test_non_releasable_inventory_cannot_be_selected() -> None:
    def mutate(root: Path) -> None:
        path = root / "profiles" / "linear.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["members"].append("execute-linear-ticket")
        write_json(path, document)

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("non-releasable profile member: linear -> execute-linear-ticket" in result.stdout, result.stdout)
    print("verified rejection: non-releasable inventory member")


def test_provenance_ordering_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "provenance.lock.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["entries"].reverse()
        next(
            entry
            for entry in document["entries"]
            if entry["id"] == "execute-linear-ticket"
        )["outputs"].reverse()
        write_json(path, document)

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("provenance entries are not sorted by id" in result.stdout, result.stdout)
    require("provenance outputs are not sorted by path: execute-linear-ticket" in result.stdout, result.stdout)
    print("verified rejection: provenance ordering")


def test_catalog_cannot_duplicate_provenance_classification() -> None:
    def mutate(root: Path) -> None:
        path = root / "catalog.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["skills"][0]["classification"] = "optional-guidance"
        write_json(path, document)

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("catalog duplicates provenance classification: bootstrap-agent-harness" in result.stdout, result.stdout)
    print("verified rejection: duplicated catalog classification")


def test_reordered_object_keys_reject() -> None:
    def mutate(root: Path) -> None:
        path = root / "catalog.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        write_json(path, {
            "skills": document["skills"],
            "schemaVersion": document["schemaVersion"],
        })

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("noncanonical serialization: catalog.json" in result.stdout, result.stdout)
    print("verified rejection: reordered object keys")


def test_core_profile_is_exact_and_deterministic() -> None:
    first = run(ROOT, "--profile", "core")
    second = run(ROOT, "--profile", "core")
    require(first.returncode == 0, first.stdout)
    require(first.stdout == second.stdout, "profile resolution is not deterministic")
    require(json.loads(first.stdout) == {
        "files": [
            "skills/compound-delivery-kernel/SKILL.md",
            "skills/compound-learning/SKILL.md",
            "skills/deliver-work/SKILL.md",
            "skills/deliver-work/agents/openai.yaml",
            "skills/deliver-work/references/capability-contract.md",
            "skills/diagnose-work/SKILL.md",
            "skills/execute-work/SKILL.md",
            "skills/obtain-review/SKILL.md",
            "skills/plan-work/SKILL.md",
            "skills/review-work/SKILL.md",
        ],
        "members": [
            "compound-delivery-kernel",
            "compound-learning",
            "deliver-work",
            "diagnose-work",
            "execute-work",
            "obtain-review",
            "plan-work",
            "review-work",
        ],
        "profile": "core",
    }, first.stdout)
    print("verified resolution: exact deterministic core profile")


def test_unmanifested_charter_rejects() -> None:
    """A charter added to the corpus but absent from the manifest must fail.

    The set-wide coverage claim is what an absent mechanism satisfies for
    free, so this plants the exact drift the claim is supposed to catch.
    """

    def mutate(root: Path) -> None:
        (root / "personas" / "smuggled.md").write_text("# Smuggled charter\n", encoding="utf-8")

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("manifest does not exactly cover the shipped charters" in result.stdout, result.stdout)
    print("verified rejection: unmanifested reviewer charter")


def test_manifested_charter_without_provenance_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "provenance.lock.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["entries"] = [e for e in document["entries"] if e["id"] != "persona-security"]
        write_json(path, document)

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("charter names no provenance record" in result.stdout, result.stdout)
    print("verified rejection: reviewer charter without provenance")


def test_host_bound_charter_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "personas" / "security.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nRun this lens under Claude Code.\n", encoding="utf-8")

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("charter carries a host binding" in result.stdout, result.stdout)
    print("verified rejection: host-bound reviewer charter")


def test_stack_bound_charter_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "personas" / "performance.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nCheck the Rails query cache.\n", encoding="utf-8")

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("charter carries a stack binding" in result.stdout, result.stdout)
    print("verified rejection: stack-bound reviewer charter")


def test_unadjudicated_charter_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "personas" / "source-adjudication.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["adjudications"] = [
            row for row in document["adjudications"] if row["sourceFile"] != "ce-security-reviewer"
        ]
        document["source"]["observedFileCount"] = len(document["adjudications"])
        write_json(path, document)

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("shipped charter has no adapted source decision" in result.stdout, result.stdout)
    print("verified rejection: shipped charter with no adapted source decision")


def test_excluded_source_without_a_declared_rule_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "personas" / "source-adjudication.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        for row in document["adjudications"]:
            if row["disposition"] == "excluded":
                row["exclusionRule"] = "because-i-said-so"
                break
        write_json(path, document)

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("exclusion names an undeclared rule" in result.stdout, result.stdout)
    print("verified rejection: exclusion without a declared rule")


def test_unmodified_persona_set_is_accepted() -> None:
    """The allow side.

    Every rejection above is also satisfied by a validator that rejects
    everything. This pins that the shipped set, untouched, passes — so the
    persona rules are falsifiable in both directions.
    """
    result = with_copy(lambda root: None)
    require(result.returncode == 0, result.stdout)
    payload = json.loads(result.stdout)
    require(payload["ok"] is True, result.stdout)
    require(payload["findingCount"] == 0, result.stdout)
    print("verified acceptance: unmodified reviewer charter set")


def main() -> int:
    tests = [
        test_missing_provenance_rejects,
        test_unknown_profile_member_rejects,
        test_missing_ownership_rejects,
        test_non_releasable_inventory_cannot_be_selected,
        test_provenance_ordering_rejects,
        test_catalog_cannot_duplicate_provenance_classification,
        test_reordered_object_keys_reject,
        test_core_profile_is_exact_and_deterministic,
        test_unmanifested_charter_rejects,
        test_manifested_charter_without_provenance_rejects,
        test_host_bound_charter_rejects,
        test_stack_bound_charter_rejects,
        test_unadjudicated_charter_rejects,
        test_excluded_source_without_a_declared_rule_rejects,
        test_unmodified_persona_set_is_accepted,
    ]
    for test in tests:
        test()
    print(f"ok: {len(tests)} registry mutation sensors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
