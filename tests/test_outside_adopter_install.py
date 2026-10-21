from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.lifecycle_support import CORPUS_ROOT, build_version, run_git


FIXTURE = CORPUS_ROOT / "tests/fixtures/outside-adopter"


def snapshot(root: Path) -> dict[str, tuple[str, bytes | str]]:
    """Include empty directories and links, excluding Git's private bookkeeping."""
    result = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative.parts[0] == ".git":
            continue
        if path.is_symlink():
            result[relative.as_posix()] = ("link", os.readlink(path))
        elif path.is_dir():
            result[relative.as_posix()] = ("directory", "")
        else:
            result[relative.as_posix()] = ("file", path.read_bytes())
    return result


class OutsideAdopterInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.release_directory = tempfile.TemporaryDirectory()
        cls.archive, cls.metadata = build_version(Path(cls.release_directory.name), "outside-core")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.release_directory.cleanup()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repository = self.base / "repository"
        shutil.copytree(FIXTURE, self.repository)
        # Preserve a pre-existing ignored directory, including unrelated bytes.
        (self.repository / ".claude").mkdir()
        (self.repository / ".claude/settings.json").write_text("{}\n")
        run_git(self.repository, "init", "-q")
        self.bin = self.base / "bin"
        self.bin.mkdir()
        (self.bin / "git").symlink_to(shutil.which("git"))
        self.environment = {
            "PATH": str(self.bin),
            "HOME": str(self.base),
            "GIT_CONFIG_NOSYSTEM": "1",
        }

    def documented_command(self, operation: str) -> subprocess.CompletedProcess[str]:
        readme = (CORPUS_ROOT / "README.md").read_text()
        section = readme.split("## Install core skills without a harness", 1)[1].split("\n## ", 1)[0]
        commands = re.search(r"```sh\n(.*?)\n```", section, re.S)
        self.assertIsNotNone(commands)
        assert commands is not None
        arguments = next(shlex.split(line) for line in commands[1].splitlines()
                         if operation in shlex.split(line))
        replacements = {
            "/path/to/core.zip": str(self.archive),
            "/path/to/core.release.json": str(self.metadata),
            "/path/to/repository": str(self.repository),
            "/path/to/repository/.agent-skills/current": str(self.repository / ".agent-skills/current"),
        }
        arguments = [replacements.get(argument, argument) for argument in arguments]
        # Execute the documented command with no site-packages, producer imports,
        # Node, npm, or shell available; the host's Python is invoked absolutely.
        return subprocess.run(
            [sys.executable, "-I", "-S", *arguments[1:]],
            cwd=self.base,
            env=self.environment,
            capture_output=True,
            text=True,
        )

    def test_ignored_claude_install_discovery_and_exact_removal(self) -> None:
        for ignore_rules, ignored in (
            (".claude/\n", True),
            (".claude/\n!.claude/skills/\n", True),
            (".claude/\n!.claude/\n.claude/*\n!.claude/skills/\n", False),
        ):
            with self.subTest(ignore_rules=ignore_rules):
                (self.repository / ".gitignore").write_text(ignore_rules)
                before = snapshot(self.repository)
                applied = self.documented_command("apply")
                self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
                installed = self.repository / ".agent-skills/current"
                status = self.documented_command("status")
                self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
                state = json.loads(status.stdout)
                self.assertEqual(state["lifecycle"], "current")
                self.assertEqual(state["blockers"], [])
                self.assertFalse((installed / "runtime").exists())
                self.assertFalse((self.repository / ".agents/policy").exists())
                for host in (".agents", ".claude"):
                    link = self.repository / host / "skills/deliver-work"
                    self.assertTrue(link.is_symlink())
                    self.assertEqual((link / "SKILL.md").read_bytes(),
                                     (installed / "skills/deliver-work/SKILL.md").read_bytes())
                check = subprocess.run(
                    [str(self.bin / "git"), "check-ignore", "-q", ".claude/skills/deliver-work"],
                    cwd=self.repository,
                )
                self.assertEqual(check.returncode, 0 if ignored else 1)
                self.assertFalse((self.repository / "AGENTS.md").exists())
                # Removal preserves adopter edits made after installation too.
                (self.repository / "CLAUDE.md").write_bytes(b"Updated adopter instructions.\n")
                before["CLAUDE.md"] = ("file", b"Updated adopter instructions.\n")
                removed = self.documented_command("remove")
                self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
                self.assertEqual(snapshot(self.repository), before)

    def test_missing_git_fails_before_mutation_with_actionable_message(self) -> None:
        (self.bin / "git").unlink()
        before = snapshot(self.repository)
        result = self.documented_command("apply")
        self.assertNotEqual(result.returncode, 0)
        error = json.loads(result.stdout)["error"]
        self.assertEqual(error["code"], "prerequisite.git_missing")
        self.assertIn("Install Git", error["message"])
        self.assertIn("PATH", error["message"])
        self.assertEqual(snapshot(self.repository), before)
