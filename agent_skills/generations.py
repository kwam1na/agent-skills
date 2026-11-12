from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from .fs import FileSafetyError, atomic_write, checked_files, contained_path
from .hooks import declared_events
from .hosts.claude import CLAUDE_CODE
from .receipt import DIGEST, OWNERSHIP, validate_version
from .release import MAX_METADATA_BYTES, VerifiedRelease, _archive_bytes, _bounded_bytes, verify_release
from .validate import canonical_json


GENERATION_MARKER = ".agent-skills-generation.json"
OPERATION_ID = re.compile(r"^[a-f0-9]{32}$")
CleanupFault = Callable[[str], None]


class GenerationError(ValueError):
    pass


def _is_python_bytecode(path: str) -> bool:
    parts = Path(path).parts
    return "__pycache__" in parts and path.endswith(".pyc")


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


# Inspections this process has already verified, keyed on the sha256 of the archive bytes and of the metadata bytes
# the inspection read. Only what a verification that passed computed is kept, in memory: never a path, a time or an
# extraction, so nothing on disk is trusted on a later inspection. Each holds the version, the catalog bytes and the
# Claude Code hook events the release's profile declares.
_INSPECTED: OrderedDict[tuple[str, str], tuple[dict, bytes, tuple[str, ...]]] = OrderedDict()
INSPECTED_LIMIT = 16


def _inspected(archive: Path, metadata: Path) -> tuple[dict, bytes, tuple[str, ...]]:
    """The version, catalog bytes and Claude Code hook events of a verified release, extracted once per content."""
    try:
        archive_bytes = _archive_bytes(archive)
        metadata_bytes = _bounded_bytes(metadata, MAX_METADATA_BYTES, "release.metadata_limit")
    except FileNotFoundError as error:
        raise GenerationError(
            "source-unavailable: release archive or metadata is unavailable"
        ) from error
    key = (hashlib.sha256(archive_bytes).hexdigest(), hashlib.sha256(metadata_bytes).hexdigest())
    if key in _INSPECTED:
        _INSPECTED.move_to_end(key)
        version, catalog, events = _INSPECTED[key]
        return copy.deepcopy(version), catalog, events
    try:
        with tempfile.TemporaryDirectory() as temporary:
            extracted = Path(temporary) / "release"
            verified = verify_release(archive, metadata, extract_to=extracted)
            if (verified.archive_sha256, verified.metadata_sha256) != key:
                raise GenerationError("source-changed: the release changed while it was inspected")
            version = _version_from_release(verified, extracted)
            events = tuple(declared_events(extracted, CLAUDE_CODE.host))
    except FileNotFoundError as error:
        raise GenerationError(
            "source-unavailable: release archive or metadata is unavailable"
        ) from error
    # The extraction is only trusted where it holds exactly the archive's own bytes.
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as source:
        contents = {info.filename: source.read(info) for info in source.infolist()}
    if {record["path"]: record["sha256"] for record in version["files"]} != {
        path: hashlib.sha256(data).hexdigest() for path, data in contents.items()
    }:
        raise GenerationError("generation.invalid: the extracted release differs from its archive")
    catalog = contents.get("catalog.json", b"")
    _INSPECTED[key] = (copy.deepcopy(version), catalog, events)
    while len(_INSPECTED) > INSPECTED_LIMIT:
        _INSPECTED.popitem(last=False)
    return version, catalog, events


def inspect_release(archive: Path, metadata: Path) -> dict:
    return _inspected(archive, metadata)[0]


def inspect_release_hook_events(archive: Path, metadata: Path) -> list[str]:
    """The Claude Code events, sorted, for which the verified release's profile declares a hook."""
    return list(_inspected(archive, metadata)[2])


def inspect_adoption_release(
    archive: Path,
    metadata: Path,
    skill: str,
) -> dict:
    version, catalog_bytes, _ = _inspected(archive, metadata)
    catalog = json.loads(catalog_bytes.decode("utf-8"))
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
        unexpected = set(files) - set(expected)
        digest_set_matches = set(files) - unexpected == set(expected) and all(
            files[path] == digest for path, digest in expected.items()
        )
        if (
            unexpected
            and all(_is_python_bytecode(path) for path in unexpected)
            and digest_set_matches
        ):
            raise GenerationError(
                "local-bytecode-divergence: remove Python bytecode from the installed generation"
            )
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
