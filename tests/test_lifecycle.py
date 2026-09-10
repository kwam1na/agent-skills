from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from agent_skills.cli import main as lifecycle_cli
from agent_skills.generations import inspect_release
from agent_skills.hosts.exposure import HOSTS, _normalized_link
from agent_skills.lifecycle import Lifecycle
from agent_skills.locking import RepositoryLock
from agent_skills.release import verify_release
from agent_skills.validate import canonical_json

from lifecycle_support import (
    LifecycleFixture,
    build_version,
    forge_provenance_failure,
    run_git,
    unrelated_digest,
)


class LifecycleTests(LifecycleFixture, unittest.TestCase):
    def _repository_directories(self) -> set[str]:
        return {
            path.relative_to(self.repository).as_posix()
            for path in self.repository.rglob("*")
            if path.is_dir() and self.repository / ".git" not in path.parents
        }

    def _make_active_receipt_legacy(self, lifecycle: Lifecycle) -> None:
        marker = self.repository / ".agent-skills" / "active.json"
        receipt = json.loads(marker.read_text(encoding="utf-8"))
        receipt.pop("exposures")
        receipt.pop("createdDirectories")
        marker.write_text(canonical_json(receipt), encoding="utf-8")
        current = self.repository / ".agent-skills" / "current"
        current.unlink(missing_ok=True)
        for host in HOSTS:
            root = self.repository / host.root
            for skill in ("compound-delivery-kernel", "deliver-work"):
                (root / skill).unlink(missing_ok=True)

    def test_absent_and_current_status_are_stable_json(self) -> None:
        lifecycle = Lifecycle(self.repository)
        absent = lifecycle.status()
        self.assertEqual(absent.to_json(), lifecycle.status().to_json())
        self.assertEqual(
            absent.as_dict(),
            {
                "active": None,
                "blockers": [],
                "exposures": [],
                "journalPhase": None,
                "lifecycle": "absent",
                "recoveryPlan": [],
                "schemaVersion": "agent-skills-status/1",
            },
        )

        result = lifecycle.install(*self.v1, maintainer=True)
        self.assertEqual(result["action"], "install")
        current = lifecycle.status().as_dict()
        self.assertEqual(current["lifecycle"], "current")
        self.assertEqual(current["active"]["releaseId"], "core-v1")
        self.assertEqual(current["blockers"], [])

    def test_legacy_receipt_status_is_readable_and_update_enriches_it(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        self._make_active_receipt_legacy(lifecycle)

        status = lifecycle.status().as_dict()
        self.assertEqual(status["lifecycle"], "current")
        self.assertEqual(status["exposures"], [])
        lifecycle.update(*self.v2, maintainer=True)
        self.assertEqual(lifecycle.status().as_dict()["active"]["releaseId"], "core-v2")
        self.assertEqual(len(lifecycle.status().as_dict()["exposures"]), 2)

    def test_legacy_receipt_can_rollback_to_retained_verified_bytes(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        lifecycle.update(*self.v2, maintainer=True)
        self._make_active_receipt_legacy(lifecycle)

        lifecycle.rollback(maintainer=True)
        status = lifecycle.status().as_dict()
        self.assertEqual(status["active"]["releaseId"], "core-v1")
        self.assertEqual(len(status["exposures"]), 2)

    def test_legacy_receipt_can_be_removed_without_host_artifacts(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        self._make_active_receipt_legacy(lifecycle)

        lifecycle.remove(maintainer=True)
        self.assertEqual(lifecycle.status().as_dict()["lifecycle"], "absent")

    def test_legacy_receipt_can_recover_an_interrupted_update(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        self._make_active_receipt_legacy(lifecycle)

        def interrupt(phase: str) -> None:
            if phase == "staged":
                raise RuntimeError("stop after legacy journal")

        with self.assertRaisesRegex(RuntimeError, "legacy journal"):
            lifecycle.update(*self.v2, maintainer=True, fault=interrupt)
        self.assertEqual(lifecycle.recover(maintainer=True)["action"], "restore-prior")
        status = lifecycle.status().as_dict()
        self.assertEqual(status["lifecycle"], "current")
        self.assertEqual(status["exposures"], [])

    def test_install_update_no_op_rollback_remove_preserve_dirty_repository(self) -> None:
        lifecycle = Lifecycle(self.repository)
        dirty_tracked = self.repository / "unrelated.txt"
        dirty_untracked = self.repository / "scratch.txt"
        dirty_tracked.write_text("dirty tracked bytes\n", encoding="utf-8")
        dirty_untracked.write_text("dirty untracked bytes\n", encoding="utf-8")
        before = unrelated_digest(self.repository)

        lifecycle.install(*self.v1, maintainer=True)
        active = json.loads(
            (self.repository / ".agent-skills" / "active.json").read_text(encoding="utf-8")
        )
        installed_body = (
            self.repository
            / ".agent-skills"
            / "generations"
            / active["generation"]
            / "skills"
            / "compound-delivery-kernel"
            / "SKILL.md"
        ).read_bytes()
        self.assertEqual(lifecycle.install(*self.v1, maintainer=True)["action"], "no-op")

        def interrupt(phase: str) -> None:
            if phase == "generation-ready":
                raise RuntimeError("interrupted update")

        with self.assertRaisesRegex(RuntimeError, "interrupted update"):
            lifecycle.update(*self.v2, maintainer=True, fault=interrupt)
        self.assertEqual(unrelated_digest(self.repository), before)
        self.assertEqual(lifecycle.recover(maintainer=True)["action"], "restore-prior")
        self.assertEqual(unrelated_digest(self.repository), before)

        lifecycle.update(*self.v2, maintainer=True)
        self.assertEqual(lifecycle.status().as_dict()["active"]["releaseId"], "core-v2")
        self.v1[0].unlink()
        self.v1[1].unlink()
        lifecycle.rollback(maintainer=True)
        self.assertEqual(lifecycle.status().as_dict()["active"]["releaseId"], "core-v1")
        rolled_back = json.loads(
            (self.repository / ".agent-skills" / "active.json").read_text(encoding="utf-8")
        )
        rolled_back_body = (
            self.repository
            / ".agent-skills"
            / "generations"
            / rolled_back["generation"]
            / "skills"
            / "compound-delivery-kernel"
            / "SKILL.md"
        ).read_bytes()
        self.assertEqual(rolled_back_body, installed_body)
        generations = list((self.repository / ".agent-skills" / "generations").iterdir())
        self.assertEqual(len(generations), 2)
        lifecycle.remove(maintainer=True)
        self.assertEqual(lifecycle.status().as_dict()["lifecycle"], "absent")
        self.assertFalse((self.repository / ".agent-skills" / ".gitattributes").exists())
        self.assertEqual(unrelated_digest(self.repository), before)
        self.assertEqual(dirty_tracked.read_text(encoding="utf-8"), "dirty tracked bytes\n")
        self.assertEqual(dirty_untracked.read_text(encoding="utf-8"), "dirty untracked bytes\n")

    def test_fresh_install_remove_from_installed_generation_restores_directory_layout(self) -> None:
        lifecycle = Lifecycle(self.repository)
        before_directories = self._repository_directories()
        before_unrelated = unrelated_digest(self.repository)

        lifecycle.install(*self.v1, maintainer=True)
        receipt = json.loads(
            (self.repository / ".agent-skills" / "active.json").read_text(
                encoding="utf-8"
            )
        )
        created_directories = receipt["createdDirectories"]
        lifecycle.update(*self.v2, maintainer=True)
        self.assertEqual(
            json.loads(
                (self.repository / ".agent-skills" / "active.json").read_text(
                    encoding="utf-8"
                )
            )["createdDirectories"],
            created_directories,
        )
        lifecycle.rollback(maintainer=True)
        self.assertEqual(
            json.loads(
                (self.repository / ".agent-skills" / "active.json").read_text(
                    encoding="utf-8"
                )
            )["createdDirectories"],
            created_directories,
        )
        self.v1[0].unlink()
        self.v1[1].unlink()

        if os.name == "nt":
            environment = {**os.environ, "PYTHONPATH": ".agent-skills/current"}
            command = [
                sys.executable,
                "-B",
                "-m",
                "agent_skills.cli",
                "--root",
                ".",
                "remove",
                "--maintenance",
            ]
        else:
            environment = None
            command = [
                "sh",
                "-c",
                "PYTHONPATH=.agent-skills/current python3 -B -m agent_skills.cli "
                "--root . remove --maintenance",
            ]
        result = subprocess.run(
            command,
            cwd=self.repository,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(json.loads(result.stdout)["status"]["lifecycle"], "absent")
        self.assertEqual(self._repository_directories(), before_directories)
        self.assertEqual(unrelated_digest(self.repository), before_unrelated)

    def test_remove_preserves_content_added_to_a_created_directory(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        local_file = self.repository / ".claude" / "local.json"
        local_file.write_text('{"existing": true}\n', encoding="utf-8")

        lifecycle.remove(maintainer=True)

        self.assertEqual(local_file.read_text(encoding="utf-8"), '{"existing": true}\n')
        self.assertTrue(self.repository.joinpath(".claude").is_dir())

    def test_directory_pruning_never_follows_a_replaced_parent_symlink(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        receipt = json.loads(
            (self.repository / ".agent-skills" / "active.json").read_text(
                encoding="utf-8"
            )
        )
        claude_root = self.repository / ".claude"
        for projection in (claude_root / "skills").iterdir():
            projection.unlink()
        (claude_root / "skills").rmdir()
        claude_root.rmdir()
        outside = self.base / "outside"
        outside.joinpath("skills").mkdir(parents=True)
        claude_root.symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "lifecycle-created directory"):
            lifecycle._prune_created_directories(receipt)
        with mock.patch.object(os, "supports_dir_fd", set()):
            with self.assertRaisesRegex(ValueError, "became a symlink"):
                lifecycle._prune_created_directories(receipt)

        self.assertTrue(outside.joinpath("skills").is_dir())

    def test_remove_preserves_discovery_directories_that_predated_install(self) -> None:
        local_skill = self.repository / ".agents" / "skills" / "local-workflow"
        local_skill.mkdir(parents=True)
        (local_skill / "SKILL.md").write_text("local workflow\n", encoding="utf-8")
        self.repository.joinpath(".claude").mkdir()
        self.repository.joinpath(".claude", "launch.json").write_text(
            '{"existing": true}\n',
            encoding="utf-8",
        )
        self.repository.joinpath(".agent-skills").mkdir()
        before_directories = self._repository_directories()
        before_unrelated = unrelated_digest(self.repository)

        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        receipt = json.loads(
            (self.repository / ".agent-skills" / "active.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            receipt["createdDirectories"],
            [".agent-skills/generations", ".claude/skills"],
        )

        lifecycle.remove(maintainer=True)

        self.assertEqual(self._repository_directories(), before_directories)
        self.assertEqual(unrelated_digest(self.repository), before_unrelated)
        self.assertEqual(
            (local_skill / "SKILL.md").read_text(encoding="utf-8"),
            "local workflow\n",
        )

    def test_fresh_install_rollback_points_to_remove(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)

        with self.assertRaisesRegex(
            ValueError,
            r"rollback-unavailable: .*use remove --maintenance to return to absent",
        ):
            lifecycle.rollback(maintainer=True)

    def test_invalid_or_missing_release_never_changes_active_version(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        before = (self.repository / ".agent-skills" / "active.json").read_bytes()
        self.v2[0].write_bytes(self.v2[0].read_bytes() + b"tampered")
        with self.assertRaisesRegex(ValueError, "release.archive_checksum"):
            lifecycle.update(*self.v2, maintainer=True)
        self.assertEqual((self.repository / ".agent-skills" / "active.json").read_bytes(), before)

        missing = self.base / "missing.zip"
        with self.assertRaisesRegex(ValueError, "source-unavailable"):
            lifecycle.update(missing, self.v2[1], maintainer=True)
        self.assertEqual(lifecycle.status().as_dict()["active"]["releaseId"], "core-v1")

    def test_receipt_uses_the_metadata_bytes_that_verification_authenticated(self) -> None:
        lifecycle = Lifecycle(self.repository)
        archive, metadata = self.v1
        verified_metadata = metadata.read_bytes()
        replacement_metadata = verified_metadata + b"\n"
        expected_digest = hashlib.sha256(verified_metadata).hexdigest()

        def verify_then_replace(*args: object, **kwargs: object):
            result = verify_release(*args, **kwargs)
            metadata.write_bytes(replacement_metadata)
            return result

        operations = (
            ("inspection", lambda: inspect_release(archive, metadata)),
            (
                "staging",
                lambda: lifecycle.store.stage(
                    archive,
                    metadata,
                    "a" * 32,
                )[0],
            ),
        )
        for name, operation in operations:
            with self.subTest(operation=name):
                metadata.write_bytes(verified_metadata)
                with mock.patch(
                    "agent_skills.generations.verify_release",
                    side_effect=verify_then_replace,
                ):
                    version = operation()
                self.assertEqual(
                    version["release"]["metadataSha256"],
                    expected_digest,
                )

    def test_provenance_failure_never_changes_active_version(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        before = (self.repository / ".agent-skills" / "active.json").read_bytes()
        forge_provenance_failure(*self.v2)

        with self.assertRaisesRegex(ValueError, "release.standalone_validation"):
            lifecycle.update(*self.v2, maintainer=True)
        self.assertEqual((self.repository / ".agent-skills" / "active.json").read_bytes(), before)
        self.assertEqual(lifecycle.status().as_dict()["active"]["releaseId"], "core-v1")

    def test_tracked_installation_survives_clean_clone_without_journal(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        run_git(
            self.repository,
            "add",
            ".agent-skills",
            ".agents/skills",
            ".claude/skills",
        )
        run_git(self.repository, "commit", "-qm", "track skill generation")

        clone = self.base / "clone"
        subprocess.run(
            [
                "git",
                "-c",
                "core.autocrlf=true",
                "clone",
                "-q",
                str(self.repository),
                str(clone),
            ],
            check=True,
        )
        cloned_status = Lifecycle(clone).status().as_dict()
        self.assertEqual(cloned_status["lifecycle"], "current")
        self.assertEqual(cloned_status["active"]["releaseId"], "core-v1")
        for host in HOSTS:
            target = "../../.agent-skills/current/skills/deliver-work"
            self.assertEqual(
                _normalized_link(os.readlink(clone / host.root / "deliver-work")),
                target,
            )
            self.assertEqual(
                run_git(
                    clone,
                    "show",
                    f"HEAD:{(host.root / 'deliver-work').as_posix()}",
                ),
                target,
            )
        self.assertTrue(
            _normalized_link(
                os.readlink(clone / ".agent-skills" / "current")
            ).startswith(
                "generations/"
            )
        )
        self.assertFalse((clone / ".git" / "agent-skills" / "journal.json").exists())

    def test_diverged_managed_checkout_attributes_is_an_external_transition(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        attributes = self.repository / ".agent-skills" / ".gitattributes"
        attributes.write_text("* text\n", encoding="utf-8")

        status = lifecycle.status().as_dict()
        self.assertEqual(status["lifecycle"], "externally-transitioned")
        self.assertIn("local-divergence", status["blockers"])

    def test_plan_and_diff_are_read_only_and_deterministic(self) -> None:
        lifecycle = Lifecycle(self.repository)
        install_plan = lifecycle.plan(*self.v1)
        self.assertEqual(install_plan, lifecycle.plan(*self.v1))
        self.assertEqual(install_plan["action"], "install")
        self.assertFalse((self.repository / ".agent-skills").exists())

        lifecycle.install(*self.v1, maintainer=True)
        self.assertEqual(lifecycle.plan(*self.v1)["action"], "no-op")
        update_plan = lifecycle.plan(*self.v2)
        self.assertEqual(update_plan["action"], "update")
        self.assertEqual(update_plan["target"]["releaseId"], "core-v2")
        self.assertEqual(lifecycle.diff(*self.v2), lifecycle.diff(*self.v2))

    def test_local_generation_divergence_blocks_destructive_operations(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        marker = json.loads(
            (self.repository / ".agent-skills" / "active.json").read_text(encoding="utf-8")
        )
        generation = self.repository / ".agent-skills" / "generations" / marker["generation"]
        body = generation / "skills" / "compound-delivery-kernel" / "SKILL.md"
        body.write_text(body.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")

        status = lifecycle.status().as_dict()
        self.assertEqual(status["lifecycle"], "externally-transitioned")
        self.assertIn("local-divergence", status["blockers"])
        with self.assertRaisesRegex(ValueError, "local-divergence"):
            lifecycle.remove(maintainer=True)

    def test_python_bytecode_divergence_names_remediation_and_blocks_update(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        receipt = json.loads(
            (self.repository / ".agent-skills" / "active.json").read_text(encoding="utf-8")
        )
        bytecode = (
            self.repository
            / ".agent-skills"
            / "generations"
            / receipt["generation"]
            / "agent_skills"
            / "__pycache__"
            / "lifecycle.cpython-311.pyc"
        )
        bytecode.parent.mkdir()
        bytecode.write_bytes(b"planted bytecode")

        status = lifecycle.status().as_dict()
        self.assertEqual(status["lifecycle"], "externally-transitioned")
        self.assertEqual(status["blockers"], ["local-bytecode-divergence"])
        self.assertEqual(status["recoveryPlan"], ["remove-python-bytecode"])
        with self.assertRaisesRegex(ValueError, "^local-bytecode-divergence:"):
            lifecycle.update(*self.v2, maintainer=True)

        managed_source = bytecode.parents[1] / "lifecycle.py"
        original_source = managed_source.read_bytes()
        managed_source.write_bytes(original_source + b"\n# mixed divergence\n")
        mixed_status = lifecycle.status().as_dict()
        self.assertEqual(mixed_status["blockers"], ["local-divergence"])
        self.assertEqual(mixed_status["recoveryPlan"], ["review-managed-diff"])
        managed_source.write_bytes(original_source)

        bytecode.unlink()
        bytecode.parent.rmdir()
        self.assertEqual(lifecycle.status().as_dict()["lifecycle"], "current")
        self.assertEqual(lifecycle.update(*self.v2, maintainer=True)["action"], "update")

    def test_python_bytecode_during_interrupted_update_keeps_named_blocker(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)

        def interrupt(phase: str) -> None:
            if phase == "marker-committed":
                raise RuntimeError("stop before update cleanup")

        with self.assertRaisesRegex(RuntimeError, "stop before update cleanup"):
            lifecycle.update(*self.v2, maintainer=True, fault=interrupt)
        receipt = json.loads(
            (self.repository / ".agent-skills" / "active.json").read_text(encoding="utf-8")
        )
        bytecode = (
            self.repository
            / ".agent-skills"
            / "generations"
            / receipt["generation"]
            / "agent_skills"
            / "__pycache__"
            / "lifecycle.cpython-311.pyc"
        )
        bytecode.parent.mkdir()
        bytecode.write_bytes(b"planted bytecode")

        status = lifecycle.status().as_dict()
        self.assertEqual(status["journalPhase"], "marker-committed")
        self.assertEqual(status["blockers"], ["local-bytecode-divergence"])
        self.assertEqual(status["recoveryPlan"], ["remove-python-bytecode"])
        with self.assertRaisesRegex(ValueError, "^local-bytecode-divergence:"):
            lifecycle.update(*self.v2, maintainer=True)

    def test_non_bytecode_extra_remains_generic_local_divergence(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        receipt = json.loads(
            (self.repository / ".agent-skills" / "active.json").read_text(encoding="utf-8")
        )
        generation = (
            self.repository
            / ".agent-skills"
            / "generations"
            / receipt["generation"]
        )
        (generation / "unexpected.txt").write_text("extra\n", encoding="utf-8")

        status = lifecycle.status().as_dict()
        self.assertEqual(status["blockers"], ["local-divergence"])
        self.assertEqual(status["recoveryPlan"], ["review-managed-diff"])
        with self.assertRaisesRegex(ValueError, "^local-divergence:"):
            lifecycle.update(*self.v2, maintainer=True)

    def test_python_bytecode_classification_requires_only_valid_cache_paths(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        receipt = json.loads(
            (self.repository / ".agent-skills" / "active.json").read_text(encoding="utf-8")
        )
        generation = (
            self.repository
            / ".agent-skills"
            / "generations"
            / receipt["generation"]
        )
        scenarios = (
            (
                "alternate valid cache path",
                ("agent_skills/__pycache__/provider.cpython-314.opt-1.pyc",),
                "local-bytecode-divergence",
                "remove-python-bytecode",
            ),
            (
                "bytecode mixed with ordinary extra",
                (
                    "agent_skills/__pycache__/provider.cpython-314.pyc",
                    "unexpected.txt",
                ),
                "local-divergence",
                "review-managed-diff",
            ),
            (
                "pyc outside cache directory",
                ("agent_skills/provider.pyc",),
                "local-divergence",
                "review-managed-diff",
            ),
            (
                "non-pyc inside cache directory",
                ("agent_skills/__pycache__/provider.txt",),
                "local-divergence",
                "review-managed-diff",
            ),
        )

        for name, relative_paths, blocker, recovery in scenarios:
            with self.subTest(name=name):
                planted = [generation / relative for relative in relative_paths]
                for path in planted:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"planted divergence")
                try:
                    status = lifecycle.status().as_dict()
                    self.assertEqual(status["blockers"], [blocker])
                    self.assertEqual(status["recoveryPlan"], [recovery])
                    with self.assertRaisesRegex(ValueError, f"^{blocker}:"):
                        lifecycle.update(*self.v2, maintainer=True)
                finally:
                    for path in planted:
                        path.unlink()
                    cache = generation / "agent_skills" / "__pycache__"
                    if cache.exists():
                        cache.rmdir()

    def test_mutation_requires_explicit_maintenance_authority(self) -> None:
        lifecycle = Lifecycle(self.repository)
        with self.assertRaisesRegex(ValueError, "maintainer"):
            lifecycle.install(*self.v1)

    def test_mutation_authority_rejects_candidate_provider_and_pull_request_contexts(self) -> None:
        lifecycle = Lifecycle(self.repository)
        for context in ("candidate", "provider"):
            with self.subTest(context=context):
                with self.assertRaisesRegex(ValueError, "context-unsupported"):
                    lifecycle.install(
                        *self.v1,
                        maintainer=True,
                        context=context,
                    )
        self.assertEqual(lifecycle.status().as_dict()["lifecycle"], "absent")

        output = StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}):
            with redirect_stdout(output):
                result = lifecycle_cli(
                    [
                        "--root",
                        str(self.repository),
                        "install",
                        "--archive",
                        str(self.v1[0]),
                        "--metadata",
                        str(self.v1[1]),
                        "--maintenance",
                    ]
                )
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "context-unsupported")
        self.assertEqual(lifecycle.status().as_dict()["lifecycle"], "absent")

    def test_concurrent_mutation_reports_stable_lock_blocker(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        with RepositoryLock(lifecycle.lock_path):
            with self.assertRaisesRegex(ValueError, "lock-busy"):
                lifecycle.update(*self.v2, maintainer=True)
        self.assertEqual(lifecycle.status().as_dict()["active"]["releaseId"], "core-v1")

    def test_stale_recovery_never_deletes_a_replacement_live_lock(self) -> None:
        lifecycle = Lifecycle(self.repository)
        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os, sys; "
                    "from pathlib import Path; "
                    "from agent_skills.locking import RepositoryLock; "
                    "lock = RepositoryLock(Path(sys.argv[1])); "
                    "lock.__enter__(); "
                    "os._exit(0)"
                ),
                str(lifecycle.lock_path),
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
        )
        self.assertTrue(lifecycle.lock_path.exists())

        real_unlink = Path.unlink
        replacement: RepositoryLock | None = None

        def replace_before_delete(
            target: Path,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal replacement
            real_unlink(target, *args, **kwargs)
            replacement = RepositoryLock(target)
            replacement.__enter__()
            real_unlink(target, *args, **kwargs)

        try:
            with (
                mock.patch(
                    "agent_skills.locking.os.kill",
                    side_effect=ProcessLookupError,
                ),
                mock.patch.object(Path, "unlink", new=replace_before_delete),
            ):
                RepositoryLock.break_stale(lifecycle.lock_path)

            if replacement is None:
                replacement = RepositoryLock(lifecycle.lock_path)
                replacement.__enter__()
            self.assertTrue(lifecycle.lock_path.exists())
            with self.assertRaisesRegex(ValueError, "lock-busy"):
                RepositoryLock.break_stale(lifecycle.lock_path)
            with self.assertRaisesRegex(ValueError, "lock-busy"):
                with RepositoryLock(lifecycle.lock_path):
                    pass
        finally:
            if replacement is not None:
                replacement.__exit__(None, None, None)

    def test_managed_root_symlink_cannot_redirect_writes_or_deletion(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("preserve\n", encoding="utf-8")
        os.symlink(outside, self.repository / ".agent-skills", target_is_directory=True)
        lifecycle = Lifecycle(self.repository)

        status = lifecycle.status().as_dict()
        self.assertEqual(status["lifecycle"], "externally-transitioned")
        self.assertIn("local-divergence", status["blockers"])
        with self.assertRaisesRegex(ValueError, "local-divergence"):
            lifecycle.install(*self.v1, maintainer=True)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")
        self.assertFalse((outside / "active.json").exists())

    @unittest.skipUnless(
        os.open in os.supports_dir_fd and os.rename in os.supports_dir_fd,
        "directory-descriptor replacement is not supported on this host",
    )
    def test_active_marker_parent_swap_cannot_redirect_external_write(self) -> None:
        outside = self.base / "external-managed-root"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("preserve\n", encoding="utf-8")
        detached = self.repository / ".agent-skills-detached"
        original_open = os.open
        managed_opens = 0
        swapped = False

        def swapping_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
            nonlocal managed_opens, swapped
            descriptor = original_open(path, flags, *args, **kwargs)
            if path == ".agent-skills" and kwargs.get("dir_fd") is not None:
                managed_opens += 1
            if managed_opens == 2 and not swapped:
                os.rename(self.repository / ".agent-skills", detached)
                os.symlink(outside, self.repository / ".agent-skills", target_is_directory=True)
                swapped = True
            return descriptor

        lifecycle = Lifecycle(self.repository)
        with mock.patch("agent_skills.fs.os.open", side_effect=swapping_open):
            with self.assertRaisesRegex(ValueError, "local-divergence"):
                lifecycle.install(*self.v1, maintainer=True)
        self.assertTrue(swapped)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")
        self.assertFalse((outside / "active.json").exists())

    def test_third_update_reclaims_only_the_unreferenced_verified_generation(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        lifecycle.update(*self.v2, maintainer=True)
        v3 = build_version(self.base, "core-v3")
        lifecycle.update(*v3, maintainer=True)

        active = json.loads(
            (self.repository / ".agent-skills" / "active.json").read_text(encoding="utf-8")
        )
        retained = {path.name for path in lifecycle.store.generations_root.iterdir()}
        self.assertEqual(
            retained,
            {active["generation"], active["previous"]["generation"]},
        )
        self.assertEqual(active["release"]["releaseId"], "core-v3")
        self.assertEqual(active["previous"]["release"]["releaseId"], "core-v2")


if __name__ == "__main__":
    unittest.main()
