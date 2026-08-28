from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .validate import canonical_json


class LockError(ValueError):
    pass


class RepositoryLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.acquired = False

    def __enter__(self) -> "RepositoryLock":
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise LockError("lock-invalid: lifecycle lock root is a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.parent.is_symlink():
            raise LockError("lock-invalid: lifecycle lock root is a symlink")
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            raise LockError("lock-busy: another lifecycle operation owns the repository lock") from error
        document = {
            "createdNs": time.time_ns(),
            "pid": os.getpid(),
            "schemaVersion": "agent-skills-lock/1",
        }
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(canonical_json(document))
            output.flush()
            os.fsync(output.fileno())
        self.acquired = True
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False

    @staticmethod
    def break_stale(path: Path) -> None:
        if path.is_symlink() or path.parent.is_symlink():
            raise LockError("lock-invalid: lifecycle lock root is a symlink")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            pid = document["pid"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise LockError("lock-invalid: inspect the lock before explicit removal") from error
        if not isinstance(pid, int) or pid <= 0:
            raise LockError("lock-invalid: inspect the lock before explicit removal")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            path.unlink()
            return
        except PermissionError as error:
            raise LockError("lock-busy: lock owner liveness cannot be disproved") from error
        raise LockError("lock-busy: lock owner is still running")
