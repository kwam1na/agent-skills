from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap-test-environment.py"


def _load_bootstrap():
    spec = importlib.util.spec_from_file_location("bootstrap_test_environment", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestEnvironmentTests(unittest.TestCase):
    def test_each_worktree_owns_its_editable_install(self) -> None:
        bootstrap = _load_bootstrap()
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            worktrees = (base / "worktree-a", base / "worktree-b")
            for worktree in worktrees:
                worktree.mkdir()

            with mock.patch.object(bootstrap.subprocess, "run") as run:
                interpreters = [bootstrap.bootstrap(worktree) for worktree in worktrees]

            resolved = [worktree.resolve() for worktree in worktrees]
            expected = [bootstrap.environment_python(worktree / ".venv") for worktree in resolved]
            self.assertEqual(interpreters, expected)
            self.assertNotEqual(*interpreters)
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    [bootstrap.sys.executable, "-m", "venv", str(resolved[0] / ".venv")],
                    [str(expected[0]), "-m", "pip", "install", "-e", ".[test]"],
                    [bootstrap.sys.executable, "-m", "venv", str(resolved[1] / ".venv")],
                    [str(expected[1]), "-m", "pip", "install", "-e", ".[test]"],
                ],
            )
            self.assertEqual(
                [call.kwargs["cwd"] for call in run.call_args_list],
                [resolved[0], resolved[0], resolved[1], resolved[1]],
            )


if __name__ == "__main__":
    unittest.main()
