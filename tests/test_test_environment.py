from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap-test-environment.py"


def _load_bootstrap():
    spec = importlib.util.spec_from_file_location("bootstrap_test_environment", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_dependency_wheel(path: Path, identity: str) -> None:
    dist_info = "fixture_dependency-1.0.dist-info"
    files = {
        "fixture_dependency.py": f"IDENTITY = {identity!r}\n",
        f"{dist_info}/METADATA": "Metadata-Version: 2.1\nName: fixture-dependency\nVersion: 1.0\n",
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: isolation-test\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    files[f"{dist_info}/RECORD"] = "".join(f"{name},,\n" for name in (*files, f"{dist_info}/RECORD"))
    with zipfile.ZipFile(path, "w") as wheel:
        for name, contents in files.items():
            wheel.writestr(name, contents)


def _write_checkout(root: Path, identity: str) -> None:
    root.mkdir()
    (root / "checkout_marker.py").write_text(f"IDENTITY = {identity!r}\n", encoding="utf-8")
    dependency = root / "fixture_dependency-1.0-py3-none-any.whl"
    _write_dependency_wheel(dependency, identity)
    (root / "pyproject.toml").write_text(
        "[build-system]\nrequires = []\nbuild-backend = \"fixture_backend\"\n"
        "backend-path = [\".\"]\n",
        encoding="utf-8",
    )
    (root / "fixture_backend.py").write_text(
        "from pathlib import Path\n"
        "import zipfile\n\n"
        "ROOT = Path(__file__).resolve().parent\n"
        "DIST_INFO = 'fixture_checkout-1.0.dist-info'\n"
        "WHEEL = 'fixture_checkout-1.0-py3-none-any.whl'\n\n"
        "def build_editable(wheel_directory, config_settings=None, metadata_directory=None):\n"
        "    files = {\n"
        "        'fixture_checkout.pth': f'{ROOT}\\n',\n"
        "        f'{DIST_INFO}/METADATA': (\n"
        "            'Metadata-Version: 2.1\\nName: fixture-checkout\\nVersion: 1.0\\n'\n"
        "            'Provides-Extra: test\\n'\n"
        f"            'Requires-Dist: fixture-dependency @ {dependency.as_uri()} ; extra == \\\"test\\\"\\n'\n"
        "        ),\n"
        "        f'{DIST_INFO}/WHEEL': (\n"
        "            'Wheel-Version: 1.0\\nGenerator: isolation-test\\n'\n"
        "            'Root-Is-Purelib: true\\nTag: py3-none-any\\n'\n"
        "        ),\n"
        "    }\n"
        "    files[f'{DIST_INFO}/RECORD'] = ''.join(\n"
        "        f'{name},,\\n' for name in (*files, f'{DIST_INFO}/RECORD')\n"
        "    )\n"
        "    with zipfile.ZipFile(Path(wheel_directory) / WHEEL, 'w') as wheel:\n"
        "        for name, contents in files.items():\n"
        "            wheel.writestr(name, contents)\n"
        "    return WHEEL\n",
        encoding="utf-8",
    )


class TestEnvironmentTests(unittest.TestCase):
    def test_each_worktree_bootstrap_installs_its_own_source_and_test_dependency(self) -> None:
        bootstrap = _load_bootstrap()
        with tempfile.TemporaryDirectory() as temporary:
            worktrees = [Path(temporary) / f"worktree-{index}" for index in range(2)]
            for index, worktree in enumerate(worktrees):
                _write_checkout(worktree, f"worktree-{index}")

            environment = {
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_INDEX": "1",
                "PIP_QUIET": "1",
            }
            with mock.patch.dict(os.environ, environment):
                interpreters = [bootstrap.bootstrap(worktree) for worktree in worktrees]

            self.assertNotEqual(*interpreters)
            observations = [
                subprocess.run(
                    [
                        str(interpreter),
                        "-I",
                        "-c",
                        (
                            "import checkout_marker, fixture_dependency; "
                            "print(checkout_marker.IDENTITY); "
                            "print(fixture_dependency.IDENTITY); "
                            "print(checkout_marker.__file__); "
                            "print(fixture_dependency.__file__)"
                        ),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.splitlines()
                for interpreter in interpreters
            ]
            for index, (worktree, lines) in enumerate(zip(worktrees, observations, strict=True)):
                self.assertEqual(lines[:2], [f"worktree-{index}", f"worktree-{index}"])
                resolved = worktree.resolve()
                self.assertTrue(Path(lines[2]).is_relative_to(resolved))
                self.assertTrue(Path(lines[3]).is_relative_to(resolved / ".venv"))

    def test_page_lane_without_node_fails_naming_node_before_creating_anything(self) -> None:
        bootstrap = _load_bootstrap()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "worktree"
            root.mkdir()
            (root / "package-lock.json").write_text("{}\n", encoding="utf-8")
            with mock.patch.object(bootstrap.shutil, "which", return_value=None):
                with self.assertRaises(SystemExit) as raised:
                    bootstrap.bootstrap(root)
            self.assertIn("Node.js 20 or newer", str(raised.exception.code))
            self.assertFalse((root / ".venv").exists())


if __name__ == "__main__":
    unittest.main()
