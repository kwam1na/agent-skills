"""Claude Code: the skills projection, and the hook entries the lifecycle owns in ``.claude/settings.json``.

The lifecycle writes one entry for each Claude Code event some installed hook
declares, under ``hooks.Stop`` or ``hooks.SessionStart``. An entry is the
lifecycle's when its command is exactly ``command(event)``; every other key and
entry in the file is the user's, and the lifecycle never reads, moves or
rewrites it.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .base import ExposureError, HostProjection


CLAUDE_CODE = HostProjection(host="claude-code", root=Path(".claude/skills"))

SETTINGS_FILE = ".claude/settings.json"
# Each hook event the dispatcher serves, by its manifest name, with the name Claude Code gives it.
HOOK_EVENTS = {"session-start": "SessionStart", "stop": "Stop"}
HOOK_TIMEOUT = 30


def command(event: str) -> str:
    """The installed command for ``event``. It names ``current``, and ``|| exit 1`` keeps it from ever exiting 2."""
    return (
        'python3 -B "${CLAUDE_PROJECT_DIR}/.agent-skills/current/__main__.py" '
        f"hook claude-code {event} || exit 1"
    )


def entry(event: str) -> dict:
    return {"hooks": [{"type": "command", "command": command(event), "timeout": HOOK_TIMEOUT}]}


def receipt_events(receipt: dict | None) -> list[str]:
    if receipt is None:
        return []
    return list(receipt.get("hooks", {}).get("events", []))


def receipt_created(receipt: dict | None) -> bool:
    if receipt is None:
        return False
    return bool(receipt.get("hooks", {}).get("created", False))


def hooks_member(events: Iterable[str], created: bool) -> dict | None:
    """The receipt's ``hooks`` member, or ``None`` when the lifecycle neither owns an entry nor created the file."""
    events = sorted(set(events))
    if not events and not created:
        return None
    return {"created": created, "events": events, "file": SETTINGS_FILE}


def _refuse(problem: str) -> ExposureError:
    return ExposureError(f"exposure-conflict: {SETTINGS_FILE} {problem}")


def _carries(item: object, event: str) -> bool:
    """Whether an event list's item holds the lifecycle's command for ``event`` anywhere in its own hooks."""
    if not isinstance(item, dict) or not isinstance(item.get("hooks"), list):
        return False
    return any(
        isinstance(hook, dict) and hook.get("command") == command(event)
        for hook in item["hooks"]
    )


@dataclass(frozen=True)
class Snapshot:
    """The settings file as one read saw it: its bytes (``None`` when absent) and its parsed object."""

    contents: bytes | None
    document: dict | None

    @property
    def digest(self) -> str | None:
        return None if self.contents is None else hashlib.sha256(self.contents).hexdigest()


class HookSettings:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root
        self.folder = repository_root / ".claude"
        self.path = repository_root / SETTINGS_FILE

    # Reading -----------------------------------------------------------------

    def _read_bytes(self) -> bytes | None:
        if self.folder.is_symlink() or (self.folder.exists() and not self.folder.is_dir()):
            raise ExposureError(
                "exposure-conflict: .claude is not a directory; make it a plain directory and retry"
            )
        if self.path.is_symlink():
            raise _refuse("is a symlink; replace it with a plain file and retry")
        if not self.path.exists():
            return None
        if not self.path.is_file():
            raise _refuse("is not a file; replace it with a JSON object and retry")
        try:
            return self.path.read_bytes()
        except OSError as error:
            raise _refuse("is unreadable; make it readable and retry") from error

    def snapshot(self) -> Snapshot:
        """The file as it is now, refused with ``exposure-conflict`` and its fix when the lifecycle can't own entries in it."""
        contents = self._read_bytes()
        if contents is None:
            return Snapshot(None, None)
        try:
            document = json.loads(contents.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            document = None
        if not isinstance(document, dict):
            raise _refuse("is not a JSON object; make it one, or remove it, and retry")
        hooks = document.get("hooks", {})
        if not isinstance(hooks, dict):
            raise _refuse('"hooks" is not an object; make it one, or remove it, and retry')
        for event, name in HOOK_EVENTS.items():
            items = hooks.get(name, [])
            if not isinstance(items, list):
                raise _refuse(f'"hooks.{name}" is not a list; make it one, or remove it, and retry')
            for item in items:
                if _carries(item, event) and item != entry(event):
                    raise _refuse(
                        f'"hooks.{name}" holds an entry running the agent-skills dispatcher that differs '
                        f"from the entry the lifecycle writes; restore it to {json.dumps(entry(event))}, "
                        "or remove it, and retry"
                    )
        return Snapshot(contents, document)

    def owned_events(self) -> list[str] | None:
        """The events whose list holds exactly one owned entry and no altered one, or ``None`` when the file can't be read."""
        try:
            contents = self._read_bytes()
        except ExposureError:
            return None
        if contents is None:
            return []
        try:
            document = json.loads(contents.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return None
        hooks = document.get("hooks", {}) if isinstance(document, dict) else None
        if not isinstance(hooks, dict):
            return None
        owned = []
        for event, name in HOOK_EVENTS.items():
            items = hooks.get(name, [])
            if not isinstance(items, list):
                return None
            carried = [item for item in items if _carries(item, event)]
            if not carried:
                continue
            if carried != [entry(event)]:
                return None
            owned.append(event)
        return sorted(owned)

    # Planning ------------------------------------------------------------------

    @staticmethod
    def planned(snapshot: Snapshot, events: Iterable[str], *, delete_if_empty: bool) -> dict | None:
        """The object the file should hold with exactly ``events`` owned, or ``None`` when it should not exist.

        Entries the lifecycle doesn't own keep their place; a missing owned entry
        goes at the end of its event's list. An event list left empty loses its
        key, and so does ``hooks`` left empty. The file is left out only when
        ``delete_if_empty`` and the object left is exactly ``{}``.
        """
        wanted = set(events)
        document = json.loads(json.dumps(snapshot.document)) if snapshot.document is not None else None
        if document is None:
            if not wanted:
                return None
            document = {}
        hooks = document.get("hooks", {})
        for event, name in HOOK_EVENTS.items():
            items = [item for item in hooks.get(name, []) if not _carries(item, event)]
            if event in wanted:
                items.append(entry(event))
            if items:
                hooks[name] = items
            else:
                hooks.pop(name, None)
        if hooks:
            document["hooks"] = hooks
        else:
            document.pop("hooks", None)
        if not document and (delete_if_empty or snapshot.contents is None):
            return None
        return document

    @staticmethod
    def render(document: dict) -> bytes:
        return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    # Writing -------------------------------------------------------------------

    def reconcile(self, snapshot: Snapshot, events: Iterable[str], *, delete_if_empty: bool) -> None:
        """Make the file hold exactly ``events`` owned, starting from ``snapshot``.

        Nothing is written when the file already matches. Immediately before the
        rename, or the deletion, the file's digest is compared with the
        snapshot's, and a change refuses with ``exposure-conflict``.
        """
        document = self.planned(snapshot, events, delete_if_empty=delete_if_empty)
        if document is None:
            if snapshot.contents is None:
                return
            self._check_unchanged(snapshot)
            self.path.unlink()
            return
        contents = self.render(document)
        if snapshot.contents is not None and json.loads(snapshot.contents.decode("utf-8")) == document:
            return
        if not self.folder.exists():
            self.folder.mkdir()
        descriptor, temporary_name = tempfile.mkstemp(prefix=".settings.json.", dir=self.folder)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(contents)
                output.flush()
                os.fsync(output.fileno())
            mode = 0o644
            if snapshot.contents is not None:
                try:
                    mode = self.path.stat().st_mode & 0o7777
                except FileNotFoundError as error:
                    raise SettingsChanged() from error
            os.chmod(temporary, mode)
            self._check_unchanged(snapshot)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _check_unchanged(self, snapshot: Snapshot) -> None:
        try:
            current = self._read_bytes()
        except ExposureError as error:
            raise SettingsChanged() from error
        if current != snapshot.contents:
            raise SettingsChanged()


class SettingsChanged(ExposureError):
    def __init__(self) -> None:
        super().__init__(
            f"exposure-conflict: {SETTINGS_FILE} changed while the lifecycle was writing it; "
            "nothing was changed, so review the file and retry"
        )
