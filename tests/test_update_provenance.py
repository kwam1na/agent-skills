from __future__ import annotations

import hashlib
import json
import os
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
        # The whole registry validates with no other edit.
        self.assertEqual(list(validate_corpus(self.root).findings), [])
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

    def test_a_real_lock_only_rebase_conflict_is_resolved_from_either_side(self) -> None:
        # Two branches edit the same output in places that merge cleanly; only
        # its digest line in the lock conflicts, as in a merge-queue sync.
        guide = "skills/orchestrate-work/references/orchestration-guide.md"

        def git(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false", *args],
                cwd=self.root, capture_output=True, text=True, encoding="utf-8", check=False,
            )

        def commit_side(branch: str, start: str, edit) -> None:
            self.assertEqual(git("checkout", "-q", "-b", branch, start).returncode, 0)
            edit(self.root / guide)
            self.assertEqual(self.write().returncode, 0)
            self.assertEqual(git("commit", "-q", "-am", branch).returncode, 0)

        self.assertEqual(git("init", "-q", "--initial-branch=base").returncode, 0)
        git("add", "-A")
        self.assertEqual(git("commit", "-q", "-m", "base").returncode, 0)
        lines = (self.root / guide).read_text(encoding="utf-8").split("\n")
        commit_side("upstream", "base", lambda path: path.write_text(
            "\n".join(lines[:2] + ["An upstream note.", ""] + lines[2:]), encoding="utf-8"))
        commit_side("lane", "base", lambda path: path.write_text(
            "\n".join(lines) + "\nA lane note.\n", encoding="utf-8"))
        rebased = git("rebase", "upstream")
        self.assertNotEqual(rebased.returncode, 0)
        conflicted = git("diff", "--name-only", "--diff-filter=U").stdout.split()
        self.assertEqual(conflicted, [LOCK])
        locks = []
        for side in ("--ours", "--theirs"):
            self.assertEqual(git("checkout", side, "--", LOCK).returncode, 0)
            completed = self.write()
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(list(validate_corpus(self.root).findings), [])
            locks.append((self.root / LOCK).read_bytes())
        self.assertEqual(locks[0], locks[1])
        git("add", LOCK)
        finished = subprocess.run(
            ["git", "-c", "core.editor=true", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false", "rebase", "--continue"],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(finished.returncode, 0, finished.stderr)
        text = (self.root / guide).read_text(encoding="utf-8")
        self.assertIn("An upstream note.", text)
        self.assertIn("A lane note.", text)
        self.assertEqual(self.write("--check").returncode, 0)

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
        # Without -B and with the environment's own guard removed, only the
        # writer's own setting keeps bytecode out of the corpus.
        environment = {key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"}
        self.append("skills/plan-work/SKILL.md", "\nEdited.\n")
        completed = subprocess.run(
            [sys.executable, str(self.root / "scripts" / "update-provenance.py")],
            capture_output=True, text=True, encoding="utf-8", check=False, env=environment,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(list(self.root.rglob("__pycache__")), [])

    def test_a_current_lock_in_another_layout_is_restored_to_canonical_form(self) -> None:
        canonical = (self.root / LOCK).read_bytes()
        (self.root / LOCK).write_text(json.dumps(json.loads(canonical)), encoding="utf-8")
        self.assertTrue([finding for finding in validate_corpus(self.root).findings if finding.rule_id == "document.canonical"])
        check = self.write("--check")
        self.assertEqual(check.returncode, 1)
        self.assertIn(f"noncanonical: {LOCK}", check.stdout)
        completed = self.write()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual((self.root / LOCK).read_bytes(), canonical)
        self.assertEqual(list(validate_corpus(self.root).findings), [])
        # A checkout that converts line endings is current, as the validator reads it.
        (self.root / LOCK).write_bytes(canonical.replace(b"\n", b"\r\n"))
        self.assertEqual(self.write("--check").returncode, 0)
        # The write is a whole-file replacement; no temporary file is left behind.
        self.assertEqual(sorted(path.name for path in self.root.glob(f".{LOCK}.*")), [])

    def test_a_malformed_lock_is_refused_not_reported_stale(self) -> None:
        pristine = (self.root / LOCK).read_bytes()
        text = pristine.decode("utf-8")
        lock = json.loads(text)
        no_digest = json.loads(text)
        del no_digest["entries"][0]["outputs"][0]["sha256"]
        cases = {
            "conflict markers": ("<<<<<<< HEAD\n" + text + "=======\n" + text + ">>>>>>> lane\n").encode("utf-8"),
            "byte-order mark": b"\xef\xbb\xbf" + pristine,
            "missing digest": (json.dumps(no_digest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        }
        for name, contents in cases.items():
            for args in ((), ("--check",)):
                with self.subTest(case=name, args=args):
                    (self.root / LOCK).write_bytes(contents)
                    completed = self.write(*args)
                    self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
                    self.assertNotIn("Traceback", completed.stderr)
                    self.assertEqual((self.root / LOCK).read_bytes(), contents)
        (self.root / LOCK).write_bytes(pristine)
        empty = self.directory / "empty"
        empty.mkdir()
        refused = subprocess.run(
            [sys.executable, "-B", str(WRITER), "--root", str(empty), "--check"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(refused.returncode, 2, refused.stderr)
        # A catalog skill whose entry the lock lacks.
        catalog = json.loads((self.root / "catalog.json").read_text(encoding="utf-8"))
        skill = catalog["skills"][0]
        lock["entries"] = [entry for entry in lock["entries"] if entry["id"] != skill["provenanceId"]]
        (self.root / LOCK).write_text(json.dumps(lock, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        missing_entry = self.write("--skill", skill["id"])
        self.assertEqual(missing_entry.returncode, 2)
        self.assertIn("has no entry", missing_entry.stderr)

    def test_an_output_outside_the_corpus_is_never_read(self) -> None:
        outside = self.directory / "outside.md"
        outside.write_text("Not the corpus.\n", encoding="utf-8")
        lock = json.loads((self.root / LOCK).read_text(encoding="utf-8"))
        original = lock["entries"][0]["outputs"][0]["path"]
        for name, prepare in (
            ("dot-dot path", lambda: lock["entries"][0]["outputs"][0].__setitem__("path", "../outside.md")),
            ("symlink", lambda: ((self.root / original).unlink(), (self.root / original).symlink_to(outside))),
        ):
            with self.subTest(case=name):
                prepare()
                (self.root / LOCK).write_text(json.dumps(lock, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
                before = (self.root / LOCK).read_bytes()
                completed = self.write()
                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertIn("outside the corpus", completed.stderr)
                self.assertEqual((self.root / LOCK).read_bytes(), before)
                lock["entries"][0]["outputs"][0]["path"] = original

    def test_a_digest_covers_the_output_bytes_exactly(self) -> None:
        # Line endings and bytes that are not UTF-8 are digested as stored.
        edited = self.root / "skills/plan-work/SKILL.md"
        edited.write_bytes(edited.read_bytes().replace(b"\n", b"\r\n") + b"\xff\xfe\n")
        self.assertEqual(self.write().returncode, 0)
        self.assertEqual(outputs(self.root)[next(key for key in outputs(self.root) if key[1] == "skills/plan-work/SKILL.md")],
                         digest(edited))


if __name__ == "__main__":
    unittest.main()
