from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_skills.lifecycle import ACTIVATION_BOUNDARIES, Lifecycle

from lifecycle_support import LifecycleFixture, initialize_repository, run_git


class FaultInjectionTests(LifecycleFixture, unittest.TestCase):
    def test_interruption_at_every_activation_boundary_recovers_idempotently(self) -> None:
        before_marker = {"staged", "generation-ready", "verifying"}
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


if __name__ == "__main__":
    unittest.main()
