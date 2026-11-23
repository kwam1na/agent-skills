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
from tests.quiet_git import quieten


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cut-release.py"
RUNTIME = ROOT / "product-runtime"
VERSION = "v1.2.3-rc.1"
PRODUCTS = ("core", "linear", "bootstrap", "run-delivery", "jira")
RECORDS = ("core", "provider", "bootstrap", "linear")
BUNDLE = f"hosted-delivery-service-{VERSION}.zip"
BUNDLE_DATE_TIME = (1980, 1, 1, 0, 0, 0)
BUNDLE_EXTERNAL_ATTR = 0o100644 << 16
WORKFLOW = ".github/workflows/hosted-delivery-viewer.yml"
# The seed's digests, frozen until a reviewed change replaces the seed.
FROZEN_SEED_SHA256 = {
    "LICENSE": "02ed546806a3298b12c633742eb2fd354821d5dc9a5ee4384d4ae8196737a83f",
    "NOTICE": "547c61e2d762d0cadff8c1560ac7b9c072d4d71315797e11629654079ce0882d",
    "bootstrap.mjs": "5df698a9e5d7bae942ec5a40819ba52b0688cab87c653ee8b42e30160ad1225a",
    "cli-api.d.mts": "b5c75d1965ab923f970c0680f2fb66d35039b09d6f420366576b2d4db1cf9e50",
    "cli-api.mjs": "f9f2919ca2b5e8bc4784fec2b327e429665c3519d55c7c73fbf3f50930b548d2",
    "cli.mjs": "940d41d950e5472ef11bb9afe3055d239660f2a2abe898a073b243b61c0c58af",
    "kernel.d.mts": "2d6ca8b1f1df5d661c59c94f95ee3b29c4b5c4f2ea4e6515549d35c8310e8fba",
    "kernel.mjs": "afd166c9bd64fd74efd644c92a59f426299e16ca32d0078410e0e221ef5e0a88",
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
        f"hosted-delivery-service-{version}.zip",
        f"hosted-delivery-service-{version}.zip.sha256",
    }


def _service_files(root: Path, roots: tuple[str, ...]) -> list[str]:
    """What the bundle should hold, worked out apart from the command: tracked, less what the tree's .gitignore ignores."""
    def listed(*options: str) -> list[str]:
        done = subprocess.run(["git", "ls-files", "-z", *options, "--", *roots], cwd=root, capture_output=True, check=True)
        return [name.decode("utf-8") for name in done.stdout.split(b"\0") if name]

    ignored = set(listed("--cached", "--ignored", "--exclude-per-directory=.gitignore"))
    return sorted(name for name in listed("--cached") if name not in ignored)


def _qualifier_double(fail: str | None = None, how: str = "exit"):
    """Stands in for the qualifier subprocesses, which the real run below exercises."""
    calls = []
    real_run = subprocess.run

    def run(command, **kwargs):
        if command[0] == "git":
            return real_run(command, **kwargs)
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

        self.real_bundle = self.command.build_service_bundle

        def bundle(root: Path, archive: Path) -> str:
            stored = self.built / "bundle.zip"
            if not stored.exists():
                self.real_bundle(root, stored)
            shutil.copyfile(stored, archive)
            return hashlib.sha256(archive.read_bytes()).hexdigest()

        for name, stand_in in (("rebound_runtime", runtime), ("build_release", build), ("build_service_bundle", bundle)):
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
        # Both bundle files are the later run's: the earlier pair is gone, and the digest names the later name.
        self.assertEqual(
            (self.out / "hosted-delivery-service-v1.0.1.zip.sha256").read_text(encoding="utf-8"),
            f"{_sha256(self.out / 'hosted-delivery-service-v1.0.1.zip')}  hosted-delivery-service-v1.0.1.zip\n",
        )

    def test_a_missing_service_root_refuses_the_cut_by_name(self) -> None:
        self.assertEqual(self._cut()[0], 0)
        for missing in ("hosted/no-such-root/", "hosted/protocol/no-such-file.md"):
            with self.subTest(missing=missing):
                stderr = io.StringIO()
                run, _ = _qualifier_double()
                with mock.patch.object(self.command, "build_service_bundle", self.real_bundle), \
                        mock.patch.object(self.command, "SERVICE_ROOTS", (*self.command.SERVICE_ROOTS, missing)), \
                        mock.patch.object(self.command.subprocess, "run", run), contextlib.redirect_stderr(stderr):
                    self.assertEqual(self.command.main(["--version", VERSION, "--out", str(self.out)]), 1)
                self.assertIn(f"cut_release.service_root_missing: {missing} holds no tracked file", stderr.getvalue())
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_a_bundle_changed_after_its_digest_was_written_refuses_the_cut_by_name(self) -> None:
        write = self.command.write_service_bundle
        copy = shutil.copyfile

        def corrupt_after_writing(root: Path, version: str, directory: Path) -> str:
            digest = write(root, version, directory)
            with (directory / f"hosted-delivery-service-{version}.zip").open("ab") as bundle:
                bundle.write(b"\0")
            return digest

        def corrupt_the_staged_copy(source, target, **options):
            copied = copy(source, target, **options)
            if Path(target).parent.name.startswith(".out.") and Path(target).name.endswith(".zip") \
                    and Path(target).name.startswith("hosted-delivery-service-"):
                Path(target).write_bytes(Path(target).read_bytes()[:-1] + b"\1")
            return copied

        for where, patcher in (
            ("after the write", mock.patch.object(self.command, "write_service_bundle", corrupt_after_writing)),
            ("in the staged set", mock.patch.object(self.command.shutil, "copyfile", corrupt_the_staged_copy)),
        ):
            with self.subTest(where=where):
                self.assertEqual(self._cut(version="v1.0.0")[0], 0)
                stderr = io.StringIO()
                run, _ = _qualifier_double()
                with patcher, mock.patch.object(self.command.subprocess, "run", run), contextlib.redirect_stderr(stderr):
                    self.assertEqual(self.command.main(["--version", VERSION, "--out", str(self.out)]), 1)
                self.assertIn(f"cut_release.service_bundle_digest: {BUNDLE} changed after its digest was written",
                              stderr.getvalue())
                self.assertEqual(list(self.directory.iterdir()), [])

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


class ServiceBundleCommandTests(unittest.TestCase):
    """`--service-bundle` builds only the bundle from a checkout, as a service check calls it; no release is cut."""

    def setUp(self) -> None:
        self.command = _load_command()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.out = Path(temporary.name) / "bundle"
        self.names = {BUNDLE, f"{BUNDLE}.sha256"}

    def _bundle(self, *extra: str) -> tuple[int, str]:
        stderr = io.StringIO()
        with mock.patch.object(self.command, "build_set") as build, contextlib.redirect_stderr(stderr):
            code = self.command.main(["--version", VERSION, "--out", str(self.out), "--service-bundle", *extra])
        build.assert_not_called()
        return code, stderr.getvalue()

    def test_the_command_writes_only_the_checked_bundle_of_the_named_checkout(self) -> None:
        done = subprocess.run([sys.executable, "-B", str(SCRIPT), "--service-bundle", "--root", str(ROOT), "--version", VERSION,
                               "--out", str(self.out)], capture_output=True, text=True, check=False)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        self.assertEqual({path.name for path in self.out.iterdir()}, self.names)
        self.assertEqual(self.command.check_service_bundle(VERSION, self.out), _sha256(self.out / BUNDLE))
        with zipfile.ZipFile(self.out / BUNDLE) as bundle:
            self.assertEqual(bundle.namelist(), _service_files(ROOT, self.command.SERVICE_ROOTS))

    def test_refusals_write_nothing_and_keep_what_was_there(self) -> None:
        for case in ("inside the checkout", "already there", "bad version", "root without the flag", "root not a folder"):
            with self.subTest(case=case):
                if self.out.exists():
                    shutil.rmtree(self.out)
                if case == "inside the checkout":
                    inside = ROOT / "hosted" / "delivery-viewer" / "node_modules" / ".service-bundle-refused"
                    stderr = io.StringIO()
                    with contextlib.redirect_stderr(stderr):
                        code = self.command.main(["--version", VERSION, "--out", str(inside), "--service-bundle"])
                    self.assertEqual(code, 2)
                    self.assertIn("outside the checkout", stderr.getvalue())
                    self.assertFalse(inside.exists())
                    continue
                if case == "already there":
                    self.out.mkdir()
                    (self.out / BUNDLE).write_bytes(b"earlier")
                    self.assertEqual(self._bundle()[0], 2)
                    self.assertEqual({path.name for path in self.out.iterdir()}, {BUNDLE})
                    self.assertEqual((self.out / BUNDLE).read_bytes(), b"earlier")
                elif case == "bad version":
                    with mock.patch("sys.stderr"):
                        self.assertEqual(self.command.main(["--version", "1.2.3", "--out", str(self.out), "--service-bundle"]), 2)
                    self.assertFalse(self.out.exists())
                elif case == "root without the flag":
                    with mock.patch("sys.stderr"), self.assertRaises(SystemExit) as caught:
                        self.command.main(["--version", VERSION, "--out", str(self.out), "--root", str(ROOT)])
                    self.assertEqual(caught.exception.code, 2)
                    self.assertFalse(self.out.exists())
                else:
                    self.assertEqual(self._bundle("--root", str(self.out.parent / "no-such-checkout"))[0], 2)
                    self.assertFalse(self.out.exists())

    def test_a_failed_build_or_recheck_leaves_neither_file(self) -> None:
        write = self.command.write_service_bundle

        def corrupt(root: Path, version: str, directory: Path) -> str:
            digest = write(root, version, directory)
            with (directory / f"hosted-delivery-service-{version}.zip").open("ab") as bundle:
                bundle.write(b"\0")
            return digest

        for case, patcher, phrase in (
            ("missing root", mock.patch.object(self.command, "SERVICE_ROOTS", (*self.command.SERVICE_ROOTS, "hosted/no-such-root/")),
             "cut_release.service_root_missing: hosted/no-such-root/"),
            ("corrupted", mock.patch.object(self.command, "write_service_bundle", corrupt), "cut_release.service_bundle_digest"),
        ):
            with self.subTest(case=case):
                with patcher:
                    code, stderr = self._bundle()
                self.assertEqual(code, 1)
                self.assertIn(phrase, stderr)
                self.assertEqual(list(self.out.iterdir()), [])


class RealRunTests(unittest.TestCase):
    """One real run: the five products and every real qualifier, as the publish command calls it."""

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

    def test_the_bundle_digest_is_its_written_bytes(self) -> None:
        self.assertEqual(self.result.returncode, 0, self.result.stdout + self.result.stderr)
        self.assertEqual((self.out / f"{BUNDLE}.sha256").read_text(encoding="utf-8"), f"{_sha256(self.out / BUNDLE)}  {BUNDLE}\n")

    def test_the_bundle_holds_the_tracked_service_files_with_fixed_entries(self) -> None:
        self.assertEqual(self.result.returncode, 0, self.result.stdout + self.result.stderr)
        roots = _load_command().SERVICE_ROOTS
        with zipfile.ZipFile(self.out / BUNDLE) as bundle:
            entries = bundle.infolist()
            names = [entry.filename for entry in entries]
            self.assertEqual(names, sorted(names))
            self.assertEqual(names, _service_files(ROOT, roots))
            for entry in entries:
                with self.subTest(entry=entry.filename):
                    self.assertEqual(entry.date_time, BUNDLE_DATE_TIME)
                    self.assertEqual(entry.external_attr, BUNDLE_EXTERNAL_ATTR)
                    self.assertEqual(bundle.read(entry), (ROOT / entry.filename).read_bytes())
        # The spec, and the conformance package's entry points, which its package.json names.
        package = json.loads((ROOT / "hosted/conformance/package.json").read_text(encoding="utf-8"))
        for required in ("hosted/protocol/hosted-delivery-api-1.md",
                         *(f"hosted/conformance/{Path(entry).name}" for entry in (*package["bin"].values(), package["exports"]["."]))):
            self.assertIn(required, names)
        for root in roots:
            self.assertTrue(any(name == root or (root.endswith("/") and name.startswith(root)) for name in names), root)

    def test_the_bundle_roots_are_the_service_workflow_triggers_less_the_workflow(self) -> None:
        workflow = json.loads((ROOT / WORKFLOW).read_text(encoding="utf-8"))
        roots = list(_load_command().SERVICE_ROOTS)
        for event in ("pull_request", "push"):
            with self.subTest(event=event):
                triggers = [path.removesuffix("**") for path in workflow["on"][event]["paths"] if path != WORKFLOW]
                self.assertEqual(triggers, roots)

    def test_a_copy_with_other_mtimes_a_mode_bit_and_stray_files_gives_identical_bundle_bytes(self) -> None:
        self.assertEqual(self.result.returncode, 0, self.result.stdout + self.result.stderr)
        command = _load_command()
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary) / "tree"
            for name in (".gitignore", *_service_files(ROOT, command.SERVICE_ROOTS)):
                (copy / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, copy / name)
            subprocess.run(["git", "init", "-q", str(copy)], check=True, capture_output=True)
            quieten(copy)
            subprocess.run(["git", "add", "-A"], cwd=copy, check=True, capture_output=True)
            # What a checkout picks up that the bundle must not: an untracked file, bytecode, and a file the
            # tree's .gitignore ignores even though someone forced it into the index.
            strays = {
                "hosted/protocol/untracked.md": b"untracked\n",
                "skills/delivery-views/scripts/__pycache__/render.cpython-311.pyc": b"bytecode",
                "hosted/delivery-viewer/node_modules/forced.js": b"ignored\n",
            }
            for name, contents in strays.items():
                (copy / name).parent.mkdir(parents=True, exist_ok=True)
                (copy / name).write_bytes(contents)
            subprocess.run(["git", "add", "-f", "hosted/delivery-viewer/node_modules/forced.js"], cwd=copy, check=True,
                           capture_output=True)
            changed = 1_000_000_000
            for path in copy.rglob("*"):
                if ".git" not in path.relative_to(copy).parts:
                    os.utime(path, (changed, changed))
            spec = copy / "hosted/protocol/hosted-delivery-api-1.md"
            spec.chmod(spec.stat().st_mode ^ 0o100)
            again = Path(temporary) / BUNDLE
            digest = command.build_service_bundle(copy, again)
            with zipfile.ZipFile(again) as bundle:
                self.assertEqual(set(bundle.namelist()) & set(strays), set())
            self.assertEqual(again.read_bytes(), (self.out / BUNDLE).read_bytes())
            self.assertEqual(digest, _sha256(self.out / BUNDLE))


def _git_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout


if __name__ == "__main__":
    unittest.main()
