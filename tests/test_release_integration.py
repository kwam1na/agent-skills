"""Exact portable workflow closure and additive routing integration sensors."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_skills import receipt, release
from agent_skills.catalog import resolve_profile
from agent_skills.release import _release_view, build_release, verify_release
from agent_skills.router import WorkflowRequest, route_workflow
from agent_skills.validate import canonical_json, validate_corpus
from lifecycle_support import initialize_repository


ROOT = Path(__file__).resolve().parents[1]
NEW_PAYLOAD = frozenset({
    "agent_skills/workflow_graph.py",
    "agent_skills/diagnosis.py",
    "agent_skills/review_orchestration.py",
    "workflows/delivery-v1.json",
    "schemas/workflow-graph.schema.json",
    "schemas/workflow-stage-result.schema.json",
    "docs/workflow-graph-v1.md",
    "tests/consumers/workflow_consumer.py",
    "tests/fixtures/workflow-graph/result-templates.json",
    "tests/scenarios/core/diagnose.json",
    "tests/scenarios/core/obtain-review.json",
    "tests/test_workflow_graph_contract.py",
    "tests/test_diagnosis.py",
    "tests/test_review_base_reopening.py",
    "tests/test_review_and_compounding.py",
    "tests/scenarios/core/review.json",
    "tests/test_review_orchestration.py",
    "skills/diagnose-work/SKILL.md",
    "skills/obtain-review/SKILL.md",
})


class ReleaseIntegrationTests(unittest.TestCase):
    def test_diagnosis_has_released_target_without_replacing_entry_point(self) -> None:
        cases = (
            (WorkflowRequest("Diagnose the unknown failure"), "diagnose-work"),
            (WorkflowRequest("Implement the change", host_workflow_hint="diagnose"), "diagnose-work"),
            (WorkflowRequest("Diagnose the failure", host_workflow_hint="review", repository_workflow="plan"), "plan-work"),
            (WorkflowRequest("Diagnose the failure", repository_workflow="unknown"), None),
            (WorkflowRequest("Diagnose the failure", required_capabilities={"tracker": "blocked"}), None),
            (WorkflowRequest("Diagnose the failure", optional_capabilities={"tracker": "absent"}), "diagnose-work"),
        )
        for request, expected in cases:
            with self.subTest(request=request):
                decision = route_workflow(request)
                self.assertEqual(decision.entry_point, "deliver-work")
                self.assertEqual(decision.workflow_skill, expected)

    def test_profiles_and_release_payload_include_exact_new_closure(self) -> None:
        profiles = {name: json.loads((ROOT / f"profiles/{name}.json").read_text()) for name in ("core", "delivery-views", "linear")}
        core_members = resolve_profile("core", profiles)
        self.assertIn("diagnose-work", core_members)
        self.assertIn("obtain-review", core_members)
        self.assertEqual(
            resolve_profile("linear", profiles),
            sorted([
                *core_members,
                "create-linear-ticket",
                "delivery-views",
                "execute-linear-ticket",
                "linear-tracker-adapter",
            ]),
        )
        core = _release_view(ROOT, "core")
        linear = _release_view(ROOT, "linear")
        self.assertLessEqual(NEW_PAYLOAD, core.keys())
        self.assertEqual(set(linear) - set(core), {
            "profiles/delivery-views.json",
            "profiles/linear.json",
            "skills/delivery-views/SKILL.md",
            "skills/delivery-views/scripts/snapshot.py",
            "skills/delivery-views/assets/history.css",
            "skills/delivery-views/assets/history.js",
            "skills/delivery-views/assets/line-map.css",
            "skills/delivery-views/assets/line-map.js",
            "skills/delivery-views/assets/run-summary.css",
            "skills/delivery-views/assets/run-summary.js",
            "skills/delivery-views/scripts/render.py",
            "skills/delivery-views/scripts/views_common.py",
            "skills/delivery-views/references/delivery-plan.schema.json",
            "skills/create-linear-ticket/SKILL.md",
            "skills/create-linear-ticket/agents/openai.yaml",
            "skills/create-linear-ticket/references/atomic-plan-template.md",
            "skills/create-linear-ticket/references/atomic-ticket-template.md",
            "skills/execute-linear-ticket/SKILL.md",
            "skills/execute-linear-ticket/agents/openai.yaml",
            "skills/linear-tracker-adapter/SKILL.md",
            "skills/linear-tracker-adapter/references/adapter-contract.json",
        })
        for path in set(core) - {"catalog.json", "provenance.lock.json", "validation-inventory.json"}:
            self.assertEqual(core[path], linear[path], path)
        inventories = [json.loads(payload["validation-inventory.json"]) for payload in (core, linear)]
        core_scenarios, linear_scenarios = [inventory.pop("scenarios") for inventory in inventories]
        for inventory, scenarios in zip(inventories, (core_scenarios, linear_scenarios)):
            self.assertEqual(inventory.pop("expectedScenarioCount"), len(scenarios))
        self.assertEqual(inventories[0], inventories[1])
        self.assertEqual(core_scenarios, [row for row in linear_scenarios if row["profile"] == "core"])
        self.assertEqual({row["profile"] for row in core_scenarios}, {"core"})
        self.assertEqual({row["profile"] for row in linear_scenarios}, {"core", "delivery-views", "linear"})
        self.assertEqual(
            {row["skill"] for row in linear_scenarios if row["profile"] == "linear"},
            set(resolve_profile("linear", profiles)) - set(core_members) - {"delivery-views"},
        )
        for path in NEW_PAYLOAD:
            self.assertEqual(core[path], (ROOT / path).read_bytes(), path)
        self.assertFalse(any(path.startswith(("qualifications/", "docs/delivery/")) for path in linear))

    def test_run_delivery_profile_adds_only_its_own_skill_to_delivery_views(self) -> None:
        profiles = {
            name: json.loads((ROOT / f"profiles/{name}.json").read_text())
            for name in ("core", "delivery-views", "linear", "run-delivery")
        }
        self.assertEqual(
            resolve_profile("run-delivery", profiles),
            sorted([*resolve_profile("delivery-views", profiles), "run-delivery"]),
        )
        self.assertNotIn("run-delivery", resolve_profile("linear", profiles))
        views = _release_view(ROOT, "delivery-views")
        run_delivery = _release_view(ROOT, "run-delivery")
        own = {path for path in run_delivery if path.startswith("skills/run-delivery/")}
        self.assertIn("skills/run-delivery/SKILL.md", own)
        self.assertEqual(set(run_delivery) - set(views), {"profiles/run-delivery.json", *own})
        for path in set(views) - {"catalog.json", "provenance.lock.json", "validation-inventory.json"}:
            self.assertEqual(views[path], run_delivery[path], path)
        catalog = {skill["id"]: skill for skill in json.loads((ROOT / "catalog.json").read_text())["skills"]}
        entry = catalog["run-delivery"]
        self.assertEqual(entry["dependencies"], [
            {"id": "delivery-views", "requirement": "required"},
            {"id": "obtain-review", "requirement": "required"},
            {"id": "orchestrate-work", "requirement": "required"},
        ])
        self.assertEqual(entry["externalTools"], ["git", "python3", "ps", "lsof", "ffmpeg", "ffprobe", "node", "gh"])
        self.assertEqual(entry["profiles"], ["run-delivery"])
        scenarios = json.loads((ROOT / "validation-inventory.json").read_text())["scenarios"]
        self.assertIn(
            {"hosts": ["claude-code", "codex"], "id": "run-delivery-profile-selects-run-delivery",
             "profile": "run-delivery", "skill": "run-delivery"},
            scenarios,
        )

    def test_schema_profile_enums_name_every_supported_release_profile(self) -> None:
        # Bootstrap releases are named in neither schema's profile enum, as before.
        expected = sorted(release.SUPPORTED_PROFILES - {"bootstrap"})
        self.assertEqual(expected, sorted(receipt.SUPPORTED_PROFILES - {"bootstrap"}))
        self.assertIn("run-delivery", expected)

        def profile_enums(node):
            if isinstance(node, dict):
                if isinstance(node.get("profile"), dict) and "enum" in node["profile"]:
                    yield node["profile"]["enum"]
                for value in node.values():
                    yield from profile_enums(value)
            elif isinstance(node, list):
                for value in node:
                    yield from profile_enums(value)

        for name in ("receipt", "release-manifest"):
            with self.subTest(schema=name):
                enums = list(profile_enums(json.loads((ROOT / f"schemas/{name}.schema.json").read_text())))
                self.assertEqual(enums, [expected])

    def test_new_runtime_is_scanned_for_provider_and_repository_leakage(self) -> None:
        for relative in sorted(path for path in NEW_PAYLOAD if path.startswith("agent_skills/")):
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as temporary:
                copied = Path(temporary) / "corpus"
                shutil.copytree(ROOT, copied, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.egg-info"))
                path = copied / relative
                path.write_text(path.read_text() + "\n# forbidden Linear transport\n")
                findings = validate_corpus(copied).findings
                self.assertTrue(any(f.rule_id == "core.boundary" and f.artifact == relative for f in findings), findings)

    def test_runtime_requirements_follow_members_without_expanding_legacy_closure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "corpus"
            shutil.copytree(ROOT, copied, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.egg-info"))
            runtime = {path for path in NEW_PAYLOAD if path.startswith("agent_skills/")}
            for relative in runtime:
                (copied / relative).unlink()
            self.assertEqual(
                {finding.artifact for finding in validate_corpus(copied).findings if finding.rule_id == "core.boundary"},
                runtime,
            )
            # Isolate the runtime scan's compatibility condition; this synthetic
            # catalog still has unrelated closure findings, not a valid release.
            profile_path = copied / "profiles/core.json"
            profile = json.loads(profile_path.read_text())
            profile["members"] = [member for member in profile["members"] if member not in {"diagnose-work", "obtain-review"}]
            profile_path.write_text(canonical_json(profile), encoding="utf-8")
            self.assertFalse(any(f.rule_id == "core.boundary" for f in validate_corpus(copied).findings))

    def test_both_profiles_validate_install_and_rollback_outside_checkout(self) -> None:
        for profile in ("core", "linear"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                artifacts = []
                for number in (1, 2):
                    archive = base / f"{profile}-{number}.zip"
                    metadata = base / f"{profile}-{number}.json"
                    identity = build_release(ROOT, archive, metadata, release_id=f"integration-{number}", profile_id=profile)
                    artifacts.append((archive, metadata))
                    self.assertEqual(verify_release(archive, metadata), identity)
                duplicate_archive, duplicate_metadata = base / "duplicate.zip", base / "duplicate.json"
                build_release(ROOT, duplicate_archive, duplicate_metadata, release_id="integration-1", profile_id=profile)
                self.assertEqual(duplicate_archive.read_bytes(), artifacts[0][0].read_bytes())
                self.assertEqual(duplicate_metadata.read_bytes(), artifacts[0][1].read_bytes())
                extracted = base / "extracted"
                verify_release(*artifacts[0], extract_to=extracted)
                result = subprocess.run([sys.executable, "-m", "agent_skills.validate", "--root", "."], cwd=extracted, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                repository = initialize_repository(base)

                def command(*arguments: str) -> dict:
                    completed = subprocess.run(
                        [sys.executable, "-m", "agent_skills.cli", "--root", str(repository), *arguments],
                        cwd=extracted,
                        capture_output=True,
                        text=True,
                        env={**os.environ, "AGENT_SKILLS_EXECUTION_CONTEXT": "", "GITHUB_EVENT_NAME": ""},
                    )
                    self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                    return json.loads(completed.stdout)

                command("install", "--archive", str(artifacts[0][0]), "--metadata", str(artifacts[0][1]), "--maintenance")
                prior = command("status")["active"]["generation"]
                for host in (".agents", ".claude"):
                    for skill in ("diagnose-work", "obtain-review"):
                        self.assertEqual((repository / host / "skills" / skill / "SKILL.md").read_bytes(), (ROOT / "skills" / skill / "SKILL.md").read_bytes())
                command("update", "--archive", str(artifacts[1][0]), "--metadata", str(artifacts[1][1]), "--maintenance")
                for archive, metadata in artifacts:
                    archive.unlink()
                    metadata.unlink()
                command("rollback", "--maintenance")
                self.assertEqual(command("status")["active"]["generation"], prior)
                for host in (".agents", ".claude"):
                    for skill in ("diagnose-work", "obtain-review"):
                        self.assertIn(prior, str((repository / host / "skills" / skill).resolve()))


if __name__ == "__main__":
    unittest.main()
