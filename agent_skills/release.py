from __future__ import annotations

import hashlib
import json
import shutil
import stat
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .catalog import resolve_profile
from .validate import canonical_json, safe_relative_path, validate_corpus


MAX_ENTRIES = 128
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_PATH_DEPTH = 8
MAX_COMPRESSION_RATIO = 100
MAX_VERIFY_SECONDS = 5
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class ReleaseError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedRelease:
    release_id: str
    profile_id: str
    archive_sha256: str


def _digest(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _archive_digest(path: Path) -> str:
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ReleaseError("release.archive_limit: compressed archive exceeds limit")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _path_digest(paths: list[str], files: dict[str, bytes]) -> str:
    records = [{"path": path, "sha256": _digest(files[path])} for path in sorted(paths)]
    return _digest(canonical_json(records).encode())


def _document(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReleaseError(f"release.document: {path.name} must be an object")
    return value


def _release_view(root: Path, profile_id: str) -> dict[str, bytes]:
    catalog = _document(root / "catalog.json")
    provenance = _document(root / "provenance.lock.json")
    profile_documents = {
        document["id"]: document
        for path in sorted((root / "profiles").glob("*.json"))
        if isinstance((document := _document(path)), dict)
    }
    if profile_id != "core" or profile_id not in profile_documents:
        raise ReleaseError("release.profile: the first release is core-only")
    members = resolve_profile(profile_id, profile_documents)
    skills = {skill["id"]: skill for skill in catalog["skills"]}
    entries = {entry["id"]: entry for entry in provenance["entries"]}
    selected_skills = []
    selected_entries = []
    selected_paths: list[str] = []
    for member in members:
        skill = dict(skills[member])
        skill["profiles"] = [profile_id]
        selected_skills.append(skill)
        entry = entries[skill["provenanceId"]]
        if entry.get("distribution") != "releasable":
            raise ReleaseError(f"release.profile: non-releasable member {member}")
        selected_entries.append(entry)
        selected_paths.extend(output["path"] for output in entry["outputs"])

    files: dict[str, bytes] = {
        "catalog.json": canonical_json({"schemaVersion": catalog["schemaVersion"], "skills": selected_skills}).encode(),
        "profiles/core.json": canonical_json(profile_documents["core"]).encode(),
        "provenance.lock.json": canonical_json({
            "entries": selected_entries,
            "schemaVersion": provenance["schemaVersion"],
            "sourceBaseline": provenance["sourceBaseline"],
        }).encode(),
        "validation-inventory.json": (root / "validation-inventory.json").read_bytes(),
    }
    for relative in selected_paths:
        files[relative] = (root / relative).read_bytes()
    for relative in (
        "schemas/catalog.schema.json",
        "schemas/profile.schema.json",
        "schemas/provenance.schema.json",
        "schemas/release-manifest.schema.json",
    ):
        files[relative] = (root / relative).read_bytes()
    for path in sorted((root / "tests" / "fixtures").rglob("*.json")):
        files[path.relative_to(root).as_posix()] = path.read_bytes()
    for relative in (
        "agent_skills/__init__.py",
        "agent_skills/catalog.py",
        "agent_skills/errors.py",
        "agent_skills/validate.py",
    ):
        files[relative] = (root / relative).read_bytes()
    return files


def _zip_write(path: Path, files: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, contents in sorted(files.items()):
            info = zipfile.ZipInfo(relative, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, contents, compresslevel=9)


def build_release(
    root: Path,
    archive_path: Path,
    metadata_path: Path,
    *,
    release_id: str,
    profile_id: str,
) -> VerifiedRelease:
    root = root.resolve()
    validation = validate_corpus(root)
    if validation.findings:
        raise ReleaseError("release.source_validation: source corpus is invalid")
    files = _release_view(root, profile_id)
    payload_paths = sorted(files)
    manifest = {
        "contentSha256": _path_digest(payload_paths, files),
        "entryPoint": "python -m agent_skills.validate --root .",
        "files": [
            {"group": "core", "path": path, "sha256": _digest(files[path])}
            for path in payload_paths
        ],
        "groups": {
            "core": {
                "paths": payload_paths,
                "sha256": _path_digest(payload_paths, files),
            }
        },
        "profile": profile_id,
        "releaseId": release_id,
        "schemaVersion": "agent-skills-release/1",
    }
    files["release-manifest.json"] = canonical_json(manifest).encode()
    _zip_write(archive_path, files)
    archive_sha = _archive_digest(archive_path)
    metadata = {
        "archiveSha256": archive_sha,
        "profile": profile_id,
        "releaseId": release_id,
        "schemaVersion": "agent-skills-release-metadata/1",
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(canonical_json(metadata), encoding="utf-8")
    return VerifiedRelease(release_id, profile_id, archive_sha)


def _validate_archive_path(name: str) -> None:
    if not safe_relative_path(name) or len(PurePosixPath(name).parts) > MAX_PATH_DEPTH:
        raise ReleaseError(f"release.path: unsafe or too-deep archive path {name}")


def verify_release(
    archive_path: Path,
    metadata_path: Path,
    *,
    extract_to: Path | None = None,
) -> VerifiedRelease:
    started = time.monotonic()
    metadata = _document(metadata_path)
    if metadata.get("schemaVersion") != "agent-skills-release-metadata/1":
        raise ReleaseError("release.identity: unsupported detached metadata")
    archive_sha = _archive_digest(archive_path)
    if metadata.get("archiveSha256") != archive_sha:
        raise ReleaseError("release.archive_checksum: detached checksum mismatch")
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES or len({info.filename for info in infos}) != len(infos):
                raise ReleaseError("release.entry_limit: too many or duplicate entries")
            if len({info.filename.casefold() for info in infos}) != len(infos):
                raise ReleaseError("release.path: case-fold archive collision")
            total = 0
            for info in infos:
                _validate_archive_path(info.filename)
                if info.is_dir() or info.file_size > MAX_FILE_BYTES:
                    raise ReleaseError("release.file_limit: directory or oversized entry")
                mode = info.external_attr >> 16
                if stat.S_IFMT(mode) == stat.S_IFLNK:
                    raise ReleaseError("release.path: symlink entries are not allowed")
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    raise ReleaseError("release.total_limit: expanded bytes exceed limit")
                if info.file_size and info.compress_size == 0:
                    raise ReleaseError("release.compression_ratio: invalid compressed size")
                if info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
                    raise ReleaseError("release.compression_ratio: entry exceeds limit")
                files[info.filename] = archive.read(info)
                if time.monotonic() - started > MAX_VERIFY_SECONDS:
                    raise ReleaseError("release.elapsed_limit: verification exceeded limit")
    except zipfile.BadZipFile as error:
        raise ReleaseError("release.archive_format: invalid ZIP") from error
    try:
        manifest = json.loads(files["release-manifest.json"])
    except (KeyError, json.JSONDecodeError, TypeError) as error:
        raise ReleaseError("release.manifest: missing or invalid manifest") from error
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != "agent-skills-release/1":
        raise ReleaseError("release.manifest: unsupported manifest")
    if manifest.get("entryPoint") != "python -m agent_skills.validate --root .":
        raise ReleaseError("release.manifest: unsupported entry point")
    records = manifest.get("files")
    if not isinstance(records, list):
        raise ReleaseError("release.manifest: files must be a list")
    declared = {
        record.get("path"): record
        for record in records
        if isinstance(record, dict) and isinstance(record.get("path"), str)
    }
    payload = {path: contents for path, contents in files.items() if path != "release-manifest.json"}
    if len(declared) != len(records) or set(declared) != set(payload):
        raise ReleaseError("release.payload_set: missing or unregistered payload")
    for path, record in declared.items():
        if record.get("group") != "core":
            raise ReleaseError(f"release.group: unassigned payload {path}")
        if record.get("sha256") != _digest(payload[path]):
            raise ReleaseError(f"release.payload_checksum: digest mismatch for {path}")
    if manifest.get("contentSha256") != _path_digest(sorted(payload), payload):
        raise ReleaseError("release.content_checksum: payload digest mismatch")
    groups = manifest.get("groups")
    if not isinstance(groups, dict) or set(groups) != {"core"}:
        raise ReleaseError("release.group: core group is required")
    core = groups["core"]
    group_paths = core.get("paths") if isinstance(core, dict) else None
    if (
        not isinstance(group_paths, list)
        or len(group_paths) != len(set(group_paths))
        or set(group_paths) != set(payload)
    ):
        raise ReleaseError("release.group: core group must own the payload")
    if core.get("sha256") != _path_digest(group_paths, payload):
        raise ReleaseError("release.group_checksum: core group digest mismatch")
    if manifest.get("releaseId") != metadata.get("releaseId") or manifest.get("profile") != metadata.get("profile"):
        raise ReleaseError("release.identity: detached metadata mismatch")
    if extract_to is not None:
        if extract_to.exists() and any(extract_to.iterdir()):
            raise ReleaseError("release.destination: extraction target is not empty")
        extract_to.mkdir(parents=True, exist_ok=True)
        for path, contents in sorted(files.items()):
            target = extract_to / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
        validation = validate_corpus(extract_to)
        if validation.findings:
            shutil.rmtree(extract_to)
            raise ReleaseError("release.standalone_validation: extracted corpus is invalid")
    return VerifiedRelease(metadata["releaseId"], metadata["profile"], archive_sha)
