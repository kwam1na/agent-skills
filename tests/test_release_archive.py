from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_skills.release import ReleaseError, build_release, verify_release


ROOT = Path(__file__).resolve().parents[1]


class ReleaseArchiveTests(unittest.TestCase):
    def build(self, directory: Path, stem: str = "core") -> tuple[Path, Path]:
        archive = directory / f"{stem}.zip"
        metadata = directory / f"{stem}.release.json"
        build_release(ROOT, archive, metadata, release_id="core-v1", profile_id="core")
        return archive, metadata

    def test_rebuild_is_byte_identical_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, first_metadata = self.build(root, "first")
            second, second_metadata = self.build(root, "second")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_metadata.read_bytes(), second_metadata.read_bytes())
            result = verify_release(first, first_metadata)
            self.assertEqual(result.release_id, "core-v1")
            self.assertEqual(result.profile_id, "core")
            with zipfile.ZipFile(first) as release:
                names = release.namelist()
            self.assertIn("skills/compound-delivery-kernel/SKILL.md", names)
            self.assertIn("skills/deliver-work/SKILL.md", names)
            self.assertIn(
                "skills/deliver-work/references/capability-contract.md", names
            )
            self.assertIn("agent_skills/capabilities.py", names)
            self.assertIn("agent_skills/fake_tracker.py", names)
            self.assertIn("agent_skills/router.py", names)
            self.assertIn(
                "tests/scenarios/core/repository-discovery.json", names
            )
            self.assertFalse(any("linear" in name for name in names))
            self.assertNotIn("skills/bootstrap-agent-harness/SKILL.md", names)

    def test_archive_checksum_tamper_rejects_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive, metadata = self.build(Path(temporary))
            archive.write_bytes(archive.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ReleaseError, "release.archive_checksum"):
                verify_release(archive, metadata)

    def test_release_identity_mismatch_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive, metadata = self.build(Path(temporary))
            document = json.loads(metadata.read_text(encoding="utf-8"))
            document["releaseId"] = "different-release"
            metadata.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ReleaseError, "release.identity"):
                verify_release(archive, metadata)

    def test_missing_or_typed_release_identity_rejects(self) -> None:
        metadata_mutations = (
            ("releaseId", None),
            ("profile", None),
            ("profile", "linear"),
        )
        for key, value in metadata_mutations:
            with self.subTest(source="metadata", key=key, value=value), tempfile.TemporaryDirectory() as temporary:
                archive, metadata = self.build(Path(temporary))
                document = json.loads(metadata.read_text(encoding="utf-8"))
                document[key] = value
                metadata.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                with self.assertRaisesRegex(ReleaseError, "release.identity"):
                    verify_release(archive, metadata)

        manifest_mutations = (("releaseId", None), ("profile", None), ("profile", "linear"))
        for key, value in manifest_mutations:
            with self.subTest(source="manifest", key=key, value=value), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                archive, metadata = self.build(directory)
                with zipfile.ZipFile(archive) as source:
                    entries = {name: source.read(name) for name in source.namelist()}
                manifest = json.loads(entries["release-manifest.json"])
                manifest[key] = value
                entries["release-manifest.json"] = (
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n"
                ).encode()
                self._rewrite_archive(archive, entries)
                self._rewrite_checksum(archive, metadata)
                with self.assertRaisesRegex(ReleaseError, "release.identity"):
                    verify_release(archive, metadata)

    def test_build_rejects_symlinked_runtime_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            corpus = directory / "corpus"
            shutil.copytree(ROOT, corpus, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            external = directory / "external-validate.py"
            external.write_text("raise RuntimeError('must not package')\n", encoding="utf-8")
            runtime = corpus / "agent_skills" / "validate.py"
            runtime.unlink()
            runtime.symlink_to(external)
            with self.assertRaisesRegex(ReleaseError, "release.source_path"):
                build_release(
                    corpus,
                    directory / "core.zip",
                    directory / "core.release.json",
                    release_id="core-v1",
                    profile_id="core",
                )

    def test_missing_and_unregistered_payload_reject(self) -> None:
        missing_paths = (
            "agent_skills/validate.py",
            "catalog.json",
            "profiles/core.json",
            "skills/compound-delivery-kernel/SKILL.md",
            "schemas/catalog.schema.json",
        )
        for mutation in (*missing_paths, "extra"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                archive, metadata = self.build(directory)
                entries: dict[str, bytes] = {}
                with zipfile.ZipFile(archive) as source:
                    for name in source.namelist():
                        entries[name] = source.read(name)
                if mutation != "extra":
                    entries.pop(mutation)
                else:
                    entries["unregistered.txt"] = b"not declared"
                self._rewrite_archive(archive, entries)
                self._rewrite_checksum(archive, metadata)
                with self.assertRaisesRegex(ReleaseError, "release.payload_set"):
                    verify_release(archive, metadata)

    def test_expansion_limit_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive, metadata = self.build(directory)
            with zipfile.ZipFile(archive) as source:
                entries = {name: source.read(name) for name in source.namelist()}
            entries["oversized.bin"] = b"x" * (2 * 1024 * 1024 + 1)
            self._rewrite_archive(archive, entries)
            self._rewrite_checksum(archive, metadata)
            with self.assertRaisesRegex(ReleaseError, "release.file_limit"):
                verify_release(archive, metadata)

    def test_incorrect_group_digest_and_path_escape_reject(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive, metadata = self.build(directory)
            with zipfile.ZipFile(archive) as source:
                entries = {name: source.read(name) for name in source.namelist()}
            manifest = json.loads(entries["release-manifest.json"])
            manifest["groups"]["core"]["sha256"] = "0" * 64
            entries["release-manifest.json"] = (
                json.dumps(manifest, indent=2, sort_keys=True) + "\n"
            ).encode()
            self._rewrite_archive(archive, entries)
            self._rewrite_checksum(archive, metadata)
            with self.assertRaisesRegex(ReleaseError, "release.group_checksum"):
                verify_release(archive, metadata)

            manifest["groups"]["core"]["sha256"] = manifest["contentSha256"]
            manifest["contentSha256"] = "0" * 64
            entries["release-manifest.json"] = (
                json.dumps(manifest, indent=2, sort_keys=True) + "\n"
            ).encode()
            self._rewrite_archive(archive, entries)
            self._rewrite_checksum(archive, metadata)
            with self.assertRaisesRegex(ReleaseError, "release.content_checksum"):
                verify_release(archive, metadata)

            entries = {"../escape": b"bad", **entries}
            self._rewrite_archive(archive, entries)
            self._rewrite_checksum(archive, metadata)
            with self.assertRaisesRegex(ReleaseError, "release.path"):
                verify_release(archive, metadata)

    def test_windows_and_noncanonical_archive_paths_reject(self) -> None:
        for unsafe in ("C:/escape.txt", "a//b", "a/./b", "./a", "alias/"):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                archive, metadata = self.build(directory)
                with zipfile.ZipFile(archive) as source:
                    entries = {name: source.read(name) for name in source.namelist()}
                entries[unsafe] = b"bad"
                self._rewrite_archive(archive, entries)
                self._rewrite_checksum(archive, metadata)
                with self.assertRaisesRegex(ReleaseError, "release.path"):
                    verify_release(archive, metadata)

    def test_archive_validates_outside_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive, metadata = self.build(directory)
            extracted = directory / "standalone"
            verify_release(archive, metadata, extract_to=extracted)
            result = subprocess.run(
                [sys.executable, "-m", "agent_skills.validate", "--root", "."],
                cwd=extracted,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            release_validation = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agent_skills.cli",
                    "validate",
                    "--archive",
                    str(archive),
                    "--metadata",
                    str(metadata),
                ],
                cwd=extracted,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                release_validation.returncode,
                0,
                release_validation.stdout + release_validation.stderr,
            )

            adopting = directory / "adopting"
            adopting.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=adopting, check=True)
            install = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agent_skills.cli",
                    "--root",
                    str(adopting),
                    "install",
                    "--archive",
                    str(archive),
                    "--metadata",
                    str(metadata),
                    "--maintenance",
                ],
                cwd=extracted,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
            self.assertEqual(json.loads(install.stdout)["status"]["lifecycle"], "current")
            status_command = [
                sys.executable,
                "-m",
                "agent_skills.cli",
                "--root",
                str(adopting),
                "status",
            ]
            first_status = subprocess.run(
                status_command,
                cwd=extracted,
                capture_output=True,
                text=True,
                check=False,
            )
            second_status = subprocess.run(
                status_command,
                cwd=extracted,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(first_status.returncode, 0, first_status.stderr)
            self.assertEqual(first_status.stdout, second_status.stdout)
            self.assertEqual(
                json.loads(first_status.stdout)["schemaVersion"],
                "agent-skills-status/1",
            )

    @staticmethod
    def _rewrite_archive(path: Path, entries: dict[str, bytes]) -> None:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for name, contents in sorted(entries.items()):
                target.writestr(name, contents)

    @staticmethod
    def _rewrite_checksum(archive: Path, metadata: Path) -> None:
        document = json.loads(metadata.read_text(encoding="utf-8"))
        document["archiveSha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        metadata.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main()
