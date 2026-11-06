from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
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


class NamedEnvironmentTests(unittest.TestCase):
    def test_the_command_line_names_the_environment_and_only_a_venv_in_the_checkout(self) -> None:
        bootstrap = _load_bootstrap()
        with mock.patch.object(bootstrap, "bootstrap", return_value=Path("python")) as run, mock.patch("builtins.print"):
            bootstrap.main([])
            bootstrap.main(["--environment", ".venv-3.11"])
        self.assertEqual([call.kwargs["environment"] for call in run.call_args_list], [".venv", ".venv-3.11"])
        for refused in ("../outside", "/tmp/absolute", "skills", ".", "", "venv311", ".venv-a/b", ".venv-"):
            with self.subTest(refused=refused), mock.patch.object(bootstrap, "bootstrap") as run, \
                    mock.patch("sys.stderr"), self.assertRaises(SystemExit):
                bootstrap.main(["--environment", refused])
            run.assert_not_called()

    def test_an_environment_can_be_named_and_the_default_stays_the_checkouts_venv(self) -> None:
        bootstrap = _load_bootstrap()
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(bootstrap.subprocess, "run") as run:
            root = Path(temporary)
            named = bootstrap.bootstrap(root, environment=".venv-3.11")
            default = bootstrap.bootstrap(root)
        root = root.resolve()
        self.assertEqual(named, bootstrap.environment_python(root / ".venv-3.11"))
        self.assertEqual(default, bootstrap.environment_python(root / ".venv"))
        created = [call.args[0][-1] for call in run.call_args_list if "venv" in call.args[0]]
        self.assertEqual(created, [str(root / ".venv-3.11"), str(root / ".venv")])


class PinnedPythonTests(unittest.TestCase):
    """A checkout's `.python-version` chooses the default environment's Python, so it matches CI's."""

    NOT_RUNNING = "3.99"
    REPOSITORY = SCRIPT.parents[1]

    def setUp(self) -> None:
        self.bootstrap = _load_bootstrap()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def pin(self, version: str) -> None:
        (self.root / ".python-version").write_text(f"{version}\n", encoding="utf-8")

    def build(self, *, path: dict[str, str] | None = None, stdout: str = "", returncode: int = 0, **kwargs) -> list[list[str]]:
        """Bootstrap with nothing really run, and return the commands it asked for, in order."""
        answer = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")
        with mock.patch.object(self.bootstrap.subprocess, "run", return_value=answer) as run, \
                mock.patch.object(self.bootstrap.shutil, "which", side_effect=(path or {}).get):
            try:
                self.bootstrap.bootstrap(self.root, **kwargs)
            finally:
                self.commands = [call.args[0] for call in run.call_args_list]
        return self.commands

    def venv_commands(self, commands: list[list[str]]) -> list[list[str]]:
        return [command for command in commands if command[1:3] == ["-m", "venv"]]

    def test_the_default_environment_uses_the_pinned_python_found_on_the_path(self) -> None:
        self.pin(self.NOT_RUNNING)
        commands = self.build(path={f"python{self.NOT_RUNNING}": "/found/python"})
        self.assertEqual(self.venv_commands(commands), [["/found/python", "-m", "venv", str(self.root / ".venv")]])

    def test_a_python_only_uv_knows_of_is_found_through_uv(self) -> None:
        self.pin(self.NOT_RUNNING)
        commands = self.build(path={"uv": "/found/uv"}, stdout="/uv/python\n")
        self.assertEqual(commands[0], ["/found/uv", "python", "find", self.NOT_RUNNING])
        self.assertEqual(self.venv_commands(commands), [["/uv/python", "-m", "venv", str(self.root / ".venv")]])

    def test_running_the_pinned_python_already_uses_it_and_looks_nowhere_else(self) -> None:
        self.pin(f"{sys.version_info.major}.{sys.version_info.minor}")
        commands = self.build()
        self.assertEqual(self.venv_commands(commands), [[sys.executable, "-m", "venv", str(self.root / ".venv")]])

    def test_a_missing_pinned_python_says_how_to_install_it_and_creates_nothing(self) -> None:
        for path, returncode in (({}, 0), ({"uv": "/found/uv"}, 2)):
            with self.subTest(path=path):
                self.pin(self.NOT_RUNNING)
                with self.assertRaises(SystemExit) as raised:
                    self.build(path=path, returncode=returncode, stdout="/not/an/answer\n")
                self.assertIn(f"uv python install {self.NOT_RUNNING}", str(raised.exception.code))
                self.assertEqual(self.venv_commands(self.commands), [])
                self.assertFalse((self.root / ".venv").exists())

    def test_a_named_environment_keeps_the_python_running_the_script(self) -> None:
        self.pin(self.NOT_RUNNING)
        commands = self.build(environment=".venv-other")
        self.assertEqual(self.venv_commands(commands), [[sys.executable, "-m", "venv", str(self.root / ".venv-other")]])

    def test_an_unpinned_checkout_keeps_the_python_running_the_script(self) -> None:
        commands = self.build()
        self.assertEqual(self.venv_commands(commands), [[sys.executable, "-m", "venv", str(self.root / ".venv")]])

    def test_an_environment_on_another_python_is_rebuilt_and_a_matching_one_is_not(self) -> None:
        self.pin(self.NOT_RUNNING)
        (self.root / ".venv").mkdir()
        path = {f"python{self.NOT_RUNNING}": "/found/python"}
        target = str(self.root / ".venv")
        for line, command in (("version = 3.14.3", ["/found/python", "-m", "venv", "--clear", target]),
                              ("version_info = 3.14.3", ["/found/python", "-m", "venv", "--clear", target]),
                              ("home = /somewhere", ["/found/python", "-m", "venv", "--clear", target]),
                              (f"version = {self.NOT_RUNNING}.1", ["/found/python", "-m", "venv", target])):
            with self.subTest(line=line):
                (self.root / ".venv/pyvenv.cfg").write_text(f"{line}\n", encoding="utf-8")
                self.assertEqual(self.venv_commands(self.build(path=path)), [command])

    def test_the_pin_is_what_ci_and_the_release_run_and_the_declared_minimum(self) -> None:
        pin = (self.REPOSITORY / ".python-version").read_text(encoding="utf-8").strip()
        self.assertIn(f'requires-python = ">={pin}"', (self.REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
        workflows = self.REPOSITORY / ".github/workflows"
        ci = (workflows / "ci.yml").read_text(encoding="utf-8")
        self.assertIn(f'python-version: ["{pin}"]', ci)
        versions = re.findall(r'python-version: "([^"]+)"', ci + (workflows / "release.yml").read_text(encoding="utf-8"))
        self.assertTrue(versions)
        self.assertEqual(set(versions), {pin})


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
