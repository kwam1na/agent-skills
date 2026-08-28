from __future__ import annotations

import ast
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from agent_skills.lifecycle import Lifecycle
from agent_skills.release import build_release, verify_release

from lifecycle_support import initialize_repository
from test_delivery_provider_rails_contract import SCHEMA_VALIDATOR


ROOT = Path(__file__).resolve().parents[1]
VERSION = "delivery-provider-rails/1"
DIGEST = "a" * 64
TREE = "b" * 40


def canonical(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


class InstalledProvider:
    def __init__(self, generation: Path, repository: Path) -> None:
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-m",
                "agent_skills.provider",
                "--root",
                str(repository),
            ],
            cwd=generation,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.last_unmatched: dict[str, object] | None = None

    def _read(self) -> dict[str, object]:
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if not line:
            assert self.process.stderr is not None
            detail = self.process.stderr.read()
            raise ValueError(detail or "provider process closed")
        return json.loads(line)

    def handle(self, message: dict[str, object]) -> list[dict[str, object]]:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        kind = message["kind"]
        if kind in {"negotiate", "cancel"}:
            return [self._read()]
        if message["payload"]["agentSkills"]["defer"] is True:
            event = self._read()
            if event["requestId"] != message["requestId"]:
                self.last_unmatched = event
                raise ValueError("active provider attempt terminated")
            return [event]
        events = []
        while True:
            event = self._read()
            if event.get("requestId") != message["requestId"]:
                self.last_unmatched = event
                raise ValueError("active provider attempt terminated")
            events.append(event)
            if event["kind"] == "terminal":
                return events

    def interrupt(self) -> list[dict[str, object]]:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        assert self.process.stdout is not None
        documents = [json.loads(line) for line in self.process.stdout if line.strip()]
        self.process.wait(timeout=10)
        self._close_pipes()
        return documents

    def close(self) -> None:
        if self.process.poll() is not None:
            self._close_pipes()
            return
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=10)
        self._close_pipes()

    def _close_pipes(self) -> None:
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


class DeliveryProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.repository = initialize_repository(self.directory)
        archive = self.directory / "provider.zip"
        metadata = self.directory / "provider.release.json"
        self.release = build_release(
            ROOT,
            archive,
            metadata,
            release_id="agent-skills-provider-v1",
            profile_id="linear",
        )
        with mock.patch.dict(
            os.environ,
            {"AGENT_SKILLS_EXECUTION_CONTEXT": "", "GITHUB_EVENT_NAME": ""},
        ):
            Lifecycle(self.repository).install(
                archive,
                metadata,
                maintainer=True,
            )
        self.binding = {
            "archiveSha256": self.release.archive_sha256,
            "metadataSha256": self.release.metadata_sha256,
            "profile": "linear",
            "releaseId": "agent-skills-provider-v1",
        }
        self.run_root = self.directory / "run"
        self.run_root.mkdir()
        self.generation = (
            self.repository
            / ".agent-skills"
            / "generations"
            / self.release.archive_sha256
        )
        self.providers: list[InstalledProvider] = []
        self.provider = self.start_provider()

    def tearDown(self) -> None:
        for provider in self.providers:
            provider.close()
        self.temporary.cleanup()

    def start_provider(self) -> InstalledProvider:
        provider = InstalledProvider(self.generation, self.repository)
        self.providers.append(provider)
        return provider

    def restart_provider(self) -> InstalledProvider:
        self.provider.close()
        self.provider = self.start_provider()
        return self.provider

    def negotiate(self, *versions: str) -> dict[str, object]:
        events = self.provider.handle(
            {"kind": "negotiate", "supportedVersions": list(versions)}
        )
        self.assertEqual(len(events), 1)
        self.assertTrue(SCHEMA_VALIDATOR.is_valid(events[0], "negotiation"))
        return events[0]

    def assert_rail_events(self, events: list[dict[str, object]]) -> None:
        self.assertTrue(events)
        for event in events:
            with self.subTest(event=event):
                self.assertTrue(SCHEMA_VALIDATOR.is_valid(event, str(event["kind"])))

    def request(
        self,
        *,
        request_id: str = "request-one",
        idempotency_key: str = "attempt-one",
        review: dict[str, object] | None = None,
        host_events: list[dict[str, object]] | None = None,
        release: dict[str, object] | None = None,
        workflow_id: str = "review-work",
        defer: bool = False,
    ) -> dict[str, object]:
        payload = {
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
                "workspaceId": "workspace-one",
            },
            "gateId": "delivery.gate",
            "obligationIds": ["review.green"],
            "providerId": "agent-skills.review",
            "runId": request_id,
            "runRoot": str(self.run_root),
            "agentSkills": {
                "defer": defer,
                "hostEvents": host_events or [],
                "recordedAt": "2026-08-28T12:00:00Z",
                "release": release or self.binding,
                "review": review,
                "workflowId": workflow_id,
            },
        }
        return {
            "idempotencyKey": idempotency_key,
            "kind": "request",
            "payload": payload,
            "requestId": request_id,
            "version": VERSION,
        }

    @staticmethod
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

    def test_supported_and_unsupported_negotiation_are_exact(self) -> None:
        self.assertEqual(
            self.negotiate("future/2", VERSION),
            {
                "kind": "negotiation",
                "outcome": "supported",
                "selectedVersion": VERSION,
                "supportedVersions": [VERSION],
            },
        )
        other = self.restart_provider()
        self.assertEqual(
            other.handle({"kind": "negotiate", "supportedVersions": ["future/2"]}),
            [
                {
                    "kind": "negotiation",
                    "outcome": "unsupported",
                    "selectedVersion": None,
                    "supportedVersions": [VERSION],
                }
            ],
        )

    def test_aligned_review_emits_redacted_events_and_complete_manifest(self) -> None:
        self.negotiate(VERSION)
        secret = "raw-reference-secret"
        events = self.provider.handle(
            self.request(
                review=self.aligned_review(),
                host_events=[
                    {
                        "kind": "progress",
                        "operation": "read",
                        "reference": secret,
                    },
                    {
                        "kind": "evidence",
                        "operation": "reconciliation",
                        "reference": secret,
                    },
                ],
            )
        )
        self.assert_rail_events(events)
        self.assertEqual([event["sequence"] for event in events], list(range(1, len(events) + 1)))
        self.assertEqual(events[-1]["kind"], "terminal")
        self.assertEqual(events[-1]["outcome"], "success")
        manifest_path = Path(events[-1]["result"]["manifestPath"])
        self.assertEqual(manifest_path, self.run_root / "manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["spec"], "delivery-evidence/1")
        self.assertEqual(manifest["provider"]["runId"], "request-one")
        self.assertEqual(
            manifest["provider"]["version"],
            f"agent-skills-provider-v1+sha256.{self.release.archive_sha256}",
        )
        self.assertEqual(manifest["candidate"]["treeSha"], TREE)
        self.assertEqual(manifest["runHistory"][-1]["preparedTreeSha"], TREE)
        self.assertEqual(manifest["claims"][0]["payloadSpec"], "review.green/1")
        payload = manifest["claims"][0]["payload"]
        self.assertEqual(payload["reviewers"]["completed"], ["correctness", "testing"])
        self.assertEqual(payload["telemetry"]["iterationCount"], 1)
        self.assertEqual(len(manifest["artifacts"]), 2)
        for artifact in manifest["artifacts"]:
            contents = (self.run_root / artifact["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(contents).hexdigest(), artifact["sha256"])
        retained = "".join(
            [json.dumps(events, sort_keys=True), manifest_path.read_text(encoding="utf-8")]
            + [
                (self.run_root / artifact["path"]).read_text(encoding="utf-8")
                for artifact in manifest["artifacts"]
            ]
        )
        self.assertNotIn(secret, retained)
        self.assertIn("read", retained)
        self.assertIn("reconciliation", retained)

    def test_all_host_native_linear_operations_are_preserved_without_transport(self) -> None:
        self.negotiate(VERSION)
        operations = ["create", "read", "update", "search", "relations", "reconciliation"]
        events = self.provider.handle(
            self.request(
                review=self.aligned_review(),
                host_events=[
                    {"kind": "progress", "operation": operation, "reference": f"secret-{operation}"}
                    for operation in operations
                ],
            )
        )
        emitted = [
            event["details"]["operation"]
            for event in events
            if event["kind"] == "progress" and "operation" in event.get("details", {})
        ]
        self.assertEqual(emitted, operations)

    def test_release_or_workflow_mismatch_blocks_before_false_success(self) -> None:
        for mutation in ("release", "workflow"):
            with self.subTest(mutation=mutation):
                provider = self.restart_provider()
                provider.handle({"kind": "negotiate", "supportedVersions": [VERSION]})
                release = {**self.binding, "archiveSha256": "0" * 64}
                events = provider.handle(
                    self.request(
                        review=self.aligned_review(),
                        release=release if mutation == "release" else None,
                        workflow_id="unknown-workflow" if mutation == "workflow" else "review-work",
                    )
                )
                self.assert_rail_events(events)
                self.assertEqual(events[-1]["outcome"], "blocked")
                self.assertTrue(any(event["kind"] == "blocker" for event in events))
                self.assertFalse((self.run_root / "manifest.json").exists())

    def test_unresolved_blocked_and_failed_reviews_never_emit_green(self) -> None:
        scenarios = {
            "unresolved": {
                "findings": [
                    {
                        "actionable": True,
                        "blocking": False,
                        "disposition": "unresolved",
                        "id": "finding-one",
                        "scope": "in_contract",
                        "severity": "P1",
                        "source": "change required",
                    }
                ],
                "maxRounds": 2,
                "requiredLenses": ["correctness"],
                "rounds": [
                    {
                        "preparedTreeSha": TREE,
                        "results": [
                            {
                                "evidence": ["inspection"],
                                "findings": ["change required"],
                                "lens": "correctness",
                                "outcome": "changes-requested",
                            }
                        ],
                    }
                ],
            },
            "failed": {
                "findings": [],
                "maxRounds": 1,
                "requiredLenses": ["correctness"],
                "rounds": [
                    {
                        "preparedTreeSha": TREE,
                        "results": [
                            {
                                "evidence": [],
                                "failure": "host unavailable",
                                "findings": [],
                                "lens": "correctness",
                                "outcome": "failed",
                            }
                        ],
                    }
                ],
            },
        }
        for name, review in scenarios.items():
            with self.subTest(name=name):
                provider = self.restart_provider()
                provider.handle({"kind": "negotiate", "supportedVersions": [VERSION]})
                events = provider.handle(self.request(review=review, request_id=f"request-{name}"))
                self.assert_rail_events(events)
                expected = "failed" if name == "failed" else "blocked"
                self.assertEqual(events[-1]["outcome"], expected)
                self.assertNotEqual(events[-1]["outcome"], "success")

    def test_resolved_review_history_is_complete_and_reference_redacted(self) -> None:
        review = {
            "findings": [
                {
                    "actionable": True,
                    "blocking": False,
                    "disposition": "resolved",
                    "id": "raw-finding-reference",
                    "scope": "in_contract",
                    "severity": "P1",
                    "source": "change required",
                }
            ],
            "maxRounds": 2,
            "requiredLenses": ["correctness"],
            "rounds": [
                {
                    "preparedTreeSha": "f" * 40,
                    "results": [
                        {
                            "evidence": ["inspection"],
                            "findings": ["change required"],
                            "lens": "correctness",
                            "outcome": "changes-requested",
                        }
                    ],
                },
                {
                    "preparedTreeSha": TREE,
                    "results": [
                        {
                            "evidence": ["focused regression"],
                            "findings": [],
                            "lens": "correctness",
                            "outcome": "aligned",
                        }
                    ],
                },
            ],
        }
        self.negotiate(VERSION)
        events = self.provider.handle(self.request(review=review))
        self.assert_rail_events(events)
        self.assertEqual(events[-1]["outcome"], "success")
        manifest = json.loads((self.run_root / "manifest.json").read_text(encoding="utf-8"))
        retained = manifest["claims"][0]["payload"]["findings"]
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0]["disposition"], "resolved")
        self.assertNotEqual(retained[0]["id"], "raw-finding-reference")

    def test_host_blocker_and_failure_are_typed_terminal_outcomes(self) -> None:
        for kind, expected in (("blocker", "blocked"), ("failure", "failed")):
            with self.subTest(kind=kind):
                provider = self.restart_provider()
                provider.handle({"kind": "negotiate", "supportedVersions": [VERSION]})
                events = provider.handle(
                    self.request(
                        request_id=f"request-{kind}",
                        idempotency_key=f"attempt-{kind}",
                        review=self.aligned_review(),
                        host_events=[
                            {
                                "kind": kind,
                                "operation": "create",
                                "reference": "sensitive-host-reference",
                            }
                        ],
                    )
                )
                self.assert_rail_events(events)
                self.assertEqual(events[-1]["outcome"], expected)
                self.assertTrue(any(event["kind"] == "blocker" for event in events))
                self.assertNotIn("sensitive-host-reference", json.dumps(events))

    def test_deferred_request_can_be_cancelled_and_interruption_is_indeterminate(self) -> None:
        self.negotiate(VERSION)
        started = self.provider.handle(self.request(defer=True))
        self.assertEqual(started[-1]["kind"], "progress")
        cancelled = self.provider.handle(
            {
                "cancellationId": "cancel-one",
                "kind": "cancel",
                "requestId": "request-one",
                "version": VERSION,
            }
        )
        self.assert_rail_events(cancelled)
        self.assertEqual(cancelled[-1]["outcome"], "cancelled")
        self.assertEqual(self.provider.handle({"cancellationId": "cancel-one", "kind": "cancel", "requestId": "request-one", "version": VERSION}), cancelled)

        self.provider.handle(
            self.request(
                request_id="request-two",
                idempotency_key="attempt-two",
                defer=True,
            )
        )
        second_cancelled = self.provider.handle(
            {
                "cancellationId": "cancel-two",
                "kind": "cancel",
                "requestId": "request-two",
                "version": VERSION,
            }
        )
        self.assertEqual(second_cancelled[-1]["outcome"], "cancelled")

        self.provider.close()
        other = self.start_provider()
        other.handle({"kind": "negotiate", "supportedVersions": [VERSION]})
        other.handle(
            self.request(
                request_id="request-three",
                idempotency_key="attempt-three",
                defer=True,
            )
        )
        interrupted = other.interrupt()
        self.assert_rail_events(interrupted)
        self.assertEqual(interrupted[-1]["requestId"], "request-three")
        self.assertEqual(interrupted[-1]["outcome"], "indeterminate")

    def test_identical_request_is_idempotent_and_conflicts_fail_closed(self) -> None:
        self.negotiate(VERSION)
        request = self.request(review=self.aligned_review())
        first = self.provider.handle(request)
        first_manifest = (self.run_root / "manifest.json").read_bytes()
        second = self.provider.handle(request)
        self.assertEqual(first, second)
        self.assertEqual(first_manifest, (self.run_root / "manifest.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "request identity"):
            self.provider.handle({**request, "idempotencyKey": "different-attempt"})

    def test_cached_success_revalidates_manifest_artifacts_before_replay(self) -> None:
        self.negotiate(VERSION)
        request = self.request(review=self.aligned_review())
        first = self.provider.handle(request)
        manifest = json.loads((self.run_root / "manifest.json").read_text(encoding="utf-8"))
        (self.run_root / manifest["artifacts"][0]["path"]).unlink()

        with self.assertRaisesRegex(ValueError, "evidence"):
            self.provider.handle(request)

        self.assertEqual(first[-1]["outcome"], "success")

    def test_cached_success_revalidates_manifest_digest_before_replay(self) -> None:
        self.negotiate(VERSION)
        request = self.request(review=self.aligned_review())
        self.provider.handle(request)
        manifest_path = self.run_root / "manifest.json"
        manifest_path.write_bytes(manifest_path.read_bytes() + b" ")

        with self.assertRaisesRegex(ValueError, "evidence"):
            self.provider.handle(request)

    def test_second_request_cannot_replace_an_active_attempt(self) -> None:
        self.negotiate(VERSION)
        self.provider.handle(self.request(defer=True))

        with self.assertRaisesRegex(ValueError, "active"):
            self.provider.handle(
                self.request(
                    request_id="request-two",
                    idempotency_key="attempt-two",
                    defer=True,
                )
            )

        self.assertEqual(
            self.provider.last_unmatched,
            {
                "action": "Inspect retained host evidence and start a new attempt.",
                "kind": "terminal",
                "outcome": "indeterminate",
                "requestId": "request-one",
                "sequence": 2,
                "summary": "The provider lost the host workflow before a terminal outcome.",
                "version": VERSION,
            },
        )

    def test_host_references_are_not_recoverable_by_dictionary_hashing(self) -> None:
        self.negotiate(VERSION)
        raw_reference = "work-one"
        events = self.provider.handle(
            self.request(
                review=self.aligned_review(),
                host_events=[
                    {
                        "kind": "progress",
                        "operation": "read",
                        "reference": raw_reference,
                    }
                ],
            )
        )
        emitted = events[1]["details"]["reference"]
        dictionary = ("work-one", "work-two", "issue-one", "context-one")
        recoverable = {
            f"opaque:{hashlib.sha256(candidate.encode()).hexdigest()[:32]}"
            for candidate in dictionary
        }
        self.assertNotIn(emitted, recoverable)
        self.assertNotIn(raw_reference, json.dumps(events))
        self.assertNotIn(
            emitted,
            (self.run_root / "manifest.json").read_text(encoding="utf-8"),
        )

    def test_mismatched_runtime_cannot_attest_the_active_installed_release(self) -> None:
        archive = self.directory / "other-provider.zip"
        metadata = self.directory / "other-provider.release.json"
        extracted = self.directory / "other-provider"
        build_release(
            ROOT,
            archive,
            metadata,
            release_id="other-provider-v1",
            profile_id="linear",
        )
        verify_release(archive, metadata, extract_to=extracted)
        self.provider.close()
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "agent_skills.provider",
                "--root",
                str(self.repository),
            ],
            cwd=extracted,
            input=json.dumps(
                {"kind": "negotiate", "supportedVersions": [VERSION]},
                separators=(",", ":"),
            )
            + "\n",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("runtime-mismatch", result.stderr)

    def test_active_provider_prevents_release_switch_until_session_ends(self) -> None:
        self.negotiate(VERSION)
        archive = self.directory / "replacement.zip"
        metadata = self.directory / "replacement.release.json"
        build_release(
            ROOT,
            archive,
            metadata,
            release_id="replacement-provider-v1",
            profile_id="linear",
        )
        with mock.patch.dict(
            os.environ,
            {"AGENT_SKILLS_EXECUTION_CONTEXT": "", "GITHUB_EVENT_NAME": ""},
        ):
            with self.assertRaisesRegex(ValueError, "lock-busy"):
                Lifecycle(self.repository).update(
                    archive,
                    metadata,
                    maintainer=True,
                )

        events = self.provider.handle(self.request(review=self.aligned_review()))
        self.assertEqual(events[-1]["outcome"], "success")
        self.assertEqual(
            events[0]["details"]["release"]["releaseId"],
            "agent-skills-provider-v1",
        )

    def test_forced_process_exit_does_not_leave_a_stale_lifecycle_lock(self) -> None:
        signals = [signal.SIGTERM]
        if os.name != "nt" and hasattr(signal, "SIGKILL"):
            signals.append(signal.SIGKILL)

        for exit_signal in signals:
            with self.subTest(signal=exit_signal.name):
                provider = self.restart_provider()
                provider.handle(
                    {"kind": "negotiate", "supportedVersions": [VERSION]}
                )
                provider.process.send_signal(exit_signal)
                provider.process.wait(timeout=10)
                provider._close_pipes()

                fresh = self.start_provider()
                self.provider = fresh
                response = fresh.handle(
                    {"kind": "negotiate", "supportedVersions": [VERSION]}
                )
                self.assertEqual(response[0]["outcome"], "supported")

    def test_manifest_anti_vacuity_and_redaction_fail_closed(self) -> None:
        review = self.aligned_review()
        review["requiredLenses"] = []
        review["rounds"] = []
        self.negotiate(VERSION)
        events = self.provider.handle(self.request(review=review))
        self.assertEqual(events[-1]["outcome"], "failed")
        self.assertFalse((self.run_root / "manifest.json").exists())

    def test_provider_module_has_no_harness_import_or_transport_ownership(self) -> None:
        path = ROOT / "agent_skills/provider.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertFalse(any("harness" in name for name in imports))
        source = path.read_text(encoding="utf-8").casefold()
        for marker in ("requests", "httpx", "aiohttp", "urllib", "subprocess", "linear.app", "graphql"):
            self.assertNotIn(marker, source)

    def test_provider_runtime_and_mapping_doc_ship_in_both_profiles(self) -> None:
        for profile in ("core", "linear"):
            with self.subTest(profile=profile):
                archive = self.directory / f"{profile}.zip"
                metadata = self.directory / f"{profile}.release.json"
                build_release(
                    ROOT,
                    archive,
                    metadata,
                    release_id=f"{profile}-provider-v1",
                    profile_id=profile,
                )
                with zipfile.ZipFile(archive) as release:
                    self.assertIn("agent_skills/provider.py", release.namelist())
                    self.assertIn("docs/agent-skills-provider.md", release.namelist())

    def test_installed_release_starts_as_a_standalone_stdio_provider(self) -> None:
        generation = self.repository / ".agent-skills" / "generations" / self.release.archive_sha256
        self.provider.close()
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "agent_skills.provider",
                "--root",
                str(self.repository),
            ],
            cwd=generation,
            input=json.dumps(
                {"kind": "negotiate", "supportedVersions": [VERSION]},
                separators=(",", ":"),
            )
            + "\n",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "kind": "negotiation",
                "outcome": "supported",
                "selectedVersion": VERSION,
                "supportedVersions": [VERSION],
            },
        )


if __name__ == "__main__":
    unittest.main()
