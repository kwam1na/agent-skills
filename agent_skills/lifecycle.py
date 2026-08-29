from __future__ import annotations

import errno
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .adoption import AdoptionError, PredecessorStore, body_sha256, skill_body
from .fs import MANAGED_GIT_ATTRIBUTES, publish_active_marker, repository_paths
from .generations import (
    GenerationError,
    GenerationStore,
    inspect_adoption_release,
    inspect_release,
)
from .hosts.exposure import HOSTS, ExposureError, ExposureManager
from .journal import (
    JOURNAL_SCHEMA,
    JournalError,
    load_journal,
    write_journal,
)
from .locking import RepositoryLock
from .receipt import (
    MANAGED_REPOSITORY_DIRECTORIES,
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
    "exposing-codex",
    "exposing-claude-code",
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
    exposures: tuple[dict[str, str], ...]
    journal_phase: str | None
    lifecycle: str
    recovery_plan: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "blockers": list(self.blockers),
            "exposures": list(self.exposures),
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
    summary = {
        "archiveSha256": receipt["release"]["archiveSha256"],
        "generation": receipt["generation"],
        "profile": receipt["release"]["profile"],
        "releaseId": receipt["release"]["releaseId"],
    }
    if "adoption" in receipt:
        summary["selectedSkills"] = [receipt["adoption"]["skill"]]
    return summary


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
        self.predecessors = PredecessorStore(self.managed_root, self.git_path)
        self.exposure = ExposureManager(self.root)

    def _created_directories(self, prior: dict | None) -> list[str] | None:
        if prior is not None:
            return prior.get("createdDirectories")
        return sorted(
            path
            for path in MANAGED_REPOSITORY_DIRECTORIES
            if not (self.root / path).exists()
        )

    def _prune_created_directories(self, receipt: dict | None) -> None:
        if receipt is None:
            return
        created = receipt.get("createdDirectories", [])
        for relative in sorted(
            created,
            key=lambda value: (len(Path(value).parts), value),
            reverse=True,
        ):
            self._prune_created_directory(relative)

    def _prune_created_directory(self, relative: str) -> None:
        parts = Path(relative).parts
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        descriptor_removal = (
            no_follow
            and os.open in os.supports_dir_fd
            and os.rmdir in os.supports_dir_fd
        )
        if not descriptor_removal:
            current = self.root
            for part in parts:
                current /= part
                if current.is_symlink():
                    raise LifecycleError(
                        "local-divergence: lifecycle-created directory became a symlink"
                    )
            try:
                current.rmdir()
            except FileNotFoundError:
                return
            except OSError as error:
                if error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                    return
                raise LifecycleError(
                    "local-divergence: lifecycle-created directory could not be removed safely"
                ) from error
            return

        descriptors = [os.open(self.root, directory_flags | no_follow)]
        try:
            for part in parts[:-1]:
                descriptors.append(
                    os.open(
                        part,
                        directory_flags | no_follow,
                        dir_fd=descriptors[-1],
                    )
                )
            os.rmdir(parts[-1], dir_fd=descriptors[-1])
        except FileNotFoundError:
            return
        except OSError as error:
            if error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                return
            raise LifecycleError(
                "local-divergence: lifecycle-created directory could not be removed safely"
            ) from error
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

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
            if "adoption" in receipt:
                try:
                    self.predecessors.verify(receipt["adoption"])
                except AdoptionError as error:
                    raise GenerationError(str(error)) from error
        return receipt

    def _orphaned_generation_exists(self) -> bool:
        root = self.store.generations_root
        return (
            self.attributes_path.exists()
            or self.attributes_path.is_symlink()
            or self.predecessors.exists()
            or (root.is_dir() and any(root.iterdir()))
        )

    def status(self) -> Status:
        try:
            journal = load_journal(self.journal_path)
        except (JournalError, ReceiptError):
            return Status(
                None,
                ("local-divergence",),
                (),
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
                (),
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
                (),
                journal["phase"] if journal else None,
                "externally-transitioned",
                ("review-managed-diff",),
            )

        if journal is not None:
            phase = journal["phase"]
            exposure_status, exposure_conflict = self.exposure.status(active)
            try:
                self.exposure.preflight(journal["target"], journal["prior"])
            except ExposureError:
                return Status(
                    _active_summary(active),
                    ("exposure-conflict",),
                    tuple(exposure_status),
                    phase,
                    "externally-transitioned",
                    ("review-host-projections",),
                )
            if phase not in {"marker-committed", "cleanup"}:
                if exposure_conflict:
                    exposure_status = [
                        {**record, "state": "transitioning"}
                        for record in exposure_status
                    ]
                return Status(
                    _active_summary(active),
                    (),
                    tuple(exposure_status),
                    phase,
                    "recovery-needed",
                    ("recover",),
                )
            if not _same_receipt(active, journal["target"]):
                return Status(
                    _active_summary(active),
                    ("local-divergence",),
                    tuple(exposure_status),
                    phase,
                    "externally-transitioned",
                    ("review-managed-diff",),
                )
            if exposure_conflict:
                return Status(
                    _active_summary(active),
                    (),
                    tuple(exposure_status),
                    phase,
                    "recovery-needed",
                    ("recover",),
                )
            lifecycle = "current" if active is not None else "absent"
            return Status(
                _active_summary(active),
                (),
                tuple(exposure_status),
                phase,
                lifecycle,
                ("finish-cleanup",),
            )

        if active is not None:
            exposure_status, exposure_conflict = self.exposure.status(active)
            if exposure_conflict:
                return Status(
                    _active_summary(active),
                    ("exposure-conflict",),
                    tuple(exposure_status),
                    None,
                    "externally-transitioned",
                    ("review-host-projections",),
                )
            try:
                retained = self.store.installed_generations()
            except GenerationError:
                return Status(
                    _active_summary(active),
                    ("local-divergence",),
                    tuple(exposure_status),
                    None,
                    "externally-transitioned",
                    ("review-orphaned-generations",),
                )
            if retained != self._keep_for(active):
                return Status(
                    _active_summary(active),
                    ("local-divergence",),
                    tuple(exposure_status),
                    None,
                    "externally-transitioned",
                    ("review-orphaned-generations",),
                )
            return Status(
                _active_summary(active),
                (),
                tuple(exposure_status),
                None,
                "current",
                (),
            )
        if self._orphaned_generation_exists():
            return Status(
                None,
                ("local-divergence",),
                (),
                None,
                "externally-transitioned",
                ("review-orphaned-generations",),
            )
        return Status(None, (), (), None, "absent", ())

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
        self.predecessors.cleanup_staging(journal["operationId"])
        if target is None:
            self._write_active(None)
            prior = journal["prior"]
            if prior is not None and "adoption" in prior:
                self.predecessors.remove(
                    prior["adoption"],
                    journal["operationId"],
                    fault,
                )
            self._prune_created_directories(prior)

    def _reconcile_target(self, target: dict | None, alternate: dict | None) -> None:
        if target is not None and "adoption" in target:
            for host in HOSTS:
                self.exposure.adopt_host(host, target)
            self.exposure.switch(target, alternate)
            return
        if target is None and alternate is not None and "adoption" in alternate:
            predecessor_path = self.predecessors.path(alternate["adoption"])
            if os.path.lexists(predecessor_path):
                predecessor = self.predecessors.verify(alternate["adoption"])
                self.exposure.restore_adoption(alternate, predecessor)
            else:
                self.exposure.verify_restored_adoption(alternate)
            return
        self.exposure.reconcile(target, alternate)

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
            self._reconcile_target(target, prior)
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
            self._reconcile_target(prior, target)
            self.store.cleanup(self._keep_for(prior), journal["operationId"])
            self.store.cleanup_staging(journal["operationId"])
            self.predecessors.cleanup_staging(journal["operationId"])
            if (
                target is not None
                and "adoption" in target
                and (prior is None or "adoption" not in prior)
            ):
                self.predecessors.remove(
                    target["adoption"],
                    journal["operationId"],
                )
            if prior is None:
                self._prune_created_directories(target)
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
            adoption = prior.get("adoption") if prior is not None else None
            created_directories = self._created_directories(prior)
            inspected_target = make_receipt(
                inspected,
                version_from_receipt(prior) if prior is not None else None,
                adoption,
                created_directories,
            )
            self.exposure.preflight(inspected_target, prior)
            operation_id = uuid.uuid4().hex
            version, stage = self.store.stage(archive, metadata, operation_id)
            try:
                action = "install" if prior is None else "update"
                target = make_receipt(
                    version,
                    version_from_receipt(prior) if prior is not None else None,
                    adoption,
                    created_directories,
                )
                self.exposure.preflight(target, prior)
            except (ExposureError, ReceiptError):
                self.store.cleanup_staging(operation_id)
                raise
            journal = self._new_journal(action, operation_id, prior, target)
            journal = write_journal(self.journal_path, journal, "staged")
            self._fault(fault, "staged")
            self.store.promote(stage, version)
            journal = write_journal(self.journal_path, journal, "generation-ready")
            self._fault(fault, "generation-ready")
            for host in HOSTS:
                self.exposure.reconcile_host(host, target, prior)
                phase = f"exposing-{host.host}"
                journal = write_journal(self.journal_path, journal, phase)
                self._fault(fault, phase)
            self.store.verify(version)
            journal = write_journal(self.journal_path, journal, "verifying")
            self._fault(fault, "verifying")
            self.store.verify(version)
            self.exposure.preflight(target, prior)
            self.exposure.switch(target, prior)
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

    def adopt(
        self,
        archive: Path,
        metadata: Path,
        *,
        skill: str,
        expected_prior_sha256: str,
        maintainer: bool = False,
        context: str | None = None,
        fault: FaultHook | None = None,
    ) -> dict[str, object]:
        require_mutation_authority(maintainer, context)
        with RepositoryLock(self.lock_path):
            self._recover_locked()
            state = self.status()
            if state.lifecycle != "absent" or state.blockers:
                raise LifecycleError(
                    f"lifecycle.{state.lifecycle}: adoption requires an absent managed installation"
                )
            inspected = inspect_adoption_release(
                archive,
                metadata,
                skill,
            )

            predecessor_hosts: list[str] = []
            predecessor_body: bytes | None = None
            for host in HOSTS:
                path = self.exposure._root(host) / skill
                if not os.path.lexists(path):
                    if host.host == "codex":
                        raise LifecycleError(
                            "adoption-prior-missing: Codex predecessor is required"
                        )
                    continue
                try:
                    body = skill_body(path)
                except AdoptionError as error:
                    raise LifecycleError(str(error)) from error
                if body_sha256(body) != expected_prior_sha256:
                    raise LifecycleError(
                        f"adoption-prior-mismatch: {host.host} predecessor digest disagrees"
                    )
                if predecessor_body is not None and body != predecessor_body:
                    raise LifecycleError(
                        "adoption-prior-mismatch: host predecessors are not byte-identical"
                    )
                predecessor_body = body
                predecessor_hosts.append(host.host)
            if predecessor_body is None:
                raise LifecycleError("adoption-prior-missing: Codex predecessor is required")

            adoption = {
                "predecessorHosts": sorted(predecessor_hosts),
                "predecessorSha256": expected_prior_sha256,
                "skill": skill,
            }
            created_directories = self._created_directories(None)
            target = make_receipt(
                inspected,
                None,
                adoption,
                created_directories,
            )
            self.exposure.preflight(target, None)
            operation_id = uuid.uuid4().hex
            version, stage = self.store.stage(archive, metadata, operation_id)
            target = make_receipt(
                version,
                None,
                adoption,
                created_directories,
            )
            journal = self._new_journal("adopt", operation_id, None, target)
            journal = write_journal(self.journal_path, journal, "staged")
            self.predecessors.write(adoption, predecessor_body, operation_id)
            self._fault(fault, "staged")
            self.store.promote(stage, version)
            journal = write_journal(self.journal_path, journal, "generation-ready")
            self._fault(fault, "generation-ready")
            for host in HOSTS:
                self.exposure.adopt_host(host, target)
                phase = f"exposing-{host.host}"
                journal = write_journal(self.journal_path, journal, phase)
                self._fault(fault, phase)
            self.store.verify(version)
            self.predecessors.verify(adoption)
            journal = write_journal(self.journal_path, journal, "verifying")
            self._fault(fault, "verifying")
            self.store.verify(version)
            self.predecessors.verify(adoption)
            self.exposure.preflight(target, None)
            self.exposure.switch(target, None)
            self._write_active(target)
            self._fault(fault, "marker-published")
            journal = write_journal(self.journal_path, journal, "marker-committed")
            self._fault(fault, "marker-committed")
            self._finish_cleanup(journal, fault)
            journal = write_journal(self.journal_path, journal, "cleanup")
            self._fault(fault, "cleanup")
            self.journal_path.unlink()
            return {
                "action": "adopt",
                "schemaVersion": "agent-skills-operation/1",
                "status": self.status().as_dict(),
            }

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
            if prior is None:
                raise LifecycleError("rollback-unavailable: no verified prior generation is retained")
            if prior["previous"] is None and "adoption" in prior:
                return self._deactivate_adoption_locked(prior, "rollback", fault)
            if prior["previous"] is None:
                raise LifecycleError(
                    "rollback-unavailable: no verified prior generation is retained; "
                    "use remove --maintenance to return to absent"
                )
            target = make_receipt(
                prior["previous"],
                version_from_receipt(prior),
                prior.get("adoption"),
                prior.get("createdDirectories"),
            )
            self.store.verify(version_from_receipt(target))
            self.exposure.preflight(target, prior)
            operation_id = uuid.uuid4().hex
            journal = self._new_journal("rollback", operation_id, prior, target)
            for phase in ("staged", "generation-ready"):
                journal = write_journal(self.journal_path, journal, phase)
                self._fault(fault, phase)
            for host in HOSTS:
                self.exposure.reconcile_host(host, target, prior)
                phase = f"exposing-{host.host}"
                journal = write_journal(self.journal_path, journal, phase)
                self._fault(fault, phase)
            self.store.verify(version_from_receipt(target))
            journal = write_journal(self.journal_path, journal, "verifying")
            self._fault(fault, "verifying")
            self.store.verify(version_from_receipt(target))
            self.exposure.preflight(target, prior)
            self.exposure.switch(target, prior)
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

    def _deactivate_adoption_locked(
        self,
        prior: dict,
        operation: str,
        fault: FaultHook | None,
    ) -> dict[str, object]:
        self.store.verify(version_from_receipt(prior))
        predecessor = self.predecessors.verify(prior["adoption"])
        self.exposure.preflight(None, prior)
        operation_id = uuid.uuid4().hex
        journal = self._new_journal(operation, operation_id, prior, None)
        for phase in ("staged", "generation-ready", "verifying"):
            journal = write_journal(self.journal_path, journal, phase)
            self._fault(fault, phase)
        self.store.verify(version_from_receipt(prior))
        self.predecessors.verify(prior["adoption"])
        self.exposure.restore_adoption(prior, predecessor)
        for host in HOSTS:
            phase = f"exposing-{host.host}"
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
            "action": "rollback-adoption" if operation == "rollback" else "remove",
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
            if "adoption" in prior:
                return self._deactivate_adoption_locked(prior, "remove", fault)
            operation_id = uuid.uuid4().hex
            self.exposure.preflight(None, prior)
            journal = self._new_journal("remove", operation_id, prior, None)
            for phase in ("staged", "generation-ready"):
                journal = write_journal(self.journal_path, journal, phase)
                self._fault(fault, phase)
            journal = write_journal(self.journal_path, journal, "verifying")
            self._fault(fault, "verifying")
            self.store.verify(version_from_receipt(prior))
            if prior["previous"] is not None:
                self.store.verify(prior["previous"])
            self.exposure.preflight(None, prior)
            self.exposure.switch(None, prior)
            for host in HOSTS:
                self.exposure.reconcile_host(host, None, prior)
                phase = f"exposing-{host.host}"
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
