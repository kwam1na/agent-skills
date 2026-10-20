from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_skills.validate import validate_corpus


ROOT = Path(__file__).resolve().parents[1]
WRITER = ROOT / "scripts" / "update-provenance.py"
LOCK = "provenance.lock.json"
IGNORED = shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc", ".pytest_cache", "build", "dist")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def outputs(root: Path) -> dict[tuple[str, str], str]:
    """Every (entry id, output path) in the lock, with its recorded digest."""
    lock = json.loads((root / LOCK).read_text(encoding="utf-8"))
    return {(entry["id"], output["path"]): output["sha256"] for entry in lock["entries"] for output in entry["outputs"]}


class UpdateProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pristine = Path(tempfile.mkdtemp(prefix="provenance-corpus-"))
        shutil.copytree(ROOT, cls.pristine / "corpus", ignore=IGNORED)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.pristine, ignore_errors=True)

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="provenance-"))
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.root = self.directory / "corpus"
        shutil.copytree(self.pristine / "corpus", self.root)

    def write(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(WRITER), "--root", str(self.root), *args],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )

    def digest_findings(self) -> list:
        return [finding for finding in validate_corpus(self.root).findings if finding.rule_id == "provenance.digest"]

    def append(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.write_bytes(path.read_bytes() + text.encode("utf-8"))

    def test_a_clean_tree_is_left_byte_for_byte(self) -> None:
        before = (self.root / LOCK).read_bytes()
        completed = self.write()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual((self.root / LOCK).read_bytes(), before)
        self.assertEqual(self.write("--check").returncode, 0)

    def test_editing_one_output_changes_exactly_that_digest_and_validation_passes(self) -> None:
        edited = "skills/orchestrate-work/SKILL.md"
        before = outputs(self.root)
        self.append(edited, "\nOne more line.\n")
        self.assertTrue(self.digest_findings())
        completed = self.write()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        after = outputs(self.root)
        changed = {key for key in before if before[key] != after[key]}
        self.assertEqual(changed, {(key[0], edited) for key in before if key[1] == edited})
        self.assertEqual(after[next(iter(changed))], digest(self.root / edited))
        self.assertEqual(self.digest_findings(), [])
        self.assertEqual(self.write().returncode, 0)
        self.assertEqual(outputs(self.root), after)

    def test_a_lock_taken_from_either_side_of_a_conflict_is_repaired(self) -> None:
        # The side that predates an output change carries stale digests; the writer makes it valid.
        lock = json.loads((self.root / LOCK).read_text(encoding="utf-8"))
        for entry in lock["entries"]:
            for output in entry["outputs"]:
                output["sha256"] = "0" * 64
        (self.root / LOCK).write_text(json.dumps(lock, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        self.assertTrue(self.digest_findings())
        self.assertEqual(self.write().returncode, 0)
        self.assertEqual(self.digest_findings(), [])
        self.assertEqual((self.root / LOCK).read_bytes(), (self.pristine / "corpus" / LOCK).read_bytes())

    def test_check_reports_stale_digests_and_writes_nothing(self) -> None:
        self.append("skills/plan-work/SKILL.md", "\nStale now.\n")
        before = (self.root / LOCK).read_bytes()
        completed = self.write("--check")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("skills/plan-work/SKILL.md", completed.stdout)
        self.assertEqual((self.root / LOCK).read_bytes(), before)

    def test_skill_limits_the_rewrite_to_that_skills_entry(self) -> None:
        self.append("skills/plan-work/SKILL.md", "\nEdited.\n")
        self.append("skills/review-work/SKILL.md", "\nEdited too.\n")
        completed = self.write("--skill", "plan-work")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        check = self.write("--check")
        self.assertEqual(check.returncode, 1)
        self.assertIn("skills/review-work/SKILL.md", check.stdout)
        self.assertNotIn("skills/plan-work/SKILL.md", check.stdout)

    def test_a_missing_output_or_an_unknown_skill_is_refused_without_writing(self) -> None:
        before = (self.root / LOCK).read_bytes()
        (self.root / "skills/plan-work/SKILL.md").unlink()
        missing = self.write()
        self.assertEqual(missing.returncode, 2)
        self.assertIn("skills/plan-work/SKILL.md", missing.stderr)
        self.assertEqual((self.root / LOCK).read_bytes(), before)
        unknown = self.write("--skill", "no-such-skill")
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("no-such-skill", unknown.stderr)
        self.assertEqual((self.root / LOCK).read_bytes(), before)

    def test_the_writer_leaves_no_bytecode_beside_it(self) -> None:
        self.write("--check")
        self.assertEqual(list((ROOT / "scripts").glob("__pycache__")), [])


if __name__ == "__main__":
    unittest.main()
