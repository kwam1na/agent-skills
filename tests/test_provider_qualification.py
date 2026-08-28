from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_skills.release import build_release


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qualify-provider.py"
RECORD = ROOT / "qualifications" / "provider-v1.json"


class ProviderQualificationTests(unittest.TestCase):
    def _run(self, directory: Path, *, record: Path | None = None):
        archive = directory / "core.zip"
        metadata = directory / "core.release.json"
        build_release(
            ROOT,
            archive,
            metadata,
            release_id="core-v1",
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
            self.assertEqual(record["release"]["releaseId"], "core-v1")
            self.assertRegex(record["release"]["archiveSha256"], r"^[a-f0-9]{64}$")
            self.assertRegex(record["release"]["metadataSha256"], r"^[a-f0-9]{64}$")
            self.assertRegex(record["installedProviderSha256"], r"^[a-f0-9]{64}$")
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
                    "unsupported-version",
                    "evidence-completeness-and-redaction",
                },
            )
            self.assertTrue(all(scenario["result"] == "passed" for scenario in record["scenarios"]))
            retained = record_path.read_text(encoding="utf-8")
            for forbidden in (
                str(directory),
                "raw-provider-reference",
                "idempotency-secret",
                "manifestPath",
                "opaque:",
            ):
                self.assertNotIn(forbidden, retained)

    def test_qualification_is_deterministic_without_preexisting_host_install(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_result, first_record = self._run(Path(first))
            second_result, second_record = self._run(Path(second))

            self.assertEqual(first_result.returncode, 0, first_result.stdout + first_result.stderr)
            self.assertEqual(second_result.returncode, 0, second_result.stdout + second_result.stderr)
            self.assertEqual(first_record.read_bytes(), second_record.read_bytes())
            self.assertFalse((Path(first) / "empty-codex").exists())
            self.assertFalse((Path(first) / "empty-claude").exists())

    def test_checked_record_replays_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result, replay = self._run(directory)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(replay.read_bytes(), RECORD.read_bytes())

    def test_behavior_mutation_in_exact_candidate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            candidate = directory / "candidate"
            shutil.copytree(
                ROOT,
                candidate,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            provider_path = candidate / "agent_skills" / "provider.py"
            source = provider_path.read_text(encoding="utf-8")
            provider_path.write_text(
                source.replace(
                    'PROTOCOL_VERSION = "delivery-provider-rails/1"',
                    'PROTOCOL_VERSION = "delivery-provider-rails/2"',
                ),
                encoding="utf-8",
            )
            archive = directory / "mutated.zip"
            metadata = directory / "mutated.release.json"
            build_release(
                candidate,
                archive,
                metadata,
                release_id="mutated-provider-v1",
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
                    str(directory / "record.json"),
                ],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("installed module starts", result.stderr)

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
            "telemetry",
        ):
            self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
