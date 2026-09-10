from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from agent_skills.release import build_release, verify_release


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qualify-bootstrap.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("qualify_bootstrap", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BootstrapQualificationTests(unittest.TestCase):
    def test_current_bootstrap_product_replays_and_matches_source(self) -> None:
        product = ROOT / "qualifications/product/bootstrap-concurrent-delivery-v1"
        archive = product.with_suffix(".zip")
        metadata = product.with_suffix(".release.json")
        checked = json.loads(product.with_suffix(".json").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            extracted = directory / "installed"
            release = verify_release(archive, metadata, extract_to=extracted)
            self.assertEqual(
                release.archive_sha256,
                "18183b739211ebcaabb684939afc9eb423f062f5a0f632787eb712cda93e165e",
            )
            self.assertEqual(load_runner().qualify(archive, metadata), checked)
            projected = build_release(
                extracted, directory / "projected.zip", directory / "projected.json",
                release_id=release.release_id, profile_id="bootstrap",
            )
            current = build_release(
                ROOT, directory / "current.zip", directory / "current.json",
                release_id=release.release_id, profile_id="bootstrap",
            )
            self.assertEqual(projected, current)

    def test_checked_bootstrap_qualification_replays_exactly(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "bootstrap.zip"
            metadata = directory / "bootstrap.release.json"
            build_release(
                ROOT,
                archive,
                metadata,
                release_id="bootstrap-workflows-concurrent-delivery-v1",
                profile_id="bootstrap",
            )
            replay = runner.qualify(archive, metadata)
        checked = json.loads(
            (ROOT / "qualifications/bootstrap-workflows-concurrent-delivery-v1.json").read_text()
        )
        self.assertEqual(replay, checked)

    def test_bootstrap_profile_is_releasable_installable_and_qualified(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "bootstrap.zip"
            metadata = directory / "bootstrap.release.json"
            build_release(
                ROOT,
                archive,
                metadata,
                release_id="bootstrap-workflows-concurrent-delivery-v1",
                profile_id="bootstrap",
            )
            record = runner.qualify(archive, metadata)
        self.assertEqual(record["schemaVersion"], "agent-skills-bootstrap-qualification/1")
        self.assertEqual(record["release"]["profile"], "bootstrap")
        self.assertEqual(record["product"], {"installedReady": False, "runtimeIncluded": False})
        self.assertEqual(record["summary"]["result"], "passed")
        self.assertEqual(
            [scenario["id"] for scenario in record["scenarios"]],
            ["fresh-install", "profile-validation", "rollback"],
        )
        self.assertTrue(all(scenario["assertionCount"] > 0 for scenario in record["scenarios"]))

    def test_qualification_refuses_a_non_bootstrap_release(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "core.zip"
            metadata = directory / "core.release.json"
            build_release(ROOT, archive, metadata, release_id="core-v1", profile_id="core")
            with self.assertRaisesRegex(ValueError, "bootstrap candidate is required"):
                runner.qualify(archive, metadata)


if __name__ == "__main__":
    unittest.main()
