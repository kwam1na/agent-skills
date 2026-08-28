from __future__ import annotations

import hashlib
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from agent_skills import adoption
from agent_skills.cli import main as lifecycle_cli
from agent_skills.lifecycle import Lifecycle

from lifecycle_support import LifecycleFixture


SKILL = "compound-delivery-kernel"
PRIOR_BODY = b"---\nname: compound-delivery-kernel\n---\n\nRepository posture.\n"


class AdoptionTests(LifecycleFixture, unittest.TestCase):
    def _codex_body(self) -> Path:
        return self.repository / ".agents" / "skills" / SKILL / "SKILL.md"

    def _claude_body(self) -> Path:
        return self.repository / ".claude" / "skills" / SKILL / "SKILL.md"

    def _write_prior(self, *, claude: bytes | None = None) -> str:
        codex = self._codex_body()
        codex.parent.mkdir(parents=True)
        codex.write_bytes(PRIOR_BODY)
        if claude is not None:
            target = self._claude_body()
            target.parent.mkdir(parents=True)
            target.write_bytes(claude)
        return hashlib.sha256(PRIOR_BODY).hexdigest()

    def test_adopt_exposes_one_skill_from_a_complete_release_and_rolls_back(self) -> None:
        expected = self._write_prior()
        lifecycle = Lifecycle(self.repository)

        result = lifecycle.adopt(
            *self.v1,
            skill=SKILL,
            expected_prior_sha256=expected,
            maintainer=True,
        )

        self.assertEqual(result["action"], "adopt")
        status = lifecycle.status().as_dict()
        self.assertEqual(status["active"]["selectedSkills"], [SKILL])
        self.assertEqual(status["active"]["releaseId"], "core-v1")
        self.assertTrue((self.repository / ".agents" / "skills" / SKILL).is_symlink())
        self.assertTrue((self.repository / ".claude" / "skills" / SKILL).is_symlink())
        self.assertFalse(
            (self.repository / ".agents" / "skills" / "deliver-work").exists()
        )
        receipt = json.loads(
            (self.repository / ".agent-skills" / "active.json").read_text(
                encoding="utf-8"
            )
        )
        generation = (
            self.repository / ".agent-skills" / "generations" / receipt["generation"]
        )
        self.assertTrue((generation / "skills" / "deliver-work" / "SKILL.md").is_file())
        self.assertEqual(receipt["adoption"]["predecessorHosts"], ["codex"])

        rolled_back = lifecycle.rollback(maintainer=True)

        self.assertEqual(rolled_back["action"], "rollback-adoption")
        self.assertEqual(self._codex_body().read_bytes(), PRIOR_BODY)
        self.assertFalse((self.repository / ".claude" / "skills" / SKILL).exists())
        self.assertEqual(lifecycle.status().as_dict()["lifecycle"], "absent")

    def test_adopt_accepts_only_an_identical_claude_predecessor(self) -> None:
        expected = self._write_prior(claude=PRIOR_BODY)
        lifecycle = Lifecycle(self.repository)
        lifecycle.adopt(
            *self.v1,
            skill=SKILL,
            expected_prior_sha256=expected,
            maintainer=True,
        )
        lifecycle.remove(maintainer=True)
        self.assertEqual(self._codex_body().read_bytes(), PRIOR_BODY)
        self.assertEqual(self._claude_body().read_bytes(), PRIOR_BODY)

    def test_update_keeps_the_single_selection_and_remove_restores_prior(self) -> None:
        expected = self._write_prior()
        lifecycle = Lifecycle(self.repository)
        lifecycle.adopt(
            *self.v1,
            skill=SKILL,
            expected_prior_sha256=expected,
            maintainer=True,
        )

        lifecycle.update(*self.v2, maintainer=True)

        status = lifecycle.status().as_dict()
        self.assertEqual(status["active"]["releaseId"], "core-v2")
        self.assertEqual(status["active"]["selectedSkills"], [SKILL])
        self.assertFalse(
            (self.repository / ".agents" / "skills" / "deliver-work").exists()
        )
        lifecycle.remove(maintainer=True)
        self.assertEqual(self._codex_body().read_bytes(), PRIOR_BODY)
        self.assertFalse((self.repository / ".claude" / "skills" / SKILL).exists())

    def test_adopt_rejects_wrong_prior_claude_conflict_and_existing_install(self) -> None:
        expected = self._write_prior(claude=b"different\n")
        lifecycle = Lifecycle(self.repository)
        with self.assertRaisesRegex(ValueError, "adoption-prior-mismatch"):
            lifecycle.adopt(
                *self.v1,
                skill=SKILL,
                expected_prior_sha256=expected,
                maintainer=True,
            )
        self.assertEqual(self._codex_body().read_bytes(), PRIOR_BODY)
        self.assertEqual(self._claude_body().read_bytes(), b"different\n")
        self.assertFalse((self.repository / ".agent-skills").exists())

        self._claude_body().unlink()
        self._claude_body().parent.rmdir()
        with self.assertRaisesRegex(ValueError, "adoption-prior-mismatch"):
            lifecycle.adopt(
                *self.v1,
                skill=SKILL,
                expected_prior_sha256="0" * 64,
                maintainer=True,
            )
        codex = self._codex_body()
        codex.unlink()
        codex.parent.rmdir()
        lifecycle.install(*self.v1, maintainer=True)
        with self.assertRaisesRegex(ValueError, "lifecycle.current"):
            lifecycle.adopt(
                *self.v2,
                skill=SKILL,
                expected_prior_sha256=expected,
                maintainer=True,
            )

    def test_adopt_rejects_a_dependency_bearing_workflow_without_expansion(self) -> None:
        skill = "deliver-work"
        body = b"---\nname: deliver-work\n---\n\nRepository router.\n"
        prior = self.repository / ".agents" / "skills" / skill / "SKILL.md"
        prior.parent.mkdir(parents=True)
        prior.write_bytes(body)

        with self.assertRaisesRegex(ValueError, "adoption-dependencies-unsupported"):
            Lifecycle(self.repository).adopt(
                *self.v1,
                skill=skill,
                expected_prior_sha256=hashlib.sha256(body).hexdigest(),
                maintainer=True,
            )

        self.assertEqual(prior.read_bytes(), body)
        self.assertFalse((self.repository / ".agent-skills").exists())
        self.assertFalse((self.repository / ".claude").exists())

    def test_adoption_status_and_rollback_fail_closed_on_predecessor_tampering(self) -> None:
        expected = self._write_prior()
        lifecycle = Lifecycle(self.repository)
        lifecycle.adopt(
            *self.v1,
            skill=SKILL,
            expected_prior_sha256=expected,
            maintainer=True,
        )
        predecessor = (
            self.repository
            / ".agent-skills"
            / "predecessors"
            / expected
            / "SKILL.md"
        )
        predecessor.write_bytes(b"tampered\n")

        status = lifecycle.status().as_dict()
        self.assertEqual(status["lifecycle"], "externally-transitioned")
        self.assertIn("local-divergence", status["blockers"])
        with self.assertRaisesRegex(ValueError, "local-divergence"):
            lifecycle.rollback(maintainer=True)

    def test_interrupted_adoption_recovers_to_the_repository_predecessor(self) -> None:
        expected = self._write_prior()
        lifecycle = Lifecycle(self.repository)

        def interrupt(phase: str) -> None:
            if phase == "exposing-codex":
                raise RuntimeError("stop during adoption")

        with self.assertRaisesRegex(RuntimeError, "stop during adoption"):
            lifecycle.adopt(
                *self.v1,
                skill=SKILL,
                expected_prior_sha256=expected,
                maintainer=True,
                fault=interrupt,
            )

        self.assertEqual(lifecycle.recover(maintainer=True)["action"], "restore-prior")
        self.assertEqual(self._codex_body().read_bytes(), PRIOR_BODY)
        self.assertFalse((self.repository / ".claude" / "skills" / SKILL).exists())
        self.assertEqual(lifecycle.status().as_dict()["lifecycle"], "absent")

    def test_cleanup_fault_after_predecessor_deletion_finishes_recovery(self) -> None:
        expected = self._write_prior()
        lifecycle = Lifecycle(self.repository)
        lifecycle.adopt(
            *self.v1,
            skill=SKILL,
            expected_prior_sha256=expected,
            maintainer=True,
        )

        def interrupt(phase: str) -> None:
            if phase == "cleanup":
                raise RuntimeError("stop after predecessor cleanup")

        with self.assertRaisesRegex(RuntimeError, "predecessor cleanup"):
            lifecycle.rollback(maintainer=True, fault=interrupt)
        self.assertFalse((self.repository / ".agent-skills" / "predecessors").exists())

        self.assertEqual(lifecycle.recover(maintainer=True)["action"], "complete-removal")
        self.assertEqual(lifecycle.status().as_dict()["lifecycle"], "absent")
        self.assertEqual(self._codex_body().read_bytes(), PRIOR_BODY)
        self.assertFalse((self.repository / ".claude" / "skills" / SKILL).exists())
        self.assertEqual(
            lifecycle.adopt(
                *self.v1,
                skill=SKILL,
                expected_prior_sha256=expected,
                maintainer=True,
            )["action"],
            "adopt",
        )

    def test_partial_predecessor_delete_finishes_from_private_trash(self) -> None:
        expected = self._write_prior()
        lifecycle = Lifecycle(self.repository)
        lifecycle.adopt(
            *self.v1,
            skill=SKILL,
            expected_prior_sha256=expected,
            maintainer=True,
        )

        def interrupt(phase: str) -> None:
            if phase == "predecessor-delete-started":
                raise RuntimeError("stop during predecessor deletion")

        with self.assertRaisesRegex(RuntimeError, "predecessor deletion"):
            lifecycle.rollback(maintainer=True, fault=interrupt)

        self.assertFalse((self.repository / ".agent-skills" / "predecessors").exists())
        self.assertTrue(lifecycle.predecessors.trash_root.exists())
        self.assertEqual(self._codex_body().read_bytes(), PRIOR_BODY)

        self.assertEqual(lifecycle.recover(maintainer=True)["action"], "complete-removal")
        self.assertEqual(lifecycle.status().as_dict()["lifecycle"], "absent")
        self.assertFalse(lifecycle.predecessors.trash_root.exists())
        self.assertEqual(self._codex_body().read_bytes(), PRIOR_BODY)

    def test_partial_predecessor_stage_is_cleanup_safe_during_recovery(self) -> None:
        expected = self._write_prior()
        lifecycle = Lifecycle(self.repository)
        real_atomic_write = adoption.atomic_write
        writes = 0

        def interrupt_marker(path: Path, contents: bytes) -> None:
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("stop during predecessor stage")
            real_atomic_write(path, contents)

        with (
            mock.patch("agent_skills.adoption.atomic_write", side_effect=interrupt_marker),
            self.assertRaisesRegex(OSError, "predecessor stage"),
        ):
            lifecycle.adopt(
                *self.v1,
                skill=SKILL,
                expected_prior_sha256=expected,
                maintainer=True,
            )

        self.assertEqual(lifecycle.recover(maintainer=True)["action"], "restore-prior")
        self.assertEqual(lifecycle.status().as_dict()["lifecycle"], "absent")
        self.assertEqual(self._codex_body().read_bytes(), PRIOR_BODY)
        self.assertFalse(lifecycle.predecessors.staging_root.exists())

    def test_adoption_recovery_preserves_unrelated_tracked_and_untracked_bytes(self) -> None:
        expected = self._write_prior()
        tracked = self.repository / "unrelated.txt"
        untracked = self.repository / "scratch.txt"
        tracked.write_bytes(b"dirty tracked bytes\n")
        untracked.write_bytes(b"dirty untracked bytes\n")
        lifecycle = Lifecycle(self.repository)
        lifecycle.adopt(
            *self.v1,
            skill=SKILL,
            expected_prior_sha256=expected,
            maintainer=True,
        )

        def interrupt(phase: str) -> None:
            if phase == "cleanup":
                raise RuntimeError("stop after cleanup")

        with self.assertRaisesRegex(RuntimeError, "after cleanup"):
            lifecycle.remove(maintainer=True, fault=interrupt)
        lifecycle.recover(maintainer=True)

        self.assertEqual(tracked.read_bytes(), b"dirty tracked bytes\n")
        self.assertEqual(untracked.read_bytes(), b"dirty untracked bytes\n")

    def test_cli_adopt_requires_explicit_maintainer_authority(self) -> None:
        expected = self._write_prior()
        arguments = [
            "--root",
            str(self.repository),
            "adopt",
            "--archive",
            str(self.v1[0]),
            "--metadata",
            str(self.v1[1]),
            "--skill",
            SKILL,
            "--expected-prior-sha256",
            expected,
        ]
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(lifecycle_cli(arguments), 1)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "maintenance-required")

        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(lifecycle_cli([*arguments, "--maintenance"]), 0)
        self.assertEqual(json.loads(output.getvalue())["action"], "adopt")


if __name__ == "__main__":
    unittest.main()
