from __future__ import annotations

import hashlib
import io
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
MAX_METADATA_BYTES = 64 * 1024
MAX_PATH_DEPTH = 8
MAX_COMPRESSION_RATIO = 100
MAX_VERIFY_SECONDS = 5
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
SUPPORTED_PROFILES = frozenset({"core", "linear"})


class ReleaseError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedRelease:
    release_id: str
    profile_id: str
    archive_sha256: str
    metadata_sha256: str


def _digest(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _bounded_bytes(path: Path, limit: int, rule_id: str) -> bytes:
    with path.open("rb") as source:
        contents = source.read(limit + 1)
    if len(contents) > limit:
        raise ReleaseError(f"{rule_id}: input exceeds limit")
    return contents


def _archive_bytes(path: Path) -> bytes:
    return _bounded_bytes(path, MAX_ARCHIVE_BYTES, "release.archive_limit")


def _archive_digest(path: Path) -> str:
    return _digest(_archive_bytes(path))


def _path_digest(paths: list[str], files: dict[str, bytes]) -> str:
    records = [{"path": path, "sha256": _digest(files[path])} for path in sorted(paths)]
    return _digest(canonical_json(records).encode())


def _document_bytes(contents: bytes, name: str) -> dict:
    try:
        value = json.loads(contents.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseError(f"release.document: {name} must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ReleaseError(f"release.document: {name} must be an object")
    return value


def _packaged_bytes(root: Path, relative: str) -> bytes:
    if not safe_relative_path(relative):
        raise ReleaseError(f"release.source_path: unsafe packaged input {relative}")
    path = root
    try:
        for part in PurePosixPath(relative).parts:
            path = path / part
            if path.is_symlink():
                raise ReleaseError(f"release.source_path: symlinked packaged input {relative}")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or not stat.S_ISREG(
            path.stat(follow_symlinks=False).st_mode
        ):
            raise ReleaseError(f"release.source_path: invalid packaged input {relative}")
        return path.read_bytes()
    except (FileNotFoundError, OSError) as error:
        raise ReleaseError(f"release.source_path: unreadable packaged input {relative}") from error


def _packaged_document(root: Path, relative: str) -> dict:
    try:
        value = json.loads(_packaged_bytes(root, relative))
    except json.JSONDecodeError as error:
        raise ReleaseError(f"release.source_document: invalid JSON {relative}") from error
    if not isinstance(value, dict):
        raise ReleaseError(f"release.source_document: {relative} must be an object")
    return value


def _release_identity(release_id: object, profile_id: object) -> tuple[str, str]:
    if not isinstance(release_id, str) or not release_id:
        raise ReleaseError("release.identity: releaseId must be a non-empty string")
    if profile_id not in SUPPORTED_PROFILES:
        raise ReleaseError("release.identity: unsupported profile")
    return release_id, profile_id


def _release_view(root: Path, profile_id: str) -> dict[str, bytes]:
    catalog = _packaged_document(root, "catalog.json")
    provenance = _packaged_document(root, "provenance.lock.json")
    profile_paths = ["profiles/core.json"]
    if profile_id == "linear":
        profile_paths.append("profiles/linear.json")
    profile_documents = {}
    for relative in profile_paths:
        document = _packaged_document(root, relative)
        profile_documents[document["id"]] = document
    if profile_id not in profile_documents:
        raise ReleaseError("release.profile: requested profile is unavailable")
    members = resolve_profile(profile_id, profile_documents)
    skills = {skill["id"]: skill for skill in catalog["skills"]}
    entries = {entry["id"]: entry for entry in provenance["entries"]}
    selected_skills = []
    selected_entries = []
    selected_paths: list[str] = []
    for member in members:
        skill = dict(skills[member])
        skill["profiles"] = [
            selected_profile
            for selected_profile in skill["profiles"]
            if selected_profile in profile_documents
        ]
        selected_skills.append(skill)
        entry = entries[skill["provenanceId"]]
        if entry.get("distribution") != "releasable":
            raise ReleaseError(f"release.profile: non-releasable member {member}")
        selected_entries.append(entry)
        selected_paths.extend(output["path"] for output in entry["outputs"])

    # The shipped reviewer charters ride every profile: the policy layer
    # resolves a lens to a charter by identity, so a release that omitted them
    # would compile no adopter policy that activates a lens. Their provenance
    # entries are selected here too, so the release-time releasable check and
    # the manifest closure cover charter bytes exactly as they cover skills.
    persona_manifest = _packaged_document(root, "personas/manifest.json")
    persona_paths = ["personas/manifest.json", "personas/source-adjudication.json"]
    for persona in persona_manifest["personas"]:
        entry = entries[persona["provenanceId"]]
        if entry.get("distribution") != "releasable":
            raise ReleaseError(f"release.persona: non-releasable charter {persona['personaId']}")
        if entry not in selected_entries:
            selected_entries.append(entry)
        persona_paths.append(persona["path"])
    metadata_entry = entries["persona-set-metadata"]
    if metadata_entry.get("distribution") != "releasable":
        raise ReleaseError("release.persona: non-releasable charter-set metadata")
    if metadata_entry not in selected_entries:
        selected_entries.append(metadata_entry)
    selected_paths.extend(persona_paths)

    files: dict[str, bytes] = {
        "catalog.json": canonical_json(
            {"schemaVersion": catalog["schemaVersion"], "skills": selected_skills}
        ).encode(),
        "provenance.lock.json": canonical_json(
            {
                "entries": sorted(selected_entries, key=lambda entry: entry["id"]),
                "schemaVersion": provenance["schemaVersion"],
                "sourceBaseline": provenance["sourceBaseline"],
            }
        ).encode(),
        "validation-inventory.json": _packaged_bytes(root, "validation-inventory.json"),
    }
    for selected_profile, document in profile_documents.items():
        files[f"profiles/{selected_profile}.json"] = canonical_json(document).encode()
    for relative in selected_paths:
        files[relative] = _packaged_bytes(root, relative)
    for relative in (
        "schemas/catalog.schema.json",
        "schemas/delivery-provider-rails.schema.json",
        "schemas/profile.schema.json",
        "schemas/provenance.schema.json",
        "schemas/receipt.schema.json",
        "schemas/release-manifest.schema.json",
    ):
        files[relative] = _packaged_bytes(root, relative)
    for relative in (
        "docs/agent-skills-provider.md",
        "docs/delivery-provider-rails-v1.md",
        "tests/fixtures/delivery-provider-rails-v1.json",
        "tests/fixtures/invalid-corpora/coverage.json",
        "tests/fixtures/valid-corpus/coverage.json",
        "tests/scenarios/core/compound.json",
        "tests/scenarios/core/execute.json",
        "tests/scenarios/core/plan.json",
        "tests/scenarios/core/review.json",
        "tests/scenarios/core/repository-discovery.json",
        "tests/scenarios/core/routing.json",
        "tests/scenarios/core/tracker-capability.json",
        "tests/test_delivery_provider_rails_contract.py",
    ):
        files[relative] = _packaged_bytes(root, relative)
    for relative in (
        "agent_skills/__init__.py",
        "agent_skills/adoption.py",
        "agent_skills/capabilities.py",
        "agent_skills/catalog.py",
        "agent_skills/cli.py",
        "agent_skills/errors.py",
        "agent_skills/fake_tracker.py",
        "agent_skills/fs.py",
        "agent_skills/generations.py",
        "agent_skills/hosts/__init__.py",
        "agent_skills/hosts/base.py",
        "agent_skills/hosts/claude.py",
        "agent_skills/hosts/codex.py",
        "agent_skills/hosts/exposure.py",
        "agent_skills/journal.py",
        "agent_skills/lifecycle.py",
        "agent_skills/locking.py",
        "agent_skills/provider.py",
        "agent_skills/receipt.py",
        "agent_skills/release.py",
        "agent_skills/router.py",
        "agent_skills/validate.py",
        "agent_skills/workflows.py",
    ):
        files[relative] = _packaged_bytes(root, relative)
    if {"diagnose-work", "obtain-review"}.intersection(members):
        for relative in (
            "agent_skills/workflow_graph.py",
            "agent_skills/diagnosis.py",
            "agent_skills/review_orchestration.py",
            "workflows/delivery-v1.json",
            "schemas/workflow-graph.schema.json",
            "schemas/workflow-stage-result.schema.json",
            "docs/workflow-graph-v1.md",
            "tests/consumers/workflow_consumer.py",
            "tests/fixtures/workflow-graph/result-templates.json",
            "tests/scenarios/core/diagnose.json",
            "tests/scenarios/core/obtain-review.json",
            "tests/test_workflow_graph_contract.py",
            "tests/test_diagnosis.py",
            "tests/test_review_orchestration.py",
        ):
            files[relative] = _packaged_bytes(root, relative)
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
    release_id, profile_id = _release_identity(release_id, profile_id)
    validation = validate_corpus(root)
    if validation.findings:
        raise ReleaseError("release.source_validation: source corpus is invalid")
    files = _release_view(root, profile_id)
    payload_paths = sorted(files)
    group = profile_id
    manifest = {
        "contentSha256": _path_digest(payload_paths, files),
        "entryPoint": "python -m agent_skills.validate --root .",
        "files": [
            {"group": group, "path": path, "sha256": _digest(files[path])}
            for path in payload_paths
        ],
        "groups": {
            group: {
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
    metadata_bytes = canonical_json(metadata).encode()
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_bytes(metadata_bytes)
    return VerifiedRelease(release_id, profile_id, archive_sha, _digest(metadata_bytes))


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
    metadata_bytes = _bounded_bytes(
        metadata_path,
        MAX_METADATA_BYTES,
        "release.metadata_limit",
    )
    metadata = _document_bytes(metadata_bytes, metadata_path.name)
    if metadata.get("schemaVersion") != "agent-skills-release-metadata/1":
        raise ReleaseError("release.identity: unsupported detached metadata")
    metadata_release_id, metadata_profile = _release_identity(
        metadata.get("releaseId"), metadata.get("profile")
    )
    archive_bytes = _archive_bytes(archive_path)
    archive_sha = _digest(archive_bytes)
    if metadata.get("archiveSha256") != archive_sha:
        raise ReleaseError("release.archive_checksum: detached checksum mismatch")
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
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
    manifest_release_id, manifest_profile = _release_identity(
        manifest.get("releaseId"), manifest.get("profile")
    )
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
        if record.get("group") != manifest_profile:
            raise ReleaseError(f"release.group: unassigned payload {path}")
        if record.get("sha256") != _digest(payload[path]):
            raise ReleaseError(f"release.payload_checksum: digest mismatch for {path}")
    if manifest.get("contentSha256") != _path_digest(sorted(payload), payload):
        raise ReleaseError("release.content_checksum: payload digest mismatch")
    groups = manifest.get("groups")
    if not isinstance(groups, dict) or set(groups) != {manifest_profile}:
        raise ReleaseError("release.group: profile group is required")
    profile_group = groups[manifest_profile]
    group_paths = (
        profile_group.get("paths") if isinstance(profile_group, dict) else None
    )
    if (
        not isinstance(group_paths, list)
        or len(group_paths) != len(set(group_paths))
        or set(group_paths) != set(payload)
    ):
        raise ReleaseError("release.group: profile group must own the payload")
    if profile_group.get("sha256") != _path_digest(group_paths, payload):
        raise ReleaseError("release.group_checksum: profile group digest mismatch")
    if manifest_release_id != metadata_release_id or manifest_profile != metadata_profile:
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
    return VerifiedRelease(
        metadata_release_id,
        metadata_profile,
        archive_sha,
        _digest(metadata_bytes),
    )
