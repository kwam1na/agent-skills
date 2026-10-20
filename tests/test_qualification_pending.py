from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from unittest import mock

from tests import qualification_pending_support
from tests.qualification_pending_support import qualification_pending


ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / qualification_pending.MARKER


def write_marker(root: Path, marker: object, *, raw: str | None = None) -> None:
    text = raw if raw is not None else json.dumps(marker, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    (root / qualification_pending.MARKER).write_text(text, encoding="utf-8")


def corpus_copy(destination: Path) -> Path:
    copy = destination / "corpus"
    shutil.copytree(
        ROOT, copy, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.egg-info", "build", "dist")
    )
    return copy


def run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts" / script), *args],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )


class QualificationPendingMarkerTests(unittest.TestCase):
    def test_the_marker_names_known_sets_with_a_reason(self) -> None:
        marker = qualification_pending.load_marker(ROOT)
        self.assertIsNotNone(marker, "the integration branch carries the marker until every set is re-earned")
        self.assertTrue(set(marker["sets"]) <= set(qualification_pending.SETS))
        self.assertTrue(marker["reason"].strip())

    def test_the_marker_skips_only_the_sets_it_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for set_id in qualification_pending.SETS:
                self.assertIsNone(qualification_pending.pending_reason(root, set_id))
            write_marker(root, {"reason": "Core records are stale.", "schemaVersion": "qualification-pending/1",
                                "sets": ["core"]})
            self.assertEqual(qualification_pending.pending_reason(root, "core"), "Core records are stale.")
            for set_id in ("bootstrap", "linear", "provider"):
                with self.subTest(set_id=set_id):
                    self.assertIsNone(qualification_pending.pending_reason(root, set_id))
            with self.assertRaises(qualification_pending.MarkerError):
                qualification_pending.pending_reason(root, "everything")

    def test_the_test_skip_names_only_the_marked_sets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_marker(root, {"reason": "Provider records are stale.", "schemaVersion": "qualification-pending/1",
                                "sets": ["provider"]})
            with mock.patch.object(qualification_pending_support, "ROOT", root):
                for set_id in ("bootstrap", "core", "linear"):
                    with self.subTest(set_id=set_id):
                        qualification_pending_support.skip_if_pending(self, set_id)
                with self.assertRaises(unittest.SkipTest) as skipped:
                    qualification_pending_support.skip_if_pending(self, "core", "provider")
                self.assertEqual(str(skipped.exception), "provider qualification pending: Provider records are stale.")

    def test_a_malformed_marker_is_refused(self) -> None:
        valid = {"reason": "Stale.", "schemaVersion": "qualification-pending/1", "sets": ["core"]}
        cases = {
            "not JSON": (None, "{"),
            "not an object": ([], None),
            "unknown field": ({**valid, "until": "later"}, None),
            "missing reason": ({key: value for key, value in valid.items() if key != "reason"}, None),
            "blank reason": ({**valid, "reason": "  "}, None),
            "wrong version": ({**valid, "schemaVersion": "qualification-pending/2"}, None),
            "no sets": ({**valid, "sets": []}, None),
            "unknown set": ({**valid, "sets": ["core", "everything"]}, None),
            "unsorted sets": ({**valid, "sets": ["provider", "core"]}, None),
            "duplicate sets": ({**valid, "sets": ["core", "core"]}, None),
            "not canonical": (None, json.dumps(valid)),
        }
        for name, (marker, raw) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as temporary:
                write_marker(Path(temporary), marker, raw=raw)
                with self.assertRaises(qualification_pending.MarkerError):
                    qualification_pending.load_marker(Path(temporary))

    def test_default_branch_refs_are_recognised_in_their_usual_spellings(self) -> None:
        for ref in ("main", "refs/heads/main", "origin/main", "refs/remotes/origin/main", " main\n"):
            with self.subTest(ref=ref):
                self.assertTrue(qualification_pending.is_default_branch(ref))
        for ref in ("orchestration-workflow-integration", "refs/heads/maintenance", "feature/main", ""):
            with self.subTest(ref=ref):
                self.assertFalse(qualification_pending.is_default_branch(ref))


class QualificationPendingGateTests(unittest.TestCase):
    def test_release_cli_refuses_while_the_marker_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "release.zip"
            metadata = Path(temporary) / "release.json"
            refused = run("build-release.py", "build", "--archive", str(archive), "--metadata", str(metadata),
                          "--release-id", "pending-check")
            self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
            self.assertEqual(json.loads(refused.stdout)["qualificationPending"], json.loads(MARKER.read_text())["sets"])
            self.assertFalse(archive.exists())
            self.assertFalse(metadata.exists())

    def test_release_cli_builds_once_the_marker_is_gone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy = corpus_copy(Path(temporary))
            (copy / qualification_pending.MARKER).unlink()
            archive = Path(temporary) / "release.zip"
            built = run("build-release.py", "build", "--root", str(copy), "--archive", str(archive),
                        "--metadata", str(Path(temporary) / "release.json"), "--release-id", "no-marker")
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            self.assertTrue(archive.is_file())

    def test_registry_validation_refuses_the_marker_only_for_the_default_branch(self) -> None:
        self.assertEqual(run("validate-registry.py").returncode, 0)
        for ref in ("main", "refs/heads/main"):
            with self.subTest(ref=ref):
                refused = run("validate-registry.py", "--target-base", ref)
                self.assertEqual(refused.returncode, 1, refused.stdout)
                self.assertIn("qualificationPending", json.loads(refused.stdout))
        refused = run("validate-registry.py", "--target-base=main")
        self.assertEqual(refused.returncode, 1, refused.stdout)
        integration = run("validate-registry.py", "--target-base", "orchestration-workflow-integration")
        self.assertEqual(integration.returncode, 0, integration.stdout)
        self.assertEqual(integration.stdout, run("validate-registry.py").stdout)

    def test_registry_validation_for_the_default_branch_passes_without_the_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy = corpus_copy(Path(temporary))
            (copy / qualification_pending.MARKER).unlink()
            accepted = run("validate-registry.py", "--root", str(copy), "--target-base", "main")
            self.assertEqual(accepted.returncode, 0, accepted.stdout)
            write_marker(copy, None, raw="{")
            malformed = run("validate-registry.py", "--root", str(copy))
            self.assertEqual(malformed.returncode, 1, malformed.stdout)

    def test_ci_passes_the_target_base_for_default_branch_events(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertRegex(workflow, r"(?m)^  pull_request:\s*$")
        self.assertRegex(workflow, r"(?m)^  push:\n    branches: \[main\]$")
        self.assertIn(
            "TARGET_BASE: ${{ github.event_name == 'pull_request' && github.base_ref || github.ref_name }}",
            workflow,
        )
        validations = re.findall(r"(?m)^\s*(?:run: |)python scripts/validate-registry\.py.*$", workflow)
        self.assertEqual(len(validations), 1, validations)
        self.assertIn('--target-base "$TARGET_BASE"', validations[0])


if __name__ == "__main__":
    unittest.main()
