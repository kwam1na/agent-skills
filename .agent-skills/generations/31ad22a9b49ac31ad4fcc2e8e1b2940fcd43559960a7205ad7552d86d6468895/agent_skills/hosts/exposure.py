from __future__ import annotations

import os
import posixpath
import stat
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

from ..adoption import AdoptionError, body_sha256, restore_skill, skill_body
from .base import HostProjection
from .claude import CLAUDE_CODE
from .codex import CODEX


HOSTS = (CODEX, CLAUDE_CODE)
WINDOWS = os.name == "nt"


DESCRIPTOR_SWITCH_SUPPORTED = (
    all(
        function in os.supports_dir_fd
        for function in (
            os.open,
            os.mkdir,
            os.readlink,
            os.rename,
            os.stat,
            os.symlink,
            os.unlink,
        )
    )
    and os.stat in os.supports_follow_symlinks
)


class ExposureError(ValueError):
    pass


def exposure_records() -> list[dict[str, str]]:
    return [host.as_dict() for host in HOSTS]


def _skill_names(receipt: dict | None) -> tuple[str, ...]:
    if receipt is None or "exposures" not in receipt:
        return ()
    if "adoption" in receipt:
        return (receipt["adoption"]["skill"],)
    names: set[str] = set()
    for record in receipt["files"]:
        parts = PurePosixPath(record["path"]).parts
        if len(parts) >= 3 and parts[0] == "skills" and parts[2] == "SKILL.md":
            names.add(parts[1])
    return tuple(sorted(names))


def _normalized_link(value: str) -> str | None:
    if not value or "\x00" in value:
        return None
    normalized = value.replace("\\", "/") if WINDOWS else value
    if PurePosixPath(normalized).is_absolute() or PureWindowsPath(value).is_absolute():
        return None
    return posixpath.normpath(normalized)


def _runtime_link(value: str) -> str:
    return value.replace("/", "\\") if WINDOWS else value


class ExposureManager:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root
        self.managed_root = repository_root / ".agent-skills"
        self.current_path = self.managed_root / "current"

    def _root(self, host: HostProjection, *, create: bool = False) -> Path:
        current = self.repository_root
        for part in host.root.parts:
            current = current / part
            if current.is_symlink():
                raise ExposureError(
                    f"exposure-conflict: {host.host} discovery root is a symlink"
                )
            if current.exists() and not current.is_dir():
                raise ExposureError(
                    f"exposure-conflict: {host.host} discovery root is not a directory"
                )
            if create and not current.exists():
                current.mkdir()
        return current

    def _expected_host_target(
        self,
        host: HostProjection,
        skill: str,
    ) -> str:
        if host.root not in {CODEX.root, CLAUDE_CODE.root}:
            raise ExposureError("exposure-conflict: unsupported host discovery root")
        return f"../../.agent-skills/current/skills/{skill}"

    @staticmethod
    def _expected_current_target(receipt: dict | None) -> str | None:
        if receipt is None or "exposures" not in receipt:
            return None
        return f"generations/{receipt['generation']}"

    @staticmethod
    def _matches_link(path: Path, expected: str | None) -> bool:
        if expected is None or not path.is_symlink():
            return False
        try:
            return _normalized_link(os.readlink(path)) == expected
        except OSError:
            return False

    def _matches_host(
        self,
        path: Path,
        host: HostProjection,
        receipt: dict | None,
        skill: str,
    ) -> bool:
        if skill not in _skill_names(receipt):
            return False
        return self._matches_link(path, self._expected_host_target(host, skill))

    @staticmethod
    def _matches_predecessor(
        path: Path,
        host: HostProjection,
        receipt: dict | None,
        skill: str,
    ) -> bool:
        if receipt is None or receipt.get("adoption", {}).get("skill") != skill:
            return False
        adoption = receipt["adoption"]
        if host.host not in adoption["predecessorHosts"]:
            return not os.path.lexists(path)
        try:
            return body_sha256(skill_body(path)) == adoption["predecessorSha256"]
        except AdoptionError:
            return False

    def _matches_current(self, receipt: dict | None) -> bool:
        return self._matches_link(
            self.current_path,
            self._expected_current_target(receipt),
        )

    def preflight(self, target: dict | None, alternate: dict | None) -> None:
        target_names = set(_skill_names(target))
        alternate_names = set(_skill_names(alternate))
        if self.managed_root.is_symlink():
            raise ExposureError("exposure-conflict: shared projection root is a symlink")
        if os.path.lexists(self.current_path):
            if not self._matches_current(target) and not self._matches_current(alternate):
                raise ExposureError(
                    "exposure-conflict: shared active projection is user-owned, broken, or diverged"
                )
        for host in HOSTS:
            root = self._root(host)
            for skill in sorted(target_names | alternate_names):
                path = root / skill
                if not os.path.lexists(path):
                    continue
                if self._matches_host(path, host, target, skill):
                    continue
                if self._matches_host(path, host, alternate, skill):
                    continue
                if self._matches_predecessor(path, host, target, skill):
                    continue
                if self._matches_predecessor(path, host, alternate, skill):
                    continue
                raise ExposureError(
                    f"exposure-conflict: {host.host} projection {host.root / skill} "
                    "is user-owned, broken, or diverged"
                )

    def reconcile_host(
        self,
        host: HostProjection,
        target: dict | None,
        alternate: dict | None,
    ) -> None:
        self.preflight(target, alternate)
        target_names = set(_skill_names(target))
        alternate_names = set(_skill_names(alternate))
        root = self._root(host, create=bool(target_names))
        for skill in sorted(target_names | alternate_names):
            path = root / skill
            if skill not in target_names:
                if os.path.lexists(path):
                    if not self._matches_host(path, host, alternate, skill):
                        raise ExposureError(
                            f"exposure-conflict: {host.host} projection "
                            f"{host.root / skill} changed after preflight"
                        )
                    path.unlink()
                continue
            if self._matches_host(path, host, target, skill):
                continue
            relative_target = self._expected_host_target(host, skill)
            try:
                if os.path.lexists(path):
                    if not self._matches_host(path, host, alternate, skill):
                        raise ExposureError(
                            f"exposure-conflict: {host.host} projection "
                            f"{host.root / skill} changed after preflight"
                        )
                    path.unlink()
                path.symlink_to(
                    _runtime_link(relative_target),
                    target_is_directory=True,
                )
            except OSError as error:
                raise ExposureError(
                    f"exposure-unavailable: {host.host} could not create a relative "
                    f"projection at {host.root / skill}"
                ) from error
        self._root(host)

    def adopt_host(self, host: HostProjection, target: dict) -> None:
        adoption = target["adoption"]
        skill = adoption["skill"]
        root = self._root(host, create=True)
        path = root / skill
        if self._matches_host(path, host, target, skill):
            return
        if not self._matches_predecessor(path, host, target, skill):
            raise ExposureError(
                f"exposure-conflict: {host.host} predecessor changed after preflight"
            )
        if os.path.lexists(path):
            if path.is_symlink():
                path.unlink()
            else:
                try:
                    path.joinpath("SKILL.md").unlink()
                    path.rmdir()
                except OSError as error:
                    raise ExposureError(
                        f"exposure-conflict: {host.host} predecessor could not be retained"
                    ) from error
        path.symlink_to(
            _runtime_link(self._expected_host_target(host, skill)),
            target_is_directory=True,
        )

    def restore_adoption(self, receipt: dict, predecessor: bytes) -> None:
        adoption = receipt["adoption"]
        skill = adoption["skill"]
        for host in HOSTS:
            root = self._root(host, create=host.host in adoption["predecessorHosts"])
            path = root / skill
            if host.host in adoption["predecessorHosts"]:
                if self._matches_predecessor(path, host, receipt, skill):
                    continue
                if not self._matches_host(path, host, receipt, skill):
                    raise ExposureError(
                        f"exposure-conflict: {host.host} projection changed before restore"
                    )
                try:
                    restore_skill(path, predecessor)
                except AdoptionError as error:
                    raise ExposureError(str(error)) from error
            elif os.path.lexists(path):
                if not self._matches_host(path, host, receipt, skill):
                    raise ExposureError(
                        f"exposure-conflict: {host.host} projection changed before restore"
                    )
                path.unlink()
        self.switch(None, receipt)

    def verify_restored_adoption(self, receipt: dict) -> None:
        adoption = receipt["adoption"]
        skill = adoption["skill"]
        if os.path.lexists(self.current_path):
            raise ExposureError(
                "exposure-conflict: shared active projection remains during restore"
            )
        for host in HOSTS:
            root = self._root(host)
            if not self._matches_predecessor(root / skill, host, receipt, skill):
                raise ExposureError(
                    f"exposure-conflict: {host.host} predecessor is not restored"
                )

    def switch(self, target: dict | None, alternate: dict | None) -> None:
        self.preflight(target, alternate)
        expected = self._expected_current_target(target)
        alternate_expected = self._expected_current_target(alternate)
        if not DESCRIPTOR_SWITCH_SUPPORTED:
            if expected is None:
                if os.path.lexists(self.current_path):
                    if not self._matches_current(alternate):
                        raise ExposureError(
                            "exposure-conflict: shared active projection changed after preflight"
                        )
                    self.current_path.unlink()
                return
            if self._matches_current(target):
                return
            self.managed_root.mkdir(exist_ok=True)
            if self.managed_root.is_symlink():
                raise ExposureError("exposure-conflict: shared projection root is a symlink")
            temporary = self.managed_root / f".current.{uuid.uuid4().hex}.tmp"
            try:
                temporary.symlink_to(
                    _runtime_link(expected),
                    target_is_directory=True,
                )
                if self.managed_root.is_symlink():
                    raise ExposureError(
                        "exposure-conflict: shared projection root changed during switch"
                    )
                if WINDOWS and os.path.lexists(self.current_path):
                    if not self._matches_current(alternate):
                        raise ExposureError(
                            "exposure-conflict: shared active projection changed after preflight"
                        )
                    self.current_path.unlink()
                os.replace(temporary, self.current_path)
            except ExposureError:
                raise
            except OSError as error:
                raise ExposureError(
                    "exposure-unavailable: shared active projection could not be switched"
                ) from error
            finally:
                temporary.unlink(missing_ok=True)
            return

        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        try:
            root_descriptor = os.open(
                self.repository_root,
                directory_flags | no_follow,
            )
        except OSError as error:
            raise ExposureError(
                "exposure-unavailable: repository root could not be held for projection switch"
            ) from error
        try:
            try:
                os.mkdir(".agent-skills", mode=0o755, dir_fd=root_descriptor)
            except FileExistsError:
                pass
            try:
                managed_descriptor = os.open(
                    ".agent-skills",
                    directory_flags | no_follow,
                    dir_fd=root_descriptor,
                )
            except OSError as error:
                raise ExposureError(
                    "exposure-conflict: shared projection root is unsafe"
                ) from error
            try:
                held = os.fstat(managed_descriptor)
                current_root = os.stat(
                    ".agent-skills",
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISDIR(current_root.st_mode)
                    or (current_root.st_dev, current_root.st_ino)
                    != (held.st_dev, held.st_ino)
                ):
                    raise ExposureError(
                        "exposure-conflict: shared projection root changed during switch"
                    )

                try:
                    current = _normalized_link(
                        os.readlink("current", dir_fd=managed_descriptor)
                    )
                    current_exists = True
                except FileNotFoundError:
                    current = None
                    current_exists = False
                except OSError as error:
                    raise ExposureError(
                        "exposure-conflict: shared active projection changed after preflight"
                    ) from error

                if current_exists and current is None:
                    raise ExposureError(
                        "exposure-conflict: shared active projection changed after preflight"
                    )
                if current_exists and current == expected:
                    return
                if current_exists and current != alternate_expected:
                    raise ExposureError(
                        "exposure-conflict: shared active projection changed after preflight"
                    )
                if expected is None:
                    if current_exists:
                        os.unlink("current", dir_fd=managed_descriptor)
                        os.fsync(managed_descriptor)
                    return

                temporary = f".current.{uuid.uuid4().hex}.tmp"
                try:
                    os.symlink(
                        expected,
                        temporary,
                        target_is_directory=True,
                        dir_fd=managed_descriptor,
                    )
                    os.rename(
                        temporary,
                        "current",
                        src_dir_fd=managed_descriptor,
                        dst_dir_fd=managed_descriptor,
                    )
                    os.fsync(managed_descriptor)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=managed_descriptor)
                    except FileNotFoundError:
                        pass
            except ExposureError:
                raise
            except OSError as error:
                raise ExposureError(
                    "exposure-unavailable: shared active projection could not be switched"
                ) from error
            finally:
                os.close(managed_descriptor)
        finally:
            os.close(root_descriptor)

    def reconcile(self, target: dict | None, alternate: dict | None) -> None:
        self.preflight(target, alternate)
        if _skill_names(target):
            for host in HOSTS:
                self.reconcile_host(host, target, alternate)
            self.switch(target, alternate)
        else:
            self.switch(target, alternate)
            for host in HOSTS:
                self.reconcile_host(host, target, alternate)

    def status(self, receipt: dict | None) -> tuple[list[dict[str, str]], bool]:
        if receipt is None or "exposures" not in receipt:
            return [], False
        names = _skill_names(receipt)
        records: list[dict[str, str]] = []
        current_matches = self._matches_current(receipt)
        conflicted = not current_matches
        for host in HOSTS:
            state = "current" if current_matches else "conflict"
            try:
                root = self._root(host)
                for skill in names:
                    if not self._matches_host(root / skill, host, receipt, skill):
                        state = "conflict"
                        conflicted = True
                        break
            except ExposureError:
                state = "conflict"
                conflicted = True
            records.append({**host.as_dict(), "state": state})
        return records, conflicted
