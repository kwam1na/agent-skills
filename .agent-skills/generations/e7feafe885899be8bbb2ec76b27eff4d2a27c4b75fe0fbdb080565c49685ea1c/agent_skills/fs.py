from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path


class FileSafetyError(ValueError):
    pass


MANAGED_GIT_ATTRIBUTES = b"* -text\n"


DESCRIPTOR_PUBLISH_SUPPORTED = all(
    function in os.supports_dir_fd
    for function in (os.open, os.mkdir, os.rename, os.unlink)
)


def sha256_bytes(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def repository_paths(root: Path) -> tuple[Path, Path]:
    resolved = root.resolve(strict=True)
    try:
        top_level = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=resolved,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        git_path = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-path", "agent-skills"],
            cwd=resolved,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise FileSafetyError("repository.invalid: lifecycle root must be a Git repository") from error
    if Path(top_level).resolve() != resolved:
        raise FileSafetyError("repository.invalid: lifecycle root must be the Git top level")
    return resolved, Path(git_path).absolute()


def contained_path(root: Path, *parts: str) -> Path:
    if any(not part or part in {".", ".."} or "/" in part or "\\" in part for part in parts):
        raise FileSafetyError("path.invalid: managed path component is unsafe")
    target = root.joinpath(*parts)
    if not target.absolute().is_relative_to(root.absolute()):
        raise FileSafetyError("path.invalid: managed path escapes its fixed root")
    return target


def atomic_write(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        try:
            directory = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            try:
                os.fsync(directory)
            except OSError:
                pass
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def publish_active_marker(repository_root: Path, contents: bytes | None) -> None:
    """Publish fixed tracked lifecycle metadata under a verified managed root."""
    if not DESCRIPTOR_PUBLISH_SUPPORTED:
        managed_root = repository_root / ".agent-skills"
        if managed_root.is_symlink():
            raise FileSafetyError("local-divergence: managed root is a symlink")
        managed_root.mkdir(exist_ok=True)
        if managed_root.is_symlink() or not managed_root.resolve().is_relative_to(repository_root):
            raise FileSafetyError("local-divergence: managed root escaped the repository")
        active = managed_root / "active.json"
        attributes = managed_root / ".gitattributes"
        if contents is None:
            active.unlink(missing_ok=True)
            attributes.unlink(missing_ok=True)
        else:
            atomic_write(attributes, MANAGED_GIT_ATTRIBUTES)
            atomic_write(active, contents)
        if managed_root.is_symlink() or not managed_root.resolve().is_relative_to(repository_root):
            raise FileSafetyError("local-divergence: managed root changed during publication")
        return

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    root_descriptor = os.open(repository_root, directory_flags | no_follow)
    try:
        try:
            os.mkdir(".agent-skills", mode=0o755, dir_fd=root_descriptor)
        except FileExistsError:
            pass
        try:
            managed_descriptor = os.open(
                ".agent-skills",
                directory_flags | no_follow,
                dir_fd=root_descriptor,
            )
        except OSError as error:
            raise FileSafetyError("local-divergence: managed root is unsafe") from error
        try:
            def publish(name: str, value: bytes | None) -> None:
                if value is None:
                    try:
                        os.unlink(name, dir_fd=managed_descriptor)
                    except FileNotFoundError:
                        pass
                    return
                temporary = f".{name}.{uuid.uuid4().hex}.tmp"
                try:
                    descriptor = os.open(
                        temporary,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow,
                        0o644,
                        dir_fd=managed_descriptor,
                    )
                    try:
                        remaining = memoryview(value)
                        while remaining:
                            written = os.write(descriptor, remaining)
                            if written <= 0:
                                raise FileSafetyError(
                                    "local-divergence: lifecycle metadata write did not progress"
                                )
                            remaining = remaining[written:]
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    os.rename(
                        temporary,
                        name,
                        src_dir_fd=managed_descriptor,
                        dst_dir_fd=managed_descriptor,
                    )
                finally:
                    try:
                        os.unlink(temporary, dir_fd=managed_descriptor)
                    except FileNotFoundError:
                        pass

            if contents is None:
                publish("active.json", None)
                publish(".gitattributes", None)
            else:
                publish(".gitattributes", MANAGED_GIT_ATTRIBUTES)
                publish("active.json", contents)
            os.fsync(managed_descriptor)
        finally:
            os.close(managed_descriptor)
    finally:
        os.close(root_descriptor)


def checked_files(root: Path) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise FileSafetyError("generation.invalid: generation root must be a directory")
    files: dict[str, str] = {}
    folded: dict[str, str] = {}
    for directory, directory_names, filenames in os.walk(root, followlinks=False):
        directory_names.sort()
        filenames.sort()
        for name in (*directory_names, *filenames):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            prior = folded.get(relative.casefold())
            if prior is not None and prior != relative:
                raise FileSafetyError("generation.invalid: case-fold path collision")
            folded[relative.casefold()] = relative
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise FileSafetyError("generation.invalid: symlinks are not allowed")
            if path.is_dir():
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise FileSafetyError("generation.invalid: special files are not allowed")
            if metadata.st_nlink != 1:
                raise FileSafetyError("generation.invalid: hardlinked files are not allowed")
            files[relative] = sha256_file(path)
    return dict(sorted(files.items()))
