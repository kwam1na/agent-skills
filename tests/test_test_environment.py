from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


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

            resolved = [worktree.resolve() for worktree in worktrees]
            interpreters = [
                bootstrap.create_environment(worktree, without_pip=True)
                for worktree in worktrees
            ]
            expected = [bootstrap.environment_python(worktree / ".venv") for worktree in resolved]
            self.assertEqual(interpreters, expected)
            self.assertNotEqual(*interpreters)

            for index, interpreter in enumerate(interpreters):
                purelib = Path(
                    subprocess.run(
                        [str(interpreter), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                )
                (purelib / "checkout_marker.py").write_text(
                    f"IDENTITY = 'worktree-{index}'\n",
                    encoding="utf-8",
                )

            observations = [
                subprocess.run(
                    [
                        str(interpreter),
                        "-I",
                        "-c",
                        "import checkout_marker; print(checkout_marker.IDENTITY); print(checkout_marker.__file__)",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.splitlines()
                for interpreter in interpreters
            ]
            self.assertEqual([lines[0] for lines in observations], ["worktree-0", "worktree-1"])
            for index, lines in enumerate(observations):
                self.assertTrue(Path(lines[1]).is_relative_to(resolved[index] / ".venv"))


if __name__ == "__main__":
    unittest.main()
