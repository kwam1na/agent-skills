from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from agent_skills.hosts.exposure import HOSTS, _normalized_link
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

    def test_interrupted_removal_after_first_host_leaves_both_unavailable(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        generation = lifecycle.status().as_dict()["active"]["generation"]

        def interrupt(phase: str) -> None:
            if phase == "exposing-codex":
                raise RuntimeError("stop during removal")

        with self.assertRaisesRegex(RuntimeError, "during removal"):
            lifecycle.remove(maintainer=True, fault=interrupt)
        for host in HOSTS:
            projection = self.repository / host.root / "deliver-work"
            with self.assertRaises(OSError):
                projection.resolve(strict=True)

        status = lifecycle.status().as_dict()
        self.assertEqual(status["lifecycle"], "recovery-needed")
        self.assertEqual(status["blockers"], [])
        self.assertEqual(lifecycle.recover(maintainer=True)["action"], "restore-prior")
        for host in HOSTS:
            projection = self.repository / host.root / "deliver-work"
            self.assertIn(generation, projection.resolve(strict=True).as_posix())

    def test_interruption_after_first_host_restores_prior_projection_set(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        first = lifecycle.status().as_dict()["active"]["generation"]

        def interrupt(phase: str) -> None:
            if phase == "exposing-codex":
                raise RuntimeError("stop after first host")

        with self.assertRaisesRegex(RuntimeError, "first host"):
            lifecycle.update(*self.v2, maintainer=True, fault=interrupt)
        for host in HOSTS:
            projection = self.repository / host.root / "deliver-work"
            self.assertIn(first, projection.resolve().as_posix())
        self.assertEqual(lifecycle.recover(maintainer=True)["action"], "restore-prior")
        for host in HOSTS:
            projection = self.repository / host.root / "deliver-work"
            self.assertIn(first, projection.resolve().as_posix())

    def test_one_shared_switch_moves_both_hosts_without_a_mixed_generation(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        first = lifecycle.status().as_dict()["active"]["generation"]
        second = lifecycle.plan(*self.v2)["target"]["generation"]

        def interrupt_marker(target: dict | None) -> None:
            if target is not None:
                raise RuntimeError("stop after shared switch")

        with mock.patch.object(lifecycle, "_write_active", side_effect=interrupt_marker):
            with self.assertRaisesRegex(RuntimeError, "shared switch"):
                lifecycle.update(*self.v2, maintainer=True)

        resolved = {
            (self.repository / host.root / "deliver-work").resolve().as_posix()
            for host in HOSTS
        }
        self.assertEqual(len(resolved), 1)
        self.assertIn(second, resolved.pop())
        status = lifecycle.status().as_dict()
        self.assertEqual(status["active"]["generation"], first)
        self.assertEqual(status["lifecycle"], "recovery-needed")
        self.assertEqual(status["blockers"], [])
        self.assertTrue(
            all(exposure["state"] == "transitioning" for exposure in status["exposures"])
        )

    def test_projection_text_is_canonical_posix_across_platform_separators(self) -> None:
        lifecycle = Lifecycle(self.repository)
        with mock.patch(
            "agent_skills.hosts.exposure.os.path.relpath",
            return_value=r"..\..\.agent-skills\wrong",
        ):
            lifecycle.install(*self.v1, maintainer=True)

        for host in HOSTS:
            projection = self.repository / host.root / "deliver-work"
            self.assertEqual(
                _normalized_link(os.readlink(projection)),
                "../../.agent-skills/current/skills/deliver-work",
            )
        self.assertEqual(lifecycle.status().as_dict()["lifecycle"], "current")

        windows_link = r"..\..\.agent-skills\current\skills\deliver-work"
        with mock.patch("agent_skills.hosts.exposure.WINDOWS", True, create=True):
            self.assertEqual(
                _normalized_link(windows_link),
                "../../.agent-skills/current/skills/deliver-work",
            )
        with mock.patch("agent_skills.hosts.exposure.WINDOWS", False, create=True):
            self.assertNotEqual(
                _normalized_link(windows_link),
                "../../.agent-skills/current/skills/deliver-work",
            )

    def test_windows_fallback_replaces_existing_current_as_directory_link(self) -> None:
        lifecycle = Lifecycle(self.repository)
        original_replace = os.replace
        original_symlink_to = Path.symlink_to
        directory_flags: list[bool] = []
        directory_targets: list[str] = []

        def reject_existing_current(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            if Path(destination) == lifecycle.exposure.current_path:
                if os.path.lexists(destination):
                    raise PermissionError("simulated Windows existing-link denial")
            original_replace(source, destination, *args, **kwargs)

        def recording_symlink_to(
            path: Path,
            target: object,
            target_is_directory: bool = False,
        ) -> None:
            directory_flags.append(target_is_directory)
            directory_targets.append(str(target))
            original_symlink_to(
                path,
                target,
                target_is_directory=target_is_directory,
            )

        with (
            mock.patch("agent_skills.hosts.exposure.DESCRIPTOR_SWITCH_SUPPORTED", False),
            mock.patch("agent_skills.hosts.exposure.WINDOWS", True, create=True),
            mock.patch("agent_skills.hosts.exposure.os.replace", side_effect=reject_existing_current),
            mock.patch.object(Path, "symlink_to", new=recording_symlink_to),
        ):
            lifecycle.install(*self.v1, maintainer=True)
            lifecycle.update(*self.v2, maintainer=True)
            self.assertEqual(lifecycle.status().as_dict()["lifecycle"], "current")

        self.assertTrue(directory_flags)
        self.assertTrue(all(directory_flags))
        self.assertTrue(all("\\" in target for target in directory_targets))
        self.assertTrue(all("/" not in target for target in directory_targets))

    def test_windows_switch_failure_after_unlink_is_jointly_unavailable_and_recovers(
        self,
    ) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        generation = lifecycle.status().as_dict()["active"]["generation"]
        original_replace = os.replace

        def crash_after_current_unlink(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            if Path(destination) == lifecycle.exposure.current_path:
                if os.path.lexists(destination):
                    raise PermissionError("simulated Windows existing-link denial")
                raise OSError("simulated interruption after shared unlink")
            original_replace(source, destination, *args, **kwargs)

        with (
            mock.patch("agent_skills.hosts.exposure.DESCRIPTOR_SWITCH_SUPPORTED", False),
            mock.patch("agent_skills.hosts.exposure.WINDOWS", True, create=True),
            mock.patch(
                "agent_skills.hosts.exposure.os.replace",
                side_effect=crash_after_current_unlink,
            ),
        ):
            with self.assertRaisesRegex(ValueError, "exposure-unavailable"):
                lifecycle.update(*self.v2, maintainer=True)
            for host in HOSTS:
                projection = self.repository / host.root / "deliver-work"
                with self.assertRaises(OSError):
                    projection.resolve(strict=True)
            status = lifecycle.status().as_dict()
            self.assertEqual(status["lifecycle"], "recovery-needed")
            self.assertEqual(status["blockers"], [])

        self.assertEqual(lifecycle.recover(maintainer=True)["action"], "restore-prior")
        for host in HOSTS:
            projection = self.repository / host.root / "deliver-work"
            self.assertIn(generation, projection.resolve(strict=True).as_posix())

    def test_unfinished_journal_with_user_projection_divergence_needs_review(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)

        def interrupt(phase: str) -> None:
            if phase == "exposing-codex":
                raise RuntimeError("stop during exposure")

        with self.assertRaisesRegex(RuntimeError, "during exposure"):
            lifecycle.update(*self.v2, maintainer=True, fault=interrupt)
        projection = self.repository / ".claude" / "skills" / "deliver-work"
        projection.unlink()
        projection.mkdir()
        (projection / "SKILL.md").write_text("user-owned\n", encoding="utf-8")

        status = lifecycle.status().as_dict()
        self.assertEqual(status["lifecycle"], "externally-transitioned")
        self.assertIn("exposure-conflict", status["blockers"])
        self.assertEqual(status["recoveryPlan"], ["review-host-projections"])
        with self.assertRaisesRegex(ValueError, "exposure-conflict"):
            lifecycle.recover(maintainer=True)

    @unittest.skipUnless(
        all(
            function in os.supports_dir_fd
            for function in (os.open, os.readlink, os.rename, os.symlink, os.unlink)
        ),
        "directory-descriptor projection replacement is not supported on this host",
    )
    def test_shared_switch_parent_swap_cannot_redirect_update(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        outside = self.base / "external-managed-root"
        outside.mkdir()
        outside_current = outside / "current"
        outside_current.write_text("preserve\n", encoding="utf-8")
        detached = self.repository / ".agent-skills-detached"
        original_open = os.open
        original_symlink_to = Path.symlink_to
        swapped = False

        def swap_parent() -> None:
            nonlocal swapped
            if swapped:
                return
            os.rename(self.repository / ".agent-skills", detached)
            os.symlink(outside, self.repository / ".agent-skills", target_is_directory=True)
            swapped = True

        def swapping_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
            descriptor = original_open(path, flags, *args, **kwargs)
            if path == ".agent-skills" and kwargs.get("dir_fd") is not None and not swapped:
                swap_parent()
            return descriptor

        def swapping_symlink_to(path: Path, *args: object, **kwargs: object) -> None:
            if path.name.startswith(".current."):
                swap_parent()
            original_symlink_to(path, *args, **kwargs)

        with (
            mock.patch("agent_skills.hosts.exposure.os.open", side_effect=swapping_open),
            mock.patch.object(Path, "symlink_to", new=swapping_symlink_to),
        ):
            with self.assertRaises(ValueError):
                lifecycle.update(*self.v2, maintainer=True)
        self.assertTrue(swapped)
        self.assertFalse(outside_current.is_symlink())
        self.assertEqual(outside_current.read_text(encoding="utf-8"), "preserve\n")

    @unittest.skipUnless(
        all(
            function in os.supports_dir_fd
            for function in (os.open, os.readlink, os.rename, os.symlink, os.unlink)
        ),
        "directory-descriptor projection removal is not supported on this host",
    )
    def test_shared_switch_parent_swap_cannot_redirect_removal(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        outside = self.base / "external-removal-root"
        outside.mkdir()
        outside_current = outside / "current"
        outside_current.write_text("preserve\n", encoding="utf-8")
        detached = self.repository / ".agent-skills-detached"
        original_open = os.open
        original_unlink = Path.unlink
        swapped = False

        def swap_parent() -> None:
            nonlocal swapped
            if swapped:
                return
            os.rename(self.repository / ".agent-skills", detached)
            os.symlink(outside, self.repository / ".agent-skills", target_is_directory=True)
            swapped = True

        def swapping_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
            descriptor = original_open(path, flags, *args, **kwargs)
            if path == ".agent-skills" and kwargs.get("dir_fd") is not None and not swapped:
                swap_parent()
            return descriptor

        def swapping_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path == lifecycle.exposure.current_path:
                swap_parent()
            original_unlink(path, *args, **kwargs)

        with (
            mock.patch("agent_skills.hosts.exposure.os.open", side_effect=swapping_open),
            mock.patch.object(Path, "unlink", new=swapping_unlink),
        ):
            with self.assertRaises(ValueError):
                lifecycle.remove(maintainer=True)
        self.assertTrue(swapped)
        self.assertFalse(outside_current.is_symlink())
        self.assertEqual(outside_current.read_text(encoding="utf-8"), "preserve\n")


if __name__ == "__main__":
    unittest.main()
