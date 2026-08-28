from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_skills.release import build_release


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qualify-core.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("qualify_core", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QualificationTests(unittest.TestCase):
    def test_complete_archive_qualification_is_reproducible_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first_archive = directory / "first.zip"
            first_metadata = directory / "first.release.json"
            second_archive = directory / "second.zip"
            second_metadata = directory / "second.release.json"
            release_id = "core-v1"
            first = build_release(
                ROOT,
                first_archive,
                first_metadata,
                release_id=release_id,
                profile_id="core",
            )
            second = build_release(
                ROOT,
                second_archive,
                second_metadata,
                release_id=release_id,
                profile_id="core",
            )
            self.assertEqual(first, second)
            self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
            self.assertEqual(first_metadata.read_bytes(), second_metadata.read_bytes())

            extracted = directory / "standalone"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build-release.py"),
                    "verify",
                    "--archive",
                    str(first_archive),
                    "--metadata",
                    str(first_metadata),
                    "--extract-to",
                    str(extracted),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            record_path = directory / "qualification.json"
            secret = "qualification-secret-must-not-leak"
            result = subprocess.run(
                [
                    sys.executable,
                    str(extracted / "scripts" / "qualify-core.py"),
                    "--archive",
                    str(first_archive),
                    "--metadata",
                    str(first_metadata),
                    "--record",
                    str(record_path),
                ],
                cwd=directory,
                env={**os.environ, "QUALIFICATION_SECRET_SENTINEL": secret},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["release"]["releaseId"], release_id)
            self.assertEqual(record["release"]["archiveSha256"], first.archive_sha256)
            self.assertEqual(record["summary"]["result"], "passed")
            self.assertEqual(
                {scenario["id"] for scenario in record["scenarios"]},
                {
                    "fresh-install-and-host-discovery",
                    "trackerless-workflows",
                    "fake-adapter-workflows",
                    "tamper-rejection",
                    "rollback",
                    "missing-capability",
                    "bounded-review-failure",
                },
            )
            self.assertTrue(all(item["result"] == "passed" for item in record["scenarios"]))
            self.assertGreater(record["summary"]["assertionCount"], len(record["scenarios"]))
            serialized = record_path.read_text(encoding="utf-8")
            self.assertNotIn(str(directory), serialized)
            self.assertNotIn(secret, serialized)
            self.assertNotIn("linear", serialized.lower())
            self.assertEqual(
                record_path.read_bytes(),
                (ROOT / "qualifications" / "core-v1.json").read_bytes(),
            )

            replay = subprocess.run(
                [
                    sys.executable,
                    str(extracted / "scripts" / "qualify-core.py"),
                    "--archive",
                    str(second_archive),
                    "--metadata",
                    str(second_metadata),
                    "--record",
                    str(directory / "replay.json"),
                ],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(replay.returncode, 0, replay.stdout + replay.stderr)
            self.assertEqual(
                record_path.read_bytes(),
                (directory / "replay.json").read_bytes(),
            )

    def test_anti_vacuity_rejects_missing_or_unasserted_scenarios(self) -> None:
        runner = _load_runner()
        release = {
            "archiveSha256": "a" * 64,
            "metadataSha256": "b" * 64,
            "profile": "core",
            "releaseId": "core-example",
        }
        with self.assertRaisesRegex(ValueError, "qualification.anti_vacuity"):
            runner.finalize_record(release, [])
        scenarios = [
            {
                "assertionCount": 0,
                "checkpointSha256": "c" * 64,
                "evidence": ["named checkpoint"],
                "id": scenario_id,
                "result": "passed",
            }
            for scenario_id in runner.REQUIRED_SCENARIOS
        ]
        with self.assertRaisesRegex(ValueError, "qualification.anti_vacuity"):
            runner.finalize_record(release, scenarios)

    def test_unexpected_failure_retains_only_a_stable_non_secret_summary(self) -> None:
        runner = _load_runner()
        secret = "unexpected-secret-must-not-leak"

        def fail(_checkpoint) -> None:
            raise RuntimeError(secret)

        record = runner._run("trackerless-workflows", fail)

        self.assertEqual(record["result"], "failed")
        self.assertEqual(record["failure"], "unexpected:RuntimeError")
        self.assertNotIn(secret, json.dumps(record))


if __name__ == "__main__":
    unittest.main()
