"""Distribution transports; installation remains the verified repository lifecycle."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .release import ReleaseError, build_release, verify_release
from .validate import canonical_json


def package_release(archive: Path, metadata: Path, destination: Path, *, package_name: str, package_version: str):
    identity = verify_release(archive, metadata)
    if not re.fullmatch(r'(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*', package_name):
        raise ReleaseError('release.npm_name: invalid package name')
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?', package_version):
        raise ReleaseError('release.npm_version: provide a semantic package version')
    if destination.exists():
        raise ReleaseError('release.destination: package destination already exists')
    # Bootstrap code is drawn from the verified release, never the caller's checkout.
    with tempfile.TemporaryDirectory() as temporary:
        extracted = Path(temporary) / 'verified'
        verify_release(archive, metadata, extract_to=extracted)
        destination.mkdir(parents=True)
        shutil.copytree(extracted / 'agent_skills', destination / 'bootstrap/agent_skills')
    shutil.copyfile(archive, destination / 'release.zip')
    shutil.copyfile(metadata, destination / 'release.json')
    (destination / 'package.json').write_text(canonical_json({
        'name': package_name, 'version': package_version,
        'description': f'Verified agent-skills {identity.profile_id} release {identity.release_id}',
        'bin': {'agent-skills-release': 'bin/apply.cjs'},
        'files': ['bin/', 'bootstrap/', 'release.zip', 'release.json'],
    }))
    (destination / 'bin').mkdir()
    (destination / 'bin/apply.cjs').write_text('''#!/usr/bin/env node
const {spawnSync} = require('node:child_process');
const path = require('node:path');
const result = spawnSync(process.env.AGENT_SKILLS_PYTHON || 'python3',
  [path.join(__dirname, '../bootstrap/apply.py'), ...process.argv.slice(2)],
  {stdio: 'inherit'});
if (result.error) console.error(result.error.message);
process.exit(result.status === null ? 1 : result.status);
''')
    (destination / 'bin/apply.cjs').chmod(0o755)
    (destination / 'bootstrap/apply.py').write_text('''from pathlib import Path
import argparse
from agent_skills.cli import main

parser = argparse.ArgumentParser(description="Apply the packaged verified agent-skills release")
parser.add_argument("command", choices=["apply"])
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--maintenance", action="store_true")
args = parser.parse_args()
package = Path(__file__).resolve().parent.parent
arguments = ["--root", str(args.root), "apply", "--archive", str(package / "release.zip"), "--metadata", str(package / "release.json")]
if args.maintenance:
    arguments.append("--maintenance")
raise SystemExit(main(arguments))
''')
    return identity


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(['git', *arguments], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _publish_github(root: Path, tag: str, archive: Path, metadata: Path) -> None:
    origin = _git(root, 'remote', 'get-url', 'origin')
    subprocess.run(['gh', 'release', 'create', tag, str(archive.resolve()), str(metadata.resolve()), '--repo', origin, '--verify-tag', '--title', tag, '--notes', 'Verified agent-skills release; see release-distribution documentation for installation and trust requirements.'], cwd=root, check=True)


def publish_release(root: Path, archive: Path, metadata: Path, *, tag: str):
    if not tag or tag.startswith('-'):
        raise ReleaseError('release.tag: invalid tag')
    # Resolve the remote default branch afresh rather than trusting a stale local main.
    remote_head = _git(root, 'ls-remote', '--symref', 'origin', 'HEAD')
    branch = next((line.split()[1] for line in remote_head.splitlines() if line.startswith('ref: refs/heads/')), None)
    if branch is None:
        raise ReleaseError('release.default_branch: remote default branch is unavailable')
    _git(root, 'fetch', '--no-tags', 'origin', branch)
    default_commit = _git(root, 'rev-parse', 'FETCH_HEAD')
    commit = _git(root, 'rev-parse', '--verify', f'refs/tags/{tag}^{{commit}}')
    remote_tag = _git(root, 'ls-remote', 'origin', f'refs/tags/{tag}', f'refs/tags/{tag}^{{}}')
    remote_commits = {line.split()[0] for line in remote_tag.splitlines()}
    if commit not in remote_commits:
        raise ReleaseError('release.tag: local tag does not match published remote tag')
    if subprocess.run(['git', 'merge-base', '--is-ancestor', commit, default_commit], cwd=root).returncode:
        raise ReleaseError('release.default_branch: tag commit is not an ancestor of the remote default branch')
    identity = verify_release(archive, metadata)
    if identity.release_id != tag:
        raise ReleaseError('release.tag: release identity must equal tag')
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        snapshot = base / 'source.zip'
        _git(root, 'archive', '--format=zip', f'--output={snapshot}', commit)
        source = base / 'source'
        with zipfile.ZipFile(snapshot) as files:
            files.extractall(source)
        rebuilt, rebuilt_metadata = base / 'release.zip', base / 'release.json'
        build_release(source, rebuilt, rebuilt_metadata, release_id=tag, profile_id=identity.profile_id)
        if rebuilt.read_bytes() != archive.read_bytes() or rebuilt_metadata.read_bytes() != metadata.read_bytes():
            raise ReleaseError('release.tag: assets differ from tagged source')
        # Use the checked copies, so publication cannot swap in different input bytes.
        assets = base / 'assets'
        assets.mkdir()
        if archive.name == metadata.name:
            raise ReleaseError('release.assets: archive and metadata need distinct names')
        shipped_archive, shipped_metadata = assets / archive.name, assets / metadata.name
        shutil.copyfile(rebuilt, shipped_archive)
        shutil.copyfile(rebuilt_metadata, shipped_metadata)
        _publish_github(root, tag, shipped_archive, shipped_metadata)
    return identity
