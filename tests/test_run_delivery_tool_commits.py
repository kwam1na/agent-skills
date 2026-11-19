"""run-delivery's own commits skip the repository's git hooks.

The lead's plan change, its approval and its base-move merge each commit files the tool wrote itself. A repository hook
that fails, or rewrites a staged file or the message, must change none of them, whatever the merge-queue binding's
`hooks` says, and the setting must never reach the repository's configuration. The commands run for real in the
base-move fixture `BaseMoveTests` builds, one fixture for the class, put back for each case.

The corpus guard reads `run_delivery.py` itself: a command that builds a commit outside `tool_commit` fails it.
"""

from __future__ import annotations

import ast
import json
import os
import stat
import subprocess
import unittest
from pathlib import Path

from unittest import mock

import tests.test_run_delivery as characterization
from tests.test_run_delivery import PROFILE, ROOT, build_release, commit, installed_release, kept_fixture, sh

TOOL_SOURCE = ROOT / "skills/run-delivery/scripts/run_delivery.py"
BUILDS = ("commit", "merge")
REWRITTEN = "rewritten by a hook"


def constant(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def stray_commits(source: str) -> list[int]:
    """The lines of every git command in `source` that builds a commit outside `tool_commit`: a `git(...)` call, or a
    `run([...])` whose argv starts with git, naming `commit` or `merge`. A `--ff-only` merge builds none, and neither
    does a command that is only printed."""
    tree = ast.parse(source)
    inside = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "tool_commit":
            inside.update(id(child) for child in ast.walk(node))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in inside or not node.args:
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        if name == "git":
            words = [constant(arg) for arg in node.args]
        elif name == "run" and isinstance(node.args[0], ast.List) and node.args[0].elts \
                and constant(node.args[0].elts[0]) == "git":
            words = [constant(arg) for arg in node.args[0].elts[1:]]
        else:
            continue
        if any(word in BUILDS for word in words) and "--ff-only" not in words:
            found.append(node.lineno)
    return found


class ToolCommitCorpusTests(unittest.TestCase):
    def test_run_delivery_builds_every_commit_through_tool_commit(self) -> None:
        source = TOOL_SOURCE.read_text(encoding="utf-8")
        self.assertIn("def tool_commit(", source)
        self.assertEqual(stray_commits(source), [])

    def test_each_spelling_outside_tool_commit_fails_the_guard(self) -> None:
        for spelling in ('git("commit", "-q", "-m", "x", cwd=root)', 'git("merge", "--no-ff", head)',
                         'run(["git", "commit", "-m", "x"])', 'run(["git", "merge", "--no-ff", "--no-commit", head])',
                         "git('commit', '-m', 'x')", 'subprocess.run(["git", "merge", head], check=False)'):
            with self.subTest(spelling=spelling):
                fixture = f"def tool_commit(*args):\n    return run(['git', *args])\n\n\ndef sync(root, head):\n    {spelling}\n"
                self.assertEqual(stray_commits(fixture), [6])

    def test_a_printed_command_and_a_fast_forward_pass_the_guard(self) -> None:
        fixture = ("def tool_commit(*args, cwd=None, check=True):\n"
                   "    return run(['git', '-c', 'core.hooksPath=x', *args], cwd=cwd, check=check)\n\n\n"
                   "def close(operator, ref, head):\n"
                   "    print(shlex.join(['git', 'merge', '--no-ff', '--no-log', head]))\n"
                   "    print(shlex.join(['git', 'commit', '-m', 'x']))\n"
                   "    run(['git', 'merge', '--ff-only', '-q', ref], cwd=operator, check=False)\n"
                   "    git('merge-base', '--is-ancestor', head, ref)\n"
                   "    tool_commit('commit', '-q', '-m', 'x', cwd=operator)\n"
                   "    tool_commit('merge', '--no-ff', '--no-commit', head, check=False)\n")
        self.assertEqual(stray_commits(fixture), [])


    def test_the_config_reference_says_which_commits_skip_which_hooks(self) -> None:
        config = " ".join((ROOT / "skills/run-delivery/references/config.md").read_text(encoding="utf-8").split())
        for phrase in ("### The repository's git hooks These are not the host hooks above.",
                       "run-delivery's own commits run none of the repository's git hooks: `lead-plan change` and "
                       "`lead-plan approval`", "and `lead-sync --merge`, which merges the default branch",
                       "whatever the binding's `hooks` says. Every hook is skipped, `pre-commit`, `commit-msg` and a "
                       "secret scanner's included, not only a formatter",
                       "A commit a lane or the lead makes with plain git still runs every hook"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, config)


class ToolCommitHookTests(unittest.TestCase):
    """The three commits, with failing hooks and with rewriting ones, under the binding's `hooks` set and unset."""

    # The base-move fixture's own methods, read off its class: a module-level name for the class would run its cases here.
    build_fixture = characterization.BaseMoveTests.build_fixture
    add_output = characterization.BaseMoveTests.add_output
    move = characterization.BaseMoveTests.move

    @classmethod
    def setUpClass(cls) -> None:
        cls.release = installed_release()
        cls.new_archive = cls.release.build / "base-move-new.zip"
        cls.new_metadata = cls.release.build / "base-move-new.release.json"
        if not cls.new_metadata.exists():
            build_release(ROOT, cls.new_archive, cls.new_metadata, release_id="base-move-new", profile_id=PROFILE,
                          runtime_directory=cls.release.build / "runtime")

    def build_planned_fixture(self):
        """The base-move fixture, its delivery planned with criteria and an approval waiting before `lead-init`."""
        setup = characterization.setup_delivery

        def planned(fx):
            path = characterization.plan_delivery(fx, setup(fx))
            delivery = json.loads(path.read_text())
            delivery["approvals"] = [{"id": "approval-1", "text": "Copy sign-off", "state": "waiting"}]
            path.write_text(json.dumps(delivery, indent=2) + "\n")
            commit(fx.main, "FX-1: an approval")
            sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
            return path

        with mock.patch.object(characterization, "setup_delivery", planned):
            return self.build_fixture()

    def fixture(self, hooks: str | None):
        """The planned fixture with the binding's `hooks` as given (None: no key), committed and pushed before any hook
        is installed."""
        fx, old = kept_fixture(self, self.build_planned_fixture)
        m = fx.main
        binding_path = m / ".agents/orchestration.json"
        binding = json.loads(binding_path.read_text())
        if hooks is None:
            binding.pop("hooks", None)
        else:
            binding["hooks"] = hooks
        binding_path.write_text(json.dumps(binding, indent=2) + "\n")
        if sh("git", "status", "--porcelain", cwd=m).stdout:
            commit(m, "The binding's hooks")
            sh("git", "push", "-q", "origin", "integrate/fixture", cwd=m)
        self.assertEqual(json.loads(binding_path.read_text()).get("hooks"), hooks)
        return fx

    def install_hooks(self, fx, scripts: dict[str, str]) -> tuple[Path, Path]:
        """Hooks through the repository's own `core.hooksPath`; each marks a folder when it runs, then runs its body."""
        hooks, ran = fx.tmp / "repository-hooks", fx.tmp / "hooks-ran"
        hooks.mkdir()
        ran.mkdir()
        for name, body in scripts.items():
            path = hooks / name
            path.write_text(f"#!/bin/sh\ntouch '{ran}/{name}'\n{body}\n")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        sh("git", "config", "core.hooksPath", str(hooks), cwd=fx.main)
        return hooks, ran

    def committed(self, fx, rev: str = "HEAD") -> str:
        """The commit's message and its change against its first parent."""
        m = fx.main
        return (sh("git", "log", "-1", "--format=%B", rev, cwd=m).stdout
                + sh("git", "diff", f"{rev}^1", rev, cwd=m).stdout)

    def three_commits(self, fx) -> dict[str, str]:
        """Run the plan change, the approval and the base move, each to a commit; return each commit's id."""
        m = fx.main
        made = {}
        code, out = fx.flow("lead-plan", "change", "--kind", "added", "--subjects", "FX-4", "--title", "Thing four",
                            "--text", "Thing four works", "--why", "A lane found the gap.", "--lane", "lane-1", cwd=m)
        self.assertEqual(code, 0, out)
        made["plan change"] = sh("git", "rev-parse", "HEAD", cwd=m).stdout.strip()
        self.assertIn("plan change", sh("git", "log", "-1", "--format=%s", cwd=m).stdout)
        code, out = fx.flow("lead-plan", "approval", "approval-1", "--state", "done", cwd=m)
        self.assertEqual(code, 0, out)
        made["approval"] = sh("git", "rev-parse", "HEAD", cwd=m).stdout.strip()
        self.assertNotEqual(made["approval"], made["plan change"])
        sh("git", "push", "-q", "origin", "integrate/fixture", cwd=m)
        code, out = self.move(fx)
        self.assertEqual(code, 0, out)
        made["merge"] = sh("git", "rev-parse", "HEAD", cwd=m).stdout.strip()
        self.assertEqual(len(sh("git", "show", "-s", "--format=%P", made["merge"], cwd=m).stdout.split()), 2)
        self.assertEqual(sh("git", "status", "--porcelain", cwd=m).stdout, "")
        return made

    def plain_commit(self, fx) -> subprocess.CompletedProcess:
        m = fx.main
        (m / "src/plain.txt").write_text("plain\n")
        sh("git", "add", "src/plain.txt", cwd=m)
        return sh("git", "commit", "-q", "-m", "A plain commit", cwd=m, check=False)

    def assert_configuration_kept(self, fx, hooks: Path) -> None:
        self.assertEqual(sh("git", "config", "--get-all", "core.hooksPath", cwd=fx.main).stdout.strip(), str(hooks))
        common = Path(sh("git", "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=fx.main).stdout.strip())
        self.assertNotIn("empty-hooks", (common / "config").read_text())
        empty = common / "run-delivery" / "empty-hooks"
        self.assertTrue(empty.is_dir())
        self.assertEqual(list(empty.iterdir()), [])

    def test_failing_hooks_stop_none_of_the_three_commits_under_bypass(self) -> None:
        fx = self.fixture("bypass")
        hooks, ran = self.install_hooks(fx, {"pre-commit": "exit 1", "commit-msg": "exit 1",
                                             "pre-merge-commit": "exit 1"})
        made = self.three_commits(fx)
        self.assertEqual(sorted(os.listdir(ran)), [], made)
        self.assert_configuration_kept(fx, hooks)
        refused = self.plain_commit(fx)
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertEqual(sorted(os.listdir(ran)), ["pre-commit"])
        self.assertEqual(sh("git", "rev-parse", "HEAD", cwd=fx.main).stdout.strip(), made["merge"])

    def test_rewriting_hooks_change_none_of_the_three_commits_with_no_hooks_setting(self) -> None:
        fx = self.fixture(None)
        rewrite = (f"for f in $(git diff --cached --name-only --diff-filter=AM); do\n"
                   f"  printf '%s\\n' '{REWRITTEN}' >> \"$f\" && git add \"$f\"\ndone\nexit 0")
        hooks, ran = self.install_hooks(fx, {"pre-commit": rewrite,
                                             "commit-msg": f"printf '%s\\n' '{REWRITTEN}' >> \"$1\"\nexit 0"})
        made = self.three_commits(fx)
        self.assertEqual(sorted(os.listdir(ran)), [], made)
        for what, rev in made.items():
            with self.subTest(commit=what):
                self.assertNotIn(REWRITTEN, self.committed(fx, rev))
        # The tool's bytes: the delivery file and the plan, as the tool left them in the checkout, are what HEAD holds.
        for name in (".agents/deliveries/FX-1.json", "docs/plan.md"):
            self.assertEqual(sh("git", "show", f"HEAD:{name}", cwd=fx.main).stdout, (fx.main / name).read_text())
        self.assertIn("FX-4", (fx.main / ".agents/deliveries/FX-1.json").read_text())
        self.assert_configuration_kept(fx, hooks)
        done = self.plain_commit(fx)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(os.listdir(ran)), ["commit-msg", "pre-commit"])
        self.assertIn(REWRITTEN, self.committed(fx))


if __name__ == "__main__":
    unittest.main()
