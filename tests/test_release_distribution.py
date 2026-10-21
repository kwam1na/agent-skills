from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_skills.distribution import _publish_github, package_release, publish_release
from agent_skills.lifecycle import Lifecycle
from agent_skills.release import ReleaseError, build_release, verify_release
from lifecycle_support import initialize_repository, run_git

ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def test_github_command_attaches_both_assets_and_requires_remote_tag(self):
        origin = 'git@github.com:trusted/agent-skills.git'
        with patch.dict(os.environ, {'GH_REPO': 'unrelated/other'}), \
                patch('agent_skills.distribution._git', return_value=origin), \
                patch('agent_skills.distribution.subprocess.run') as run:
            _publish_github(ROOT, 'v1.0.0', ROOT / 'core.zip', ROOT / 'core.json')
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ['gh', 'release', 'create', 'v1.0.0'])
        self.assertIn(str(ROOT / 'core.zip'), command)
        self.assertIn(str(ROOT / 'core.json'), command)
        self.assertIn('--verify-tag', command)
        self.assertIn('--repo', command)
        self.assertEqual(command[command.index('--repo') + 1], origin)
        self.assertTrue(run.call_args.kwargs['check'])

    def test_documented_adopter_commands_work_without_producer_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            downloads = base / 'downloads'
            downloads.mkdir()
            build_release(ROOT, downloads / 'core.zip', downloads / 'core.release.json',
                          release_id='v1.0.0', profile_id='core')
            repository = initialize_repository(base)
            document = (ROOT / 'docs/release-distribution.md').read_text()
            block = next(block for block in re.findall(r'```sh\n(.*?)```', document, re.S)
                         if 'gh release download' in block)
            commands = block[block.index('python3 '):]
            commands = commands.replace('/absolute/path/to/repository', str(repository))
            commands = commands.replace('python3 ', shlex.quote(sys.executable) + ' -S ')
            result = subprocess.run(['sh', '-eu', '-c', commands], cwd=base,
                                    capture_output=True, text=True,
                                    env={**os.environ, 'PYTHONPATH': '',
                                         'AGENT_SKILLS_EXECUTION_CONTEXT': '', 'GITHUB_EVENT_NAME': ''})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            status = Lifecycle(repository).status().as_dict()
            self.assertEqual(status['lifecycle'], 'current')
            self.assertEqual(status['blockers'], [])

    def test_npm_tarball_applies_same_generation_and_refuses_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            archive, metadata = base / 'release.zip', base / 'release.json'
            build_release(ROOT, archive, metadata, release_id='v1.0.0', profile_id='core')
            package = base / 'package'
            package_release(archive, metadata, package, package_name='@fixture/skills', package_version='1.0.0')
            packed = subprocess.run(['npm', 'pack', '--json'], cwd=package, check=True, capture_output=True, text=True)
            tarball = package / json.loads(packed.stdout)[0]['filename']
            consumer = base / 'consumer'
            consumer.mkdir()
            subprocess.run(['npm', 'install', '--ignore-scripts', '--no-audit', '--no-fund', '--prefix', str(consumer), str(tarball)], check=True, capture_output=True)
            direct_parent, npm_parent = base / 'direct', base / 'npm'
            direct_parent.mkdir()
            npm_parent.mkdir()
            direct = initialize_repository(direct_parent)
            npm_repo = initialize_repository(npm_parent)
            Lifecycle(direct).install(archive, metadata, maintainer=True)
            installed = consumer / 'node_modules/@fixture/skills'
            def apply():
                return subprocess.run(['node', str(installed / 'bin/apply.cjs'), 'apply', '--root', str(npm_repo), '--maintenance'], capture_output=True, text=True, env={**os.environ, 'AGENT_SKILLS_PYTHON': sys.executable})
            result = apply()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(Lifecycle(direct).status().as_dict()['active']['generation'], Lifecycle(npm_repo).status().as_dict()['active']['generation'])
            original = (installed / 'release.zip').read_bytes()
            (installed / 'release.zip').write_bytes(original + b'tamper')
            self.assertNotEqual(apply().returncode, 0)
            (installed / 'release.zip').write_bytes(original)
            document = json.loads((installed / 'release.json').read_text())
            document['releaseId'] = 'wrong'
            (installed / 'release.json').write_text(json.dumps(document))
            self.assertNotEqual(apply().returncode, 0)

    def test_publish_checks_remote_default_ancestry_and_publishes_verified_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / 'source'
            shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__', '*.egg-info'))
            run_git(source, 'init', '-q', '-b', 'main')
            run_git(source, 'config', 'user.email', 'fixture@example.invalid')
            run_git(source, 'config', 'user.name', 'Fixture')
            run_git(source, 'add', '.')
            run_git(source, 'commit', '-qm', 'release')
            remote = base / 'remote.git'
            subprocess.run(['git', 'clone', '--bare', str(source), str(remote)], check=True, capture_output=True)
            run_git(source, 'remote', 'add', 'origin', str(remote))
            run_git(source, 'tag', 'v1.0.0')
            run_git(source, 'push', 'origin', 'v1.0.0')
            archive, metadata = base / 'release.zip', base / 'release.json'
            build_release(source, archive, metadata, release_id='v1.0.0', profile_id='core')
            def inspect_assets(root, tag, shipped_archive, shipped_metadata):
                self.assertEqual(verify_release(shipped_archive, shipped_metadata), verify_release(archive, metadata))
            with patch('agent_skills.distribution._publish_github', side_effect=inspect_assets) as publish:
                publish_release(source, archive, metadata, tag='v1.0.0')
                publish.assert_called_once()
            wrong_archive, wrong_metadata = base / 'wrong.zip', base / 'wrong.json'
            build_release(source, wrong_archive, wrong_metadata, release_id='other', profile_id='core')
            with patch('agent_skills.distribution._publish_github') as publish:
                with self.assertRaisesRegex(ReleaseError, 'release.tag'):
                    publish_release(source, wrong_archive, wrong_metadata, tag='v1.0.0')
                publish.assert_not_called()
            (source / 'extra').write_text('candidate')
            run_git(source, 'add', 'extra')
            run_git(source, 'commit', '-qm', 'candidate')
            run_git(source, 'tag', 'v2.0.0')
            run_git(source, 'push', 'origin', 'v2.0.0')
            with patch('agent_skills.distribution._publish_github') as publish:
                with self.assertRaisesRegex(ReleaseError, 'default_branch'):
                    publish_release(source, archive, metadata, tag='v2.0.0')
                publish.assert_not_called()
