from __future__ import annotations

import ast
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from agent_skills.capabilities import TrackerOperation
from agent_skills.lifecycle import Lifecycle
from agent_skills.release import ReleaseError, build_release, verify_release
from agent_skills.validate import canonical_json

from lifecycle_support import initialize_repository


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "skills/linear-tracker-adapter/references/adapter-contract.json"
ADAPTER = ROOT / "skills/linear-tracker-adapter/SKILL.md"
PROPERTIES_DOCUMENT = ".agents/tracker-properties.json"
TRACKING_WORKFLOWS = ("create-linear-ticket", "execute-linear-ticket")
# Resolution that belongs to the adapter must not reappear as a native call in
# a tracking workflow body.
PROVIDER_TOOL_IDENTIFIERS = (
    "get_issue",
    "get_project",
    "get_team",
    "list_issues",
    "save_comment",
    "save_issue",
)


class LinearProfileTests(unittest.TestCase):
    def contract(self) -> dict:
        return json.loads(CONTRACT.read_text(encoding="utf-8"))

    def transport_findings(self, documents: dict[str, str]) -> dict[str, list[str]]:
        markers = self.contract()["transportBoundary"]["forbiddenActionMarkers"]
        findings = {}
        for name, content in documents.items():
            matches = [
                category
                for category, category_markers in markers.items()
                if any(marker in content.casefold() for marker in category_markers)
            ]
            if matches:
                findings[name] = matches
        return findings

    def build(
        self,
        directory: Path,
        stem: str,
    ) -> tuple[Path, Path]:
        archive = directory / f"{stem}.zip"
        metadata = directory / f"{stem}.release.json"
        build_release(
            ROOT,
            archive,
            metadata,
            release_id="linear-v2",
            profile_id="linear",
        )
        return archive, metadata

    def test_contract_maps_neutral_operations_only_to_host_native_tools(self) -> None:
        self.assertEqual(
            CONTRACT.read_text(encoding="utf-8"),
            canonical_json(self.contract()),
        )
        mappings = {
            entry["trackerOperation"]: entry
            for entry in self.contract()["operationMappings"]
        }

        self.assertEqual(set(mappings), {item.value for item in TrackerOperation})
        self.assertEqual(
            {
                operation: entry["hostOperations"]
                for operation, entry in mappings.items()
            },
            {
                "resolve-context": ["search", "read"],
                "create-work": ["create"],
                "link-dependencies": ["relations"],
                "update-status": ["update"],
                "attach-evidence": ["create"],
                "close-or-handoff": ["update"],
                "list-work": ["search", "read"],
            },
        )
        self.assertEqual(
            set(self.contract()["allowedHostOperations"]),
            {"create", "read", "update", "search", "relations", "reconciliation"},
        )

    def test_contract_allowlists_normalized_outputs_and_redacts_audit_data(self) -> None:
        expected_outputs = {
            "resolve-context": {
                "teamReference",
                "projectReference",
                "workReference",
                "auditReference",
            },
            "create-work": {"workReference", "auditReference"},
            "link-dependencies": {
                "workReference",
                "dependencyReferences",
                "auditReference",
            },
            "update-status": {"workReference", "status", "auditReference"},
            "attach-evidence": {
                "workReference",
                "evidenceReference",
                "auditReference",
            },
            "close-or-handoff": {
                "workReference",
                "status",
                "handoffReference",
                "auditReference",
            },
            "list-work": {"parentReference", "workItems", "auditReference"},
        }
        mappings = self.contract()["operationMappings"]

        self.assertEqual(
            {
                entry["trackerOperation"]: set(entry["allowedOutputFields"])
                for entry in mappings
            },
            expected_outputs,
        )
        self.assertEqual(
            self.contract()["outputPolicy"],
            {
                "auditReferences": "opaque-redacted",
                "forbiddenForwarding": [
                    "raw-host-payload",
                    "raw-host-action",
                    "secret",
                    "raw-idempotency-key",
                ],
            },
        )

    def test_contract_fails_closed_after_ambiguous_mutations(self) -> None:
        self.assertEqual(
            self.contract()["failureOutcomes"],
            {
                "missing": "unavailable",
                "auth": "auth-required",
                "malformed": "malformed",
                "unsupported": "unsupported",
                "read-timeout": "retry",
                "read-ambiguous": "retry",
                "mutation-timeout": "reconciliation-required",
                "mutation-ambiguous": "reconciliation-required",
                "reconciliation-miss": "reconciliation-required",
                "reconciliation-auth": "reconciliation-required",
                "reconciliation-unavailable": "reconciliation-required",
                "reconciliation-ambiguous": "reconciliation-required",
                "reconciliation-malformed": "reconciliation-required",
                "reconciliation-timeout": "reconciliation-required",
            },
        )
        self.assertEqual(
            self.contract()["mutationSafety"],
            {
                "authoritativeHostIdempotencyFence": False,
                "exactReconciliationMayConfirmSuccess": True,
                "initialMutationAttemptsPerInvocation": 1,
                "repeatMutationAfterUnconfirmedResult": False,
            },
        )

    def test_transport_boundary_rejects_each_alternate_access_category(self) -> None:
        self.assertEqual(
            self.contract()["transportBoundary"]["mode"],
            "host-native-mcp-only",
        )
        hostile_documents = {
            "direct-linear-api": "POST https://linear.app/graphql",
            "http-client-or-sdk": "Import requests for provider calls",
            "command-downloader": "Run curl against the provider",
            "subprocess-or-shell-bridge": "Call subprocess.run for the mutation",
            "oauth-flow": "Start an OAuth flow",
            "credential-token-env": "Read API_KEY from os.environ",
            "fallback-transport": "Use a fallback transport when MCP is absent",
        }
        self.assertEqual(
            set(
                self.contract()["transportBoundary"]["forbiddenActionMarkers"]
            ),
            set(hostile_documents),
        )

        for category, hostile_content in hostile_documents.items():
            with self.subTest(category=category):
                self.assertEqual(
                    self.transport_findings({"hostile": hostile_content}),
                    {"hostile": [category]},
                )

    def test_adapter_declares_the_repository_tracker_properties_document(self) -> None:
        adapter = ADAPTER.read_text(encoding="utf-8")

        self.assertIn(f"`{PROPERTIES_DOCUMENT}`", adapter)
        self.assertNotIn(f"]({PROPERTIES_DOCUMENT})", adapter)

    def test_tracking_workflows_use_neutral_operations_through_the_adapter(self) -> None:
        operations = {
            entry["trackerOperation"] for entry in self.contract()["operationMappings"]
        }
        bodies = {
            skill: (ROOT / f"skills/{skill}/SKILL.md").read_text(encoding="utf-8")
            for skill in TRACKING_WORKFLOWS
        }

        named = set()
        for skill, body in bodies.items():
            with self.subTest(skill=skill):
                self.assertIn("$linear-tracker-adapter", body)
                mentioned = {
                    operation for operation in operations if f"`{operation}`" in body
                }
                self.assertTrue(mentioned)
                named |= mentioned
                for identifier in PROVIDER_TOOL_IDENTIFIERS:
                    self.assertNotIn(identifier, body)
        self.assertEqual(named, operations)

    def test_linear_archive_ships_the_tracking_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive, metadata = self.build(directory, "tracking")
            extracted = directory / "extracted"
            verify_release(archive, metadata, extract_to=extracted)

            for skill in TRACKING_WORKFLOWS:
                with self.subTest(skill=skill):
                    self.assertTrue(
                        (extracted / f"skills/{skill}/SKILL.md").is_file()
                    )

    def test_linear_archive_is_deterministic_verified_and_installable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first, first_metadata = self.build(directory, "first")
            second, second_metadata = self.build(directory, "second")

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_metadata.read_bytes(), second_metadata.read_bytes())
            with zipfile.ZipFile(first) as release:
                manifest = json.loads(release.read("release-manifest.json"))
                self.assertEqual(set(manifest["groups"]), {"linear"})
                self.assertTrue(
                    all(record["group"] == "linear" for record in manifest["files"])
                )
            extracted = directory / "extracted"
            verified = verify_release(first, first_metadata, extract_to=extracted)
            self.assertEqual(verified.profile_id, "linear")
            self.assertTrue((extracted / "profiles/core.json").is_file())
            self.assertTrue((extracted / "profiles/linear.json").is_file())
            self.assertTrue(
                (extracted / "skills/linear-tracker-adapter/SKILL.md").is_file()
            )
            self.assertTrue(
                (
                    extracted
                    / "skills/linear-tracker-adapter/references/adapter-contract.json"
                ).is_file()
            )
            self.assertFalse((extracted / "agent_skills/linear_mcp.py").exists())

            repository = initialize_repository(directory)
            with mock.patch.dict(
                os.environ,
                {"AGENT_SKILLS_EXECUTION_CONTEXT": "", "GITHUB_EVENT_NAME": ""},
            ):
                result = Lifecycle(repository).install(
                    first,
                    first_metadata,
                    maintainer=True,
                )
            self.assertEqual(result["status"]["lifecycle"], "current")
            receipt = json.loads(
                (repository / ".agent-skills/active.json").read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["release"]["profile"], "linear")
            for root in (".agents/skills", ".claude/skills"):
                self.assertTrue(
                    (repository / root / "linear-tracker-adapter").is_symlink()
                )

    def test_supported_profile_identity_mismatch_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "core.zip"
            metadata = directory / "core.release.json"
            build_release(
                ROOT,
                archive,
                metadata,
                release_id="core-v1",
                profile_id="core",
            )
            document = json.loads(metadata.read_text(encoding="utf-8"))
            document["profile"] = "linear"
            metadata.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ReleaseError, "detached metadata mismatch"):
                verify_release(archive, metadata)

    def test_linear_archive_has_no_alternate_transport_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive, _ = self.build(directory, "linear")
            core_archive = directory / "core.zip"
            core_metadata = directory / "core.release.json"
            build_release(
                ROOT,
                core_archive,
                core_metadata,
                release_id="core-v1",
                profile_id="core",
            )
            with zipfile.ZipFile(archive) as release:
                names = release.namelist()
                python_sources = {
                    name: release.read(name).decode("utf-8")
                    for name in names
                    if name.endswith(".py")
                }
                adapter_files = {
                    name: release.read(name).decode("utf-8")
                    for name in names
                    if name.startswith("skills/linear-tracker-adapter/")
                }
            with zipfile.ZipFile(core_archive) as release:
                core_python_sources = {
                    name: release.read(name).decode("utf-8")
                    for name in release.namelist()
                    if name.endswith(".py")
                }

            forbidden_imports = {
                "aiohttp",
                "httpx",
                "requests",
                "urllib3",
            }
            for name, source in python_sources.items():
                with self.subTest(path=name):
                    tree = ast.parse(source)
                    imported = {
                        node.module.split(".")[0]
                        for node in ast.walk(tree)
                        if isinstance(node, ast.ImportFrom) and node.module
                    }
                    imported.update(
                        alias.name.split(".")[0]
                        for node in ast.walk(tree)
                        if isinstance(node, ast.Import)
                        for alias in node.names
                    )
                    self.assertTrue(forbidden_imports.isdisjoint(imported))
            self.assertEqual(python_sources, core_python_sources)
            self.assertEqual(
                set(adapter_files),
                {
                    "skills/linear-tracker-adapter/SKILL.md",
                    "skills/linear-tracker-adapter/references/adapter-contract.json",
                },
            )
            self.assertFalse(
                any(
                    marker in name.casefold()
                    for name in names
                    for marker in (
                        "api-client",
                        "credential",
                        "fallback-transport",
                        "linear_mcp.py",
                        "oauth",
                        "sdk-client",
                        "subprocess",
                    )
                )
            )
            contract_path = CONTRACT.relative_to(ROOT).as_posix()
            contract_document = json.loads(adapter_files[contract_path])
            del contract_document["transportBoundary"]
            adapter_files[contract_path] = canonical_json(contract_document)
            self.assertEqual(self.transport_findings(adapter_files), {})


if __name__ == "__main__":
    unittest.main()
