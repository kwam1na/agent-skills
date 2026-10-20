#!/usr/bin/env python3
"""Mutation sensors for the v1 metadata-only registry."""

from __future__ import annotations

import hashlib
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
    fixture = "retained-inventory-fixture"

    def mutate(root: Path) -> None:
        body = (
            "---\n"
            f"name: {fixture}\n"
            "description: Synthetic retained inventory used as a selection control.\n"
            "---\n"
            "\n"
            "# Retained Inventory Fixture\n"
            "\n"
            "Retained inventory that no profile may select.\n"
        )
        skill_path = root / "skills" / fixture / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(body, encoding="utf-8")
        output = f"skills/{fixture}/SKILL.md"

        catalog_path = root / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["skills"].append({
            "dependencies": [],
            "externalTools": [],
            "hosts": ["claude-code", "codex"],
            "id": fixture,
            "path": output,
            "profiles": ["linear"],
            "provenanceId": fixture,
        })
        catalog["skills"].sort(key=lambda skill: skill["id"])
        write_json(catalog_path, catalog)

        provenance_path = root / "provenance.lock.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance["entries"].append({
            "classification": "retained-overlay",
            "distribution": "source-only",
            "id": fixture,
            "license": "LicenseRef-Athena-internal",
            "modification": {
                "reason": "Synthetic retained inventory for the selection control.",
                "status": "independently-authored",
            },
            "outputs": [{
                "path": output,
                "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            }],
            "owner": "agent-skills-corpus",
            "source": {
                "kind": "independent-origin",
                "ownerApproval": {
                    "owner": "agent-skills-corpus",
                    "reference": "registry-selection-control",
                },
                "rationale": "Selection control authored for this sensor only.",
            },
        })
        provenance["entries"].sort(key=lambda entry: entry["id"])
        write_json(provenance_path, provenance)

        profile_path = root / "profiles" / "linear.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["members"] = sorted([*profile["members"], fixture])
        profile["expectedFiles"] = sorted([*profile["expectedFiles"], output])
        write_json(profile_path, profile)

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require(f"non-releasable profile member: linear -> {fixture}" in result.stdout, result.stdout)
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
            "skills/execute-work/references/run-observation-contract.md",
            "skills/execute-work/scripts/report_observation.py",
            "skills/obtain-review/SKILL.md",
            "skills/obtain-review/references/round-brief-template.md",
            "skills/orchestrate-work/SKILL.md",
            "skills/orchestrate-work/references/schemas/delivery-journal.json",
            "skills/orchestrate-work/references/schemas/delivery-state.json",
            "skills/orchestrate-work/scripts/_schema.py",
            "skills/orchestrate-work/scripts/delivery_state.py",
            "skills/orchestrate-work/scripts/render_state.py",
            "skills/orchestrate-work/scripts/renderers/markdown.py",
            "skills/orchestrate-work/scripts/renderers/static_page.py",
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
            "orchestrate-work",
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


# Each neutrality arm needs its own falsifier. Deleting one pattern from
# PERSONA_BINDING_PATTERNS must fail a named sensor here, or the host- and
# stack-neutral claim is asserted by a rule set nothing pins.


def test_model_bound_charter_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "personas" / "coherence.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nUse gpt-4 for this lens.\n", encoding="utf-8")

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("charter carries a model binding" in result.stdout, result.stdout)
    print("verified rejection: model-bound reviewer charter")


def test_tool_bound_charter_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "personas" / "scope.md"
        path.write_text(path.read_text(encoding="utf-8") + "\ntools: Grep, Bash\n", encoding="utf-8")

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("charter carries a tool binding" in result.stdout, result.stdout)
    print("verified rejection: tool-bound reviewer charter")


def test_frontmatter_bound_charter_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "personas" / "product.md"
        path.write_text("---\nname: reviewer\n---\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("charter carries a frontmatter binding" in result.stdout, result.stdout)
    print("verified rejection: frontmatter-bound reviewer charter")


def test_presentation_bound_charter_rejects() -> None:
    def mutate(root: Path) -> None:
        path = root / "personas" / "feasibility.md"
        path.write_text(path.read_text(encoding="utf-8") + "\ncolor: red\n", encoding="utf-8")

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("charter carries a presentation binding" in result.stdout, result.stdout)
    print("verified rejection: presentation-bound reviewer charter")


def test_second_host_alternative_is_pinned() -> None:
    """The host arm names several hosts; one sensor pins only the one it plants."""

    def mutate(root: Path) -> None:
        path = root / "personas" / "simplicity.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nRun this lens under Codex.\n", encoding="utf-8")

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("charter carries a host binding" in result.stdout, result.stdout)
    print("verified rejection: second host alternative")


def test_second_stack_alternative_is_pinned() -> None:
    def mutate(root: Path) -> None:
        path = root / "personas" / "reliability.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nCheck the Django settings module.\n", encoding="utf-8")

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("charter carries a stack binding" in result.stdout, result.stdout)
    print("verified rejection: second stack alternative")


def test_inflated_observed_source_count_rejects() -> None:
    """The adjudication declares how many source files it covers.

    Without this, an author could drop a decision row, decrement the count,
    recanonicalise and restamp the digest, and shrink the adjudication with a
    clean validation run.
    """

    def mutate(root: Path) -> None:
        path = root / "personas" / "source-adjudication.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["source"]["observedFileCount"] = document["source"]["observedFileCount"] + 1
        write_json(path, document)

    result = with_copy(mutate)
    require(result.returncode == 1, result.stdout)
    require("decision count does not match the observed source count" in result.stdout, result.stdout)
    print("verified rejection: adjudication count disagreeing with its decisions")


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
        test_model_bound_charter_rejects,
        test_tool_bound_charter_rejects,
        test_frontmatter_bound_charter_rejects,
        test_presentation_bound_charter_rejects,
        test_second_host_alternative_is_pinned,
        test_second_stack_alternative_is_pinned,
        test_inflated_observed_source_count_rejects,
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
