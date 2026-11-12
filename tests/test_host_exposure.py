from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_skills.hosts.exposure import HOSTS, _normalized_link
from agent_skills.lifecycle import Lifecycle

from lifecycle_support import (
    HOOK_SKILL,
    LifecycleFixture,
    build_hooked_version,
    hooked_corpus,
    initialize_repository,
    unrelated_digest,
)


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



STOP_COMMAND = 'python3 -B "${CLAUDE_PROJECT_DIR}/.agent-skills/current/__main__.py" hook claude-code stop || exit 1'
START_COMMAND = (
    'python3 -B "${CLAUDE_PROJECT_DIR}/.agent-skills/current/__main__.py" hook claude-code session-start || exit 1'
)
CLAUDE_EVENTS = {"stop": ("Stop", STOP_COMMAND), "session-start": ("SessionStart", START_COMMAND)}
USER_STOP_ENTRY = {"hooks": [{"command": "echo mine", "type": "command"}]}


def owned_entry(event: str) -> dict:
    return {"hooks": [{"type": "command", "command": CLAUDE_EVENTS[event][1], "timeout": 30}]}


class ClaudeHookExposureTests(LifecycleFixture, unittest.TestCase):
    """Decision 4: the lifecycle installs, records and removes one dispatcher entry per Claude Code event."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.releases_folder = tempfile.TemporaryDirectory()
        base = Path(cls.releases_folder.name)
        stop = hooked_corpus(base / "corpus-stop", ("stop",))
        both = hooked_corpus(base / "corpus-both", ("stop", "session-start"))
        cls.stop_a = build_hooked_version(stop, base, "core-stop-a")
        cls.stop_b = build_hooked_version(stop, base, "core-stop-b")
        cls.both = build_hooked_version(both, base, "core-both")
        codex = hooked_corpus(base / "corpus-codex", ("stop",), hosts=("codex",))
        cls.codex_only = build_hooked_version(codex, base, "core-codex-only")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.releases_folder.cleanup()

    # Helpers ---------------------------------------------------------------

    def fresh(self) -> None:
        """Point the row at a new repository of its own, for one sub-case."""
        self.fresh_count = getattr(self, "fresh_count", 0) + 1
        self.repository = initialize_repository(self.base / f"fresh-{self.fresh_count}")

    @property
    def settings(self) -> Path:
        return self.repository / ".claude" / "settings.json"

    def read_settings(self) -> object:
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def write_settings(self, value: object) -> None:
        self.settings.parent.mkdir(exist_ok=True)
        self.settings.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def receipt(self) -> dict:
        return json.loads((self.repository / ".agent-skills" / "active.json").read_text(encoding="utf-8"))

    def receipt_events(self) -> list[str]:
        marker = self.repository / ".agent-skills" / "active.json"
        if not marker.exists():
            return []
        return self.receipt().get("hooks", {}).get("events", [])

    def owned_events(self) -> list[str]:
        if not self.settings.exists():
            return []
        hooks = self.read_settings().get("hooks", {})
        return sorted(
            event
            for event, (name, _) in CLAUDE_EVENTS.items()
            if owned_entry(event) in hooks.get(name, [])
        )

    def without_owned(self, document: dict) -> dict:
        """The settings with every owned entry taken out, and nothing else changed."""
        document = json.loads(json.dumps(document))
        for event, (name, _) in CLAUDE_EVENTS.items():
            entries = document.get("hooks", {}).get(name)
            if isinstance(entries, list):
                document["hooks"][name] = [entry for entry in entries if entry != owned_entry(event)]
        return document

    # 1. Install --------------------------------------------------------------

    def test_a_fresh_install_writes_the_stop_entry_and_records_it(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.stop_a, maintainer=True)

        self.assertEqual(self.read_settings(), {"hooks": {"Stop": [owned_entry("stop")]}})
        text = self.settings.read_text(encoding="utf-8")
        self.assertEqual(text, json.dumps(self.read_settings(), indent=2, ensure_ascii=False) + "\n")
        receipt = self.receipt()
        self.assertEqual(receipt["hooks"], {"created": True, "events": ["stop"], "file": ".claude/settings.json"})
        self.assertIn(".claude", receipt["createdDirectories"])
        self.assertEqual(lifecycle.status().as_dict()["blockers"], [])

    def test_an_install_keeps_every_other_key_and_entry(self) -> None:
        user = {
            "permissions": {"allow": ["Bash(ls:*)"]},
            "hooks": {"Stop": [USER_STOP_ENTRY], "PreToolUse": [{"matcher": "Bash", "hooks": []}]},
            "env": {"A": "1"},
        }
        self.write_settings(user)
        Lifecycle(self.repository).install(*self.stop_a, maintainer=True)

        after = self.read_settings()
        self.assertEqual(self.without_owned(after), user)
        self.assertEqual(after["hooks"]["Stop"], [USER_STOP_ENTRY, owned_entry("stop")])
        self.assertEqual(list(after), list(user), "the user's key order is kept")
        self.assertEqual(self.receipt()["hooks"]["created"], False)
        self.assertNotIn(".claude", self.receipt()["createdDirectories"])

    def test_a_release_with_no_hooks_writes_nothing_and_records_nothing(self) -> None:
        Lifecycle(self.repository).install(*self.v1, maintainer=True)
        self.assertFalse(self.settings.exists())
        self.assertNotIn("hooks", self.receipt())

    def test_a_hook_declared_only_for_codex_is_never_installed(self) -> None:
        lifecycle = Lifecycle(self.repository)
        self.assertEqual(lifecycle.plan(*self.codex_only)["hooks"], {"added": [], "removed": []})
        lifecycle.install(*self.codex_only, maintainer=True)
        self.assertFalse(self.settings.exists())
        self.assertNotIn("hooks", self.receipt())

    # 2. Update, remove and rollback -----------------------------------------

    def test_an_update_to_no_hooks_and_a_removal_each_take_out_both_entries(self) -> None:
        for finish in ("update", "remove"):
            with self.subTest(finish=finish):
                self.fresh()
                user = {"hooks": {"Stop": [USER_STOP_ENTRY], "SessionStart": [USER_STOP_ENTRY]}}
                self.write_settings(user)
                lifecycle = Lifecycle(self.repository)
                lifecycle.install(*self.both, maintainer=True)
                self.assertEqual(self.owned_events(), ["session-start", "stop"])
                self._install_then(finish, lifecycle)
                self.assertEqual(self.read_settings(), user)
                self.assertEqual(self.owned_events(), [])

    def test_an_update_that_adds_session_start_adds_only_that_entry(self) -> None:
        self.write_settings({"hooks": {"Stop": [USER_STOP_ENTRY]}})
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.stop_a, maintainer=True)
        before = self.read_settings()
        lifecycle.update(*self.both, maintainer=True)

        after = self.read_settings()
        self.assertEqual(after["hooks"]["Stop"], before["hooks"]["Stop"])
        self.assertEqual(after["hooks"]["SessionStart"], [owned_entry("session-start")])
        self.assertEqual({**after, "hooks": {"Stop": after["hooks"]["Stop"]}}, before)
        self.assertEqual(self.receipt()["hooks"]["events"], ["session-start", "stop"])

    def test_an_update_between_generations_with_the_same_events_leaves_the_file_byte_identical(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.stop_a, maintainer=True)
        before = self.settings.read_bytes()
        stat_before = self.settings.stat()
        lifecycle.update(*self.stop_b, maintainer=True)
        self.assertEqual(self.receipt()["release"]["releaseId"], "core-stop-b")
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(self.settings.stat().st_ino, stat_before.st_ino, "the file is not rewritten")

    def _install_then(self, finish: str, lifecycle: Lifecycle) -> None:
        if finish == "update":
            lifecycle.update(*self.v1, maintainer=True)
        else:
            lifecycle.remove(maintainer=True)

    def test_a_user_entry_beside_the_owned_one_is_kept_after_an_update_and_after_a_removal(self) -> None:
        for finish in ("update", "remove"):
            with self.subTest(finish=finish):
                self.fresh()
                user = {"hooks": {"Stop": [USER_STOP_ENTRY]}}
                self.write_settings(user)
                lifecycle = Lifecycle(self.repository)
                lifecycle.install(*self.stop_a, maintainer=True)
                self._install_then(finish, lifecycle)
                self.assertEqual(self.read_settings(), user)

    def test_a_created_file_with_a_key_the_user_added_is_kept_with_that_key(self) -> None:
        for finish in ("update", "remove"):
            with self.subTest(finish=finish):
                self.fresh()
                lifecycle = Lifecycle(self.repository)
                lifecycle.install(*self.stop_a, maintainer=True)
                self.assertTrue(self.receipt()["hooks"]["created"])
                self.write_settings({**self.read_settings(), "model": "opus"})
                self._install_then(finish, lifecycle)
                self.assertEqual(self.read_settings(), {"model": "opus"})

    def test_a_file_the_lifecycle_did_not_create_is_kept_when_it_is_left_empty(self) -> None:
        for finish in ("update", "remove"):
            with self.subTest(finish=finish):
                self.fresh()
                self.write_settings({})
                lifecycle = Lifecycle(self.repository)
                lifecycle.install(*self.stop_a, maintainer=True)
                self.assertFalse(self.receipt()["hooks"]["created"])
                self._install_then(finish, lifecycle)
                self.assertEqual(self.read_settings(), {})

    def test_a_created_file_left_empty_is_deleted(self) -> None:
        for finish in ("update", "remove"):
            with self.subTest(finish=finish):
                self.fresh()
                lifecycle = Lifecycle(self.repository)
                lifecycle.install(*self.stop_a, maintainer=True)
                self._install_then(finish, lifecycle)
                self.assertFalse(self.settings.exists())
                if finish == "update":
                    self.assertNotIn("hooks", self.receipt())
                    self.assertTrue((self.repository / ".claude" / "skills").is_dir())
                else:
                    self.assertFalse((self.repository / ".claude").exists())

    def test_a_kept_created_file_is_still_deleted_once_it_is_left_empty(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.stop_a, maintainer=True)
        self.write_settings({**self.read_settings(), "model": "opus"})
        lifecycle.update(*self.v1, maintainer=True)
        self.assertEqual(self.receipt()["hooks"], {"created": True, "events": [], "file": ".claude/settings.json"})
        self.write_settings({})
        lifecycle.remove(maintainer=True)
        self.assertFalse(self.settings.exists())

    def test_a_rollback_restores_the_prior_receipts_entries(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.stop_a, maintainer=True)
        lifecycle.update(*self.both, maintainer=True)
        self.assertEqual(self.owned_events(), ["session-start", "stop"])
        lifecycle.rollback(maintainer=True)
        self.assertEqual(self.receipt()["release"]["releaseId"], "core-stop-a")
        self.assertEqual(self.owned_events(), ["stop"])
        self.assertEqual(self.read_settings(), {"hooks": {"Stop": [owned_entry("stop")]}})

        lifecycle.update(*self.v1, maintainer=True)
        self.assertEqual(self.owned_events(), [])
        lifecycle.rollback(maintainer=True)
        self.assertEqual(self.owned_events(), ["stop"])
        self.assertEqual(self.receipt_events(), ["stop"])

    # 3. Recovery -------------------------------------------------------------

    def test_a_fault_at_every_phase_recovers_to_exactly_the_committed_events(self) -> None:
        from agent_skills.lifecycle import ACTIVATION_BOUNDARIES

        self.assertIn("exposing-claude-code-hooks", ACTIVATION_BOUNDARIES)
        directions = {"none-to-stop": (self.v1, self.stop_a), "stop-to-none": (self.stop_a, self.v1)}
        for direction, (start, finish) in directions.items():
            for boundary in ACTIVATION_BOUNDARIES:
                with self.subTest(direction=direction, boundary=boundary):
                    self.fresh()
                    lifecycle = Lifecycle(self.repository)
                    lifecycle.install(*start, maintainer=True)

                    def interrupt(phase: str, boundary: str = boundary) -> None:
                        if phase == boundary:
                            raise RuntimeError(f"interrupted:{phase}")

                    with self.assertRaisesRegex(RuntimeError, f"interrupted:{boundary}"):
                        lifecycle.update(*finish, maintainer=True, fault=interrupt)
                    lifecycle.recover(maintainer=True)
                    self.assertEqual(lifecycle.recover(maintainer=True)["action"], "no-op")
                    self.assertEqual(self.owned_events(), self.receipt_events())
                    self.assertEqual(lifecycle.status().as_dict()["blockers"], [])

    def test_a_fault_while_removing_or_installing_recovers_to_the_committed_events(self) -> None:
        from agent_skills.lifecycle import ACTIVATION_BOUNDARIES

        for operation in ("install", "remove"):
            for boundary in ACTIVATION_BOUNDARIES:
                with self.subTest(operation=operation, boundary=boundary):
                    self.fresh()
                    lifecycle = Lifecycle(self.repository)
                    if operation == "remove":
                        lifecycle.install(*self.stop_a, maintainer=True)

                    def interrupt(phase: str, boundary: str = boundary) -> None:
                        if phase == boundary:
                            raise RuntimeError(f"interrupted:{phase}")

                    with self.assertRaisesRegex(RuntimeError, f"interrupted:{boundary}"):
                        if operation == "install":
                            lifecycle.install(*self.stop_a, maintainer=True, fault=interrupt)
                        else:
                            lifecycle.remove(maintainer=True, fault=interrupt)
                    lifecycle.recover(maintainer=True)
                    self.assertEqual(self.owned_events(), self.receipt_events())
                    if not (self.repository / ".agent-skills" / "active.json").exists():
                        self.assertFalse(self.settings.exists(), "a created file left empty is deleted")

    def test_recovery_completing_the_target_writes_the_entries_it_commits(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)

        def interrupt(phase: str) -> None:
            if phase == "marker-committed":
                raise RuntimeError("interrupted")

        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            lifecycle.update(*self.stop_a, maintainer=True, fault=interrupt)
        self.settings.unlink()
        self.assertEqual(lifecycle.recover(maintainer=True)["action"], "complete-current")
        self.assertEqual(self.receipt_events(), ["stop"])
        self.assertEqual(self.read_settings(), {"hooks": {"Stop": [owned_entry("stop")]}})

    def test_an_unfinished_operation_over_a_malformed_settings_file_is_a_conflict_to_review(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)

        def interrupt(phase: str) -> None:
            if phase == "exposing-claude-code":
                raise RuntimeError("interrupted")

        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            lifecycle.update(*self.stop_a, maintainer=True, fault=interrupt)
        self.write_settings([])
        status = lifecycle.status().as_dict()
        self.assertEqual((status["blockers"], status["recoveryPlan"]), (["exposure-conflict"], ["review-host-projections"]))
        with self.assertRaisesRegex(ValueError, "exposure-conflict: .claude/settings.json is not a JSON object"):
            lifecycle.recover(maintainer=True)
        self.write_settings({"model": "opus"})
        self.assertEqual(lifecycle.recover(maintainer=True)["action"], "restore-prior")
        self.assertEqual(self.read_settings(), {"model": "opus"})

    # 4. Refusals -------------------------------------------------------------

    def test_preflight_refuses_each_malformed_settings_file_naming_the_fix(self) -> None:
        altered = owned_entry("stop")
        altered["hooks"][0]["timeout"] = 10
        cases = {
            "not an object": ([], "is not a JSON object"),
            "not JSON": ("{not json", "is not a JSON object"),
            "hooks not an object": ({"hooks": []}, '"hooks" is not an object'),
            "event list not a list": ({"hooks": {"Stop": {}}}, '"hooks.Stop" is not a list'),
            "altered owned entry": ({"hooks": {"Stop": [altered]}},
                                    '"hooks.Stop" holds an entry running the agent-skills dispatcher that differs'),
        }
        for name, (contents, message) in cases.items():
            with self.subTest(case=name):
                self.fresh()
                self.settings.parent.mkdir(exist_ok=True)
                if isinstance(contents, str):
                    self.settings.write_text(contents, encoding="utf-8")
                else:
                    self.write_settings(contents)
                before = self.settings.read_bytes()
                with self.assertRaisesRegex(ValueError, f"exposure-conflict: .claude/settings.json {message}.*;"):
                    Lifecycle(self.repository).install(*self.stop_a, maintainer=True)
                self.assertEqual(self.settings.read_bytes(), before)
                self.assertFalse((self.repository / ".agent-skills").exists())
                self.assertFalse((self.repository / ".claude" / "skills").exists())

    def test_a_settings_change_between_preflight_and_the_write_ends_at_the_prior_endpoint(self) -> None:
        # Each racer's file is one restoring the prior entries with settings reconciled would change or refuse:
        # not an object, and an owned entry the prior generation doesn't declare.
        racer = {"rewrite": {"racer": True}, "non-object": [], "owned entry": {"hooks": {"Stop": [owned_entry("stop")]}}}
        racer_bytes = {name: (json.dumps(value, indent=2) + "\n").encode() for name, value in racer.items()}
        for start, change in ((None, "rewrite"), ("v1", "rewrite"), ("v1", "delete"), ("v1", "non-object"),
                              ("v1", "owned entry")):
            with self.subTest(start=start, change=change):
                self.fresh()
                lifecycle = Lifecycle(self.repository)
                if change == "delete":
                    self.write_settings({"model": "opus"})
                if start:
                    lifecycle.install(*self.v1, maintainer=True)
                prior_receipt = (self.repository / ".agent-skills" / "active.json").read_bytes() if start else None
                prior_links = {
                    host.root.as_posix(): sorted(
                        (path.name, os.readlink(path)) for path in (self.repository / host.root).iterdir()
                    ) if (self.repository / host.root).exists() else None
                    for host in HOSTS
                }
                prior_current = (
                    os.readlink(self.repository / ".agent-skills" / "current") if start else None
                )

                def race(phase: str, change: str = change) -> None:
                    if phase != "exposing-claude-code":
                        return
                    if change == "delete":
                        self.settings.unlink()
                    else:
                        self.write_settings(racer[change])

                with self.assertRaisesRegex(ValueError, "exposure-conflict: .claude/settings.json changed"):
                    lifecycle.update(*self.stop_a, maintainer=True, fault=race) if start else lifecycle.install(
                        *self.stop_a, maintainer=True, fault=race)
                if change == "delete":
                    self.assertFalse(self.settings.exists(), "the deletion stands")
                else:
                    self.assertEqual(self.settings.read_bytes(), racer_bytes[change], "the racer's file is untouched")
                self.assertFalse(lifecycle.journal_path.exists())
                if start:
                    self.assertEqual((self.repository / ".agent-skills" / "active.json").read_bytes(), prior_receipt)
                    self.assertEqual(os.readlink(self.repository / ".agent-skills" / "current"), prior_current)
                else:
                    self.assertEqual(lifecycle.status().as_dict()["lifecycle"], "absent")
                links = {
                    host.root.as_posix(): sorted(
                        (path.name, os.readlink(path)) for path in (self.repository / host.root).iterdir()
                    ) if (self.repository / host.root).exists() else None
                    for host in HOSTS
                }
                self.assertEqual(links, prior_links)

    def test_a_settings_change_before_the_deletion_refuses_and_keeps_the_racers_file(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.stop_a, maintainer=True)
        self.assertTrue(self.receipt()["hooks"]["created"])
        prior_receipt = (self.repository / ".agent-skills" / "active.json").read_bytes()
        racer = {"model": "opus", "hooks": {"Stop": [owned_entry("stop")]}}

        def race(phase: str) -> None:
            if phase == "exposing-claude-code":
                self.write_settings(racer)

        with self.assertRaisesRegex(ValueError, "exposure-conflict: .claude/settings.json changed"):
            lifecycle.update(*self.v1, maintainer=True, fault=race)
        self.assertEqual(self.read_settings(), racer)
        self.assertEqual((self.repository / ".agent-skills" / "active.json").read_bytes(), prior_receipt)
        self.assertFalse(lifecycle.journal_path.exists())

    def test_preflight_refuses_a_settings_path_that_is_not_a_plain_file(self) -> None:
        cases = ("settings is a symlink", "settings is a directory", ".claude is a symlink", ".claude is a file")
        messages = {
            "settings is a symlink": "exposure-conflict: .claude/settings.json is a symlink; replace it",
            "settings is a directory": "exposure-conflict: .claude/settings.json is not a file; replace it",
            ".claude is a symlink": "exposure-conflict: .claude is not a directory; make it a plain directory",
            ".claude is a file": "exposure-conflict: .claude is not a directory; make it a plain directory",
        }
        for case in cases:
            with self.subTest(case=case):
                self.fresh()
                elsewhere = self.base / f"elsewhere-{self.fresh_count}"
                elsewhere.mkdir()
                (elsewhere / "settings.json").write_text("{}\n", encoding="utf-8")
                folder = self.repository / ".claude"
                if case == "settings is a symlink":
                    folder.mkdir()
                    self.settings.symlink_to(elsewhere / "settings.json")
                elif case == "settings is a directory":
                    self.settings.mkdir(parents=True)
                elif case == ".claude is a symlink":
                    folder.symlink_to(elsewhere, target_is_directory=True)
                else:
                    folder.write_text("not a folder\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, messages[case]):
                    Lifecycle(self.repository).install(*self.stop_a, maintainer=True)
                self.assertFalse((self.repository / ".agent-skills").exists())
                self.assertEqual((elsewhere / "settings.json").read_text(encoding="utf-8"), "{}\n")

    def test_writing_entries_keeps_the_files_mode(self) -> None:
        self.write_settings({"model": "opus"})
        self.settings.chmod(0o640)
        Lifecycle(self.repository).install(*self.stop_a, maintainer=True)
        self.assertEqual(self.settings.stat().st_mode & 0o7777, 0o640)
        self.assertEqual(self.owned_events(), ["stop"])

        self.fresh()
        Lifecycle(self.repository).install(*self.stop_a, maintainer=True)
        self.assertEqual(self.settings.stat().st_mode & 0o7777, 0o644, "a new file is not left private")

    # 5. The installed command ------------------------------------------------

    def _run_installed(self, command: str) -> subprocess.CompletedProcess[str]:
        environment = {
            key: value for key, value in os.environ.items()
            if key not in {"CLAUDE_PROJECT_DIR", "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"}
        }
        environment["CLAUDE_PROJECT_DIR"] = str(self.repository)
        environment["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), environment.get("PATH", "")])
        payload = {"cwd": str(self.repository), "hook_event_name": "Stop", "session_id": "s",
                   "stop_hook_active": False}
        return subprocess.run(["sh", "-c", command], input=json.dumps(payload), capture_output=True, text=True,
                              env=environment, cwd=self.base, timeout=60)

    def test_the_installed_command_runs_the_dispatcher_and_never_exits_2(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.stop_a, maintainer=True)
        command = self.read_settings()["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertEqual(command, STOP_COMMAND)

        held = self._run_installed(command)
        self.assertEqual(held.returncode, 0, held.stderr)
        self.assertEqual(json.loads(held.stdout), {"decision": "block", "reason": "held by the fixture"})
        self.assertEqual(lifecycle.status().as_dict()["blockers"], [], "the run wrote no bytecode")

        current = self.repository / ".agent-skills" / "current"
        target = os.readlink(current)
        current.unlink()
        missing = self._run_installed(command)
        self.assertEqual((missing.returncode, missing.stdout), (1, ""))

        older = self.repository / ".agent-skills" / "generations" / ("e" * 64)
        older.mkdir()
        (older / "__main__.py").write_text(
            "import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument('command', choices=['status'])\n"
            "parser.parse_args()\n",
            encoding="utf-8",
        )
        current.symlink_to(f"generations/{'e' * 64}")
        without_hook = self._run_installed(command)
        self.assertEqual((without_hook.returncode, without_hook.stdout), (1, ""))
        self.assertIn("invalid choice", without_hook.stderr)
        current.unlink()
        current.symlink_to(target)

    # 6. Reports --------------------------------------------------------------

    def test_plan_and_diff_report_the_hook_events_added_and_removed(self) -> None:
        lifecycle = Lifecycle(self.repository)
        none = {"added": [], "removed": []}
        self.assertEqual(lifecycle.plan(*self.stop_a)["hooks"], {"added": ["stop"], "removed": []})
        self.assertEqual(lifecycle.diff(*self.stop_a)["hooks"], {"added": ["stop"], "removed": []})
        self.assertEqual(lifecycle.plan(*self.v1)["hooks"], none)
        lifecycle.install(*self.stop_a, maintainer=True)
        self.assertEqual(lifecycle.plan(*self.stop_a)["hooks"], none)
        self.assertEqual(lifecycle.plan(*self.stop_b)["hooks"], none)
        self.assertEqual(lifecycle.plan(*self.both)["hooks"], {"added": ["session-start"], "removed": []})
        self.assertEqual(lifecycle.diff(*self.both)["hooks"], {"added": ["session-start"], "removed": []})
        lifecycle.update(*self.both, maintainer=True)
        self.assertEqual(lifecycle.plan(*self.v1)["hooks"], {"added": [], "removed": ["session-start", "stop"]})
        self.assertEqual(lifecycle.diff(*self.v1)["hooks"], {"added": [], "removed": ["session-start", "stop"]})
        self.assertEqual(lifecycle.diff(*self.v1)["schemaVersion"], "agent-skills-diff/1")

    # 7. Divergence -----------------------------------------------------------

    def test_status_reports_hook_divergence_only_for_owned_entries(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.stop_a, maintainer=True)
        installed = self.read_settings()

        self.write_settings({"hooks": {"Stop": []}})
        status = lifecycle.status().as_dict()
        self.assertEqual(status["blockers"], ["local-divergence: hooks"])
        self.assertEqual(status["lifecycle"], "externally-transitioned")

        edited = owned_entry("stop")
        edited["hooks"][0]["timeout"] = 5
        self.write_settings({"hooks": {"Stop": [edited]}})
        self.assertEqual(lifecycle.status().as_dict()["blockers"], ["local-divergence: hooks"])

        renamed = owned_entry("stop")
        renamed["hooks"][0]["command"] = STOP_COMMAND.replace("|| exit 1", "")
        self.write_settings({"hooks": {"Stop": [renamed]}})
        self.assertEqual(lifecycle.status().as_dict()["blockers"], ["local-divergence: hooks"])

        self.write_settings({"theme": "dark", "hooks": {"Stop": [USER_STOP_ENTRY, *installed["hooks"]["Stop"]]}})
        status = lifecycle.status().as_dict()
        self.assertEqual(status["blockers"], [])
        self.assertEqual(status["lifecycle"], "current")

        self.write_settings([])
        self.assertEqual(lifecycle.status().as_dict()["blockers"], ["local-divergence: hooks"])

    def test_an_unreadable_settings_file_is_no_hook_divergence_when_no_entries_are_expected(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        for contents in ("[]\n", "{not json\n", '{"hooks": []}\n'):
            with self.subTest(contents=contents):
                self.settings.parent.mkdir(exist_ok=True)
                self.settings.write_text(contents, encoding="utf-8")
                status = lifecycle.status().as_dict()
                self.assertEqual((status["blockers"], status["lifecycle"]), ([], "current"))

    # 8. Adoption -------------------------------------------------------------

    def test_adopting_a_hook_bearing_skill_writes_no_entry_while_installing_it_does(self) -> None:
        prior = b"---\nname: compound-delivery-kernel\n---\n\nRepository posture.\n"
        codex = self.repository / ".agents" / "skills" / HOOK_SKILL / "SKILL.md"
        codex.parent.mkdir(parents=True)
        codex.write_bytes(prior)
        lifecycle = Lifecycle(self.repository)
        lifecycle.adopt(*self.stop_a, skill=HOOK_SKILL, expected_prior_sha256=hashlib.sha256(prior).hexdigest(),
                        maintainer=True)
        self.assertFalse(self.settings.exists())
        self.assertNotIn("hooks", self.receipt())
        self.assertEqual(lifecycle.plan(*self.stop_b)["hooks"], {"added": [], "removed": []})
        lifecycle.update(*self.stop_b, maintainer=True)
        self.assertFalse(self.settings.exists())
        self.assertNotIn("hooks", self.receipt())
        lifecycle.rollback(maintainer=True)
        self.assertEqual(self.receipt()["release"]["releaseId"], "core-stop-a")
        self.assertEqual(self.receipt()["adoption"]["skill"], HOOK_SKILL)
        self.assertFalse(self.settings.exists())
        self.assertNotIn("hooks", self.receipt())

        beside = initialize_repository(self.base / "beside")
        Lifecycle(beside).install(*self.stop_a, maintainer=True)
        self.assertEqual(
            json.loads((beside / ".claude" / "settings.json").read_text(encoding="utf-8")),
            {"hooks": {"Stop": [owned_entry("stop")]}},
        )


if __name__ == "__main__":
    unittest.main()
