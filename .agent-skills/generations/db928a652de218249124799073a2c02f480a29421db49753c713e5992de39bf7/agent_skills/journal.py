from __future__ import annotations

import json
import re
from pathlib import Path

from .fs import atomic_write
from .receipt import validate_receipt
from .validate import canonical_json


JOURNAL_SCHEMA = "agent-skills-journal/1"
JOURNAL_PHASES = (
    "staged",
    "generation-ready",
    "exposing-codex",
    "exposing-claude-code",
    "exposure-switched",
    "verifying",
    "marker-committed",
    "cleanup",
)
OPERATIONS = {"adopt", "install", "update", "rollback", "remove"}


class JournalError(ValueError):
    pass


def validate_journal(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "operation",
        "operationId",
        "phase",
        "prior",
        "schemaVersion",
        "target",
    }:
        raise JournalError("journal-invalid: journal shape is malformed")
    if value["schemaVersion"] != JOURNAL_SCHEMA:
        raise JournalError("journal-invalid: unsupported journal schema")
    if value["operation"] not in OPERATIONS:
        raise JournalError("journal-invalid: unknown operation")
    if (
        not isinstance(value["operationId"], str)
        or re.fullmatch(r"[a-f0-9]{32}", value["operationId"]) is None
    ):
        raise JournalError("journal-invalid: operation id is unsafe")
    if value["phase"] not in JOURNAL_PHASES:
        raise JournalError("journal-invalid: unknown phase")
    if value["prior"] is not None:
        validate_receipt(value["prior"])
    if value["target"] is not None:
        validate_receipt(value["target"])
    if value["operation"] == "remove":
        if value["target"] is not None:
            raise JournalError("journal-invalid: remove target must be absent")
        if value["prior"] is None:
            raise JournalError("journal-invalid: removal predecessor is missing")
    elif value["operation"] == "rollback" and value["target"] is None:
        prior = value["prior"]
        if prior is None or "adoption" not in prior:
            raise JournalError("journal-invalid: deactivation predecessor is missing")
    elif value["target"] is None:
        raise JournalError("journal-invalid: activation target is missing")
    if value["operation"] == "adopt" and (
        value["prior"] is not None or "adoption" not in value["target"]
    ):
        raise JournalError("journal-invalid: adoption endpoints are malformed")
    return value


def load_journal(path: Path) -> dict | None:
    if not path.exists():
        return None
    if path.is_symlink():
        raise JournalError("journal-invalid: journal path is a symlink")
    try:
        contents = path.read_text(encoding="utf-8")
        value = json.loads(contents)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise JournalError("journal-invalid: journal is unreadable") from error
    validate_journal(value)
    if canonical_json(value) != contents:
        raise JournalError("journal-invalid: journal is not canonical JSON")
    return value


def write_journal(path: Path, journal: dict, phase: str) -> dict:
    updated = {**journal, "phase": phase}
    validate_journal(updated)
    atomic_write(path, canonical_json(updated).encode())
    return updated
