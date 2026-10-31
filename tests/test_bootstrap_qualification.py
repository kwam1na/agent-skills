from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from agent_skills.release import build_release, verify_release
from agent_skills.validate import canonical_json
from tests.qualification_pending_support import pending, skip_if_pending


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
        # The retained record's driver digest covers the corpus validator, whose
        # provider rule changed; it is re-earned with the pending bootstrap set.
        skip_if_pending(self, "bootstrap")
        product = ROOT / "qualifications/product/bootstrap-lead-selected-checks-v1"
        archive = product.with_suffix(".zip")
        metadata = product.with_suffix(".release.json")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            extracted = directory / "installed"
            release = verify_release(archive, metadata, extract_to=extracted)
            self.assertEqual(
                release.archive_sha256,
                "90e47c7a85a125a5af2f628863c680f76952a5a940d68ade23530e5963141f65",
            )
            self.assertEqual(canonical_json(load_runner().qualify(archive, metadata)).encode(),
                             product.with_suffix(".json").read_bytes(), "the record replays byte for byte")
            projected = build_release(
                extracted, directory / "projected.zip", directory / "projected.json",
                release_id=release.release_id, profile_id="bootstrap",
            )
            current = build_release(
                ROOT, directory / "current.zip", directory / "current.json",
                release_id=release.release_id, profile_id="bootstrap",
            )
            if pending("bootstrap") is None:
                self.assertEqual(projected, current)
        skip_if_pending(self, "bootstrap")

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
                release_id="bootstrap-workflows-lead-selected-checks-v1",
                profile_id="bootstrap",
            )
            replay = runner.qualify(archive, metadata)
        checked = (ROOT / "qualifications/bootstrap-workflows-lead-selected-checks-v1.json").read_bytes()
        skip_if_pending(self, "bootstrap")
        self.assertEqual(canonical_json(replay).encode(), checked, "the record replays byte for byte")

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
                release_id="bootstrap-workflows-lead-selected-checks-v1",
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
