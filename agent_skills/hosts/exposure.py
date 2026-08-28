from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from .base import HostProjection
from .claude import CLAUDE_CODE
from .codex import CODEX


HOSTS = (CODEX, CLAUDE_CODE)


class ExposureError(ValueError):
    pass


def exposure_records() -> list[dict[str, str]]:
    return [host.as_dict() for host in HOSTS]


def _skill_names(receipt: dict | None) -> tuple[str, ...]:
    if receipt is None:
        return ()
    names = {
        PurePosixPath(record["path"]).parts[1]
        for record in receipt["files"]
        if len(PurePosixPath(record["path"]).parts) >= 3
        and PurePosixPath(record["path"]).parts[0] == "skills"
        and PurePosixPath(record["path"]).parts[2] == "SKILL.md"
    }
    return tuple(sorted(names))


class ExposureManager:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root

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

    def _expected_target(
        self,
        host: HostProjection,
        generation: str,
        skill: str,
    ) -> str:
        target = (
            self.repository_root
            / ".agent-skills"
            / "generations"
            / generation
            / "skills"
            / skill
        )
        parent = self.repository_root / host.root
        return os.path.relpath(target, parent)

    def _matches(
        self,
        path: Path,
        host: HostProjection,
        receipt: dict | None,
        skill: str,
    ) -> bool:
        if receipt is None or skill not in _skill_names(receipt) or not path.is_symlink():
            return False
        try:
            return os.readlink(path) == self._expected_target(
                host,
                receipt["generation"],
                skill,
            )
        except OSError:
            return False

    def preflight(self, target: dict | None, alternate: dict | None) -> None:
        target_names = set(_skill_names(target))
        alternate_names = set(_skill_names(alternate))
        for host in HOSTS:
            root = self._root(host)
            for skill in sorted(target_names | alternate_names):
                path = root / skill
                if not os.path.lexists(path):
                    continue
                if self._matches(path, host, target, skill):
                    continue
                if self._matches(path, host, alternate, skill):
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
                    if not self._matches(path, host, alternate, skill):
                        raise ExposureError(
                            f"exposure-conflict: {host.host} projection "
                            f"{host.root / skill} changed after preflight"
                        )
                    path.unlink()
                continue
            if self._matches(path, host, target, skill):
                continue
            relative_target = self._expected_target(
                host,
                target["generation"],
                skill,
            )
            try:
                if os.path.lexists(path):
                    if not self._matches(path, host, alternate, skill):
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

    def reconcile(self, target: dict | None, alternate: dict | None) -> None:
        self.preflight(target, alternate)
        for host in HOSTS:
            self.reconcile_host(host, target, alternate)

    def status(self, receipt: dict | None) -> tuple[list[dict[str, str]], bool]:
        if receipt is None:
            return [], False
        names = _skill_names(receipt)
        records: list[dict[str, str]] = []
        conflicted = False
        for host in HOSTS:
            state = "current"
            try:
                root = self._root(host)
                for skill in names:
                    if not self._matches(root / skill, host, receipt, skill):
                        state = "conflict"
                        conflicted = True
                        break
            except ExposureError:
                state = "conflict"
                conflicted = True
            records.append({**host.as_dict(), "state": state})
        return records, conflicted
