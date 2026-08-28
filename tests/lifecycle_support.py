from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from agent_skills.release import build_release
from agent_skills.validate import canonical_json


CORPUS_ROOT = Path(__file__).resolve().parents[1]
ADOPTING_FIXTURE = CORPUS_ROOT / "tests" / "fixtures" / "adopting-repos"


def run_git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def initialize_repository(parent: Path) -> Path:
    root = parent / "adopting-repository"
    shutil.copytree(ADOPTING_FIXTURE, root)
    run_git(root, "init", "-q")
    run_git(root, "config", "user.email", "tests@example.invalid")
    run_git(root, "config", "user.name", "Lifecycle Tests")
    run_git(root, "add", ".")
    run_git(root, "commit", "-qm", "initial fixture")
    return root


def build_version(parent: Path, release_id: str) -> tuple[Path, Path]:
    archive = parent / f"{release_id}.zip"
    metadata = parent / f"{release_id}.release.json"
    build_release(
        CORPUS_ROOT,
        archive,
        metadata,
        release_id=release_id,
        profile_id="core",
    )
    return archive, metadata


def forge_provenance_failure(archive: Path, metadata: Path) -> None:
    with zipfile.ZipFile(archive) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    provenance = json.loads(entries["provenance.lock.json"])
    provenance["entries"][0]["outputs"][0]["sha256"] = "0" * 64
    entries["provenance.lock.json"] = canonical_json(provenance).encode()
    manifest = json.loads(entries["release-manifest.json"])
    payload = {path: contents for path, contents in entries.items() if path != "release-manifest.json"}

    def path_digest(paths: list[str]) -> str:
        records = [
            {"path": path, "sha256": hashlib.sha256(payload[path]).hexdigest()}
            for path in sorted(paths)
        ]
        return hashlib.sha256(canonical_json(records).encode()).hexdigest()

    for record in manifest["files"]:
        record["sha256"] = hashlib.sha256(payload[record["path"]]).hexdigest()
    paths = sorted(payload)
    manifest["contentSha256"] = path_digest(paths)
    manifest["groups"]["core"]["sha256"] = path_digest(paths)
    entries["release-manifest.json"] = canonical_json(manifest).encode()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for path, contents in sorted(entries.items()):
            target.writestr(path, contents)
    detached = json.loads(metadata.read_text(encoding="utf-8"))
    detached["archiveSha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    metadata.write_text(canonical_json(detached), encoding="utf-8")


def unrelated_digest(root: Path) -> str:
    digest = hashlib.sha256()
    managed = root / ".agent-skills"
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path == root / ".git" or managed in path.parents or root / ".git" in path.parents:
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class LifecycleFixture:
    def setUp(self) -> None:
        self.maintenance_environment = mock.patch.dict(
            os.environ,
            {
                "AGENT_SKILLS_EXECUTION_CONTEXT": "",
                "GITHUB_EVENT_NAME": "",
            },
        )
        self.maintenance_environment.start()
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.repository = initialize_repository(self.base)
        self.v1 = build_version(self.base, "core-v1")
        self.v2 = build_version(self.base, "core-v2")

    def tearDown(self) -> None:
        self.temporary.cleanup()
        self.maintenance_environment.stop()
