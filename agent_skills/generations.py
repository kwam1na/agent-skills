from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Callable

from .fs import FileSafetyError, atomic_write, checked_files, contained_path
from .receipt import DIGEST, OWNERSHIP, validate_version
from .release import VerifiedRelease, verify_release
from .validate import canonical_json


GENERATION_MARKER = ".agent-skills-generation.json"
OPERATION_ID = re.compile(r"^[a-f0-9]{32}$")
CleanupFault = Callable[[str], None]


class GenerationError(ValueError):
    pass


def _records(files: dict[str, str]) -> list[dict[str, str]]:
    return [{"path": path, "sha256": digest} for path, digest in sorted(files.items())]


def _version_from_release(verified: VerifiedRelease, extracted: Path) -> dict:
    files = checked_files(extracted)
    version = {
        "files": _records(files),
        "generation": verified.archive_sha256,
        "release": {
            "archiveSha256": verified.archive_sha256,
            "metadataSha256": verified.metadata_sha256,
            "profile": verified.profile_id,
            "releaseId": verified.release_id,
        },
    }
    validate_version(version)
    return version


def inspect_release(archive: Path, metadata: Path) -> dict:
    try:
        with tempfile.TemporaryDirectory() as temporary:
            extracted = Path(temporary) / "release"
            verified = verify_release(archive, metadata, extract_to=extracted)
            return _version_from_release(verified, extracted)
    except FileNotFoundError as error:
        raise GenerationError(
            "source-unavailable: release archive or metadata is unavailable"
        ) from error


def inspect_adoption_release(
    archive: Path,
    metadata: Path,
    skill: str,
) -> dict:
    try:
        with tempfile.TemporaryDirectory() as temporary:
            extracted = Path(temporary) / "release"
            verified = verify_release(archive, metadata, extract_to=extracted)
            version = _version_from_release(verified, extracted)
            catalog = json.loads((extracted / "catalog.json").read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise GenerationError(
            "source-unavailable: release archive or metadata is unavailable"
        ) from error
    members = {
        record["id"]: record
        for record in catalog["skills"]
        if isinstance(record, dict) and isinstance(record.get("id"), str)
    }
    selected = members.get(skill)
    selected_path = f"skills/{skill}/SKILL.md"
    if selected is None or selected_path not in {
        record["path"] for record in version["files"]
    }:
        raise GenerationError("adoption-skill-missing: selected skill is absent from release")
    if any(
        dependency["requirement"] in {"required", "routing"}
        for dependency in selected["dependencies"]
    ):
        raise GenerationError(
            "adoption-dependencies-unsupported: selected skill requires other workflows"
        )
    return version


class GenerationStore:
    def __init__(self, managed_root: Path, private_root: Path) -> None:
        self.managed_root = managed_root
        self.generations_root = managed_root / "generations"
        self.private_root = private_root
        self.staging_root = private_root / "staging"
        self.trash_root = private_root / "trash"

    def _assert_managed_roots(self) -> None:
        if self.managed_root.is_symlink() or self.generations_root.is_symlink():
            raise GenerationError("local-divergence: managed root is a symlink")

    def _assert_private_roots(self) -> None:
        if (
            self.private_root.is_symlink()
            or self.staging_root.is_symlink()
            or self.trash_root.is_symlink()
        ):
            raise GenerationError("local-divergence: private lifecycle root is a symlink")

    def generation_path(self, generation: str) -> Path:
        self._assert_managed_roots()
        if DIGEST.fullmatch(generation) is None:
            raise GenerationError("generation.invalid: unsafe generation id")
        return contained_path(self.generations_root, generation)

    def staging_path(self, operation_id: str) -> Path:
        self._assert_private_roots()
        if OPERATION_ID.fullmatch(operation_id) is None:
            raise GenerationError("journal-invalid: operation id is unsafe")
        return contained_path(self.staging_root, operation_id)

    def operation_trash_path(self, operation_id: str) -> Path:
        self._assert_private_roots()
        if OPERATION_ID.fullmatch(operation_id) is None:
            raise GenerationError("journal-invalid: operation id is unsafe")
        return contained_path(self.trash_root, operation_id)

    def stage(
        self,
        archive: Path,
        metadata: Path,
        operation_id: str,
    ) -> tuple[dict, Path]:
        stage = self.staging_path(operation_id)
        if stage.exists():
            raise GenerationError("generation.staging_conflict: private staging already exists")
        stage.parent.mkdir(parents=True, exist_ok=True)
        self._assert_private_roots()
        try:
            verified: VerifiedRelease = verify_release(archive, metadata, extract_to=stage)
        except FileNotFoundError as error:
            if stage.exists() and not stage.is_symlink():
                shutil.rmtree(stage)
            raise GenerationError(
                "source-unavailable: release archive or metadata is unavailable"
            ) from error
        except (OSError, ValueError):
            if stage.exists() and not stage.is_symlink():
                shutil.rmtree(stage)
            raise
        files = checked_files(stage)
        version = {
            "files": _records(files),
            "generation": verified.archive_sha256,
            "release": {
                "archiveSha256": verified.archive_sha256,
                "metadataSha256": verified.metadata_sha256,
                "profile": verified.profile_id,
                "releaseId": verified.release_id,
            },
        }
        validate_version(version)
        marker = {
            "files": version["files"],
            "generation": version["generation"],
            "ownership": OWNERSHIP,
            "schemaVersion": "agent-skills-generation/1",
        }
        atomic_write(stage / GENERATION_MARKER, canonical_json(marker).encode())
        self.verify_path(stage, version)
        return version, stage

    def promote(self, stage: Path, version: dict) -> Path:
        target = self.generation_path(version["generation"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self.verify_path(target, version)
            shutil.rmtree(stage)
            return target
        os.replace(stage, target)
        self.verify_path(target, version)
        return target

    def verify(self, version: dict) -> None:
        validate_version(version)
        self.verify_path(self.generation_path(version["generation"]), version)

    def verify_path(self, path: Path, version: dict) -> None:
        try:
            files = checked_files(path)
            marker_contents = (path / GENERATION_MARKER).read_text(encoding="utf-8")
            marker = json.loads(marker_contents)
        except (FileSafetyError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GenerationError("local-divergence: generation is unreadable or unsafe") from error
        expected_marker = {
            "files": version["files"],
            "generation": version["generation"],
            "ownership": OWNERSHIP,
            "schemaVersion": "agent-skills-generation/1",
        }
        if marker != expected_marker or marker_contents != canonical_json(marker):
            raise GenerationError("local-divergence: generation ownership marker disagrees")
        expected = {record["path"]: record["sha256"] for record in version["files"]}
        expected[GENERATION_MARKER] = files.get(GENERATION_MARKER, "")
        if set(files) != set(expected) or any(files[path] != digest for path, digest in expected.items()):
            raise GenerationError("local-divergence: generation digest set disagrees")

    def cleanup_staging(self, operation_id: str) -> None:
        stage = self.staging_path(operation_id)
        if stage.exists():
            if stage.is_symlink():
                raise GenerationError("journal-invalid: staging root is a symlink")
            shutil.rmtree(stage)

    def cleanup(
        self,
        keep: set[str],
        operation_id: str,
        fault: CleanupFault | None = None,
    ) -> None:
        self._assert_managed_roots()
        trash = self.operation_trash_path(operation_id)
        if self.generations_root.exists():
            for path in sorted(self.generations_root.iterdir()):
                if not path.is_dir() or DIGEST.fullmatch(path.name) is None or path.name in keep:
                    continue
                self._retire_owned(path.name, trash)
        self._finish_operation_trash(trash, fault)

    def installed_generations(self) -> set[str]:
        self._assert_managed_roots()
        if not self.generations_root.exists():
            return set()
        names: set[str] = set()
        for path in self.generations_root.iterdir():
            if path.is_symlink() or not path.is_dir() or DIGEST.fullmatch(path.name) is None:
                raise GenerationError("local-divergence: unexpected generation entry")
            names.add(path.name)
        return names

    def _retire_owned(self, generation: str, operation_trash: Path) -> None:
        path = self.generation_path(generation)
        if not path.exists():
            return
        try:
            marker_contents = (path / GENERATION_MARKER).read_text(encoding="utf-8")
            marker = json.loads(marker_contents)
            version = {
                "files": marker["files"],
                "generation": marker["generation"],
                "release": {
                    "archiveSha256": generation,
                    "metadataSha256": "0" * 64,
                    "profile": "core",
                    "releaseId": "ownership-check",
                },
            }
            self.verify_path(path, version)
        except (KeyError, TypeError, OSError, json.JSONDecodeError, GenerationError) as error:
            raise GenerationError("local-divergence: refusing to delete unverified generation") from error
        operation_trash.mkdir(parents=True, exist_ok=True)
        self._assert_private_roots()
        retired = contained_path(operation_trash, generation)
        if retired.exists():
            raise GenerationError("local-divergence: managed and retired generation both exist")
        os.replace(path, retired)

    def _finish_operation_trash(
        self,
        operation_trash: Path,
        fault: CleanupFault | None,
    ) -> None:
        if not operation_trash.exists():
            return
        if operation_trash.is_symlink() or not operation_trash.is_dir():
            raise GenerationError("journal-invalid: operation trash is unsafe")
        fault_fired = False

        def remove_tree(path: Path) -> None:
            nonlocal fault_fired
            for child in sorted(path.iterdir()):
                mode = child.lstat().st_mode
                if stat.S_ISDIR(mode):
                    remove_tree(child)
                    child.rmdir()
                else:
                    child.unlink()
                if fault is not None and not fault_fired:
                    fault_fired = True
                    fault("obsolete-generation-delete-started")

        remove_tree(operation_trash)
        operation_trash.rmdir()
        if self.trash_root.exists() and not any(self.trash_root.iterdir()):
            self.trash_root.rmdir()
