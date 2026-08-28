from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_skills.lifecycle import ACTIVATION_BOUNDARIES, Lifecycle

from lifecycle_support import (
    LifecycleFixture,
    build_version,
    initialize_repository,
    run_git,
)


class FaultInjectionTests(LifecycleFixture, unittest.TestCase):
    def test_interruption_at_every_activation_boundary_recovers_idempotently(self) -> None:
        before_marker = {
            "staged",
            "generation-ready",
            "exposing-codex",
            "exposing-claude-code",
            "verifying",
        }
        for boundary in ACTIVATION_BOUNDARIES:
            with self.subTest(boundary=boundary):
                repository = initialize_repository(self.base / boundary)
                lifecycle = Lifecycle(repository)
                lifecycle.install(*self.v1, maintainer=True)

                def interrupt(phase: str) -> None:
                    if phase == boundary:
                        raise RuntimeError(f"interrupted:{phase}")

                with self.assertRaisesRegex(RuntimeError, f"interrupted:{boundary}"):
                    lifecycle.update(*self.v2, maintainer=True, fault=interrupt)

                status = lifecycle.status().as_dict()
                if boundary in before_marker:
                    self.assertEqual(status["active"]["releaseId"], "core-v1")
                else:
                    self.assertEqual(status["active"]["releaseId"], "core-v2")
                self.assertIn(status["lifecycle"], {"current", "recovery-needed"})

                first = lifecycle.recover(maintainer=True)
                second = lifecycle.recover(maintainer=True)
                self.assertIn(first["action"], {"restore-prior", "complete-current", "no-op"})
                self.assertEqual(second["action"], "no-op")
                expected = "core-v1" if boundary in before_marker else "core-v2"
                self.assertEqual(lifecycle.status().as_dict()["active"]["releaseId"], expected)
                self.assertFalse(lifecycle.journal_path.exists())

    def test_journal_is_git_private_and_corrupt_journal_is_non_destructive(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)

        def interrupt(phase: str) -> None:
            if phase == "staged":
                raise RuntimeError("stop")

        with self.assertRaises(RuntimeError):
            lifecycle.update(*self.v2, maintainer=True, fault=interrupt)
        self.assertTrue(lifecycle.journal_path.exists())
        self.assertTrue(lifecycle.journal_path.is_relative_to(Path(lifecycle.git_path)))
        self.assertNotIn("journal.json", run_git(self.repository, "status", "--short"))

        lifecycle.journal_path.write_text('{"generation":"../../outside"}\n', encoding="utf-8")
        before = (self.repository / ".agent-skills" / "active.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "journal"):
            lifecycle.recover(maintainer=True)
        self.assertEqual((self.repository / ".agent-skills" / "active.json").read_bytes(), before)

    def test_target_swap_between_preflight_and_marker_never_activates(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        prior_generation = lifecycle.status().as_dict()["active"]["generation"]

        def swap_target(phase: str) -> None:
            if phase != "verifying":
                return
            journal = json.loads(
                lifecycle.journal_path.read_text(encoding="utf-8")
            )
            generation = lifecycle.store.generation_path(journal["target"]["generation"])
            body = generation / "skills" / "compound-delivery-kernel" / "SKILL.md"
            body.write_text("mutated after preflight\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "local-divergence"):
            lifecycle.update(*self.v2, maintainer=True, fault=swap_target)
        self.assertEqual(lifecycle.status().as_dict()["active"]["releaseId"], "core-v1")
        for host in (".agents/skills", ".claude/skills"):
            projection = self.repository / host / "deliver-work"
            self.assertIn(prior_generation, projection.resolve().as_posix())

    def test_rollback_target_mutation_at_verifying_keeps_prior_exposure(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        rollback_generation = lifecycle.status().as_dict()["active"]["generation"]
        lifecycle.update(*self.v2, maintainer=True)
        current = lifecycle.status().as_dict()["active"]["generation"]

        def mutate_rollback_target(phase: str) -> None:
            if phase != "verifying":
                return
            body = (
                lifecycle.store.generation_path(rollback_generation)
                / "skills"
                / "deliver-work"
                / "SKILL.md"
            )
            body.write_text("mutated rollback target\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "local-divergence"):
            lifecycle.rollback(maintainer=True, fault=mutate_rollback_target)
        for host in (".agents/skills", ".claude/skills"):
            projection = self.repository / host / "deliver-work"
            self.assertIn(current, projection.resolve().as_posix())

    def test_interrupted_obsolete_generation_delete_recovers_and_allows_next_update(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        lifecycle.update(*self.v2, maintainer=True)
        v3 = build_version(self.base, "core-v3")
        v4 = build_version(self.base, "core-v4")

        def interrupt(phase: str) -> None:
            if phase == "obsolete-generation-delete-started":
                raise RuntimeError("interrupted private trash deletion")

        with self.assertRaisesRegex(RuntimeError, "interrupted private trash deletion"):
            lifecycle.update(*v3, maintainer=True, fault=interrupt)
        self.assertEqual(lifecycle.status().as_dict()["active"]["releaseId"], "core-v3")
        self.assertTrue(lifecycle.store.trash_root.exists())

        self.assertEqual(lifecycle.recover(maintainer=True)["action"], "complete-current")
        self.assertEqual(lifecycle.recover(maintainer=True)["action"], "no-op")
        self.assertFalse(lifecycle.store.trash_root.exists())
        lifecycle.update(*v4, maintainer=True)
        self.assertEqual(lifecycle.status().as_dict()["active"]["releaseId"], "core-v4")
        self.assertEqual(len(lifecycle.store.installed_generations()), 2)


if __name__ == "__main__":
    unittest.main()
