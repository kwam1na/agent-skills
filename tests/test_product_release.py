"""Product generation closure and lifecycle remain one release transaction."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from agent_skills.lifecycle import Lifecycle
from agent_skills.release import RUNTIME_FILES, ReleaseError, _path_digest, _release_view, build_release, verify_release
from lifecycle_support import initialize_repository

ROOT = Path(__file__).resolve().parents[1]


class ProductReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()
        for name in RUNTIME_FILES - {"runtime.json"}:
            (self.runtime / name).write_text("// bounded fixture\n")
        self.descriptor = {
            "schemaVersion": "delivery-runtime/1", "runtimeVersion": "fixture-1", "nodeMinimum": "22.6.0",
            "workflowContentSha256": _path_digest(sorted(_release_view(ROOT, "core")), _release_view(ROOT, "core")),
            "files": [{"path": name, "sha256": hashlib.sha256((self.runtime / name).read_bytes()).hexdigest()} for name in sorted(RUNTIME_FILES - {"runtime.json"})],
        }
        self.save_descriptor()

    def save_descriptor(self) -> None:
        (self.runtime / "runtime.json").write_text(json.dumps(self.descriptor))

    def build(self, number: int) -> tuple[Path, Path]:
        archive, metadata = self.base / f"product-{number}.zip", self.base / f"product-{number}.json"
        build_release(ROOT, archive, metadata, release_id=f"fixture-{number}", profile_id="core", runtime_directory=self.runtime)
        return archive, metadata

    def test_installed_launcher_forwards_help_to_native_command_inventory(self) -> None:
        (self.runtime / "cli.mjs").write_text('if (!process.argv.includes("--help")) process.exit(7); console.log("prepare review-context gate record resume");')
        self.descriptor["files"] = [{"path": name, "sha256": hashlib.sha256((self.runtime / name).read_bytes()).hexdigest()} for name in sorted(RUNTIME_FILES - {"runtime.json"})]
        self.save_descriptor()
        archive, metadata = self.build(1)
        consumer = initialize_repository(self.base)
        Lifecycle(consumer).install(archive, metadata, maintainer=True)
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                result = subprocess.run([sys.executable, "-B", str(consumer / ".agent-skills/current"), "--root", str(consumer), "harness", flag], text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("prepare review-context gate record resume", result.stdout)

    def test_runtime_requires_exact_workflow_and_own_bytes(self) -> None:
        self.descriptor["workflowContentSha256"] = "0" * 64
        self.save_descriptor()
        with self.assertRaisesRegex(ReleaseError, "product.workflow_incompatible"):
            self.build(1)
        self.descriptor["workflowContentSha256"] = _path_digest(sorted(_release_view(ROOT, "core")), _release_view(ROOT, "core"))
        self.save_descriptor()
        (self.runtime / "cli.mjs").write_text("changed")
        with self.assertRaisesRegex(ReleaseError, "product.runtime_checksum"):
            self.build(1)

    def test_zip_is_consumer_entry_and_tamper_blocks_without_switch(self) -> None:
        archive, metadata = self.build(1)
        consumer = initialize_repository(self.base)
        env = {**os.environ, "PYTHONPATH": "", "PYTHONDONTWRITEBYTECODE": "1", "AGENT_SKILLS_EXECUTION_CONTEXT": "", "GITHUB_EVENT_NAME": ""}
        result = subprocess.run([sys.executable, "-B", str(archive), "--root", str(consumer), "--product", "install", "--archive", str(archive), "--metadata", str(metadata), "--maintenance"], cwd=self.base, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["status"]["productReady"])
        lifecycle = Lifecycle(consumer)
        prior = lifecycle.status().as_dict()["active"]
        damaged, damaged_metadata = self.build(2)
        damaged.write_bytes(damaged.read_bytes() + b"damage")
        with self.assertRaisesRegex(ReleaseError, "release.archive_checksum"):
            lifecycle.update(damaged, damaged_metadata, maintainer=True)
        self.assertEqual(lifecycle.status().as_dict()["active"], prior)
        (consumer / ".agent-skills/current/runtime/cli.mjs").write_text("tampered")
        self.assertIn("local-divergence", lifecycle.status().as_dict()["blockers"])

    def test_product_apply_selects_install_update_and_no_op_without_checkout_state(self) -> None:
        first, second = self.build(1), self.build(2)
        consumer = initialize_repository(self.base)
        environment = {
            **os.environ,
            "AGENT_SKILLS_CHECKOUT": "",
            "AGENT_SKILLS_EXECUTION_CONTEXT": "",
            "GITHUB_EVENT_NAME": "",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "",
        }

        def apply(artifacts: tuple[Path, Path]) -> dict[str, object]:
            archive, metadata = artifacts
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(archive),
                    "--root",
                    str(consumer),
                    "--product",
                    "apply",
                    "--archive",
                    str(archive),
                    "--metadata",
                    str(metadata),
                    "--maintenance",
                ],
                cwd=self.base,
                env=environment,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)

        self.assertEqual(apply(first)["action"], "install")
        self.assertEqual(apply(second)["action"], "update")
        self.assertEqual(apply(second)["action"], "no-op")
        self.assertEqual(Lifecycle(consumer).status().as_dict()["active"]["releaseId"], "fixture-2")

    def test_product_apply_forwards_explicit_first_policy_bootstrap(self) -> None:
        (self.runtime / "policy.mjs").write_text(
            """import {existsSync,writeFileSync} from 'node:fs';
const snapshot='.agents/policy/compiled-snapshot.json';
if(process.argv.includes('--bootstrap')) {
  if(existsSync(snapshot)) process.exit(7);
  writeFileSync(snapshot, JSON.stringify({bootstrapped:true})+'\\n', {flag:'wx'});
}
process.exit(existsSync(snapshot) ? 0 : 8);
"""
        )
        self.descriptor["files"] = [
            {"path": name, "sha256": hashlib.sha256((self.runtime / name).read_bytes()).hexdigest()}
            for name in sorted(RUNTIME_FILES - {"runtime.json"})
        ]
        self.save_descriptor()
        archive, metadata = self.build(1)
        consumer = initialize_repository(self.base)
        policy = consumer / ".agents/policy"
        policy.mkdir(parents=True)
        (policy / "repository-policy.json").write_text("{}\n")
        (policy / "adapters.json").write_text("[]\n")
        (policy / "bootstrap-inputs.json").write_text(
            '{"productTrustRevocationEpoch":0,"repositoryAuthorityRevocationEpoch":0}\n'
        )
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(archive),
                "--root",
                str(consumer),
                "--product",
                "apply",
                "--archive",
                str(archive),
                "--metadata",
                str(metadata),
                "--bootstrap-policy",
                "--maintenance",
            ],
            cwd=self.base,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((policy / "compiled-snapshot.json").read_text()), {"bootstrapped": True})
        self.assertTrue(json.loads(result.stdout)["status"]["productReady"])

    def test_real_product_entrypoints_refuse_bootstrap_without_explicit_policy_inputs(self) -> None:
        checked = ROOT / "qualifications" / "product"
        extracted = self.base / "checked-linear-product"
        verify_release(
            checked / "linear-adoption-v1.zip",
            checked / "linear-adoption-v1.release.json",
            extract_to=extracted,
        )
        descriptor_path = extracted / "runtime/runtime.json"
        descriptor = json.loads(descriptor_path.read_text())
        release_view = _release_view(ROOT, "linear")
        descriptor["workflowContentSha256"] = _path_digest(
            sorted(release_view), release_view
        )
        descriptor_path.write_text(json.dumps(descriptor))
        artifacts = {}
        for release_id in ("prior-linear-product", "current-linear-product"):
            archive = self.base / f"{release_id}.zip"
            metadata = self.base / f"{release_id}.release.json"
            build_release(
                ROOT,
                archive,
                metadata,
                release_id=release_id,
                profile_id="linear",
                runtime_directory=extracted / "runtime",
            )
            artifacts[release_id] = (archive, metadata)

        def install(
            consumer: Path,
            command: str,
            release_id: str,
            *,
            bootstrap: bool,
        ) -> subprocess.CompletedProcess[str]:
            archive, metadata = artifacts[release_id]
            return subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(archive),
                    "--root",
                    str(consumer),
                    "--product",
                    command,
                    "--archive",
                    str(archive),
                    "--metadata",
                    str(metadata),
                    *(["--bootstrap-policy"] if bootstrap else []),
                    "--maintenance",
                ],
                cwd=self.base,
                text=True,
                capture_output=True,
            )

        for command in ("apply", "install", "update"):
            with self.subTest(command=command):
                ordinary = initialize_repository(self.base / f"ordinary-{command}")
                missing_inputs = initialize_repository(self.base / f"bootstrap-{command}")
                if command == "update":
                    for consumer in (ordinary, missing_inputs):
                        prior = install(
                            consumer,
                            "install",
                            "prior-linear-product",
                            bootstrap=False,
                        )
                        self.assertEqual(prior.returncode, 0, prior.stdout + prior.stderr)

                installed = install(
                    ordinary,
                    command,
                    "current-linear-product",
                    bootstrap=False,
                )
                self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
                self.assertTrue(json.loads(installed.stdout)["status"]["productReady"])

                refused = install(
                    missing_inputs,
                    command,
                    "current-linear-product",
                    bootstrap=True,
                )
                self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
                self.assertEqual(
                    json.loads(refused.stdout)["error"]["code"],
                    "product.policy_unready",
                )
                self.assertFalse(
                    (missing_inputs / ".agents/policy/compiled-snapshot.json").exists()
                )
                self.assertEqual(
                    Lifecycle(missing_inputs).status().as_dict()["lifecycle"],
                    "current",
                )

    def test_policy_bootstrap_flag_requires_product_mode(self) -> None:
        archive, metadata = self.build(1)
        consumer = initialize_repository(self.base)
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(archive),
                "--root",
                str(consumer),
                "apply",
                "--archive",
                str(archive),
                "--metadata",
                str(metadata),
                "--bootstrap-policy",
                "--maintenance",
            ],
            cwd=self.base,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "product.bootstrap_requires_product")

    def test_policy_failure_retains_generation_but_blocks_readiness_and_runtime_until_recovery(self) -> None:
        policy = self.runtime / "policy.mjs"
        policy.write_text('import {existsSync} from "node:fs"; process.exit(existsSync(".agents/policy/allow") ? 0 : 1);')
        self.descriptor["files"] = [{"path": name, "sha256": hashlib.sha256((self.runtime / name).read_bytes()).hexdigest()} for name in sorted(RUNTIME_FILES - {"runtime.json"})]
        self.save_descriptor()
        archive, metadata = self.build(1)
        consumer = initialize_repository(self.base)
        (consumer / ".agents/policy").mkdir(parents=True)
        def command(*arguments: str) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, "-B", str(archive), "--root", str(consumer), "--product", *arguments], cwd=self.base, text=True, capture_output=True)
        installed = command("install", "--archive", str(archive), "--metadata", str(metadata), "--maintenance")
        self.assertNotEqual(installed.returncode, 0)
        self.assertIn("product.policy_unready", installed.stdout)
        self.assertEqual(Lifecycle(consumer).status().as_dict()["lifecycle"], "current")
        self.assertFalse(json.loads(command("status").stdout)["productReady"])
        self.assertIn("product.policy_unready", command("harness", "gate").stdout)
        (consumer / ".agents/policy/allow").touch()
        recovered = command("recover", "--maintenance")
        self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
        self.assertTrue(json.loads(command("status").stdout)["productReady"])

    def test_product_rollback_recompiles_with_retained_runtime_after_archives_removed(self) -> None:
        artifacts = []
        for number in (1, 2):
            (self.runtime / "policy.mjs").write_text('import {readFileSync,writeFileSync} from "node:fs"; const p=".agents/policy/value"; const expected="' + str(number) + '"; if(process.argv.includes("--check")){process.exit(readFileSync(p,"utf8")===expected?0:1)}else{writeFileSync(p,expected)}')
            self.descriptor["files"] = [{"path": name, "sha256": hashlib.sha256((self.runtime / name).read_bytes()).hexdigest()} for name in sorted(RUNTIME_FILES - {"runtime.json"})]
            self.save_descriptor()
            artifacts.append(self.build(number))
        consumer = initialize_repository(self.base)
        (consumer / ".agents/policy").mkdir(parents=True)
        for operation, (archive, metadata) in zip(("install", "update"), artifacts):
            result = subprocess.run([sys.executable, "-B", str(archive), "--root", str(consumer), "--product", operation, "--archive", str(archive), "--metadata", str(metadata), "--maintenance"], cwd=self.base, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((consumer / ".agents/policy/value").read_text(), "2")
        for archive, metadata in artifacts:
            archive.unlink()
            metadata.unlink()
        result = subprocess.run([sys.executable, "-B", str(consumer / ".agent-skills/current"), "--root", str(consumer), "--product", "rollback", "--maintenance"], cwd=self.base, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((consumer / ".agents/policy/value").read_text(), "1")
        self.assertTrue(json.loads(result.stdout)["status"]["productReady"])

    def test_runtime_holds_existing_shared_lock_against_lifecycle_switch(self) -> None:
        from agent_skills.product import run_runtime
        from agent_skills.locking import LockError
        first, second = self.build(1), self.build(2)
        consumer = initialize_repository(self.base)
        lifecycle = Lifecycle(consumer)
        lifecycle.install(*first, maintainer=True)
        def run(*args, **kwargs):
            with self.assertRaisesRegex(LockError, "lock-busy"):
                lifecycle.update(*second, maintainer=True)
            return 0
        with patch("agent_skills.product._run_command", side_effect=run):
            self.assertEqual(run_runtime(lifecycle, ["gate"]), 0)

    def test_interrupted_update_recovers_one_generation_and_retained_rollback(self) -> None:
        first, second = self.build(1), self.build(2)
        consumer = initialize_repository(self.base)
        lifecycle = Lifecycle(consumer)
        lifecycle.install(*first, maintainer=True)
        original = lifecycle.status().as_dict()["active"]["generation"]
        def interrupt(phase: str) -> None:
            if phase == "marker-committed":
                raise RuntimeError("interrupted")
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            lifecycle.update(*second, maintainer=True, fault=interrupt)
        lifecycle.recover(maintainer=True)
        for artifact in (*first, *second):
            artifact.unlink()
        lifecycle.rollback(maintainer=True)
        self.assertEqual(lifecycle.status().as_dict()["active"]["generation"], original)
        for host in (".agents", ".claude"):
            self.assertIn(original, str((consumer / host / "skills/execute-work").resolve()))
        self.assertIn(original, str((consumer / ".agent-skills/current/runtime/cli.mjs").resolve()))


if __name__ == "__main__":
    unittest.main()
