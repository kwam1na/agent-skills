from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_skills.release import VerifiedRelease, build_release
from tests.qualification_pending_support import pending, skip_if_pending


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qualify-provider.py"
RECORD = ROOT / "qualifications" / "provider-workflows-run-delivery-v1.json"


def _load_runner():
    spec = importlib.util.spec_from_file_location("qualify_provider", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProviderQualificationTests(unittest.TestCase):
    def test_checked_product_provider_replays_exactly(self) -> None:
        product = ROOT / "qualifications/product"
        current = "core-run-delivery-v1"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "provider.json"
            result = subprocess.run([sys.executable, "-B", str(SCRIPT),
                                     "--archive", str(product / f"{current}.zip"),
                                     "--metadata", str(product / f"{current}.release.json"),
                                     "--record", str(output)], cwd=temporary, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(output.read_bytes(), (product / "provider-run-delivery-v1.json").read_bytes())

    def test_record_cannot_overwrite_a_qualification_input(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive, metadata = directory / "candidate.zip", directory / "candidate.json"
            for target in (archive, metadata):
                target.write_bytes(b"retained input")
            for output in (archive, metadata):
                with self.subTest(output=output.name), \
                        mock.patch.object(runner, "qualify", return_value={}) as qualify, \
                        mock.patch.object(sys, "stderr"), mock.patch.object(sys, "stdout"):
                    result = runner.main([
                        "--archive", str(archive), "--metadata", str(metadata), "--record", str(output),
                    ])
                    self.assertEqual(result, 1)
                    qualify.assert_not_called()
                    self.assertEqual(output.read_bytes(), b"retained input")

    def test_failed_qualification_removes_stale_success_record(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            record = directory / "previous-success.json"
            record.write_text('{"summary":{"result":"passed"}}\n')
            with mock.patch.object(runner, "qualify", side_effect=runner.QualificationFailure("probe failed")), \
                    mock.patch.object(sys, "stderr"):
                result = runner.main([
                    "--archive", str(directory / "candidate.zip"),
                    "--metadata", str(directory / "candidate.json"),
                    "--record", str(record),
                ])
            self.assertEqual(result, 1)
            self.assertFalse(record.exists())

    def test_missing_duplicate_or_unasserted_scenarios_cannot_qualify(self) -> None:
        runner = _load_runner()
        release = VerifiedRelease("fixture", "core", "a" * 64, "b" * 64)
        cases = (
            ((), 1, "synthetic assertion"),
            (runner.SCENARIOS[:-1], 1, "synthetic assertion"),
            ((*runner.SCENARIOS, runner.SCENARIOS[0]), 1, "synthetic assertion"),
            (runner.SCENARIOS, 0, "synthetic assertion"),
            (runner.SCENARIOS, True, "synthetic assertion"),
            (runner.SCENARIOS, 1, " "),
        )
        for scenario_names, count, evidence in cases:
            with self.subTest(scenarios=scenario_names, assertions=count, evidence=evidence), \
                    mock.patch.object(runner, "verify_release", return_value=release), \
                    mock.patch.object(runner, "Lifecycle"), \
                    mock.patch.object(runner, "sha256_file", return_value="a" * 64), \
                    mock.patch.object(runner, "SCENARIOS", scenario_names), \
                    mock.patch.object(runner, "run_scenario", side_effect=lambda name, operation: {
                        "id": name, "assertionCount": count, "evidence": [evidence], "result": "passed",
                    }):
                with self.assertRaises(runner.QualificationFailure):
                    runner.qualify(Path("candidate.zip"), Path("candidate.json"))

    def _run(self, directory: Path, *, record: Path | None = None):
        archive = directory / "core.zip"
        metadata = directory / "core.release.json"
        build_release(
            ROOT,
            archive,
            metadata,
            release_id="core-workflows-run-delivery-v1",
            profile_id="core",
        )
        output = record or directory / "provider.qualification.json"
        environment = dict(os.environ)
        environment.update(
            {
                "HOME": str(directory / "empty-home"),
                "CODEX_HOME": str(directory / "empty-codex"),
                "CLAUDE_CONFIG_DIR": str(directory / "empty-claude"),
                "AGENT_SKILLS_EXECUTION_CONTEXT": "pull-request",
                "GITHUB_EVENT_NAME": "pull_request",
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--archive",
                str(archive),
                "--metadata",
                str(metadata),
                "--record",
                str(output),
            ],
            cwd=directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, output

    def test_exact_installed_provider_qualification_is_complete_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result, record_path = self._run(directory)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["schemaVersion"], "agent-skills-provider-qualification/1")
            self.assertEqual(record["protocolVersion"], "delivery-provider-rails/1")
            self.assertEqual(record["release"]["profile"], "core")
            self.assertEqual(record["release"]["releaseId"], "core-workflows-run-delivery-v1")
            self.assertRegex(record["release"]["archiveSha256"], r"^[a-f0-9]{64}$")
            self.assertRegex(record["release"]["metadataSha256"], r"^[a-f0-9]{64}$")
            self.assertRegex(record["installedProviderSha256"], r"^[a-f0-9]{64}$")
            self.assertEqual(
                record["workflowGraphSha256"],
                hashlib.sha256((ROOT / "workflows/delivery-v1.json").read_bytes()).hexdigest(),
            )
            self.assertEqual(record["summary"]["result"], "passed")
            self.assertEqual(
                {scenario["id"] for scenario in record["scenarios"]},
                {
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
                },
            )
            self.assertTrue(all(scenario["result"] == "passed" for scenario in record["scenarios"]))
            fresh = next(item for item in record["scenarios"] if item["id"] == "fresh-install-and-startup")
            self.assertIn("installed workflow graph and both new skills match the exact candidate", fresh["evidence"])
            success = next(
                scenario
                for scenario in record["scenarios"]
                if scenario["id"] == "success-and-native-operations"
            )
            self.assertIn(
                "the same low-entropy host reference maps differently across fresh sessions",
                success["evidence"],
            )
            unsupported = next(
                scenario
                for scenario in record["scenarios"]
                if scenario["id"] == "unsupported-version"
            )
            self.assertIn(
                "unsupported negotiation closes the session to later work",
                unsupported["evidence"],
            )
            retained = record_path.read_text(encoding="utf-8")
            for forbidden in (
                str(directory),
                "raw-provider-reference",
                "idempotency-secret",
                "manifestPath",
                "opaque:",
            ):
                self.assertNotIn(forbidden, retained)
            self.assertNotIn("degraded evidence retains no raw host reference", retained)

    def test_qualification_is_deterministic_without_preexisting_host_install(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_result, first_record = self._run(Path(first))
            second_result, second_record = self._run(Path(second))

            self.assertEqual(first_result.returncode, 0, first_result.stdout + first_result.stderr)
            self.assertEqual(second_result.returncode, 0, second_result.stdout + second_result.stderr)
            self.assertEqual(first_record.read_bytes(), second_record.read_bytes())
            self.assertFalse((Path(first) / "empty-codex").exists())
            self.assertFalse((Path(first) / "empty-claude").exists())

    def test_qualification_never_overlaps_sessions_and_releases_every_process_handle(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "core.zip"
            metadata = directory / "core.release.json"
            build_release(
                ROOT,
                archive,
                metadata,
                release_id="core-workflows-v2",
                profile_id="core",
            )
            original = runner.InstalledProvider
            active = 0
            instances = []

            class StrictSequentialProvider(original):
                def __init__(self, *arguments, **keywords):
                    nonlocal active
                    if active:
                        raise AssertionError("provider sessions overlapped")
                    super().__init__(*arguments, **keywords)
                    active += 1
                    self.test_closed = False
                    instances.append(self)

                def close(self):
                    nonlocal active
                    try:
                        super().close()
                    finally:
                        if not self.test_closed:
                            self.test_closed = True
                            active -= 1

            with mock.patch.object(runner, "InstalledProvider", StrictSequentialProvider):
                record = runner.qualify(archive, metadata)

            self.assertEqual(record["summary"]["result"], "passed")
            self.assertEqual(active, 0)
            self.assertTrue(instances)
            self.assertTrue(all(instance.process.poll() is not None for instance in instances))
            self.assertTrue(
                all(
                    stream is None or stream.closed
                    for instance in instances
                    for stream in (
                        instance.process.stdin,
                        instance.process.stdout,
                        instance.process.stderr,
                    )
                )
            )

    def test_checked_record_replays_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result, replay = self._run(directory)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            skip_if_pending(self, "provider")
            self.assertEqual(replay.read_bytes(), RECORD.read_bytes())

    def test_hostile_installed_provider_mutations_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            mutations = {
                "protocol": (
                    'PROTOCOL_VERSION = "delivery-provider-rails/1"',
                    'PROTOCOL_VERSION = "delivery-provider-rails/2"',
                    "installed module starts with isolated Python module search",
                ),
                "deterministic-reference": (
                    'reference = f"opaque:{secrets.token_hex(16)}"',
                    'reference = f"opaque:{hashlib.sha256(value.encode()).hexdigest()[:32]}"',
                    "the same low-entropy host reference maps differently across fresh sessions",
                ),
                "ignored-release-mismatch": (
                    "if expected_release != release:",
                    "if False:",
                    "wrong expected release fails closed with a blocked terminal",
                ),
                "accepted-after-unsupported": (
                    "self.negotiated = supported\n        self.closed = not supported",
                    "self.negotiated = True\n        self.closed = False",
                    "unsupported negotiation closes the session to later work",
                ),
            }

            for name, (before, after, expected_failure) in mutations.items():
                with self.subTest(name=name):
                    candidate = directory / name
                    shutil.copytree(
                        ROOT,
                        candidate,
                        ignore=shutil.ignore_patterns(".git", "__pycache__"),
                    )
                    provider_path = candidate / "agent_skills" / "provider.py"
                    source = provider_path.read_text(encoding="utf-8")
                    self.assertIn(before, source)
                    provider_path.write_text(
                        source.replace(before, after, 1),
                        encoding="utf-8",
                    )
                    archive = directory / f"{name}.zip"
                    metadata = directory / f"{name}.release.json"
                    build_release(
                        candidate,
                        archive,
                        metadata,
                        release_id=f"{name}-provider-v1",
                        profile_id="core",
                    )
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(SCRIPT),
                            "--archive",
                            str(archive),
                            "--metadata",
                            str(metadata),
                            "--record",
                            str(directory / f"{name}.record.json"),
                        ],
                        cwd=directory,
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn(expected_failure, result.stderr)

    def test_qualification_executes_installed_provider_without_harness_or_transport(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"-B", "-m", "agent_skills.provider"', source)
        self.assertNotIn("from agent_skills.provider", source)
        self.assertNotIn("import agent_skills.provider", source)
        lowered = source.casefold()
        for forbidden in (
            "harness",
            "requests",
            "httpx",
            "aiohttp",
            "linear.app",
            "graphql",
            "scheduler",
            "cache",
            "daemon",
            "registry",
        ):
            self.assertNotIn(forbidden, lowered)
        # Evidence's own telemetry is part of the installed provider contract;
        # it is not a dependency on an adopter's telemetry subsystem.
        self.assertIn('["payload"]["telemetry"]["iterationCount"]', source)
        self.assertNotIn("import telemetry", lowered)
        self.assertNotIn("delivery_run_telemetry", lowered)


if __name__ == "__main__":
    unittest.main()
