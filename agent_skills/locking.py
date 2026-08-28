from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .validate import canonical_json


if os.name == "nt":
    import msvcrt
else:
    import fcntl


LOCK_PREFIX = b" "
MAX_LOCK_RECORD_BYTES = 4096


class LockError(ValueError):
    pass


def _open_lock(path: Path, *, create: bool) -> int:
    flags = os.O_RDWR
    if create:
        flags |= os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileNotFoundError as error:
        raise LockError("lock-invalid: lifecycle lock does not exist") from error
    except OSError as error:
        raise LockError("lock-invalid: lifecycle lock is unsafe or unreadable") from error
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, LOCK_PREFIX)
            os.fsync(descriptor)
        return descriptor
    except OSError:
        os.close(descriptor)
        raise


def _try_lock(descriptor: int, *, shared: bool = False) -> bool:
    if os.name == "nt":
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            mode = msvcrt.LK_NBRLCK if shared else msvcrt.LK_NBLCK
            msvcrt.locking(descriptor, mode, 1)
        except OSError:
            return False
        return True
    try:
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(descriptor: int) -> None:
    if os.name == "nt":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _owner_document(descriptor: int) -> dict | None:
    size = os.fstat(descriptor).st_size
    if size < 1 or size > MAX_LOCK_RECORD_BYTES + 1:
        raise LockError("lock-invalid: inspect the lock before explicit recovery")
    os.lseek(descriptor, 1, os.SEEK_SET)
    contents = os.read(descriptor, size - 1)
    if not contents:
        return None
    try:
        document = json.loads(contents.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LockError("lock-invalid: inspect the lock before explicit recovery") from error
    if (
        not isinstance(document, dict)
        or document.get("schemaVersion") != "agent-skills-lock/1"
        or not isinstance(document.get("createdNs"), int)
        or document["createdNs"] <= 0
        or not isinstance(document.get("pid"), int)
        or document["pid"] <= 0
    ):
        raise LockError("lock-invalid: inspect the lock before explicit recovery")
    return document


def _write_owner(descriptor: int, document: dict | None) -> None:
    contents = b"" if document is None else canonical_json(document).encode()
    os.ftruncate(descriptor, 1)
    os.lseek(descriptor, 1, os.SEEK_SET)
    while contents:
        written = os.write(descriptor, contents)
        contents = contents[written:]
    os.fsync(descriptor)


class RepositoryLock:
    def __init__(self, path: Path, *, shared: bool = False) -> None:
        self.path = path
        self.shared = shared
        self.descriptor: int | None = None

    def __enter__(self) -> "RepositoryLock":
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise LockError("lock-invalid: lifecycle lock root is a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.parent.is_symlink():
            raise LockError("lock-invalid: lifecycle lock root is a symlink")
        descriptor = _open_lock(self.path, create=True)
        locked = False
        try:
            if not _try_lock(descriptor, shared=self.shared):
                raise LockError(
                    "lock-busy: another lifecycle operation owns the repository lock"
                )
            locked = True
            if _owner_document(descriptor) is not None:
                raise LockError(
                    "lock-stale: explicit stale-lock recovery is required"
                )
            if not self.shared:
                _write_owner(
                    descriptor,
                    {
                        "createdNs": time.time_ns(),
                        "pid": os.getpid(),
                        "schemaVersion": "agent-skills-lock/1",
                    },
                )
            self.descriptor = descriptor
            return self
        except Exception:
            if locked:
                _unlock(descriptor)
            os.close(descriptor)
            raise

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            return
        try:
            if not self.shared:
                _write_owner(descriptor, None)
        finally:
            try:
                _unlock(descriptor)
            finally:
                os.close(descriptor)
                self.descriptor = None

    @staticmethod
    def break_stale(path: Path) -> None:
        if path.is_symlink() or path.parent.is_symlink():
            raise LockError("lock-invalid: lifecycle lock root is a symlink")
        descriptor = _open_lock(path, create=False)
        locked = False
        try:
            if not _try_lock(descriptor):
                raise LockError("lock-busy: lock owner is still running")
            locked = True
            _owner_document(descriptor)
            _write_owner(descriptor, None)
        finally:
            if locked:
                _unlock(descriptor)
            os.close(descriptor)
