from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Callable

from .fs import atomic_write
from .validate import canonical_json


SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
OWNERSHIP = "agent-skills-managed"
PREDECESSOR_SCHEMA = "agent-skills-predecessor/1"
OPERATION_ID = re.compile(r"^[a-f0-9]{32}$")
DeleteFault = Callable[[str], None]


class AdoptionError(ValueError):
    pass


def validate_adoption(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "predecessorHosts",
        "predecessorSha256",
        "skill",
    }:
        raise AdoptionError("receipt-invalid: adoption record is malformed")
    if not isinstance(value["skill"], str) or SKILL_NAME.fullmatch(value["skill"]) is None:
        raise AdoptionError("receipt-invalid: adopted skill name is unsafe")
    if (
        not isinstance(value["predecessorSha256"], str)
        or DIGEST.fullmatch(value["predecessorSha256"]) is None
    ):
        raise AdoptionError("receipt-invalid: predecessor digest is malformed")
    hosts = value["predecessorHosts"]
    if (
        not isinstance(hosts, list)
        or not hosts
        or hosts != sorted(hosts)
        or len(hosts) != len(set(hosts))
        or not set(hosts).issubset({"claude-code", "codex"})
        or "codex" not in hosts
    ):
        raise AdoptionError("receipt-invalid: predecessor hosts are malformed")
    return value


def skill_body(path: Path) -> bytes:
    if path.is_symlink() or not path.is_dir():
        raise AdoptionError("adoption-prior-unsafe: predecessor must be a skill directory")
    entries = list(path.iterdir())
    body = path / "SKILL.md"
    if len(entries) != 1 or entries[0].name != "SKILL.md" or body.is_symlink():
        raise AdoptionError(
            "adoption-prior-unsafe: predecessor must contain only a regular SKILL.md"
        )
    try:
        mode = body.stat(follow_symlinks=False).st_mode
        contents = body.read_bytes()
    except OSError as error:
        raise AdoptionError("adoption-prior-unsafe: predecessor is unreadable") from error
    if not stat.S_ISREG(mode):
        raise AdoptionError("adoption-prior-unsafe: predecessor body is not regular")
    return contents


def body_sha256(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


class PredecessorStore:
    def __init__(self, managed_root: Path, private_root: Path) -> None:
        self.root = managed_root / "predecessors"
        self.staging_root = private_root / "predecessors"
        self.trash_root = private_root / "predecessor-trash"

    def path(self, adoption: dict) -> Path:
        validate_adoption(adoption)
        return self.root / adoption["predecessorSha256"]

    def _staging_path(self, operation_id: str) -> Path:
        if OPERATION_ID.fullmatch(operation_id) is None:
            raise AdoptionError("journal-invalid: operation id is unsafe")
        return self.staging_root / operation_id

    def _trash_path(self, operation_id: str) -> Path:
        if OPERATION_ID.fullmatch(operation_id) is None:
            raise AdoptionError("journal-invalid: operation id is unsafe")
        return self.trash_root / operation_id

    @staticmethod
    def _marker(adoption: dict) -> dict:
        return {
            "ownership": OWNERSHIP,
            "schemaVersion": PREDECESSOR_SCHEMA,
            "sha256": adoption["predecessorSha256"],
            "skill": adoption["skill"],
        }

    def _verify_path(self, target: Path, adoption: dict) -> bytes:
        marker_path = target / ".agent-skills-predecessor.json"
        body_path = target / "SKILL.md"
        try:
            if target.is_symlink() or not target.is_dir():
                raise OSError
            entries = sorted(path.name for path in target.iterdir())
            if entries != [".agent-skills-predecessor.json", "SKILL.md"]:
                raise OSError
            if marker_path.is_symlink() or body_path.is_symlink():
                raise OSError
            marker_contents = marker_path.read_text(encoding="utf-8")
            marker = json.loads(marker_contents)
            body = body_path.read_bytes()
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise AdoptionError("local-divergence: predecessor is unreadable or unsafe") from error
        expected_marker = self._marker(adoption)
        if marker != expected_marker or marker_contents != canonical_json(marker):
            raise AdoptionError("local-divergence: predecessor ownership marker disagrees")
        if body_sha256(body) != adoption["predecessorSha256"]:
            raise AdoptionError("local-divergence: predecessor digest disagrees")
        return body

    def write(self, adoption: dict, contents: bytes, operation_id: str) -> None:
        validate_adoption(adoption)
        expected = adoption["predecessorSha256"]
        if body_sha256(contents) != expected:
            raise AdoptionError("adoption-prior-mismatch: predecessor digest disagrees")
        target = self.path(adoption)
        if target.exists() or target.is_symlink():
            self.verify(adoption)
            return
        stage = self._staging_path(operation_id)
        if os.path.lexists(stage):
            raise AdoptionError("local-divergence: predecessor staging already exists")
        if self.staging_root.is_symlink() or self.root.is_symlink():
            raise AdoptionError("local-divergence: predecessor root is unsafe")
        stage.mkdir(parents=True)
        atomic_write(stage / "SKILL.md", contents)
        atomic_write(
            stage / ".agent-skills-predecessor.json",
            canonical_json(self._marker(adoption)).encode(),
        )
        self._verify_path(stage, adoption)
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise AdoptionError("local-divergence: predecessor root is unsafe")
        os.replace(stage, target)
        if self.staging_root.exists() and not any(self.staging_root.iterdir()):
            self.staging_root.rmdir()
        self.verify(adoption)

    def verify(self, adoption: dict) -> bytes:
        target = self.path(adoption)
        if self.root.is_symlink():
            raise AdoptionError("local-divergence: predecessor root is unsafe")
        return self._verify_path(target, adoption)

    def cleanup_staging(self, operation_id: str) -> None:
        stage = self._staging_path(operation_id)
        if not os.path.lexists(stage):
            return
        if self.staging_root.is_symlink() or stage.is_symlink() or not stage.is_dir():
            raise AdoptionError("local-divergence: predecessor staging is unsafe")
        allowed = {".agent-skills-predecessor.json", "SKILL.md"}
        entries = list(stage.iterdir())
        if any(path.name not in allowed or path.is_symlink() or not path.is_file() for path in entries):
            raise AdoptionError("local-divergence: predecessor staging is unsafe")
        for path in entries:
            path.unlink()
        stage.rmdir()
        if self.staging_root.exists() and not any(self.staging_root.iterdir()):
            self.staging_root.rmdir()

    def _verify_partial_trash(self, trash: Path, adoption: dict) -> None:
        if self.trash_root.is_symlink() or trash.is_symlink() or not trash.is_dir():
            raise AdoptionError("local-divergence: predecessor trash is unsafe")
        allowed = {".agent-skills-predecessor.json", "SKILL.md"}
        entries = list(trash.iterdir())
        if any(path.name not in allowed or path.is_symlink() or not path.is_file() for path in entries):
            raise AdoptionError("local-divergence: predecessor trash is unsafe")
        body = trash / "SKILL.md"
        if body.exists() and body_sha256(body.read_bytes()) != adoption["predecessorSha256"]:
            raise AdoptionError("local-divergence: predecessor trash digest disagrees")
        marker_path = trash / ".agent-skills-predecessor.json"
        if marker_path.exists():
            try:
                contents = marker_path.read_text(encoding="utf-8")
                marker = json.loads(contents)
            except (OSError, UnicodeDecodeError, ValueError) as error:
                raise AdoptionError("local-divergence: predecessor trash is unreadable") from error
            if marker != self._marker(adoption) or contents != canonical_json(marker):
                raise AdoptionError("local-divergence: predecessor trash marker disagrees")

    def _finish_trash(
        self,
        trash: Path,
        adoption: dict,
        fault: DeleteFault | None,
    ) -> None:
        self._verify_partial_trash(trash, adoption)
        fault_fired = False
        for name in ("SKILL.md", ".agent-skills-predecessor.json"):
            path = trash / name
            if not path.exists():
                continue
            path.unlink()
            if fault is not None and not fault_fired:
                fault_fired = True
                fault("predecessor-delete-started")
        trash.rmdir()
        if self.trash_root.exists() and not any(self.trash_root.iterdir()):
            self.trash_root.rmdir()

    def remove(
        self,
        adoption: dict,
        operation_id: str,
        fault: DeleteFault | None = None,
    ) -> None:
        target = self.path(adoption)
        trash = self._trash_path(operation_id)
        if os.path.lexists(trash):
            if os.path.lexists(target):
                raise AdoptionError(
                    "local-divergence: predecessor exists in active and retired storage"
                )
            self._finish_trash(trash, adoption, fault)
            return
        if not os.path.lexists(target):
            return
        self.verify(adoption)
        if self.trash_root.is_symlink():
            raise AdoptionError("local-divergence: predecessor trash is unsafe")
        trash.parent.mkdir(parents=True, exist_ok=True)
        if self.trash_root.is_symlink():
            raise AdoptionError("local-divergence: predecessor trash is unsafe")
        os.replace(target, trash)
        if self.root.exists() and not any(self.root.iterdir()):
            self.root.rmdir()
        self._finish_trash(trash, adoption, fault)

    def exists(self) -> bool:
        return self.root.exists() or self.root.is_symlink()


def restore_skill(path: Path, contents: bytes) -> None:
    if os.path.lexists(path):
        if path.is_symlink():
            path.unlink()
        elif path.is_dir() and skill_body(path) == contents:
            return
        else:
            raise AdoptionError("exposure-conflict: predecessor path changed")
    path.mkdir(parents=True)
    atomic_write(path / "SKILL.md", contents)
