from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from agent_skills.release import build_release
from agent_skills.validate import canonical_json
from tests.built_product import build_product


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qualify-bootstrap.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("qualify_bootstrap", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BootstrapQualificationTests(unittest.TestCase):
    def test_bootstrap_product_built_from_the_tree_qualifies_and_replays(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            archive, metadata = build_product(Path(temporary), "bootstrap", "bootstrap-qualification-fixture")
            record = runner.qualify(archive, metadata)
            replay = runner.qualify(archive, metadata)
        self.assertEqual(canonical_json(replay), canonical_json(record))
        self.assertEqual(
            set(record),
            {"product", "qualificationDriverSha256", "release", "replay", "scenarios", "schemaVersion", "summary"},
        )
        self.assertEqual(record["schemaVersion"], "agent-skills-bootstrap-qualification/1")
        self.assertEqual(record["release"]["profile"], "bootstrap")
        self.assertEqual(record["release"]["releaseId"], "bootstrap-qualification-fixture")
        self.assertEqual(record["product"], {"installedReady": True, "runtimeIncluded": True})
        self.assertEqual(record["summary"]["result"], "passed")
        self.assertEqual(
            [scenario["id"] for scenario in record["scenarios"]],
            ["fresh-install", "profile-validation", "rollback"],
        )

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
                release_id="bootstrap-qualification-fixture",
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
