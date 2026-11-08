from __future__ import annotations

import hashlib
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from agent_skills.release import RUNTIME_FILES, _path_digest, _release_view, verify_release


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cut-release.py"
RUNTIME = ROOT / "product-runtime"
VERSION = "v1.2.3-rc.1"
PRODUCTS = ("core", "linear", "bootstrap", "run-delivery", "jira")
RECORDS = ("core", "provider", "bootstrap", "linear")
# The seed's digests, frozen until a reviewed change replaces the seed.
FROZEN_SEED_SHA256 = {
    "LICENSE": "02ed546806a3298b12c633742eb2fd354821d5dc9a5ee4384d4ae8196737a83f",
    "NOTICE": "547c61e2d762d0cadff8c1560ac7b9c072d4d71315797e11629654079ce0882d",
    "bootstrap.mjs": "5df698a9e5d7bae942ec5a40819ba52b0688cab87c653ee8b42e30160ad1225a",
    "cli-api.d.mts": "9239b67f3cbc6a6b822d95a40cdb50bbd435d465dfc89b392c052ddb604610b3",
    "cli-api.mjs": "a957863e0420acdc184f2606b127062d1ab10dcce1a47a66185c231c17f001bb",
    "cli.mjs": "28ba844370716cf6f90fe927402cb3bc0e88a7ae5b3487ae361a7ae5521c5ddd",
    "kernel.d.mts": "d39bb80d058b22fc65ec8c67a30c5dd21d85bcae2fcf6f42d20a067d201cdc25",
    "kernel.mjs": "7ae17f7e6c4f6ecc919bc9ad72a7dfb2a1e848b248de4b5a151f74927d5ed79f",
    "loader.mjs": "d00b40605a54f7defd9b8cab05825009af57baf2d5884d736eddc391ba35a1e8",
    "policy.mjs": "320aa341d55833061c08b4c9e47f998c341229e964e84bebbf9db8da875a0d24",
}


def _load_command():
    spec = importlib.util.spec_from_file_location("cut_release", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_digests() -> dict[str, str]:
    return {path.name: _sha256(path) for path in RUNTIME.iterdir()}


def _named_outputs(version: str) -> set[str]:
    return {
        *(f"{profile}-{version}.zip" for profile in PRODUCTS),
        *(f"{profile}-{version}.release.json" for profile in PRODUCTS),
        *(f"{record}-{version}.json" for record in RECORDS),
    }


def _qualifier_double(fail: str | None = None, how: str = "exit"):
    """Stands in for the qualifier subprocesses, which the real run below exercises."""
    calls = []

    def run(command, **_kwargs):
        script = Path(command[1]).name
        calls.append(command)
        record = Path(command[command.index("--record") + 1])
        name = script.removeprefix("qualify-").removesuffix(".py")
        failing = name == fail
        if failing and how == "exit":
            return subprocess.CompletedProcess(command, 1, "", "qualifier says no")
        if failing and how == "stdout-only":
            return subprocess.CompletedProcess(command, 1, '{"error": "qualifier says no on stdout"}', "")
        if failing and how == "extra-file":
            (record.parent / "stray.txt").write_text("stray\n", encoding="utf-8")
            record.write_text(json.dumps({"summary": {"result": "passed"}}) + "\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        if failing and how == "no-record":
            return subprocess.CompletedProcess(command, 0, "", "")
        if failing and how == "malformed":
            record.write_text("not json\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        result = "failed" if failing else "passed"
        scenarios = [{"id": "named-scenario", "result": "failed", "failure": "named reason"}] if failing else []
        record.write_text(json.dumps({"scenarios": scenarios, "summary": {"result": result}}) + "\n", encoding="utf-8")
        exit_code = 1 if failing and how == "failed-record-exit" else 0
        return subprocess.CompletedProcess(command, exit_code, "", "")

    return run, calls


class VersionTests(unittest.TestCase):
    def test_accepted_versions(self) -> None:
        command = _load_command()
        for version in ("v1.2.3", "v1.2.3-rc.1", "v0.0.0", "v10.20.30-rc.0"):
            with self.subTest(version=version):
                self.assertTrue(command.valid_version(version))

    def test_refused_versions_write_nothing(self) -> None:
        command = _load_command()
        for version in (
            "1.2.3", "v1.2", "v01.2.3", "v1.02.3", "v1.2.03", "v1.2.3-beta", "v1.2.3-rc.01", "v1.2.3-rc",
            "some-set-v1", "", "v1.2.3\n", " v1.2.3", "v1.2.3-rc.1 ", "v١.2.3", "v1.2.3/../../x",
        ):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temporary:
                out = Path(temporary) / "out"
                with mock.patch.object(command, "build_set") as build, mock.patch("sys.stderr"):
                    self.assertEqual(command.main(["--version", version, "--out", str(out)]), 2)
                build.assert_not_called()
                self.assertEqual(list(Path(temporary).iterdir()), [])


class RuntimeSeedTests(unittest.TestCase):
    def test_the_seed_holds_exactly_the_frozen_files(self) -> None:
        digests = _runtime_digests()
        self.assertEqual(set(digests), set(RUNTIME_FILES))
        self.assertEqual({name: digest for name, digest in digests.items() if name != "runtime.json"}, FROZEN_SEED_SHA256)

    def test_the_descriptor_names_the_frozen_files(self) -> None:
        descriptor = json.loads((RUNTIME / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(descriptor["files"], [{"path": path, "sha256": FROZEN_SEED_SHA256[path]} for path in sorted(FROZEN_SEED_SHA256)])

    def test_the_seed_carries_the_protocol_an_adopter_uses(self) -> None:
        self.assertIn(b"installed-data-proof/1", (RUNTIME / "cli.mjs").read_bytes())


class RebindingTests(unittest.TestCase):
    def test_only_the_workflow_digest_changes_and_the_store_is_untouched(self) -> None:
        command = _load_command()
        before = _runtime_digests()
        stored = (RUNTIME / "runtime.json").read_text(encoding="utf-8")
        stored_digest = json.loads(stored)["workflowContentSha256"]
        with tempfile.TemporaryDirectory() as temporary:
            rebound = command.rebound_runtime("core", Path(temporary) / "core")
            view = _release_view(ROOT, "core")
            expected = _path_digest(sorted(view), view)
            self.assertEqual(
                (rebound / "runtime.json").read_text(encoding="utf-8"),
                stored.replace(stored_digest, expected),
            )
            for name in RUNTIME_FILES - {"runtime.json"}:
                self.assertEqual((rebound / name).read_bytes(), (RUNTIME / name).read_bytes(), name)
        self.assertEqual(_runtime_digests(), before)

    def test_a_store_that_is_not_exactly_the_runtime_files_is_refused(self) -> None:
        command = _load_command()
        for change in ("extra", "missing", "repeated-digest"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                store = directory / "store"
                shutil.copytree(RUNTIME, store)
                if change == "extra":
                    (store / "stray.mjs").write_text("// stray\n", encoding="utf-8")
                elif change == "missing":
                    (store / "NOTICE").unlink()
                else:
                    descriptor = store / "runtime.json"
                    text = descriptor.read_text(encoding="utf-8")
                    digest = json.loads(text)["workflowContentSha256"]
                    version = json.loads(text)["runtimeVersion"]
                    descriptor.write_text(text.replace(f'"{version}"', f'"{digest}"'), encoding="utf-8")
                with mock.patch.object(command, "RUNTIME_SOURCE", store), self.assertRaises(ValueError):
                    command.rebound_runtime("core", directory / "core")


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.command = _load_command()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.first = Path(cls.temporary.name) / "first"
        cls.second = Path(cls.temporary.name) / "second"
        cls.command.build_products(VERSION, cls.first)
        cls.command.build_products(VERSION, cls.second)

    def test_building_twice_gives_identical_digests(self) -> None:
        for profile in PRODUCTS:
            for suffix in (".zip", ".release.json"):
                name = f"{profile}-{VERSION}{suffix}"
                self.assertEqual(_sha256(self.first / name), _sha256(self.second / name), name)

    def test_each_product_verifies_and_binds_its_own_workflows(self) -> None:
        stored = json.loads((RUNTIME / "runtime.json").read_text(encoding="utf-8"))
        bindings = set()
        for profile in PRODUCTS:
            with self.subTest(profile=profile):
                archive = self.first / f"{profile}-{VERSION}.zip"
                release = verify_release(archive, self.first / f"{profile}-{VERSION}.release.json")
                self.assertEqual(release.profile_id, profile)
                self.assertEqual(release.release_id, f"{profile}-{VERSION}")
                with zipfile.ZipFile(archive) as opened:
                    for name in RUNTIME_FILES - {"runtime.json"}:
                        self.assertEqual(opened.read(f"runtime/{name}"), (RUNTIME / name).read_bytes(), name)
                    descriptor = json.loads(opened.read("runtime/runtime.json"))
                view = _release_view(ROOT, profile)
                self.assertEqual(descriptor["workflowContentSha256"], _path_digest(sorted(view), view))
                bindings.add(descriptor["workflowContentSha256"])
                self.assertEqual(
                    {key: value for key, value in descriptor.items() if key != "workflowContentSha256"},
                    {key: value for key, value in stored.items() if key != "workflowContentSha256"},
                )
        self.assertEqual(len(bindings), len(PRODUCTS))

    def test_a_runtime_file_altered_without_its_descriptor_refuses_to_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = directory / "store"
            shutil.copytree(RUNTIME, store)
            with (store / "kernel.mjs").open("ab") as kernel:
                kernel.write(b"// altered\n")
            out = directory / "out"
            with mock.patch.object(self.command, "RUNTIME_SOURCE", store), mock.patch("sys.stderr"):
                self.assertEqual(self.command.main(["--version", VERSION, "--out", str(out)]), 1)
            self.assertFalse(out.exists())


class SetTests(unittest.TestCase):
    """The set's outputs and its failure path, around a stand-in for the qualifier subprocesses."""

    @classmethod
    def setUpClass(cls) -> None:
        holder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(holder.cleanup)
        cls.built = Path(holder.name)
        (cls.built / "runtime").mkdir()

    def setUp(self) -> None:
        self.command = _load_command()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.out = self.directory / "out"
        self._build_each_product_once()

    def _build_each_product_once(self) -> None:
        """The class's runs share each real build: these tests are about the set around them, and BuildTests and the real run about the builds."""
        real_runtime, real_build = self.command.rebound_runtime, self.command.build_release

        def runtime(profile: str, _target: Path) -> Path:
            stored = self.built / "runtime" / profile
            return stored if stored.exists() else real_runtime(profile, stored)

        def build(root: Path, archive: Path, metadata: Path, *, release_id: str, **options: object) -> None:
            stored = self.built / release_id
            if not stored.exists():
                stored.mkdir()
                real_build(root, stored / archive.name, stored / metadata.name, release_id=release_id, **options)
            shutil.copyfile(stored / archive.name, archive)
            shutil.copyfile(stored / metadata.name, metadata)

        for name, stand_in in (("rebound_runtime", runtime), ("build_release", build)):
            patcher = mock.patch.object(self.command, name, stand_in)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _cut(self, *, version: str = VERSION, fail: str | None = None, how: str = "exit") -> tuple[int, list]:
        run, calls = _qualifier_double(fail, how)
        with mock.patch.object(self.command.subprocess, "run", run), mock.patch("sys.stderr"), mock.patch("sys.stdout"):
            return self.command.main(["--version", version, "--out", str(self.out)]), calls

    def test_a_run_holds_exactly_the_named_outputs(self) -> None:
        code, _ = self._cut()
        self.assertEqual(code, 0)
        self.assertEqual({path.name for path in self.out.iterdir()}, _named_outputs(VERSION))
        self.assertEqual([path.name for path in self.directory.iterdir()], ["out"])

    def test_each_qualifier_gets_the_exact_archives(self) -> None:
        _, calls = self._cut()
        by_script = {Path(command[1]).name: command for command in calls}
        self.assertEqual(
            [Path(command[1]).name for command in calls],
            ["qualify-core.py", "qualify-provider.py", "qualify-bootstrap.py", "qualify-linear.py"],
        )

        def named(command: list[str], option: str) -> str:
            return Path(command[command.index(option) + 1]).name

        for script, product, record in (
            ("qualify-core.py", "core", "core"),
            ("qualify-provider.py", "core", "provider"),
            ("qualify-bootstrap.py", "bootstrap", "bootstrap"),
        ):
            self.assertEqual(named(by_script[script], "--archive"), f"{product}-{VERSION}.zip")
            self.assertEqual(named(by_script[script], "--metadata"), f"{product}-{VERSION}.release.json")
            self.assertEqual(named(by_script[script], "--record"), f"{record}-{VERSION}.json")
        linear = by_script["qualify-linear.py"]
        self.assertEqual(named(linear, "--core-archive"), f"core-{VERSION}.zip")
        self.assertEqual(named(linear, "--core-metadata"), f"core-{VERSION}.release.json")
        self.assertEqual(named(linear, "--core-record"), f"core-{VERSION}.json")
        self.assertEqual(named(linear, "--linear-archive"), f"linear-{VERSION}.zip")
        self.assertEqual(named(linear, "--linear-metadata"), f"linear-{VERSION}.release.json")
        self.assertEqual(named(linear, "--run-delivery-archive"), f"run-delivery-{VERSION}.zip")
        self.assertEqual(named(linear, "--run-delivery-metadata"), f"run-delivery-{VERSION}.release.json")
        self.assertEqual(named(linear, "--record"), f"linear-{VERSION}.json")
        self.assertNotIn("--attestation", linear)
        self.assertNotIn("--collector-binding", linear)

    def test_any_qualifier_failing_leaves_no_set_over_an_earlier_run(self) -> None:
        for fail in ("core", "provider", "bootstrap", "linear"):
            for how in ("exit", "stdout-only", "failed-record", "failed-record-exit", "no-record", "malformed", "extra-file"):
                with self.subTest(fail=fail, how=how):
                    earlier, _ = self._cut(version="v1.0.0")
                    self.assertEqual(earlier, 0)
                    code, _ = self._cut(fail=fail, how=how)
                    self.assertEqual(code, 1)
                    self.assertEqual(list(self.directory.iterdir()), [])

    def test_a_failing_qualifier_says_why(self) -> None:
        for how, reason in (
            ("exit", "qualifier says no"),
            ("stdout-only", "qualifier says no on stdout"),
            ("failed-record", "named-scenario: named reason"),
            ("failed-record-exit", "named-scenario: named reason"),
        ):
            with self.subTest(how=how):
                run, _ = _qualifier_double("provider", how)
                stderr = io.StringIO()
                with mock.patch.object(self.command.subprocess, "run", run), contextlib.redirect_stderr(stderr):
                    self.assertEqual(self.command.main(["--version", VERSION, "--out", str(self.out)]), 1)
                self.assertIn(reason, stderr.getvalue())

    def test_a_failed_build_leaves_no_set_over_an_earlier_run(self) -> None:
        self.assertEqual(self._cut()[0], 0)
        with mock.patch.object(self.command, "build_release", side_effect=OSError("disk full")):
            self.assertEqual(self._cut()[0], 1)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_a_refused_version_leaves_no_set_over_an_earlier_run(self) -> None:
        self.assertEqual(self._cut()[0], 0)
        self.assertEqual(self._cut(version="1.2.3")[0], 2)
        self.assertEqual(list(self.out.iterdir()) if self.out.exists() else [], [])

    def test_a_later_run_replaces_an_earlier_version(self) -> None:
        self.assertEqual(self._cut(version="v1.0.0")[0], 0)
        self.assertEqual(self._cut(version="v1.0.1")[0], 0)
        self.assertEqual({path.name for path in self.out.iterdir()}, _named_outputs("v1.0.1"))

    def test_a_failed_check_of_the_staged_set_leaves_no_set(self) -> None:
        self.assertEqual(self._cut()[0], 0)
        verify = self.command.verify_release
        staged = []

        def refuse_staged(archive: Path, metadata: Path):
            if archive.parent.name.startswith(".out."):
                staged.append(archive)
                raise ValueError("the staged copy is wrong")
            return verify(archive, metadata)

        with mock.patch.object(self.command, "verify_release", refuse_staged):
            self.assertEqual(self._cut()[0], 1)
        self.assertTrue(staged, "the staged set was never checked")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_a_failed_rename_leaves_no_set(self) -> None:
        self.assertEqual(self._cut()[0], 0)
        with mock.patch.object(self.command.os, "rename", side_effect=OSError("cannot rename")) as rename:
            self.assertEqual(self._cut()[0], 1)
        rename.assert_called_once()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_a_directory_holding_other_files_is_refused_and_they_are_kept(self) -> None:
        self.out.mkdir()
        other = self.out / "notes.txt"
        other.write_text("mine\n", encoding="utf-8")
        earlier = self.out / f"core-{VERSION}.zip"
        earlier.write_bytes(b"earlier")
        run, calls = _qualifier_double()
        with mock.patch.object(self.command.subprocess, "run", run), mock.patch("sys.stderr"):
            self.assertEqual(self.command.main(["--version", VERSION, "--out", str(self.out)]), 2)
        self.assertEqual(calls, [])
        self.assertEqual(other.read_text(encoding="utf-8"), "mine\n")
        self.assertFalse(earlier.exists())

    def test_an_output_inside_the_checkout_must_be_ignored(self) -> None:
        for out in (ROOT / "cut-release-output-is-not-ignored", ROOT / "skills", ROOT):
            with self.subTest(out=out.name), mock.patch("sys.stderr"):
                before = out.exists()
                self.assertEqual(self.command.main(["--version", VERSION, "--out", str(out)]), 2)
                self.assertEqual(out.exists(), before)


class RealRunTests(unittest.TestCase):
    """One real run: the five products and every real qualifier, as the release workflow calls it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.out = Path(cls.temporary.name) / "out"
        cls.runtime_before = _runtime_digests()
        cls.status_before = _git_status()
        cls.result = subprocess.run(
            [sys.executable, str(SCRIPT), "--version", VERSION, "--out", str(cls.out)],
            cwd=cls.temporary.name,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "GITHUB_EVENT_NAME": "push"},
        )

    def test_the_run_succeeds_with_exactly_the_named_outputs(self) -> None:
        self.assertEqual(self.result.returncode, 0, self.result.stdout + self.result.stderr)
        self.assertEqual({path.name for path in self.out.iterdir()}, _named_outputs(VERSION))
        self.assertEqual(self.result.stdout, "")

    def test_every_product_verifies_and_every_record_passes(self) -> None:
        self.assertEqual(self.result.returncode, 0, self.result.stdout + self.result.stderr)
        for profile in PRODUCTS:
            verified = verify_release(self.out / f"{profile}-{VERSION}.zip", self.out / f"{profile}-{VERSION}.release.json")
            self.assertEqual(verified.release_id, f"{profile}-{VERSION}")
        for record in RECORDS:
            document = json.loads((self.out / f"{record}-{VERSION}.json").read_text(encoding="utf-8"))
            self.assertEqual(document["summary"]["result"], "passed", record)

    def test_the_run_writes_nothing_to_the_repository(self) -> None:
        self.assertEqual(_git_status(), self.status_before)
        self.assertEqual(_runtime_digests(), self.runtime_before)
        self.assertEqual(list((ROOT / "skills").rglob("__pycache__")), [])


def _git_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout


if __name__ == "__main__":
    unittest.main()
