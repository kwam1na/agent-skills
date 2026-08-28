from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path

from agent_skills.hosts.exposure import HOSTS
from agent_skills.lifecycle import Lifecycle

from lifecycle_support import LifecycleFixture, unrelated_digest


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "hosts"


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_digests(root: Path) -> dict[str, str]:
    records: dict[str, str] = {}
    for directory, directory_names, filenames in os.walk(root, followlinks=True):
        directory_names.sort()
        filenames.sort()
        for name in filenames:
            path = Path(directory) / name
            records[path.relative_to(root).as_posix()] = file_digest(path)
    return records


class HostExposureTests(LifecycleFixture, unittest.TestCase):
    def test_codex_and_claude_code_resolve_one_exact_generation(self) -> None:
        fixtures = [
            json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))
            for name in ("codex.json", "claude-code.json")
        ]
        self.assertEqual(
            [host.as_dict() for host in HOSTS],
            fixtures,
        )

        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        receipt = json.loads(
            (self.repository / ".agent-skills" / "active.json").read_text(
                encoding="utf-8"
            )
        )
        generation = receipt["generation"]
        canonical = (
            self.repository
            / ".agent-skills"
            / "generations"
            / generation
            / "skills"
        )

        for skill in ("compound-delivery-kernel", "deliver-work"):
            codex = self.repository / ".agents" / "skills" / skill
            claude = self.repository / ".claude" / "skills" / skill
            with self.subTest(skill=skill):
                self.assertTrue(codex.is_symlink())
                self.assertTrue(claude.is_symlink())
                self.assertFalse(os.path.isabs(os.readlink(codex)))
                self.assertFalse(os.path.isabs(os.readlink(claude)))
                expected = file_digest(canonical / skill / "SKILL.md")
                self.assertEqual(file_digest(codex / "SKILL.md"), expected)
                self.assertEqual(file_digest(claude / "SKILL.md"), expected)
                self.assertEqual(
                    content_digests(codex),
                    content_digests(canonical / skill),
                )
                self.assertEqual(content_digests(claude), content_digests(codex))

        self.assertEqual(receipt["exposures"], fixtures)
        status = lifecycle.status().as_dict()
        self.assertEqual(
            status["exposures"],
            [{**fixture, "state": "current"} for fixture in fixtures],
        )
        self.assertEqual(status["blockers"], [])

    def test_user_owned_projection_conflict_blocks_before_mutation(self) -> None:
        conflict = self.repository / ".agents" / "skills" / "deliver-work"
        conflict.mkdir(parents=True)
        (conflict / "SKILL.md").write_text("user-owned\n", encoding="utf-8")
        before = unrelated_digest(self.repository)

        lifecycle = Lifecycle(self.repository)
        with self.assertRaisesRegex(ValueError, "exposure-conflict"):
            lifecycle.install(*self.v1, maintainer=True)

        self.assertEqual(unrelated_digest(self.repository), before)
        self.assertFalse((self.repository / ".agent-skills").exists())
        self.assertFalse((self.repository / ".claude").exists())
        self.assertEqual((conflict / "SKILL.md").read_text(encoding="utf-8"), "user-owned\n")

    def test_broken_projection_is_an_actionable_blocker(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        projection = self.repository / ".claude" / "skills" / "deliver-work"
        projection.unlink()
        projection.symlink_to("../../missing/deliver-work", target_is_directory=True)

        status = lifecycle.status().as_dict()
        self.assertEqual(status["lifecycle"], "externally-transitioned")
        self.assertIn("exposure-conflict", status["blockers"])
        self.assertIn("review-host-projections", status["recoveryPlan"])
        with self.assertRaisesRegex(ValueError, "exposure-conflict"):
            lifecycle.remove(maintainer=True)

    def test_rollback_restores_both_hosts_to_the_retained_generation(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        first = lifecycle.status().as_dict()["active"]["generation"]
        lifecycle.update(*self.v2, maintainer=True)
        second = lifecycle.status().as_dict()["active"]["generation"]
        self.assertNotEqual(first, second)

        for host in HOSTS:
            projection = self.repository / host.root / "deliver-work"
            self.assertIn(second, projection.resolve().as_posix())

        lifecycle.rollback(maintainer=True)
        for host in HOSTS:
            projection = self.repository / host.root / "deliver-work"
            self.assertIn(first, projection.resolve().as_posix())
            self.assertNotIn(second, os.readlink(projection))

    def test_remove_deletes_only_owned_links_from_both_hosts(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        local_skill = self.repository / ".agents" / "skills" / "local-workflow"
        local_skill.mkdir()
        (local_skill / "SKILL.md").write_text("local\n", encoding="utf-8")

        lifecycle.remove(maintainer=True)

        for host in HOSTS:
            self.assertFalse((self.repository / host.root / "deliver-work").exists())
            self.assertFalse(
                (self.repository / host.root / "compound-delivery-kernel").exists()
            )
        self.assertEqual((local_skill / "SKILL.md").read_text(encoding="utf-8"), "local\n")
        self.assertEqual(lifecycle.status().as_dict()["exposures"], [])

    def test_interruption_after_first_host_restores_prior_projection_set(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        first = lifecycle.status().as_dict()["active"]["generation"]

        def interrupt(phase: str) -> None:
            if phase == "exposing-codex":
                raise RuntimeError("stop after first host")

        with self.assertRaisesRegex(RuntimeError, "first host"):
            lifecycle.update(*self.v2, maintainer=True, fault=interrupt)
        self.assertEqual(lifecycle.recover(maintainer=True)["action"], "restore-prior")
        for host in HOSTS:
            projection = self.repository / host.root / "deliver-work"
            self.assertIn(first, projection.resolve().as_posix())


if __name__ == "__main__":
    unittest.main()
