"""A corpus copy leaves out what no fixture reads, ends, and never lets one test's writes reach another."""

from __future__ import annotations

import os
import re
import shutil
import socket
import stat
import tempfile
import unittest
from pathlib import Path

from corpus_copy import NEVER_COPIED, ROOT, SharedCorpus, copy_corpus


def tree(root: Path) -> dict[str, object]:
    """Every entry under ``root``: a link's target, a folder's mode, or a file's mode and bytes."""
    entries: dict[str, object] = {".": stat.S_IMODE(root.stat().st_mode)}
    for path in root.rglob("*"):
        relative = str(path.relative_to(root))
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            entries[relative] = ("link", os.readlink(path))
        elif path.is_dir():
            entries[relative] = ("folder", mode)
        else:
            entries[relative] = ("file", mode, path.read_bytes())
    return entries


class CopyCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.source = self.base / "checkout"
        (self.source / "skills/one").mkdir(parents=True)
        (self.source / "skills/one/SKILL.md").write_text("one\n", encoding="utf-8")
        (self.source / "catalog.json").write_text("{}\n", encoding="utf-8")

    def test_what_no_fixture_reads_is_left_out_at_any_depth(self) -> None:
        for name in (".git", ".venv", ".venv-3.11", "node_modules", ".worktrees", ".claude", ".agent-skills",
                     "__pycache__", "agent_skills_corpus.egg-info"):
            (self.source / name).mkdir()
            (self.source / name / "held").write_text("x\n", encoding="utf-8")
            (self.source / "skills/one" / name).mkdir()
            (self.source / "skills/one" / name / "held").write_text("x\n", encoding="utf-8")
        (self.source / ".test-timings.json").write_text("{}\n", encoding="utf-8")
        copied = copy_corpus(self.base / "copy", source=self.source)
        self.assertEqual(sorted(str(path.relative_to(copied)) for path in copied.rglob("*") if path.is_file()),
                         ["catalog.json", "skills/one/SKILL.md"])

    def test_a_target_inside_the_source_does_not_copy_into_itself(self) -> None:
        for target in (self.source / "copy", self.source / ".tmp/deep/copy", self.source / "skills/copy"):
            with self.subTest(target=target):
                copied = copy_corpus(target, source=self.source)
                self.assertEqual((copied / "catalog.json").read_text(encoding="utf-8"), "{}\n")
                self.assertFalse((copied / target.relative_to(self.source)).exists())

    def test_the_folders_leading_to_a_target_inside_the_source_are_left_out(self) -> None:
        copied = copy_corpus(self.source / ".tmp/deep/copy", source=self.source)
        self.assertFalse((copied / ".tmp").exists())

    def test_a_target_reached_through_a_link_does_not_copy_into_itself(self) -> None:
        (self.source / "scratch").mkdir()
        link = self.base / "through-a-link"
        link.symlink_to(self.source / "scratch")
        copied = copy_corpus(link / "copy", source=self.source)
        self.assertEqual((copied / "catalog.json").read_text(encoding="utf-8"), "{}\n")
        self.assertFalse((copied / "scratch").exists())

    def test_a_folder_whose_name_starts_the_targets_is_kept(self) -> None:
        (self.source / "copy").mkdir()
        (self.source / "copy/held").write_text("x\n", encoding="utf-8")
        copied = copy_corpus(self.source / "copy-of-notes", source=self.source)
        self.assertTrue((copied / "copy/held").exists())

    def test_a_folder_starting_with_the_targets_name_is_kept(self) -> None:
        (self.source / "copy-of-notes").mkdir()
        (self.source / "copy-of-notes/held").write_text("x\n", encoding="utf-8")
        copied = copy_corpus(self.source / "copy", source=self.source)
        self.assertTrue((copied / "copy-of-notes/held").exists())

    def test_a_target_spelled_another_way_does_not_copy_into_itself(self) -> None:
        (self.source / "scratch").mkdir()
        (self.base / "nfc-\u00e9").mkdir()
        (self.base / "nfc-\u00e9/catalog.json").write_text("{}\n", encoding="utf-8")
        for source, spelled in ((self.source, self.source / "SCRATCH"),
                                (self.source, self.base / "CHECKOUT/scratch"),
                                (self.base / "nfc-\u00e9", self.base / "nfc-e\u0301/inner")):
            if not spelled.parent.exists():
                self.skipTest("this filesystem tells these spellings apart")
            with self.subTest(target=spelled):
                if source == self.base / "nfc-\u00e9":
                    (source / "inner").mkdir(exist_ok=True)
                copied = copy_corpus(spelled / "copy", source=source)
                self.assertEqual((copied / "catalog.json").read_text(encoding="utf-8"), "{}\n")
                self.assertEqual([path for path in copied.rglob("copy")], [])
                shutil.rmtree(spelled / "copy")

    def test_the_repository_root_copies_without_its_environments(self) -> None:
        copied = copy_corpus(self.base / "corpus", leaving_out=("qualifications", "tests", "docs"))
        names = {path.name for path in copied.iterdir()}
        self.assertIn("catalog.json", names)
        self.assertFalse(names & {".git", ".venv", "node_modules"})

    def test_no_test_copies_the_repository_root_any_other_way(self) -> None:
        # Those copies are what wrote every environment's files, and what recursed under a TMPDIR in a checkout.
        direct = re.compile(r"copytree\(\s*(ROOT|CORPUS_ROOT|REPOSITORY|REPO_ROOT)\b")
        found = [path.name for path in (ROOT / "tests").glob("*.py") if direct.search(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [])

    def test_the_environments_named_are_the_ones_a_checkout_makes(self) -> None:
        self.assertLessEqual({".git", ".venv", ".venv-*", "node_modules", "__pycache__"}, set(NEVER_COPIED))


class SharedCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        source = self.base / "source"
        (source / "skills/one/references").mkdir(parents=True)
        (source / "empty").mkdir()
        (source / "skills/one/SKILL.md").write_text("one\n", encoding="utf-8")
        (source / "skills/one/references/guide.md").write_text("guide\n", encoding="utf-8")
        (source / "catalog.json").write_text("{}\n", encoding="utf-8")
        (source / "tool.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        os.chmod(source / "tool.sh", 0o755)
        (source / "readonly.txt").write_text("fixed\n", encoding="utf-8")
        os.chmod(source / "readonly.txt", 0o444)
        self.corpus = SharedCorpus(self.base / "shared", source=source)
        self.addCleanup(self._open_everything)
        self.root = self.corpus.root
        self.first = tree(self.root)

    def _open_everything(self) -> None:
        for folder, names, _files in os.walk(self.base):
            for name in names:
                path = Path(folder) / name
                if not path.is_symlink():
                    os.chmod(path, 0o755)

    def assertRestored(self) -> None:
        self.assertEqual(self.corpus.restored(), self.root)
        self.assertEqual(tree(self.root), self.first)

    def test_nothing_a_test_did_reaches_the_next(self) -> None:
        outside = self.base / "outside"
        outside.write_text("outside\n", encoding="utf-8")

        def changed() -> None:
            (self.root / "catalog.json").write_text("changed\n", encoding="utf-8")

        def changed_same_size() -> None:
            (self.root / "catalog.json").write_text("[]\n", encoding="utf-8")

        def removed_file() -> None:
            (self.root / "catalog.json").unlink()

        def added_file() -> None:
            (self.root / "skills/one/new.md").write_text("new\n", encoding="utf-8")

        def added_folder() -> None:
            (self.root / "new/deep").mkdir(parents=True)
            (self.root / "new/deep/held").write_text("x\n", encoding="utf-8")

        def removed_folder() -> None:
            shutil.rmtree(self.root / "skills/one")

        def removed_empty_folder() -> None:
            (self.root / "empty").rmdir()

        def file_became_folder() -> None:
            (self.root / "catalog.json").unlink()
            (self.root / "catalog.json").mkdir()
            (self.root / "catalog.json/inner").write_text("x\n", encoding="utf-8")

        def folder_became_file() -> None:
            shutil.rmtree(self.root / "skills/one/references")
            (self.root / "skills/one/references").write_text("x\n", encoding="utf-8")

        def file_became_link() -> None:
            (self.root / "catalog.json").unlink()
            (self.root / "catalog.json").symlink_to(outside)

        def folder_became_link() -> None:
            shutil.rmtree(self.root / "skills/one")
            (self.root / "skills/one").symlink_to(self.base)

        def mode_changed() -> None:
            os.chmod(self.root / "tool.sh", 0o644)

        def file_closed() -> None:
            os.chmod(self.root / "catalog.json", 0o000)

        def readonly_file_rewritten() -> None:
            os.chmod(self.root / "readonly.txt", 0o644)
            (self.root / "readonly.txt").write_text("rewritten\n", encoding="utf-8")
            os.chmod(self.root / "readonly.txt", 0o444)

        def folder_closed() -> None:
            os.chmod(self.root / "skills/one", 0o000)

        def folder_read_only_with_a_stray() -> None:
            (self.root / "skills/one/stray").write_text("x\n", encoding="utf-8")
            os.chmod(self.root / "skills/one", 0o500)

        def root_closed() -> None:
            (self.root / "stray").write_text("x\n", encoding="utf-8")
            os.chmod(self.root, 0o500)

        def everything_gone() -> None:
            shutil.rmtree(self.root)

        for change in (changed, changed_same_size, removed_file, added_file, added_folder, removed_folder,
                       removed_empty_folder, file_became_folder, folder_became_file, file_became_link,
                       folder_became_link, mode_changed, file_closed,
                       readonly_file_rewritten, folder_closed, folder_read_only_with_a_stray, root_closed,
                       everything_gone):
            with self.subTest(change=change.__name__):
                change()
                self.assertRestored()
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")

    def test_restoring_through_a_planted_link_never_writes_outside(self) -> None:
        outside = self.base / "outside.json"
        outside.write_text("outside\n", encoding="utf-8")
        (self.root / "catalog.json").unlink()
        (self.root / "catalog.json").symlink_to(outside)
        self.assertRestored()
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")

    def test_a_link_in_the_source_is_held_as_what_it_points_to(self) -> None:
        source = self.base / "linked"
        (source / "one").mkdir(parents=True)
        (source / "one/held").write_text("held\n", encoding="utf-8")
        (source / "two").symlink_to("one", target_is_directory=True)
        corpus = SharedCorpus(self.base / "linked-copy", source=source)
        (corpus.root / "two/held").write_text("changed\n", encoding="utf-8")
        self.assertEqual((corpus.restored() / "two/held").read_text(encoding="utf-8"), "held\n")
        self.assertFalse((corpus.root / "two").is_symlink())

    def test_a_second_name_for_a_held_file_takes_nothing_back(self) -> None:
        kept = self.base / "kept-outside"
        os.link(self.root / "tool.sh", kept)
        (self.root / "tool.sh").write_text("changed\n", encoding="utf-8")
        self.assertRestored()
        self.assertEqual(kept.read_text(encoding="utf-8"), "changed\n")
        self.assertEqual((self.root / "tool.sh").stat().st_nlink, 1)

    def test_two_held_files_made_one_are_parted(self) -> None:
        (self.root / "catalog.json").unlink()
        os.link(self.root / "tool.sh", self.root / "catalog.json")
        self.assertRestored()
        self.assertEqual((self.root / "catalog.json").stat().st_nlink, 1)

    def test_a_kept_handle_cannot_reach_the_next_test(self) -> None:
        with (self.root / "catalog.json").open("a", encoding="utf-8") as kept:
            kept.write("first\n")
            kept.flush()
            self.assertRestored()
            kept.write("late\n")
            kept.flush()
            self.assertEqual(tree(self.root), self.first)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs named pipes")
    def test_a_pipe_or_socket_at_a_held_path_is_replaced(self) -> None:
        def pipe() -> None:
            os.mkfifo(self.root / "catalog.json")

        def bound_socket() -> None:
            listener = socket.socket(socket.AF_UNIX)
            self.addCleanup(listener.close)
            # A socket's path has a short limit, so bind from inside the folder.
            previous = os.getcwd()
            os.chdir(self.root)
            try:
                listener.bind("catalog.json")
            finally:
                os.chdir(previous)

        for make in (pipe, bound_socket):
            with self.subTest(make=make.__name__):
                (self.root / "catalog.json").unlink()
                make()
                self.assertRestored()

    def test_the_copy_itself_replaced_is_put_back(self) -> None:
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "mine.txt").write_text("mine\n", encoding="utf-8")

        def replaced_by_a_link() -> None:
            shutil.rmtree(self.root)
            self.root.symlink_to(elsewhere)

        def replaced_by_a_file() -> None:
            shutil.rmtree(self.root)
            self.root.write_text("x\n", encoding="utf-8")

        for change in (replaced_by_a_link, replaced_by_a_file):
            with self.subTest(change=change.__name__):
                change()
                self.assertRestored()
                self.assertFalse(self.root.is_symlink())
        self.assertEqual([path.name for path in elsewhere.iterdir()], ["mine.txt"])

    def test_restoring_an_untouched_copy_writes_nothing(self) -> None:
        stamps = {path: path.lstat().st_mtime_ns for path in self.root.rglob("*")}
        self.corpus.restored()
        self.assertEqual({path: path.lstat().st_mtime_ns for path in self.root.rglob("*")}, stamps)

    def test_only_what_changed_is_written_back(self) -> None:
        (self.root / "catalog.json").write_text("changed\n", encoding="utf-8")
        stamps = {path: path.lstat().st_mtime_ns for path in self.root.rglob("*") if path.name != "catalog.json"}
        self.corpus.restored()
        self.assertEqual({path: path.lstat().st_mtime_ns for path in stamps}, stamps)

    def test_it_holds_its_first_bytes_when_the_source_later_changes(self) -> None:
        (self.base / "source/catalog.json").write_text("moved on\n", encoding="utf-8")
        (self.root / "catalog.json").write_text("changed\n", encoding="utf-8")
        self.assertRestored()


class SharedAcrossTestsTests(unittest.TestCase):
    """The same copy through a real class's tests, in either order: neither sees the other's write."""

    @classmethod
    def setUpClass(cls) -> None:
        holder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(holder.cleanup)
        source = Path(holder.name) / "source"
        source.mkdir()
        (source / "catalog.json").write_text("{}\n", encoding="utf-8")
        cls.corpus = SharedCorpus(Path(holder.name) / "shared", source=source)

    def setUp(self) -> None:
        self.addCleanup(self.corpus.restored)
        self.root = self.corpus.restored()

    def test_first_writer(self) -> None:
        self.assertEqual((self.root / "catalog.json").read_text(encoding="utf-8"), "{}\n")
        self.assertFalse((self.root / "stray").exists())
        (self.root / "catalog.json").write_text("first\n", encoding="utf-8")
        (self.root / "stray").write_text("first\n", encoding="utf-8")

    def test_second_writer(self) -> None:
        self.assertEqual((self.root / "catalog.json").read_text(encoding="utf-8"), "{}\n")
        self.assertFalse((self.root / "stray").exists())
        (self.root / "catalog.json").write_text("second\n", encoding="utf-8")
        (self.root / "stray").write_text("second\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
