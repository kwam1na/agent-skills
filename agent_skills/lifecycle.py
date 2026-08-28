from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .fs import MANAGED_GIT_ATTRIBUTES, publish_active_marker, repository_paths
from .generations import GenerationError, GenerationStore, inspect_release
from .journal import (
    JOURNAL_SCHEMA,
    JournalError,
    load_journal,
    write_journal,
)
from .locking import RepositoryLock
from .receipt import (
    ReceiptError,
    load_receipt,
    make_receipt,
    validate_receipt,
    version_from_receipt,
)
from .validate import canonical_json


ACTIVATION_BOUNDARIES = (
    "staged",
    "generation-ready",
    "verifying",
    "marker-published",
    "marker-committed",
    "cleanup",
)
FaultHook = Callable[[str], None]


class LifecycleError(ValueError):
    pass


def require_mutation_authority(
    maintainer: bool,
    context: str | None = None,
) -> None:
    pull_request_event = os.environ.get("GITHUB_EVENT_NAME") in {
        "pull_request",
        "pull_request_target",
    }
    declared_context = os.environ.get("AGENT_SKILLS_EXECUTION_CONTEXT") or None
    unsupported = None
    if pull_request_event:
        unsupported = "pull-request"
    elif context not in {None, "maintainer"}:
        unsupported = context
    elif declared_context not in {None, "maintainer"}:
        unsupported = declared_context
    if unsupported is not None:
        raise LifecycleError(
            f"context-unsupported: lifecycle mutation is forbidden in {unsupported} context"
        )
    if not maintainer:
        raise LifecycleError(
            "maintenance-required: mutation requires explicit maintainer repository authority"
        )


@dataclass(frozen=True)
class Status:
    active: dict | None
    blockers: tuple[str, ...]
    journal_phase: str | None
    lifecycle: str
    recovery_plan: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "blockers": list(self.blockers),
            "journalPhase": self.journal_phase,
            "lifecycle": self.lifecycle,
            "recoveryPlan": list(self.recovery_plan),
            "schemaVersion": "agent-skills-status/1",
        }

    def to_json(self) -> str:
        return canonical_json(self.as_dict())


def _active_summary(receipt: dict | None) -> dict | None:
    if receipt is None:
        return None
    return {
        "archiveSha256": receipt["release"]["archiveSha256"],
        "generation": receipt["generation"],
        "profile": receipt["release"]["profile"],
        "releaseId": receipt["release"]["releaseId"],
    }


def _same_receipt(left: dict | None, right: dict | None) -> bool:
    return left == right


class Lifecycle:
    def __init__(self, root: Path) -> None:
        self.root, self.git_path = repository_paths(root)
        self.managed_root = self.root / ".agent-skills"
        self.active_path = self.managed_root / "active.json"
        self.attributes_path = self.managed_root / ".gitattributes"
        self.journal_path = self.git_path / "journal.json"
        self.lock_path = self.git_path / "lock.json"
        self.store = GenerationStore(self.managed_root, self.git_path)

    def _active(self, *, verify: bool = True) -> dict | None:
        if (
            self.managed_root.is_symlink()
            or self.active_path.is_symlink()
            or self.attributes_path.is_symlink()
        ):
            raise GenerationError("local-divergence: active marker root is a symlink")
        if not self.active_path.exists():
            return None
        try:
            if self.attributes_path.read_bytes() != MANAGED_GIT_ATTRIBUTES:
                raise GenerationError("local-divergence: managed Git attributes disagree")
        except OSError as error:
            raise GenerationError("local-divergence: managed Git attributes are unreadable") from error
        receipt = load_receipt(self.active_path)
        if verify:
            self.store.verify(version_from_receipt(receipt))
            if receipt["previous"] is not None:
                self.store.verify(receipt["previous"])
        return receipt

    def _orphaned_generation_exists(self) -> bool:
        root = self.store.generations_root
        return (
            self.attributes_path.exists()
            or self.attributes_path.is_symlink()
            or (root.is_dir() and any(root.iterdir()))
        )

    def status(self) -> Status:
        try:
            journal = load_journal(self.journal_path)
        except (JournalError, ReceiptError):
            return Status(
                None,
                ("local-divergence",),
                "invalid",
                "recovery-needed",
                ("inspect-invalid-journal",),
            )
        try:
            active = self._active()
        except ReceiptError:
            return Status(
                None,
                ("receipt-invalid",),
                journal["phase"] if journal else None,
                "externally-transitioned",
                ("review-receipt",),
            )
        except GenerationError:
            try:
                summary = _active_summary(self._active(verify=False))
            except (ReceiptError, GenerationError):
                summary = None
            return Status(
                summary,
                ("local-divergence",),
                journal["phase"] if journal else None,
                "externally-transitioned",
                ("review-managed-diff",),
            )

        if journal is not None:
            phase = journal["phase"]
            if phase not in {"marker-committed", "cleanup"}:
                return Status(
                    _active_summary(active),
                    (),
                    phase,
                    "recovery-needed",
                    ("recover",),
                )
            if not _same_receipt(active, journal["target"]):
                return Status(
                    _active_summary(active),
                    ("local-divergence",),
                    phase,
                    "externally-transitioned",
                    ("review-managed-diff",),
                )
            lifecycle = "current" if active is not None else "absent"
            return Status(
                _active_summary(active),
                (),
                phase,
                lifecycle,
                ("finish-cleanup",),
            )

        if active is not None:
            try:
                retained = self.store.installed_generations()
            except GenerationError:
                return Status(
                    _active_summary(active),
                    ("local-divergence",),
                    None,
                    "externally-transitioned",
                    ("review-orphaned-generations",),
                )
            if retained != self._keep_for(active):
                return Status(
                    _active_summary(active),
                    ("local-divergence",),
                    None,
                    "externally-transitioned",
                    ("review-orphaned-generations",),
                )
            return Status(_active_summary(active), (), None, "current", ())
        if self._orphaned_generation_exists():
            return Status(
                None,
                ("local-divergence",),
                None,
                "externally-transitioned",
                ("review-orphaned-generations",),
            )
        return Status(None, (), None, "absent", ())

    def plan(self, archive: Path, metadata: Path) -> dict[str, object]:
        before = self.status()
        if before.blockers or before.lifecycle in {"recovery-needed", "externally-transitioned"}:
            return {
                "action": "blocked",
                "blockers": list(before.blockers),
                "current": before.active,
                "schemaVersion": "agent-skills-plan/1",
                "target": None,
            }
        target = inspect_release(archive, metadata)
        current = self._active()
        if current is None:
            action = "install"
        elif current["generation"] == target["generation"]:
            action = "no-op"
        else:
            action = "update"
        return {
            "action": action,
            "blockers": [],
            "current": _active_summary(current),
            "schemaVersion": "agent-skills-plan/1",
            "target": {
                "archiveSha256": target["release"]["archiveSha256"],
                "generation": target["generation"],
                "profile": target["release"]["profile"],
                "releaseId": target["release"]["releaseId"],
            },
        }

    def diff(self, archive: Path, metadata: Path) -> dict[str, object]:
        target = inspect_release(archive, metadata)
        try:
            current = self._active()
        except (ReceiptError, GenerationError) as error:
            raise LifecycleError("local-divergence: cannot diff an invalid installation") from error
        current_files = (
            {record["path"]: record["sha256"] for record in current["files"]}
            if current is not None
            else {}
        )
        target_files = {record["path"]: record["sha256"] for record in target["files"]}
        return {
            "added": sorted(set(target_files) - set(current_files)),
            "changed": sorted(
                path
                for path in set(current_files) & set(target_files)
                if current_files[path] != target_files[path]
            ),
            "removed": sorted(set(current_files) - set(target_files)),
            "schemaVersion": "agent-skills-diff/1",
        }

    @staticmethod
    def _fault(fault: FaultHook | None, phase: str) -> None:
        if fault is not None:
            fault(phase)

    def _write_active(self, target: dict | None) -> None:
        if self.managed_root.is_symlink():
            raise LifecycleError("local-divergence: managed root is a symlink")
        contents = None
        if target is not None:
            validate_receipt(target)
            contents = canonical_json(target).encode()
        publish_active_marker(self.root, contents)

    def _new_journal(
        self,
        operation: str,
        operation_id: str,
        prior: dict | None,
        target: dict | None,
    ) -> dict:
        return {
            "operation": operation,
            "operationId": operation_id,
            "phase": "staged",
            "prior": prior,
            "schemaVersion": JOURNAL_SCHEMA,
            "target": target,
        }

    def _keep_for(self, receipt: dict | None) -> set[str]:
        if receipt is None:
            return set()
        keep = {receipt["generation"]}
        if receipt["previous"] is not None:
            keep.add(receipt["previous"]["generation"])
        return keep

    def _finish_cleanup(
        self,
        journal: dict,
        fault: FaultHook | None = None,
    ) -> None:
        target = journal["target"]
        self.store.cleanup(
            self._keep_for(target),
            journal["operationId"],
            fault,
        )
        self.store.cleanup_staging(journal["operationId"])
        if target is None:
            self._write_active(None)

    def _recover_locked(self) -> dict[str, object]:
        journal = load_journal(self.journal_path)
        if journal is None:
            return {"action": "no-op", "schemaVersion": "agent-skills-operation/1"}
        try:
            active = self._active()
        except (ReceiptError, GenerationError) as error:
            raise LifecycleError("local-divergence: active state changed during recovery") from error
        target = journal["target"]
        prior = journal["prior"]
        if _same_receipt(active, target):
            if target is not None:
                self.store.verify(version_from_receipt(target))
                if target["previous"] is not None:
                    self.store.verify(target["previous"])
            journal = write_journal(self.journal_path, journal, "marker-committed")
            self._finish_cleanup(journal)
            write_journal(self.journal_path, journal, "cleanup")
            self.journal_path.unlink()
            return {
                "action": "complete-current" if target is not None else "complete-removal",
                "schemaVersion": "agent-skills-operation/1",
            }
        if _same_receipt(active, prior):
            if prior is not None:
                self.store.verify(version_from_receipt(prior))
            else:
                self._write_active(None)
            self.store.cleanup(self._keep_for(prior), journal["operationId"])
            self.store.cleanup_staging(journal["operationId"])
            self.journal_path.unlink()
            return {
                "action": "restore-prior",
                "schemaVersion": "agent-skills-operation/1",
            }
        raise LifecycleError(
            "local-divergence: active marker matches neither recovery endpoint"
        )

    def recover(
        self,
        *,
        maintainer: bool = False,
        context: str | None = None,
    ) -> dict[str, object]:
        require_mutation_authority(maintainer, context)
        with RepositoryLock(self.lock_path):
            return self._recover_locked()

    def _apply_release(
        self,
        archive: Path,
        metadata: Path,
        *,
        require_current: bool,
        maintainer: bool,
        context: str | None,
        fault: FaultHook | None,
    ) -> dict[str, object]:
        require_mutation_authority(maintainer, context)
        with RepositoryLock(self.lock_path):
            self._recover_locked()
            state = self.status()
            if state.lifecycle not in {"absent", "current"} or state.blockers:
                raise LifecycleError("local-divergence: installation is not safe to mutate")
            prior = self._active()
            if require_current and prior is None:
                raise LifecycleError("lifecycle.absent: update requires a current installation")
            inspected = inspect_release(archive, metadata)
            if prior is not None and prior["generation"] == inspected["generation"]:
                return {
                    "action": "no-op",
                    "schemaVersion": "agent-skills-operation/1",
                    "status": self.status().as_dict(),
                }
            operation_id = uuid.uuid4().hex
            version, stage = self.store.stage(archive, metadata, operation_id)
            action = "install" if prior is None else "update"
            target = make_receipt(
                version,
                version_from_receipt(prior) if prior is not None else None,
            )
            journal = self._new_journal(action, operation_id, prior, target)
            journal = write_journal(self.journal_path, journal, "staged")
            self._fault(fault, "staged")
            self.store.promote(stage, version)
            journal = write_journal(self.journal_path, journal, "generation-ready")
            self._fault(fault, "generation-ready")
            self.store.verify(version)
            journal = write_journal(self.journal_path, journal, "verifying")
            self._fault(fault, "verifying")
            self.store.verify(version)
            self._write_active(target)
            self._fault(fault, "marker-published")
            journal = write_journal(self.journal_path, journal, "marker-committed")
            self._fault(fault, "marker-committed")
            self._finish_cleanup(journal, fault)
            journal = write_journal(self.journal_path, journal, "cleanup")
            self._fault(fault, "cleanup")
            self.journal_path.unlink()
            return {
                "action": action,
                "schemaVersion": "agent-skills-operation/1",
                "status": self.status().as_dict(),
            }

    def install(
        self,
        archive: Path,
        metadata: Path,
        *,
        maintainer: bool = False,
        context: str | None = None,
        fault: FaultHook | None = None,
    ) -> dict[str, object]:
        return self._apply_release(
            archive,
            metadata,
            require_current=False,
            maintainer=maintainer,
            context=context,
            fault=fault,
        )

    def update(
        self,
        archive: Path,
        metadata: Path,
        *,
        maintainer: bool = False,
        context: str | None = None,
        fault: FaultHook | None = None,
    ) -> dict[str, object]:
        return self._apply_release(
            archive,
            metadata,
            require_current=True,
            maintainer=maintainer,
            context=context,
            fault=fault,
        )

    def rollback(
        self,
        *,
        maintainer: bool = False,
        context: str | None = None,
        fault: FaultHook | None = None,
    ) -> dict[str, object]:
        require_mutation_authority(maintainer, context)
        with RepositoryLock(self.lock_path):
            self._recover_locked()
            prior = self._active()
            if prior is None or prior["previous"] is None:
                raise LifecycleError("rollback-unavailable: no verified prior generation is retained")
            target = make_receipt(prior["previous"], version_from_receipt(prior))
            self.store.verify(version_from_receipt(target))
            operation_id = uuid.uuid4().hex
            journal = self._new_journal("rollback", operation_id, prior, target)
            for phase in ("staged", "generation-ready", "verifying"):
                journal = write_journal(self.journal_path, journal, phase)
                self._fault(fault, phase)
            self.store.verify(version_from_receipt(target))
            self._write_active(target)
            self._fault(fault, "marker-published")
            journal = write_journal(self.journal_path, journal, "marker-committed")
            self._fault(fault, "marker-committed")
            self._finish_cleanup(journal, fault)
            journal = write_journal(self.journal_path, journal, "cleanup")
            self._fault(fault, "cleanup")
            self.journal_path.unlink()
            return {
                "action": "rollback",
                "schemaVersion": "agent-skills-operation/1",
                "status": self.status().as_dict(),
            }

    def remove(
        self,
        *,
        maintainer: bool = False,
        context: str | None = None,
        fault: FaultHook | None = None,
    ) -> dict[str, object]:
        require_mutation_authority(maintainer, context)
        with RepositoryLock(self.lock_path):
            self._recover_locked()
            state = self.status()
            if state.blockers:
                raise LifecycleError(state.blockers[0] + ": destructive removal is blocked")
            prior = self._active()
            if prior is None:
                return {"action": "no-op", "schemaVersion": "agent-skills-operation/1"}
            operation_id = uuid.uuid4().hex
            journal = self._new_journal("remove", operation_id, prior, None)
            for phase in ("staged", "generation-ready", "verifying"):
                journal = write_journal(self.journal_path, journal, phase)
                self._fault(fault, phase)
            self._write_active(None)
            self._fault(fault, "marker-published")
            journal = write_journal(self.journal_path, journal, "marker-committed")
            self._fault(fault, "marker-committed")
            self._finish_cleanup(journal, fault)
            journal = write_journal(self.journal_path, journal, "cleanup")
            self._fault(fault, "cleanup")
            self.journal_path.unlink()
            return {
                "action": "remove",
                "schemaVersion": "agent-skills-operation/1",
                "status": self.status().as_dict(),
            }
