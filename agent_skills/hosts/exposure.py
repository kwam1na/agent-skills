from __future__ import annotations

import os
import posixpath
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

from .base import HostProjection
from .claude import CLAUDE_CODE
from .codex import CODEX


HOSTS = (CODEX, CLAUDE_CODE)


class ExposureError(ValueError):
    pass


def exposure_records() -> list[dict[str, str]]:
    return [host.as_dict() for host in HOSTS]


def _skill_names(receipt: dict | None) -> tuple[str, ...]:
    if receipt is None or "exposures" not in receipt:
        return ()
    names: set[str] = set()
    for record in receipt["files"]:
        parts = PurePosixPath(record["path"]).parts
        if len(parts) >= 3 and parts[0] == "skills" and parts[2] == "SKILL.md":
            names.add(parts[1])
    return tuple(sorted(names))


def _normalized_link(value: str) -> str | None:
    if not value or "\x00" in value:
        return None
    normalized = value.replace("\\", "/") if os.name == "nt" else value
    if PurePosixPath(normalized).is_absolute() or PureWindowsPath(value).is_absolute():
        return None
    return posixpath.normpath(normalized)


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
                path.symlink_to(relative_target, target_is_directory=True)
            except OSError as error:
                raise ExposureError(
                    f"exposure-unavailable: {host.host} could not create a relative "
                    f"projection at {host.root / skill}"
                ) from error
        self._root(host)

    def switch(self, target: dict | None, alternate: dict | None) -> None:
        self.preflight(target, alternate)
        expected = self._expected_current_target(target)
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
            temporary.symlink_to(expected, target_is_directory=True)
            os.replace(temporary, self.current_path)
        except OSError as error:
            raise ExposureError(
                "exposure-unavailable: shared active projection could not be switched"
            ) from error
        finally:
            temporary.unlink(missing_ok=True)

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
