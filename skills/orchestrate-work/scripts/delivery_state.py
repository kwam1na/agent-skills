#!/usr/bin/env python3
"""Keep a delivery's state document and append-only journal.

Run with ``python3 -B``. Every write takes a short state lock, validates the
result against the skill's schemas, and replaces the file atomically. A patch
is pinned to the state version it was written against; a stale version is
refused and nothing is written. Journal appends are serialized under the same
short lock and are never refused for concurrency.

The state lock is separate from the merge-queue runner's long-held queue lock,
so a runner holding the queue never blocks the lead from writing state.

All input and output is byte-exact and independent of the platform: files are
UTF-8 with "\n" line ends, stdin is read as UTF-8, and stdout is ASCII JSON, so
any console encoding can carry it.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import base64
import hashlib
import contextlib
import copy
import datetime
import fnmatch
import json
import math
import posixpath
import os
import re
import select
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Iterator


if os.name == "nt":
    import msvcrt
else:
    import fcntl


STATE_FILE = "state.json"
JOURNAL_FILE = "journal.jsonl"
STATE_LOCK = "state.lock"
# Reserved for the merge-queue runner, which holds it for a whole run.
QUEUE_LOCK = "queue.lock"
# What was last published, and the outbox a host publisher leaves for the lead.
PUBLISHED_FILE = "published.json"
# One line per successful write, for a live view to wait on: never read as the record.
CHANGES_FILE = "changes.jsonl"
# The change log is trimmed to its latest half once past this; a live view
# needs only to see that it moved, and a page that reconnects gets the whole
# record, so no history is needed to catch up.
CHANGES_LIMIT_BYTES = 64 * 1024
# A recovery in progress: written before a missed merge is recorded, removed
# once its journal entry is appended, and finished by the next reconcile.
RECOVERY_FILE = "recovery.json"
RECOVERY_QUARANTINE = "recovery.json.invalid"
OUTBOX_DIRECTORY = "outbox"
OUTBOX_FILE = "outbox.json"
# A snapshot older than this is refused, so a skipped listing fails loudly.
SNAPSHOT_MAX_AGE_SECONDS = 900.0
# How far ahead of this machine a snapshot's clock may run before it is refused.
SNAPSHOT_CLOCK_SKEW_SECONDS = 60.0
RECONCILE_ATTEMPTS = 5
# How many times a lane's focus write reads a record that moved under it again before it is refused.
FOCUS_ATTEMPTS = 5
STATE_VERSION = "delivery-state/1"
WRITERS = ("lead", "runner", "lane")
# What a lane writes about itself, through the tool.
LANE_FIELDS = ("focus", "state", "waitingOn", "since")
# The runner writes a row only to say what became of a candidate; focusAt, focusBy, waitingBy and restedFrom are the tool's alone, for every writer.
RUNNER_LANE = ("focus", "state", "waitingOn", "since")
# A review round is the lead's or a lane's record of its own review; the runner
# never reviews, so it never writes one.
REVIEW_KIND = "review.round"
SEVERITIES = ("P0", "P1", "P2", "P3")
LOCK_TIMEOUT_SECONDS = 30.0

# Arrays whose elements a patch may select, and the field that names each one.
ELEMENT_KEYS = {
    "queue": "subject",
    "planned": "subject",
    "lanes": "id",
    "leases": "id",
    "finish": "id",
    "followups": "id",
}
SELECTOR = re.compile(r"^(?P<array>[a-z]+)\[(?P<key>[A-Za-z]+)=(?P<value>[^\]]+)\]$")
# Fields only the runner writes. Everything else, apart from the fields the
# tool itself maintains, belongs to the lead.
CLOSED_KIND = "delivery.closed"
RUNNER_META = ("checking", "baseHead", "releasedHead", "checkHistory", "baseReruns", "sources", "sourceReports")
RUNNER_QUEUE = ("stage", "checks", "syncedHead", "checkSelection")
# Written only by a lane's hand-off, through the tool: no patch, the lead's or the runner's, changes it.
HANDOFF_QUEUE = ("evidence",)
# Written only by the lead's select command, through the tool, so every selection is validated and journaled.
SELECT_QUEUE = ("leadSelection",)
EVIDENCE_FIELDS = ("check", "definition", "head", "tree", "result")
EVIDENCE_COMMIT = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
EVIDENCE_DEFINITION = re.compile(r"^sha256:[0-9a-f]{64}$")
TOOL_OWNED = ("schemaVersion", "version")
# A queue element the lead adds starts with the runner's fields empty.
QUEUE_START = {"stage": "queued", "checks": [], "syncedHead": None}


# A standard-library validator for the schema subset the skill's schemas use:
# type, const, enum, properties, required, additionalProperties, items,
# minLength, minimum, pattern, anyOf, $ref (local "#/$defs/..." only) and $defs.
# Annotation keywords ($id, title, description) are ignored. Any other keyword
# is refused when a schema is loaded, so a schema can never rely on a keyword
# this validator would silently skip.

# Every schema the skill uses, keyed by its name, such as "delivery-state/1".
SCHEMA_FILE = Path(__file__).resolve().parent.parent / "references" / "schemas.json"
SUPPORTED = frozenset({
    "type", "const", "enum", "properties", "required", "additionalProperties",
    "items", "minLength", "maxLength", "minimum", "pattern", "anyOf", "$ref", "$defs",
})
ANNOTATIONS = frozenset({"$id", "title", "description"})
TYPES = frozenset({"null", "boolean", "integer", "number", "string", "array", "object"})


class SchemaError(ValueError):
    """The schema itself uses something this validator does not support."""


def _check_schema(node: object, where: str, root: dict | None = None) -> None:
    if isinstance(node, bool):
        return
    if not isinstance(node, dict):
        raise SchemaError(f"{where}: a schema must be an object or a boolean")
    unknown = set(node) - SUPPORTED - ANNOTATIONS
    if unknown:
        raise SchemaError(f"{where}: unsupported keyword {sorted(unknown)[0]}")
    declared = node.get("type")
    if declared is not None:
        names = declared if isinstance(declared, list) else [declared]
        if not names or any(name not in TYPES for name in names):
            raise SchemaError(f"{where}: unsupported type {declared!r}")
    root = node if root is None else root
    pattern = node.get("pattern")
    if pattern is not None and (not isinstance(pattern, str) or "$" in pattern[:-1] or pattern.endswith("\\$")):
        # ECMA-262 anchors are applied only to a single final "$".
        raise SchemaError(f"{where}: a pattern may anchor with \"$\" only at its very end")
    if "$ref" in node:
        reference = node["$ref"]
        name = reference[len("#/$defs/"):] if isinstance(reference, str) else ""
        # Plain names only: no pointer escapes, no nesting, and the target exists.
        if (
            not isinstance(reference, str)
            or not reference.startswith("#/$defs/")
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", name)
            or name not in root.get("$defs", {})
        ):
            raise SchemaError(f"{where}: $ref must name an existing #/$defs entry: {reference!r}")
    for key in ("properties", "$defs"):
        for name, child in node.get(key, {}).items():
            _check_schema(child, f"{where}/{key}/{name}", root)
    for key in ("items", "additionalProperties"):
        if key in node:
            _check_schema(node[key], f"{where}/{key}", root)
    for index, child in enumerate(node.get("anyOf", [])):
        _check_schema(child, f"{where}/anyOf/{index}", root)


def load_schema(name: str) -> dict:
    """Load and check one schema, by name, from the skill's references/schemas.json."""
    schemas = json.loads(SCHEMA_FILE.read_bytes().decode("utf-8"))
    if name not in schemas:
        raise SchemaError(f"no schema named {name}")
    schema = schemas[name]
    _check_schema(schema, name)
    return schema


def _is_type(value: object, name: str) -> bool:
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        if isinstance(value, bool):
            return False
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "string":
        return isinstance(value, str)
    if name == "array":
        return isinstance(value, list)
    return isinstance(value, dict)


def _equal(left: object, right: object) -> bool:
    # JSON equality: booleans are never numbers, and 1 equals 1.0.
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right or (
        isinstance(left, (int, float)) and isinstance(right, (int, float)) and left == right
    )


def _ecma_anchors(pattern: str) -> str:
    # In ECMA-262, which the schema dialect specifies, "$" matches only at the
    # very end; Python's "$" also matches before a final newline. These schemas
    # anchor only at the end of the pattern.
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        return pattern[:-1] + r"\Z"
    return pattern


def _pointer(path: tuple[object, ...]) -> str:
    return "/" + "/".join(str(part) for part in path) if path else "/"


def _errors(value: object, node: object, root: dict, path: tuple[object, ...]) -> list[str]:
    if node is True:
        return []
    if node is False:
        return [f"{_pointer(path)}: no value is allowed here"]
    assert isinstance(node, dict)
    if "$ref" in node:
        found = _errors(value, root["$defs"][node["$ref"][len("#/$defs/"):]], root, path)
        if found:
            return found
    errors: list[str] = []
    where = _pointer(path)
    if "type" in node:
        names = node["type"] if isinstance(node["type"], list) else [node["type"]]
        if not any(_is_type(value, name) for name in names):
            return [f"{where}: expected {' or '.join(names)}"]
    if "const" in node and not _equal(value, node["const"]):
        errors.append(f"{where}: expected {json.dumps(node['const'])}")
    if "enum" in node and not any(_equal(value, option) for option in node["enum"]):
        errors.append(f"{where}: not one of the allowed values")
    if "anyOf" in node and not any(
        not _errors(value, option, root, path) for option in node["anyOf"]
    ):
        errors.append(f"{where}: matches none of the allowed shapes")
    if isinstance(value, str):
        if "minLength" in node and len(value) < node["minLength"]:
            errors.append(f"{where}: shorter than {node['minLength']}")
        if "maxLength" in node and len(value) > node["maxLength"]:
            errors.append(f"{where}: longer than {node['maxLength']} characters")
        if "pattern" in node and re.search(_ecma_anchors(node["pattern"]), value) is None:
            errors.append(f"{where}: does not match the required pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in node and value < node["minimum"]:
            errors.append(f"{where}: below {node['minimum']}")
    if isinstance(value, list) and "items" in node:
        for index, item in enumerate(value):
            errors.extend(_errors(item, node["items"], root, (*path, index)))
    if isinstance(value, dict):
        for name in node.get("required", []):
            if name not in value:
                errors.append(f"{where}: missing {name}")
        properties = node.get("properties", {})
        for name, item in value.items():
            if name in properties:
                errors.extend(_errors(item, properties[name], root, (*path, name)))
            elif "additionalProperties" in node:
                errors.extend(_errors(item, node["additionalProperties"], root, (*path, name)))
    return errors


def validate(value: object, schema: dict) -> list[str]:
    """Return every violation of the schema, or an empty list."""
    return _errors(value, schema, schema, ())


class StateError(Exception):
    """A refused operation. ``code`` is stable; the message is for people."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def dumps(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _require_unicode(value: object, code: str) -> None:
    # A lone surrogate (from a "\\ud800" escape or an undecodable argument)
    # has no UTF-8 form, so neither file may hold it.
    try:
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        raise StateError(code, "text must be valid Unicode: a lone surrogate cannot be stored") from None


def emitted(value: object) -> str:
    """The one JSON document a command prints."""
    return json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True) + "\n"


def emit(value: object) -> None:
    """Write ``value`` to stdout as ASCII JSON, whatever the console encoding."""
    text = emitted(value)
    sys.stdout.flush()
    sys.stdout.buffer.write(text.encode("ascii"))
    sys.stdout.buffer.flush()


@contextlib.contextmanager
def file_lock(path: Path, timeout: float | None = None) -> Iterator[None]:
    """Hold an OS-released exclusive lock on ``path`` for the block."""
    timeout = LOCK_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as error:
        # A read-only copy of a state directory cannot be locked, so it cannot
        # be read consistently either; say so rather than fail with a trace.
        raise StateError("lock_unavailable", f"cannot open {path.name}: {error.strerror or error}") from None
    try:
        if os.fstat(descriptor).st_size == 0:
            try:
                os.write(descriptor, b" ")
            except OSError as error:
                raise StateError("lock_unavailable", f"cannot write {path.name}: {error.strerror or error}") from None
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise StateError("lock_timeout", f"could not take {path.name} within {timeout:g}s")
                time.sleep(0.02)
        try:
            yield
        finally:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _note_change(directory: Path, change: str, *, version: int | None = None, journal: int | None = None) -> None:
    """Record one successful write, for a live view; called under the state lock.

    Best-effort: a write that succeeded is never failed by its notification.
    """
    line = json.dumps({"at": now(), "change": change, "journal": journal, "version": version},
                      ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    path = directory / CHANGES_FILE
    with contextlib.suppress(OSError):
        with open(path, "ab") as stream:
            stream.write(line.encode("ascii"))
            size = stream.tell()
        if size > CHANGES_LIMIT_BYTES:
            lines = path.read_bytes().splitlines(keepends=True)
            kept: list[bytes] = []
            total = 0
            for entry in reversed(lines):
                if total + len(entry) > CHANGES_LIMIT_BYTES // 2:
                    break
                kept.append(entry)
                total += len(entry)
            _write_atomically(path, b"".join(reversed(kept)).decode("ascii", errors="replace"))


def _write_atomically(path: Path, text: str) -> None:
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _unique(state: dict) -> list[str]:
    problems = []
    for array, key in ELEMENT_KEYS.items():
        names = [item.get(key) for item in state.get(array, []) if isinstance(item, dict)]
        if len(names) != len(set(map(json.dumps, names))):
            problems.append(f"/{array}: {key} values must be unique")
    queued = {item.get("subject") for item in state.get("queue", []) if isinstance(item, dict)}
    both = sorted(str(item.get("subject")) for item in state.get("planned", [])
                  if isinstance(item, dict) and item.get("subject") in queued)
    if both:
        problems.append(f"/planned: {', '.join(both)} already in the queue; work is planned or queued, never both")
    return problems


def state_errors(state: object) -> list[str]:
    errors = validate(state, load_schema("delivery-state/1"))
    if not errors and isinstance(state, dict):
        errors = _unique(state)
    return errors


def journal_errors(entries: list[object]) -> list[str]:
    schema = load_schema("delivery-journal/1")
    errors = []
    for index, entry in enumerate(entries):
        found = validate(entry, schema)
        errors.extend(f"entry {index + 1}{error}" for error in found)
        if isinstance(entry, dict) and entry.get("id") != index + 1:
            errors.append(f"entry {index + 1}: ids must run in sequence from 1")
        if not found and isinstance(entry, dict):
            try:
                _parse_time(entry["at"], exact=True)
            except ValueError:
                # The pattern admits dates that do not exist; the stats read every time.
                errors.append(f"entry {index + 1}/at: not a real time")
            errors.extend(f"entry {index + 1}: {problem}" for problem in _review_problems(entry))
            if (entry["kind"] == "review.evidence") != ("reviewArtifact" in entry) or (entry["kind"] == "review.evidence" and entry["subject"] is None):
                errors.append(f"entry {index + 1}: review.evidence names its subject and artifact")
            if "reviewCarry" in entry and (entry["kind"] != "decision.recorded" or entry["subject"] is None):
                errors.append(f"entry {index + 1}: only a decision about a subject carries its review")
            if "occurredAt" in entry:
                if entry["kind"] != "candidate.merged":
                    errors.append(f"entry {index + 1}: only a merge carries occurredAt")
                if entry["occurredAt"] is not None:
                    try:
                        if _parse_time(entry["occurredAt"], exact=True) > _parse_time(entry["at"], exact=True):
                            errors.append(f"entry {index + 1}: occurrence follows observation")
                    except ValueError:
                        errors.append(f"entry {index + 1}/occurredAt: not a real time")
            if "recovery" in entry and (entry["kind"] != "candidate.merged" or "occurredAt" not in entry):
                errors.append(f"entry {index + 1}: only a recovered merge carries source identity")
    return errors


def _review_problems(entry: dict) -> list[str]:
    """What the schema cannot say about a review round."""
    review = entry.get("review")
    if entry["kind"] != REVIEW_KIND:
        return [] if review is None else [f"only a {REVIEW_KIND} entry carries a review"]
    if review is None:
        return [f"a {REVIEW_KIND} entry carries its review"]
    problems = []
    if entry["subject"] is None:
        problems.append(f"a {REVIEW_KIND} entry names the candidate it reviewed")
    if not review["verdicts"]:
        problems.append("a review round has at least one lens verdict")
    try:
        if _parse_time(review["finishedAt"], exact=True) < _parse_time(review["startedAt"], exact=True):
            problems.append("a review round finishes after it starts")
    except ValueError:
        problems.append("a review round's startedAt and finishedAt are real times")
    return problems


def read_state(directory: Path) -> dict:
    path = directory / STATE_FILE
    try:
        state = json.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        raise StateError("state_missing", f"no state at {path}; run init first") from None
    except UnicodeDecodeError:
        raise StateError("state_invalid", "state is not UTF-8") from None
    except json.JSONDecodeError as error:
        raise StateError("state_invalid", f"state is not JSON: {error}") from None
    errors = state_errors(state)
    if errors:
        raise StateError("state_invalid", "; ".join(errors))
    return state


def _journal_text(directory: Path) -> tuple[str, int]:
    """The journal's complete lines, and the byte length they occupy.

    Lines end only at "\n": a summary may carry other line separators. A
    final segment without its newline is an append that never finished; it is
    left out, and the next append replaces it.
    """
    try:
        data = (directory / JOURNAL_FILE).read_bytes()
    except FileNotFoundError:
        return "", 0
    # Cut before decoding, so a torn tail can never split a character.
    complete = data[: data.rfind(b"\n") + 1]
    try:
        return complete.decode("utf-8"), len(complete)
    except UnicodeDecodeError:
        raise StateError("journal_invalid", "journal is not UTF-8") from None


def read_journal(directory: Path) -> list[dict]:
    lines = _journal_text(directory)[0].split("\n")[:-1]
    entries = []
    for number, line in enumerate(lines, start=1):
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            raise StateError("journal_invalid", f"journal line {number} is not JSON") from None
    errors = journal_errors(entries)
    if errors:
        raise StateError("journal_invalid", "; ".join(errors))
    return entries


def initial_state(headline: str) -> dict:
    return {
        "schemaVersion": STATE_VERSION,
        "version": 1,
        "meta": {
            "headline": headline,
            "updatedAt": now(),
            "baseHead": None,
            "releasedHead": None,
            "checking": {"now": None, "next": []},
        },
        "lanes": [],
        "queue": [],
        "finish": [],
        "followups": [],
        "leases": [],
        "extensions": {},
    }


def init(directory: Path, headline: str) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    with file_lock(directory / STATE_LOCK):
        if (directory / STATE_FILE).exists():
            raise StateError("state_exists", f"state already exists at {directory / STATE_FILE}")
        state = initial_state(headline)
        _require_unicode(state, "state_invalid")
        _write_atomically(directory / STATE_FILE, dumps(state))
        (directory / JOURNAL_FILE).touch()
        _note_change(directory, "init", version=state["version"], journal=0)
    return state


def merge_patch(target: object, patch: object) -> object:
    """Apply a JSON merge patch (RFC 7396) without changing ``target``."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def _select(state: dict, selector: str) -> tuple[str, str, int]:
    match = SELECTOR.match(selector)
    if not match:
        raise StateError("selector_invalid", f"selector must look like queue[subject=...]: {selector}")
    array, key, value = match["array"], match["key"], match["value"]
    if ELEMENT_KEYS.get(array) != key:
        raise StateError("selector_invalid", f"{array} elements are selected by {ELEMENT_KEYS.get(array, 'nothing')}")
    found = [index for index, item in enumerate(state[array]) if item.get(key) == value]
    if len(found) != 1:
        raise StateError("selector_unmatched", f"{selector} matches {len(found)} elements")
    return array, key, found[0]


def _assign(target: dict, pointer: str, value: object) -> None:
    """Set the field a JSON Pointer names, keeping null as a value."""
    if not pointer.startswith("/") or pointer == "/":
        raise StateError("patch_invalid", f"a set pointer must name a field: {pointer!r}")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    parent: object = target
    for part in parts[:-1]:
        if not isinstance(parent, dict) or part not in parent:
            raise StateError("patch_invalid", f"a set pointer must name an existing object: {pointer}")
        parent = parent[part]
    if not isinstance(parent, dict):
        raise StateError("patch_invalid", f"a set pointer must name a field of an object: {pointer}")
    parent[parts[-1]] = copy.deepcopy(value)


def apply_patch(
    state: dict, patch: object, selector: str | None, assignments: object = None
) -> dict:
    """Apply a merge patch, then exact assignments, to the root or one element.

    A merge patch removes a field it sets to null; ``assignments`` maps JSON
    Pointers to values and sets them exactly, which is how a nullable field is
    set back to null.
    """
    patch = {} if patch is None else patch
    assignments = {} if assignments is None else assignments
    if not isinstance(patch, dict):
        raise StateError("patch_invalid", "a patch must be a JSON object")
    if not isinstance(assignments, dict):
        raise StateError("patch_invalid", "assignments must be a JSON object of pointers and values")
    updated = copy.deepcopy(state)
    if selector is None:
        target = merge_patch(updated, patch)
        for pointer, value in assignments.items():
            _assign(target, pointer, value)  # type: ignore[arg-type]
        return target  # type: ignore[return-value]
    array, key, index = _select(state, selector)
    if key in patch or any(pointer.split("/")[1:2] == [key] for pointer in assignments):
        raise StateError("patch_invalid", f"a selected element's {key} cannot change")
    element = merge_patch(state[array][index], patch)
    for pointer, value in assignments.items():
        _assign(element, pointer, value)  # type: ignore[arg-type]
    updated[array][index] = element
    return updated


def ownership_violations(before: dict, after: dict, writer: str) -> list[str]:
    """Every field ``writer`` changed without owning it."""
    violations = []
    for field in TOOL_OWNED:
        if before.get(field) != after.get(field):
            violations.append(f"/{field} is maintained by the tool")
    earlier = {item.get("subject"): item for item in before.get("queue", [])}
    for item in after.get("queue", []):
        previous = earlier.get(item.get("subject"), {})
        for field in HANDOFF_QUEUE:
            if item.get(field) != previous.get(field):
                violations.append(f"/queue[subject={item.get('subject')}]/{field} is written only by a lane's hand-off")
        for field in SELECT_QUEUE:
            if item.get(field) != previous.get(field):
                violations.append(f"/queue[subject={item.get('subject')}]/{field} is written only by the select command")
    meta_before, meta_after = before.get("meta", {}), after.get("meta", {})
    if meta_before.get("updatedAt") != meta_after.get("updatedAt"):
        violations.append("/meta/updatedAt is maintained by the tool")
    if writer == "runner":
        for field in set(before) | set(after):
            if field in TOOL_OWNED or field in ("meta", "queue", "lanes"):
                continue
            if before.get(field) != after.get(field):
                violations.append(f"/{field} belongs to the lead")
        for field in set(meta_before) | set(meta_after):
            if field not in RUNNER_META and field != "updatedAt" and meta_before.get(field) != meta_after.get(field):
                # serve may hold itself when its setup breaks, but only where no hold is set; the lead clears it.
                if field == "hold" and not meta_before.get("hold") and meta_after.get("hold"):
                    continue
                violations.append(f"/meta/{field} belongs to the lead")
        rows_before, rows_after = before.get("lanes", []), after.get("lanes", [])
        if [entry.get("id") for entry in rows_before] != [entry.get("id") for entry in rows_after]:
            violations.append("/lanes elements are added, removed and ordered by the lead")
        else:
            for row_before, row_after in zip(rows_before, rows_after):
                for field in set(row_before) | set(row_after):
                    if field not in RUNNER_LANE and row_before.get(field) != row_after.get(field):
                        violations.append(f"/lanes[id={row_before.get('id')}]/{field} belongs to the lead")
        old, new = before.get("queue", []), after.get("queue", [])
        if [item.get("subject") for item in old] != [item.get("subject") for item in new]:
            violations.append("/queue elements are added, removed and ordered by the lead")
        else:
            for item_before, item_after in zip(old, new):
                for field in set(item_before) | set(item_after):
                    if field not in RUNNER_QUEUE and item_before.get(field) != item_after.get(field):
                        # Recording a merge ends any wait for the lead; the runner clears it only in a write that leaves the item merged.
                        if field == "pending" and item_after.get("stage") == "merged" and "pending" not in item_after:
                            continue
                        violations.append(f"/queue[subject={item_before.get('subject')}]/{field} belongs to the lead")
        return violations
    for field in RUNNER_META:
        if meta_before.get(field) != meta_after.get(field):
            violations.append(f"/meta/{field} belongs to the runner")
    old = {item.get("subject"): item for item in before.get("queue", [])}
    for item in after.get("queue", []):
        previous = old.get(item.get("subject"))
        for field in RUNNER_QUEUE:
            expected = previous.get(field) if previous is not None else QUEUE_START.get(field)
            if item.get(field) != expected:
                violations.append(f"/queue[subject={item.get('subject')}]/{field} belongs to the runner")
    return violations


def lane_violations(before: dict, after: dict, lane: str) -> list[str]:
    """What a lane changed beyond its own lane's focus, state and waiting-on, and beyond the one stage it moves: a story
    planned in its own lane, from planned to building, when it starts it. Anything else at all is refused."""
    if lane not in [entry.get("id") for entry in before.get("lanes", [])]:
        return [f"there is no lane {lane}"]
    expected = copy.deepcopy(after)
    for entry in expected.get("lanes", []):
        if entry.get("id") == lane:
            previous = next(item for item in before["lanes"] if item.get("id") == lane)
            for field in LANE_FIELDS:
                if field in previous:
                    entry[field] = previous[field]
    if len(expected.get("planned", [])) == len(before.get("planned", [])):
        for entry, previous in zip(expected.get("planned", []), before.get("planned", [])):
            if (entry.get("subject") == previous.get("subject") and entry.get("lane") == previous.get("lane") == lane
                    and previous.get("stage") == "planned" and entry.get("stage") == "building"):
                entry["stage"] = "planned"
    if expected != before:
        return [f"a lane writes only its own lane's {', '.join(LANE_FIELDS[:3])}, and moves a story planned in its own "
                "lane from planned to building"]
    return []


def _stamp_lane(before: dict, after: dict, lane: str) -> None:
    """The tool keeps since: it moves when the lane's state changes, and only then."""
    old = next(entry for entry in before["lanes"] if entry["id"] == lane)
    new = next(entry for entry in after["lanes"] if entry["id"] == lane)
    if "state" in new:
        new["since"] = now() if new["state"] != old.get("state") else old.get("since")


def _own_word(row: dict | None) -> dict | None:
    """A row's last word from its own lane: the row itself when the lane or the lead wrote it, else what the queue kept of
    it when it spoke over it."""
    if row is None:
        return None
    if row.get("focusBy") == "runner":
        return row.get("restedFrom")
    at = row.get("focusAt") or row.get("since")
    return {"state": row["state"], "at": at} if row.get("state") and at else None


def _stamp_focus(before: dict, after: dict, writer: str, spoke: str | None) -> None:
    """The tool keeps focusAt and focusBy: when a row's words last changed or its lane said them again, and whether the
    queue said them. It keeps restedFrom too: when the queue speaks over a row, the lane's own last word, its state and
    when it said it, so the record carries that word past a merge whether or not anything read it in between. The
    lane's or the lead's next word ends it."""
    rows = after.get("lanes")
    if not isinstance(rows, list):
        return
    old = {entry.get("id"): entry for entry in before.get("lanes", [])}
    for entry in rows:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        previous = old.get(entry["id"])
        entry.pop("focusAt", None)
        entry.pop("focusBy", None)
        entry.pop("restedFrom", None)
        if previous is None or previous.get("focus") != entry.get("focus") or entry["id"] == spoke:
            if entry.get("focus"):
                entry["focusAt"] = now()
                if writer == "runner":
                    entry["focusBy"] = "runner"
                    kept = _own_word(previous)
                    if kept:
                        entry["restedFrom"] = kept
        else:
            entry.update({key: previous[key] for key in ("focusAt", "focusBy", "restedFrom") if key in previous})


def _waiting_by(row: dict) -> str | None:
    """Who set the row's waitingOn. A row written before the tool kept it has none: the queue set it if the queue's words stand."""
    if not row.get("waitingOn"):
        return None
    if "waitingBy" in row:
        return row["waitingBy"]
    return "runner" if row.get("focusBy") == "runner" else None


def _written_field(selector: str | None, changes: object, assignments: object, field: str) -> str | None:
    """The lane row a patch selected and wrote ``field`` on, even to the value it already held."""
    match = SELECTOR.match(selector or "")
    if not match or match["array"] != "lanes":
        return None
    if (isinstance(changes, dict) and field in changes) or (isinstance(assignments, dict) and f"/{field}" in assignments):
        return match["value"]
    return None


def _stamp_waiting(before: dict, after: dict, writer: str, wrote: str | None) -> None:
    """The tool keeps waitingBy: who set a row's waitingOn, so only the queue's own wait ends when its lane speaks."""
    rows = after.get("lanes")
    if not isinstance(rows, list):
        return
    old = {entry.get("id"): entry for entry in before.get("lanes", []) if isinstance(entry, dict)}
    for entry in rows:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        previous = old.get(entry["id"])
        entry.pop("waitingBy", None)
        if not entry.get("waitingOn"):
            continue
        # The queue says a row's words whole, so a row it gives new words has its wait too, as focusBy says.
        queue_wrote = (writer == "runner" and previous is not None and bool(entry.get("focus"))
                       and previous.get("focus") != entry.get("focus"))
        if previous is None or previous.get("waitingOn") != entry["waitingOn"] or entry["id"] == wrote or queue_wrote:
            entry["waitingBy"] = writer
        elif _waiting_by(previous) is not None:
            entry["waitingBy"] = _waiting_by(previous)


def _end_queue_wait(row: dict) -> None:
    """End a row's waitingOn if the queue set it; a wait the lane or the lead set stays theirs."""
    if _waiting_by(row) == "runner":
        row["waitingOn"] = None


def merged_item(item: dict, **fields: object) -> dict:
    """A queue item as recording its merge leaves it: the merge ends any wait for the lead."""
    merged = {**item, "stage": "merged", **fields}
    merged.pop("pending", None)
    return merged


def lane_rows_after(directory: Path, state: dict, subject: str, *, stopped: str | None = None) -> list[dict]:
    """The lanes once the queue has merged a candidate, or stopped it for ``stopped``, so the row can't contradict the line.

    A row about other work stays: the lane's own word wins. That is one that doesn't name the candidate, from a lane with
    another assignment open, or still active and speaking since its hand-off. A lane blocked with other work open keeps its
    question, and the queue's word that another candidate stopped stays while that candidate is open. A lane with nothing
    else open rests.
    """
    rows = [dict(entry) for entry in state["lanes"]]
    lane = next((item.get("lane") for item in state["queue"] if item["subject"] == subject), None)
    row = next((entry for entry in rows if entry["id"] == lane), None)
    if row is None:
        return rows
    work = [*state["queue"], *state.get("planned", [])]
    waiting = any(item.get("lane") == lane and item["subject"] != subject and item.get("stage") != "merged" for item in work)
    names = re.search(rf"(?<![\w-]){re.escape(subject)}(?![\w-])", row["focus"]) is not None
    by_queue = row.get("focusBy") == "runner"
    if waiting and row["state"] == "blocked":
        return rows
    if by_queue and not names and waiting and row["waitingOn"]:
        return rows
    if row["focus"] and not names and not by_queue:
        if waiting:
            return rows
        try:
            handed = [entry["at"] for entry in read_journal(directory) if entry["kind"] == "candidate.handed" and entry.get("subject") == subject]
        except (StateError, OSError):
            # The stage still has to be written, and a row can't be judged without its hand-off, so it stays.
            return rows
        if row["state"] in ("assigned", "working", "reviewing", "blocked") and "focusAt" in row and handed \
                and _parse_time(row["focusAt"], exact=True) >= _parse_time(handed[-1], exact=True):
            return rows
    resting = row["state"]
    if not waiting:
        resting = "idle"
    elif resting == "handed":
        resting = "assigned"
    if resting != row["state"]:
        row["since"] = now()
    row.update(focus=f"{subject} merged" if stopped is None else f"{subject} stopped: {stopped}", state=resting,
               waitingOn=None if stopped is None else "lead")
    return rows


def lane_rows_put_back(state: dict, subject: str) -> list[dict]:
    """The lanes once the queue has put a candidate back: a row saying it stopped no longer holds, so the record's words take over."""
    rows = [dict(entry) for entry in state["lanes"]]
    lane = next((item.get("lane") for item in state["queue"] if item["subject"] == subject), None)
    for row in rows:
        if row["id"] == lane and row.get("focusBy") == "runner" and row["focus"].startswith(f"{subject} stopped:"):
            if row["state"] != "handed":
                row["since"] = now()
            _end_queue_wait(row)
            row.update(focus="", state="handed")
    return rows


def _tool_write(directory: Path, change: Callable[[dict], object]) -> tuple[dict, object]:
    """A write the tool makes for a lane or the lead, under the state lock, with the record's own checks."""
    with file_lock(directory / STATE_LOCK):
        state = read_state(directory)
        updated = copy.deepcopy(state)
        outcome = change(updated)
        updated["version"] = state["version"] + 1
        updated["meta"]["updatedAt"] = now()
        errors = state_errors(updated)
        if errors:
            raise StateError("patch_invalid", "; ".join(errors))
        _require_unicode(updated, "patch_invalid")
        _write_atomically(directory / STATE_FILE, dumps(updated))
        _note_change(directory, "patch", version=updated["version"])
    return updated, outcome


def _set_lane_state(state: dict, lane: str, lane_state: str) -> None:
    for entry in state["lanes"]:
        if entry["id"] == lane and entry["state"] != lane_state:
            entry.update(state=lane_state, since=now())


def evidence_errors(evidence: object) -> list[str]:
    """Problems with a lane's suite evidence: what ``merge_queue.py evidence`` prints, one entry a check."""
    if not isinstance(evidence, list):
        return ["evidence is a list of entries"]
    problems = []
    for entry in evidence:
        if isinstance(entry, dict) and set(entry) == {"reason", "detail"}:
            # What merge_queue.py evidence prints when it refuses.
            problems.append(f"the evidence command refused: {entry['reason']}: {entry['detail']}")
            continue
        if not isinstance(entry, dict) or set(entry) != set(EVIDENCE_FIELDS):
            problems.append(f"an evidence entry has exactly {', '.join(EVIDENCE_FIELDS)}")
            continue
        if not isinstance(entry["check"], str) or not entry["check"]:
            problems.append("an evidence entry names its check")
        if not isinstance(entry["definition"], str) or not EVIDENCE_DEFINITION.match(entry["definition"]):
            problems.append(f"{entry['check']}: the definition is a sha256: digest")
        for field in ("head", "tree"):
            if not isinstance(entry[field], str) or not EVIDENCE_COMMIT.match(entry[field]):
                problems.append(f"{entry['check']}: the {field} is a full object id")
        if entry["result"] not in ("passed", "failed"):
            problems.append(f"{entry['check']}: the result is passed or failed")
    checks = [entry.get("check") for entry in evidence if isinstance(entry, dict)]
    if len(checks) != len(set(checks)):
        problems.append("one evidence entry a check")
    return problems


def handoff(
    directory: Path,
    *,
    lane: str,
    subject: str,
    head: str,
    change_request: str,
    rounds: int | None = None,
    evidence: list[dict] | None = None,
    run: str | None = None,
) -> dict:
    """A lane hands its reviewed work to the queue, and the hand-off is journaled.

    The subject is planned in the lane, or queued in it and waiting or stopped,
    for a hand-off at a new head. Work in flight is never handed off again.
    ``evidence`` records the lane's own runs of the binding's reusable checks,
    each with the head and tree it ran on; a hand-off without it keeps none.
    ``run`` names the run that built the work; a hand-off without it keeps the
    run already stored, from an earlier hand-off or the lead's plan. A run another
    planned or queued subject holds is refused with ``run_bound``.
    """
    problems = evidence_errors(evidence or [])
    if problems:
        raise StateError("handoff_invalid", "; ".join(problems))

    def change(state: dict) -> dict:
        if lane not in [entry["id"] for entry in state["lanes"]]:
            raise StateError("writer_not_owned", f"there is no lane {lane}")
        if run is not None:
            # A run builds one subject: as bind-run refuses, a hand-off can't name a run another subject holds.
            holder = next((entry["subject"] for entry in [*state.get("planned", []), *state["queue"]]
                           if entry["subject"] != subject and entry.get("runId") == run), None)
            if holder is not None:
                raise StateError("run_bound", f"run {run} is already bound to {holder}")
        queued = next((item for item in state["queue"] if item["subject"] == subject), None)
        if queued is not None:
            if queued["lane"] != lane:
                raise StateError("writer_not_owned", f"{subject} belongs to {queued['lane']}, not {lane}")
            if queued["stage"] not in ("queued", "stopped"):
                raise StateError("handoff_invalid", f"{subject} is {queued['stage']}; hand it off once it stops")
            queued.update(stage="queued", head=head, syncedHead=None, checks=[], changeRequest=change_request)
            queued.pop("checkSelection", None)
            # A lead selection is for the head it was made at; new code is never checked with it.
            if queued.get("leadSelection", {}).get("head") != head:
                queued.pop("leadSelection", None)
            item = queued
        else:
            planned = next((entry for entry in state.get("planned", []) if entry["subject"] == subject), None)
            if planned is None:
                raise StateError("subject_unknown", f"{subject} is neither planned nor queued")
            if planned["lane"] != lane:
                raise StateError("writer_not_owned", f"{subject} is planned in {planned['lane']}, not {lane}")
            state["planned"] = [entry for entry in state["planned"] if entry["subject"] != subject]
            item = {
                "order": max([entry["order"] for entry in state["queue"]], default=0) + 1,
                "subject": subject, "title": planned["title"], "lane": lane, "stage": "queued", "head": head,
                "syncedHead": None, "leadInputs": [], "reportedRounds": None, "runId": planned.get("runId"),
                "checks": [], "changeRequest": change_request,
            }
            # A ticket reconcile saw started stays so in the queue, so a rejection can hand the flag back.
            if planned.get("trackerStarted"):
                item["trackerStarted"] = True
            state["queue"].append(item)
        if rounds is not None:
            item["reportedRounds"] = rounds
        if run is not None:
            item["runId"] = run
        item.pop("evidence", None)
        if evidence:
            item["evidence"] = [{**entry, "recordedAt": now()} for entry in evidence]
        # The lead's own policy, from the record, so no caller can leave it out; only the lead clears the wait.
        if state["meta"].get("handoffsNeedAcceptance"):
            item["pending"] = True
        _set_lane_state(state, lane, "handed")
        for entry in state["lanes"]:
            # A new hand-off ends what the queue said: the record's derived words read from the line again.
            if entry["id"] != lane:
                continue
            # A wait the queue set ends whoever said the row's words last; the queue's words end only while they stand.
            _end_queue_wait(entry)
            if not entry["waitingOn"]:
                entry.pop("waitingBy", None)
            entry.pop("restedFrom", None)  # the hand-off is the lane's newer word
            if entry.get("focusBy") == "runner":
                entry["focus"] = ""
                del entry["focusBy"]
                entry.pop("focusAt", None)
        return copy.deepcopy(item)

    _, item = _tool_write(directory, change)
    waiting = "; waiting for the lead to accept it" if item.get("pending") else ""
    rounds_text = f" after {rounds} review rounds" if rounds is not None else ""
    proof = "".join(f"; suite evidence for {entry['check']} ({entry['result']}) at {entry['head']}"
                    for entry in evidence or [])
    append_journal(directory, kind="candidate.handed", lane=lane, subject=subject,
                   summary=f"{lane} handed {subject} at {head} ({change_request}){rounds_text}{proof}{waiting}")
    return item  # type: ignore[return-value]


def _pending(state: dict, subject: str) -> dict:
    item = next((entry for entry in state["queue"] if entry["subject"] == subject), None)
    # A merged item an earlier version left carrying the flag has no hand-off left to accept or reject.
    if item is None or not item.get("pending") or item["stage"] == "merged":
        raise StateError("handoff_invalid", f"{subject} has no hand-off waiting for the lead")
    return item


def accept_handoff(directory: Path, subject: str) -> dict:
    """The lead accepts a waiting hand-off; the runner may now take it."""

    def change(state: dict) -> dict:
        item = _pending(state, subject)
        item.pop("pending")
        return copy.deepcopy(item)

    _, item = _tool_write(directory, change)
    append_journal(directory, kind="decision.recorded", subject=subject, summary=f"Accepted the hand-off of {subject}")
    return item  # type: ignore[return-value]


def reject_handoff(directory: Path, subject: str, reason: str) -> dict:
    """The lead rejects a waiting hand-off; the work goes back to its lane as planned."""

    def change(state: dict) -> dict:
        item = _pending(state, subject)
        state["queue"] = [entry for entry in state["queue"] if entry["subject"] != subject]
        back = {"subject": subject, "title": item["title"], "lane": item["lane"], "stage": "review"}
        if "changeRequest" in item:
            back["changeRequest"] = item["changeRequest"]
        if item.get("runId") is not None:
            back["runId"] = item["runId"]
        if item.get("trackerStarted"):
            back["trackerStarted"] = True
        state.setdefault("planned", []).append(back)
        if item["lane"] is not None:
            _set_lane_state(state, item["lane"], "working")
        return back

    _, back = _tool_write(directory, change)
    append_journal(directory, kind="decision.recorded", subject=subject, lane=back["lane"],  # type: ignore[index]
                   summary=f"Rejected the hand-off of {subject}: {reason}")
    return back  # type: ignore[return-value]


def lead_selection_errors(checks: object, binding: dict) -> list[str]:
    """Problems with the lead's checks for a candidate, beyond their shape, as the runner would find them."""
    if not isinstance(checks, list) or not checks:
        return ["--checks is a non-empty JSON list of checks"]
    schema = load_schema("delivery-state/1")
    problems = []
    for index, check in enumerate(checks):
        problems += [f"check {index + 1}{error}" for error in _errors(check, schema["$defs"]["check"], schema, ())]
    if problems:
        return problems
    ids = [check["id"] for check in checks]
    if len(ids) != len(set(ids)):
        problems.append("check ids must be unique")
    own = {check.get("id") for name in ("alwaysRun", "fallback") for check in binding.get(name) or []
           if isinstance(check, dict)}
    profiles = binding.get("profiles") if isinstance(binding.get("profiles"), dict) else {}
    ceiling = binding.get("checkTimeoutCeiling")
    for check in checks:
        if not check["argv"]:
            problems.append(f"{check['id']}: argv must not be empty")
        if check["id"] in own:
            problems.append(f"{check['id']}: reuses the id of a binding check; alwaysRun runs anyway")
        missing = [name for name in check["prerequisites"] if name not in ids]
        if missing:
            problems.append(f"{check['id']}: prerequisites name unselected checks {missing}")
        if "environmentProfile" in check and check["environmentProfile"] not in profiles:
            problems.append(f"{check['id']}: unknown environment profile {check['environmentProfile']}")
        if isinstance(ceiling, int) and check.get("timeoutSeconds", 0) > ceiling:
            problems.append(f"{check['id']}: timeoutSeconds above the ceiling")
        if posixpath.isabs(check["cwd"]) or Path(check["cwd"]).is_absolute() or ".." in re.split(r"[\\/]", check["cwd"]):
            problems.append(f"{check['id']}: cwd leaves the checkout")
    if not problems:
        done: set[str] = set()
        remaining = list(checks)
        while remaining:
            ready = [check for check in remaining if all(name in done for name in check["prerequisites"])]
            if not ready:
                problems.append("prerequisites form a cycle")
                break
            done.add(ready[0]["id"])
            remaining.remove(ready[0])
    return problems


def _binding_pattern(pattern: str) -> re.Pattern[str]:
    """A binding path pattern, as the runner reads one: ``*`` matches within one segment and ``**`` any number."""
    parts = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            parts.append(".*")
            index += 2
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(parts) + r"\Z")


def _git_out(repo: Path, *arguments: str) -> str | None:
    """A git command's stdout in ``repo``, or None when it fails or git is missing."""
    try:
        completed = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, check=False,
                                   stdin=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", "replace")


def current_base_head(repo: Path, binding: dict) -> str | None:
    """The base head the checkout ``repo`` holds for the binding's base, from its remote-tracking ref; None unread."""
    base = binding.get("base")
    if not (isinstance(base, dict) and isinstance(base.get("remote"), str) and isinstance(base.get("ref"), str)):
        return None
    out = _git_out(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/{base['remote']}/{base['ref']}^{{commit}}")
    head = (out or "").strip()
    return head if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head) else None


def candidate_changes(repo: Path, base_head: str, head: str) -> list[str] | None:
    """The paths ``head`` changes from where it left the base, a rename as both of its paths; None unread."""
    if not all(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit or "") for commit in (base_head, head)):
        return None
    out = _git_out(repo, "diff", "--name-only", "--no-renames", "-z", f"{base_head}...{head}", "--")
    if out is None:
        return None
    return sorted({path for path in out.split("\0") if path})


def catalogue_problems(binding: dict) -> list[str]:
    """What is wrong with the binding's catalogue, guards and readers, beyond their shape."""
    selection = binding.get("selection") if isinstance(binding.get("selection"), dict) else {}
    catalogue = [entry for entry in selection.get("catalogue") or [] if isinstance(entry, dict)]
    ids = [entry.get("id") for entry in catalogue]
    problems = []
    if len(ids) != len(set(ids)):
        problems.append("/selection/catalogue: ids must be unique")
    own = {check.get("id") for name in ("alwaysRun", "fallback") for check in binding.get(name) or []
           if isinstance(check, dict)}
    profiles = binding.get("profiles") if isinstance(binding.get("profiles"), dict) else {}
    ceiling = binding.get("checkTimeoutCeiling")
    for entry in catalogue:
        if entry.get("id") in own:
            problems.append(f"/selection/catalogue: {entry.get('id')} reuses the id of a binding check")
        if "environmentProfile" in entry and entry["environmentProfile"] not in profiles:
            problems.append(f"/selection/catalogue: {entry.get('id')} names unknown environment profile "
                            f"{entry['environmentProfile']}")
        if isinstance(ceiling, int) and isinstance(entry.get("timeoutSeconds"), int) and entry["timeoutSeconds"] > ceiling:
            problems.append(f"/selection/catalogue: {entry.get('id')}: timeoutSeconds above the ceiling")
        missing = [name for name in entry.get("prerequisites") or [] if name not in ids]
        if missing:
            problems.append(f"/selection/catalogue: {entry.get('id')}'s prerequisites name checks outside the catalogue {missing}")
    # A cycle is traced only through unique ids whose prerequisites all name catalogue checks.
    sound = len(ids) == len(set(ids)) and all(name in ids for entry in catalogue for name in entry.get("prerequisites") or [])
    if sound:
        done: set = set()
        remaining = list(catalogue)
        while remaining:
            ready = [entry for entry in remaining if all(name in done for name in entry.get("prerequisites") or [])]
            if not ready:
                problems.append("/selection/catalogue: prerequisites form a cycle among "
                                + ", ".join(sorted(str(entry.get("id")) for entry in remaining)))
                break
            done.add(ready[0].get("id"))
            remaining.remove(ready[0])
    for name in selection.get("guards") or []:
        if name not in ids:
            problems.append(f"/selection/guards: {name} is not a catalogue id")
    guards = selection.get("guards") or []
    if len(guards) != len(set(guards)):
        problems.append("/selection/guards: ids must be unique")
    for index, reader in enumerate(selection.get("readers") or []):
        if isinstance(reader, dict) and (not reader.get("paths") or not reader.get("checks")):
            problems.append(f"/selection/readers/{index}: name at least one path and one check")
        for name in reader.get("checks") or [] if isinstance(reader, dict) else []:
            if name not in ids:
                problems.append(f"/selection/readers/{index}: {name} is not a catalogue id")
    return problems


def resolve_selection(checks: object, binding: dict, changed: Callable[[], list[str] | None]) -> tuple[list, list[dict]]:
    """The lead's checks as full entries, with the guards and the readers of what the candidate changes added.

    ``checks`` is a list whose strings are catalogue ids and whose objects are full entries, so a JSON list as
    before selects exactly what it lists, plus the guards. An id brings its catalogue prerequisites with it.
    ``changed`` gives the candidate's changed paths; it is called only when the binding declares readers. Returns
    the checks and what was added beyond what the lead named, each ``{id, why}``.
    """
    if not isinstance(checks, list):
        return checks, []  # type: ignore[return-value]
    selection = binding.get("selection") if isinstance(binding.get("selection"), dict) else {}
    catalogue = {entry["id"]: entry for entry in selection.get("catalogue") or []
                 if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
    named = [check for check in checks if isinstance(check, str)]
    unknown = [name for name in named if name not in catalogue]
    if unknown:
        known = ", ".join(sorted(catalogue)) or "none"
        raise StateError("select_invalid", f"unknown check ids {', '.join(unknown)}: the binding's catalogue has {known}")
    resolved: list = []
    added: list[dict] = []

    def present(name: object) -> dict | None:
        return next((check for check in resolved if isinstance(check, dict) and check.get("id") == name), None)

    def include(name: str, why: str | None) -> None:
        existing = present(name)
        if existing is not None:
            if existing != catalogue[name]:
                raise StateError("select_invalid", f"{name}: the selection defines it differently from the catalogue")
            return
        for prerequisite in catalogue[name].get("prerequisites") or []:
            include(prerequisite, why or f"a prerequisite of {name}")
        resolved.append(copy.deepcopy(catalogue[name]))
        if why is not None:
            added.append({"id": name, "why": why})

    for check in checks:
        if isinstance(check, str):
            include(check, None)
            continue
        name = check.get("id") if isinstance(check, dict) else None
        existing = present(name)
        if existing is not None and name in catalogue:
            # A catalogue check named both ways is selected once; any other repeat is refused as before.
            if existing != check:
                raise StateError("select_invalid", f"{name}: the selection defines it differently from the catalogue")
            continue
        resolved.append(check)
    for name in selection.get("guards") or []:
        if name in catalogue:
            include(name, "a guard")
    readers = [reader for reader in selection.get("readers") or [] if isinstance(reader, dict)]
    if readers:
        paths = changed()
        if paths is None:
            raise StateError("select_invalid", "the binding names readers, and the candidate's changes from the base "
                                               "can't be read here: run select in a checkout holding both, or pass --repo")
        for reader in readers:
            hits = [path for path in paths if any(_binding_pattern(pattern).match(path) for pattern in reader["paths"])]
            for name in reader["checks"]:
                if hits and name in catalogue:
                    include(name, f"reads {hits[0]}")
    return resolved, added


BASE_RERUNS_KEPT = 100


def base_rerun_notices(state: dict, check_ids: list[str], base_head: str | None) -> tuple[list[str], list[str]]:
    """What the record says of the selected checks at the current base head: warnings, then notes.

    A selected check whose latest base re-run at ``base_head`` failed is warned about, by id and head. A head no
    re-run checked out says nothing is known there. A failure at another head, or of a check not selected, says
    nothing.
    """
    if base_head is None:
        return [], ["the current base head can't be read here, so nothing is known of base re-runs at it"]
    at_head = [entry for entry in state["meta"].get("baseReruns") or [] if entry["baseHead"] == base_head]
    if not at_head:
        return [], [f"nothing is known at {base_head}: this delivery's queue has re-run no check on it"]
    latest = {entry["check"]: entry for entry in at_head}
    warnings = [f"{name} failed its base re-run at {base_head}, for {latest[name]['subject']}: the base may fail it too"
                for name in check_ids if name in latest and latest[name]["result"] == "failed"]
    return warnings, []


def select_checks(directory: Path, subject: str, *, binding: dict, head: str | None = None,
                  checks: object = None, reason: str | None = None, repo: Path | None = None,
                  base_head: str | None = None) -> dict:
    """The lead records the checks a candidate runs at its reviewed head, or clears them with no head.

    The runner uses a selection only while the binding's ``selection.lead`` is
    true and the candidate is still at the head the selection names; a
    hand-off at a new head drops it. alwaysRun runs beside it.
    """
    clearing = head is None
    if not clearing:
        if not (isinstance(binding.get("selection"), dict) and binding["selection"].get("lead") is True):
            raise StateError("select_invalid", "the binding does not enable lead selection: set selection.lead to true")
        if not reason or not reason.strip():
            raise StateError("select_invalid", "say why these checks with --reason")
        problems = catalogue_problems(binding)
        if problems:
            raise StateError("binding_invalid", "; ".join(problems))
        where = Path.cwd() if repo is None else repo

        def changed() -> list[str] | None:
            base = base_head or current_base_head(where, binding)
            return None if base is None else candidate_changes(where, base, head)  # type: ignore[arg-type]

        checks, added = resolve_selection(checks, binding, changed)
        problems = lead_selection_errors(checks, binding)
        if problems:
            raise StateError("select_invalid", "; ".join(problems))

    def change(state: dict) -> dict:
        item = next((entry for entry in state["queue"] if entry["subject"] == subject), None)
        if item is None:
            raise StateError("subject_unknown", f"{subject} is not queued")
        if clearing:
            if "leadSelection" not in item:
                raise StateError("select_invalid", f"{subject} has no lead selection to clear")
            item.pop("leadSelection")
            return copy.deepcopy(item)
        if item["stage"] in ("merging", "merged"):
            raise StateError("select_invalid", f"{subject} is {item['stage']}")
        if head != item["head"]:
            raise StateError("select_invalid", f"{subject}'s reviewed head is {item['head']}, not {head}")
        item["leadSelection"] = {"head": head, "checks": copy.deepcopy(checks), "reason": reason, "recordedAt": now()}
        return copy.deepcopy(item)

    _, item = _tool_write(directory, change)
    if clearing:
        summary = f"Cleared the lead's check selection for {subject}; the provider selects its checks"
    else:
        names = ", ".join(check["id"] for check in checks)  # type: ignore[union-attr]
        summary = f"The lead selected checks for {subject} at {head}: {names}. Why: {reason}"
        if added:
            summary += ". Added: " + "; ".join(f"{entry['id']} ({entry['why']})" for entry in added)
    append_journal(directory, kind="check.selected", subject=subject, lane=item.get("lane"), summary=summary)  # type: ignore[union-attr]
    return item  # type: ignore[return-value]


def set_focus(directory: Path, lane: str, text: str, *, state: str | None = None,
              waiting_on: object = ..., between_read_and_write: Callable[[], None] | None = None) -> dict:
    """A lane says what it is doing, as itself.

    The lane's fields are its own, so a record another writer moved meanwhile is read again and the focus applied to
    it, a bounded number of times; the lane's checks run under the lock on each attempt. ``between_read_and_write`` is
    ``patch``'s seam, for concurrency tests.
    """
    changes: dict = {"focus": text}
    if state is not None:
        changes["state"] = state
    # waitingOn is set exactly: a merge patch would read None as removing it, and the record requires it.
    assignments = {} if waiting_on is ... else {"/waitingOn": waiting_on}
    for attempt in range(1, FOCUS_ATTEMPTS + 1):
        current = read_state(directory)
        try:
            return patch(directory, writer="lane", lane=lane, version=current["version"], changes=changes,
                         selector=f"lanes[id={lane}]", assignments=assignments,
                         between_read_and_write=between_read_and_write)
        except StateError as error:
            if error.code != "version_stale" or attempt == FOCUS_ATTEMPTS:
                raise
    raise AssertionError("unreachable")


def bind_run(directory: Path, *, lane: str, subject: str, run: str) -> dict:
    """A lane binds the run building its planned work, so the views show the run's rounds before the hand-off.

    Only the lane's own planned element takes a run, and a bound run is never replaced: binding the same run again
    changes nothing, and a different one, or a run another subject holds, is refused with ``run_bound``. The hand-off
    keeps the bound run unless it names another.
    """
    if not isinstance(run, str) or not run:
        raise StateError("run_bound", "name the run to bind")

    def change(state: dict) -> dict | None:
        if lane not in [entry["id"] for entry in state["lanes"]]:
            raise StateError("writer_not_owned", f"there is no lane {lane}")
        element = next((entry for entry in state.get("planned", []) if entry["subject"] == subject), None)
        if element is None:
            queued = any(item["subject"] == subject for item in state["queue"])
            raise StateError("writer_not_owned" if queued else "subject_unknown",
                             f"{subject} is queued; its hand-off names its run" if queued else f"{subject} is not planned")
        if element["lane"] != lane:
            raise StateError("writer_not_owned", f"{subject} is planned in {element['lane']}, not {lane}")
        holder = next((entry["subject"] for entry in [*state.get("planned", []), *state["queue"]]
                       if entry["subject"] != subject and entry.get("runId") == run), None)
        if holder is not None:
            raise StateError("run_bound", f"run {run} is already bound to {holder}")
        if element.get("runId") not in (None, run):
            raise StateError("run_bound", f"{subject} is already bound to run {element['runId']}")
        if element.get("runId") == run:
            return None
        element["runId"] = run
        return copy.deepcopy(element)

    # Binding the same run again writes nothing, so the record's version stays where it is; the write checks again.
    current = read_state(directory)
    if change(copy.deepcopy(current)) is None:
        return copy.deepcopy(next(entry for entry in current["planned"] if entry["subject"] == subject))
    _, bound = _tool_write(directory, change)
    if bound is None:
        return copy.deepcopy(next(entry for entry in read_state(directory)["planned"] if entry["subject"] == subject))
    return bound  # type: ignore[return-value]


def patch(
    directory: Path,
    *,
    writer: str,
    version: int,
    changes: object = None,
    selector: str | None = None,
    assignments: object = None,
    between_read_and_write: Callable[[], None] | None = None,
    lane: str | None = None,
) -> dict:
    """Apply ``changes`` as ``writer``, pinned to ``version``; a lane's write names its ``lane``.

    ``between_read_and_write`` runs after the state is read and the patch is
    prepared, and before the lock is taken for the write: a deterministic seam
    for concurrency tests.
    """
    if writer not in WRITERS:
        raise StateError("writer_invalid", f"writer must be one of {', '.join(WRITERS)}")
    if writer == "lane" and not lane:
        raise StateError("writer_invalid", "a lane's write names its lane")
    state = read_state(directory)
    if state["version"] != version:
        raise StateError("version_stale", f"patch is pinned to version {version}; state is at {state['version']}")
    updated = apply_patch(state, changes, selector, assignments)
    if between_read_and_write is not None:
        between_read_and_write()
    with file_lock(directory / STATE_LOCK):
        current = read_state(directory)
        if current["version"] != version:
            raise StateError("version_stale", f"patch is pinned to version {version}; state is at {current['version']}")
        if writer == "lane":
            violations = lane_violations(current, updated, lane)
            if violations:
                raise StateError("writer_not_owned", "; ".join(violations))
            _stamp_lane(current, updated, lane)
            # A lane speaks when it writes its focus, by --patch or by --set.
            speaks = _written_field(selector, changes, assignments, "focus") == lane
            wrote = _written_field(selector, changes, assignments, "waitingOn")
            # What the queue said a lane waits on ends when the lane speaks, unless the lane says it still does.
            if speaks and wrote is None:
                previous = next(entry for entry in current["lanes"] if entry["id"] == lane)
                if _waiting_by(previous) == "runner":
                    next(entry for entry in updated["lanes"] if entry["id"] == lane)["waitingOn"] = None
            _stamp_waiting(current, updated, writer, wrote)
            _stamp_focus(current, updated, writer, lane if speaks else None)
        else:
            violations = ownership_violations(current, updated, writer)
            if violations:
                raise StateError("field_not_owned", "; ".join(sorted(set(violations))))
            _stamp_waiting(current, updated, writer, _written_field(selector, changes, assignments, "waitingOn"))
            _stamp_focus(current, updated, writer, None)
        updated["version"] = version + 1
        if isinstance(updated.get("meta"), dict):
            updated["meta"]["updatedAt"] = now()
        errors = state_errors(updated)
        if errors:
            raise StateError("patch_invalid", "; ".join(errors))
        _require_unicode(updated, "patch_invalid")
        _write_atomically(directory / STATE_FILE, dumps(updated))
        _note_change(directory, "patch", version=updated["version"])
    return updated


def append_journal(
    directory: Path,
    *,
    kind: str,
    summary: str,
    lane: str | None = None,
    subject: str | None = None,
    review: dict | None = None,
    writer: str | None = None,
    recovered: bool = False,
    occurred_at: str | None = None,
    recovery: dict | None = None,
    review_artifact: dict | None = None,
    review_carry: dict | None = None,
) -> dict:
    """Append one entry. ``writer`` is "runner" when the runner writes; the lead and lanes leave it unset."""
    if writer == "runner" and kind == REVIEW_KIND:
        raise StateError("kind_not_owned", f"the runner never writes {REVIEW_KIND}: the lead or the lane records its review")
    if writer == "lane":
        if kind != REVIEW_KIND:
            raise StateError("writer_not_owned", f"a lane journals only {REVIEW_KIND}; the tool journals its hand-off")
        state = read_state(directory)
        owner = next((entry.get("lane") for entry in [*state["queue"], *state.get("planned", [])]
                      if entry["subject"] == subject), None)
        if lane is None or owner != lane:
            raise StateError("writer_not_owned", f"{subject} is not {lane}'s work")
    with file_lock(directory / STATE_LOCK):
        current = read_state(directory)
        entries = read_journal(directory)
        entry = {
            "id": len(entries) + 1,
            "at": now(),
            "kind": kind,
            "lane": lane,
            "subject": subject,
            "summary": summary,
        }
        if review is not None:
            entry["review"] = review
        if recovered:
            entry["occurredAt"] = occurred_at
        if recovery is not None:
            entry["recovery"] = recovery
            if not any(item["stage"] == "merged" and _same_recovered_merge(entry, item)
                       for item in current["queue"]):
                raise StateError("recovery_identity", "candidate identity changed before recovery journal append")
        if review_artifact is not None:
            entry["reviewArtifact"] = review_artifact
        if review_carry is not None:
            entry["reviewCarry"] = review_carry
        errors = journal_errors([*entries, entry])
        if errors:
            raise StateError("entry_invalid", "; ".join(errors))
        _require_unicode(entry, "entry_invalid")
        if kind == CLOSED_KIND:
            # The close's started mark goes in with the entry, so a close journaled by hand carries it too. It is
            # written first: a delivery.closed line never stands without its mark.
            current = _with_closing(directory, current, {"started": {"entry": entry["id"], "at": entry["at"]}})
        line = json.dumps(entry, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        complete = _journal_text(directory)[1]
        with open(directory / JOURNAL_FILE, "ab") as stream:
            # Drop the remains of an append that never finished.
            stream.truncate(complete)
            stream.write(line.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        _note_change(directory, "journal", version=current["version"], journal=entry["id"])
    return entry


def _with_closing(directory: Path, current: dict, closing: dict) -> dict:
    """Write the record's close marks, under the state lock the caller holds; returns the written state."""
    updated = copy.deepcopy(current)
    updated["meta"]["closing"] = closing
    updated["version"] = current["version"] + 1
    updated["meta"]["updatedAt"] = now()
    errors = state_errors(updated)
    if errors:
        raise StateError("patch_invalid", "; ".join(errors))
    _write_atomically(directory / STATE_FILE, dumps(updated))
    _note_change(directory, "patch", version=updated["version"])
    return updated


def close_finished(directory: Path, *, entry: int) -> dict:
    """The close's finished mark: the archive for delivery.closed entry ``entry`` has landed.

    Only the latest delivery.closed counts, and only once its started mark is there; marking it again changes nothing.
    """
    with file_lock(directory / STATE_LOCK):
        current = read_state(directory)
        closed = [item for item in read_journal(directory) if item["kind"] == CLOSED_KIND]
        if not closed or closed[-1]["id"] != entry:
            raise StateError("close_unknown", f"journal entry {entry} is not the latest {CLOSED_KIND}")
        closing = current["meta"].get("closing") or {}
        if (closing.get("started") or {}).get("entry") != entry:
            raise StateError("close_unknown", f"the record has no started mark for journal entry {entry}")
        if (closing.get("finished") or {}).get("entry") == entry:
            return current
        return _with_closing(directory, current, {**closing, "finished": {"entry": entry, "at": now()}})


# Native review observations: independent of product runs and admission.
REVIEW_ARTIFACT_LIMIT = 4 * 1024 * 1024
REVIEW_SOURCE_LIMIT = 1024 * 1024
REVIEW_READ_BUDGET = 8 * 1024 * 1024


def _review_json(data: bytes) -> object:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(data.decode("utf-8"), object_pairs_hook=unique)


def _review_refusal(where: str, what: str) -> StateError:
    """A refusal that names what was wrong and where: the round, the lens and the field."""
    return StateError("review_artifact_invalid", f"native review outcome {where}: {what}" if where else
                      f"native review outcome: {what}")


def _review_outcomes(payload: dict, subject: str, candidate: str) -> list[dict]:
    """Validate identity and originating dispositions, without granting admission."""
    def require(ok, what):
        if not ok:
            raise _review_refusal("", what)
    require(isinstance(payload, dict), "the capture is not a JSON object")
    require(payload.get("spec") == "delivery-review-observation/1", "spec is not delivery-review-observation/1")
    require(payload.get("subjectRef") == subject, f"subjectRef is not {subject}")
    require(payload.get("candidateRef") == candidate, f"candidateRef is not {candidate}")
    sources = payload.get("outcomes")
    require(isinstance(sources, list) and 1 <= len(sources) <= 32, "outcomes is not a list of one to 32 rounds")
    outcomes = []
    for number, encoded in enumerate(sources, 1):
        def require_round(ok, what, number=number):
            if not ok:
                raise _review_refusal(f"round {number}", what)
        require_round(isinstance(encoded, str) and len(encoded) <= REVIEW_SOURCE_LIMIT * 2, "the outcome is not bounded base64 text")
        try:
            raw = base64.b64decode(encoded, validate=True)
            require_round(len(raw) <= REVIEW_SOURCE_LIMIT, "the outcome file exceeds its byte limit")
            outcome = _review_json(raw)
            _require_unicode(outcome, "review_artifact_invalid")
        except (ValueError, UnicodeError, RecursionError) as error:
            raise _review_refusal(f"round {number}", f"the outcome file is not bounded JSON ({error})") from error
        outcomes.append(outcome)
    validate_review_outcomes(outcomes, subject, candidate)
    return outcomes


def validate_review_outcomes(outcomes: object, subject: str, candidate: str) -> dict[str, str]:
    """Validate decoded reports at both the state reader and renderer boundary.

    Every refusal names the round, and the lens and field where one is at fault, so a lane can mend its outcome files.
    Returns the findings still open after the last round, each with its severity.
    """
    def require(ok, what, where=""):
        if not ok:
            raise _review_refusal(where, what)
    require(isinstance(outcomes, list) and 1 <= len(outcomes) <= 32, "outcomes is not a list of one to 32 rounds")
    require(isinstance(candidate, str) and 0 < len(candidate) <= 2048, "the candidate is not a reference of 1 to 2048 characters")
    selected, origins, severities, open_findings = None, {}, {}, set()
    _require_unicode(outcomes, "review_artifact_invalid")
    for number, outcome in enumerate(outcomes, 1):
        here = f"round {number}"
        require(isinstance(outcome, dict), "the outcome is not a JSON object", here)
        require(type(outcome.get("round")) is int and outcome["round"] == number,
                f"round is {outcome.get('round')!r}, not {number}: outcomes go in round order from 1", here)
        entries = outcome.get("entries")
        require(isinstance(entries, list) and 1 <= len(entries) <= 16, "entries is not a list of one to 16 lenses", here)
        lenses, round_candidate = [], None
        for index, entry in enumerate(entries, 1):
            require(isinstance(entry, dict), f"entries[{index}] is not a JSON object", here)
            lens, binding = entry.get("lens"), entry.get("binding")
            require(isinstance(lens, str) and 0 < len(lens) <= 200, f"entries[{index}].lens is not a name of 1 to 200 characters", here)
            at = f"{here}, lens {lens}"
            require(lens not in lenses, "the lens appears twice in the round", at)
            require(isinstance(binding, dict), "binding is not a JSON object", at)
            lenses.append(lens)
            ref = binding.get("candidateRef")
            require(binding.get("subjectRef") == subject, f"binding.subjectRef is {binding.get('subjectRef')!r}, not {subject!r}", at)
            require(type(binding.get("round")) is int and binding["round"] == number,
                    f"binding.round is {binding.get('round')!r}, not {number}", at)
            require(isinstance(ref, str) and 0 < len(ref) <= 2048, "binding.candidateRef is not a reference of 1 to 2048 characters", at)
            require(round_candidate is None or round_candidate == ref,
                    f"binding.candidateRef {ref!r} differs from the round's other lenses ({round_candidate!r})", at)
            round_candidate = ref
            if entry.get("state") != "obtained":
                require(entry.get("state") in ("failed", "missing", "indeterminate"),
                        f"state is {entry.get('state')!r}, not obtained, failed, missing or indeterminate", at)
                require("result" not in entry, f"a {entry['state']} lens carries a result", at)
                continue
            result = entry.get("result")
            require(isinstance(result, dict), "result is not a JSON object", at)
            require(result.get("schemaVersion") == "review-lens-result/1", "result.schemaVersion is not review-lens-result/1", at)
            require(result.get("lensId") == lens, f"result.lensId is {result.get('lensId')!r}, not the entry's lens", at)
            require(result.get("subjectRef") == subject, f"result.subjectRef is {result.get('subjectRef')!r}, not {subject!r}", at)
            require(result.get("candidateRef") == ref, f"result.candidateRef is {result.get('candidateRef')!r}, not binding.candidateRef {ref!r}", at)
            require(type(result.get("round")) is int and result["round"] == number, f"result.round is {result.get('round')!r}, not {number}", at)
            require(result.get("outcome") in ("aligned", "changes-requested"),
                    f"result.outcome is {result.get('outcome')!r}, not aligned or changes-requested", at)
            require(bool(result.get("evidence")), "result.evidence is empty", at)
            require(not ("resolvedFindings" in result and "resolutions" in result),
                    "result carries both resolvedFindings and resolutions", at)
            findings = result.get("findings")
            closures = result.get("resolvedFindings", result.get("resolutions", []))
            require(isinstance(findings, list) and len(findings) <= 200, "result.findings is not a list of at most 200", at)
            require(isinstance(closures, list) and len(closures) <= 200, "result.resolvedFindings is not a list of at most 200", at)
            for finding in findings:
                require(isinstance(finding, dict) and isinstance(finding.get("id"), str) and bool(finding["id"]),
                        "a finding has no id", at)
                identifier = finding["id"]
                about = f"{at}, finding {identifier}"
                require(finding.get("severity") in SEVERITIES, f"severity is {finding.get('severity')!r}, not one of {', '.join(SEVERITIES)}", about)
                require(isinstance(finding.get("summary"), str) and bool(finding["summary"]), "summary is empty", about)
                require(identifier not in origins or origins[identifier] == lens,
                        f"the id was raised by {origins.get(identifier)!r}, another lens", about)
                origins[identifier] = lens
                severities[identifier] = min(severities.get(identifier, finding["severity"]), finding["severity"])
                if finding.get("state") == "deferred":
                    # The out-of-delta rule's one P1 deferral: a verification round's new P1 the lens lists as late.
                    late = result.get("lateFindings") if isinstance(result.get("lateFindings"), list) else []
                    require(severities[identifier] in ("P2", "P3")
                            or (severities[identifier] == "P1" and number > 1 and identifier in late),
                            "only a P2 or P3 finding may be deferred, or a P1 a verification round lists in lateFindings",
                            about)
                    require(isinstance(finding.get("deferredIssue"), str) and bool(finding["deferredIssue"].strip()),
                            "a deferred finding names no deferredIssue", about)
                    open_findings.discard(identifier)
                else:
                    open_findings.add(identifier)
            for closure in closures:
                require(isinstance(closure, dict) and isinstance(closure.get("id"), str), "a resolved finding has no id", at)
                about = f"{at}, resolved finding {closure['id']}"
                require(origins.get(closure["id"]) == lens, "no finding with this id was raised by this lens in an earlier or this round", about)
                require(closure.get("originatingLens", lens) == lens, "originatingLens is not this lens", about)
                require(closure.get("state") in ("closed", "fixed", "withdrawn", "dropped", "deferred", "open"),
                        f"state is {closure.get('state')!r}, not closed, fixed, withdrawn, dropped, deferred or open", about)
                require(isinstance(closure.get("resolution", closure.get("reason")), str)
                        and bool(closure.get("resolution", closure.get("reason")).strip()), "resolution is empty", about)
                if closure["state"] == "deferred":
                    require(severities[closure["id"]] in ("P2", "P3"), "only a P2 or P3 finding may be deferred", about)
                    require(isinstance(closure.get("deferredIssue"), str) and bool(closure["deferredIssue"].strip()),
                            "a deferred finding names no deferredIssue", about)
                if closure["state"] in ("closed", "fixed", "withdrawn", "dropped", "deferred"):
                    open_findings.discard(closure["id"])
                else:
                    open_findings.add(closure["id"])
        if outcome.get("outcome") == "aligned":
            require(not open_findings, f"the round is aligned with findings still open: {', '.join(sorted(open_findings))}", here)
            require(all(entry.get("state") == "obtained" and entry["result"]["outcome"] == "aligned" for entry in entries),
                    "the round is aligned but a lens is missing or asked for changes", here)
        require(selected is None or set(selected) == set(lenses),
                f"the lenses are {sorted(lenses)}, not the earlier rounds' {sorted(selected or [])}", here)
        selected = lenses
        if number == len(outcomes):
            require(round_candidate == candidate,
                    f"the last round reviewed {round_candidate!r}, not the captured candidate {candidate!r}", here)
    return {identifier: severities[identifier] for identifier in sorted(open_findings)}


def _review_artifact_path(directory: Path, digest: str) -> Path:
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise StateError("review_artifact_invalid", "invalid review digest")
    folder = directory / "review-artifacts"
    path = folder / (digest + ".json")
    if folder.is_symlink() or path.is_symlink():
        raise StateError("review_artifact_invalid", "review artifacts must not be symlinks")
    return path


def _read_review_file(path: Path, limit: int, budget: list[int] | None = None) -> bytes:
    if budget is not None and budget[0] <= 0:
        raise StateError("review_artifact_invalid", "review read budget exhausted")
    if not path.is_file() or path.is_symlink():
        raise StateError("review_artifact_invalid", "review artifact is not a regular file")
    with path.open("rb") as stream:
        allowance = limit + 1 if budget is None else min(limit + 1, budget[0])
        data = stream.read(allowance)
        if budget is not None:
            budget[0] -= len(data)
    if len(data) > limit or (len(data) == allowance and allowance <= limit):
        raise StateError("review_artifact_invalid", "review artifact exceeds its byte limit")
    return data


def capture_review(directory: Path, *, subject: str, candidate: str, outcomes: list[Path]) -> dict:
    """Keep exact native outcome bytes and append their observational reference once."""
    try:
        if not 1 <= len(outcomes) <= 32:
            raise StateError("review_artifact_invalid", "capture needs one to 32 outcome files")
        payload = {"spec": "delivery-review-observation/1", "subjectRef": subject, "candidateRef": candidate,
                   "outcomes": [base64.b64encode(_read_review_file(Path(path), REVIEW_SOURCE_LIMIT)).decode("ascii")
                                for path in outcomes]}
        _review_outcomes(payload, subject, candidate)
        raw = dumps(payload).encode("utf-8")
        if len(raw) > REVIEW_ARTIFACT_LIMIT:
            raise StateError("review_artifact_invalid", "review artifact exceeds its byte limit")
        digest = hashlib.sha256(raw).hexdigest()
        reference = {"digest": digest, "candidateRef": candidate}
        with file_lock(directory / "review-capture.lock"):
            state = read_state(directory)
            item = next((item for item in [*state["queue"], *state.get("planned", [])] if item["subject"] == subject), None)
            if item is None or item.get("runId"):
                raise StateError("review_artifact_invalid", "capture names existing work without a product run")
            previous = next((entry for entry in reversed(read_journal(directory))
                             if entry["subject"] == subject and entry["kind"] == "review.evidence"), None)
            if previous is not None:
                old_ref = previous["reviewArtifact"]
                old_raw = _read_review_file(_review_artifact_path(directory, old_ref["digest"]), REVIEW_ARTIFACT_LIMIT)
                if hashlib.sha256(old_raw).hexdigest() != old_ref["digest"]:
                    raise StateError("review_artifact_invalid", "retained review history is damaged")
                old = _review_json(old_raw)
                _review_outcomes(old, subject, old_ref["candidateRef"])
                shared = min(len(old["outcomes"]), len(payload["outcomes"]))
                if old["outcomes"][:shared] != payload["outcomes"][:shared]:
                    raise StateError("review_artifact_invalid", "capture rewrites retained review history")
                if len(payload["outcomes"]) <= len(old["outcomes"]):
                    return previous
            path = _review_artifact_path(directory, digest)
            path.parent.mkdir(exist_ok=True)
            if path.exists() and _read_review_file(path, REVIEW_ARTIFACT_LIMIT) != raw:
                raise StateError("review_artifact_invalid", "existing review artifact differs from its digest")
            if not path.exists():
                _write_atomically(path, raw.decode("utf-8"))
            return append_journal(directory, kind="review.evidence", subject=subject, lane=item.get("lane"),
                                  summary="Native review outcomes captured for display; no admission authority",
                                  review_artifact=reference)
    except (OSError, ValueError, UnicodeError, RecursionError) as error:
        raise StateError("review_artifact_invalid", "native review artifact could not be read or stored") from error


def _standing_carry(journal: list[dict], subject: str, capture: dict) -> dict | None:
    """The lead's latest carry of a subject's capture, while it still holds: after that capture and its last round."""
    carry = next((entry for entry in reversed(journal) if entry["subject"] == subject and "reviewCarry" in entry), None)
    if carry is None or carry["reviewCarry"]["digest"] != capture["reviewArtifact"]["digest"] or carry["id"] < capture["id"]:
        return None
    rounds = [entry["id"] for entry in journal if entry["subject"] == subject and entry["kind"] == "review.round"]
    return carry if not rounds or max(rounds) < carry["id"] else None


def carry_review(directory: Path, *, subject: str, from_head: str, to_head: str, reason: str) -> dict:
    """The lead carries a subject's captured review across a commit it judged needs no new round.

    The carry changes nothing the evidence says: a capture whose last round isn't aligned, or that leaves a P0 or P1
    open, is refused. It is display and acceptance evidence only, never a run, a review round or admission evidence.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise StateError("carry_invalid", "say why the commit needs no new round with --reason")
    with file_lock(directory / "review-capture.lock"):
        state, journal = read_state(directory), read_journal(directory)
        item = next((entry for entry in state["queue"] if entry["subject"] == subject), None)
        if item is None:
            planned = any(entry["subject"] == subject for entry in state.get("planned", []))
            raise StateError("carry_invalid", f"{subject} is planned, not handed off: a carry moves evidence to a queued head"
                             if planned else f"the record holds no queued work named {subject}")
        if item.get("runId"):
            raise StateError("carry_invalid", f"{subject} is built by run {item['runId']}, which keeps its own evidence")
        if item["stage"] == "merged":
            raise StateError("carry_invalid", f"{subject} is merged")
        capture = next((entry for entry in reversed(journal)
                        if entry["subject"] == subject and entry["kind"] == "review.evidence"), None)
        if capture is None:
            raise StateError("carry_invalid", f"{subject} has no captured review to carry")
        reference = capture["reviewArtifact"]
        rounds = [entry for entry in journal if entry["subject"] == subject and entry["kind"] == "review.round"]
        if rounds and rounds[-1]["id"] > capture["id"]:
            raise StateError("carry_invalid", f"{subject}'s latest review round has not been captured")
        standing = _standing_carry(journal, subject, capture)
        covered = standing["reviewCarry"]["to"] if standing else reference["candidateRef"]
        if from_head != covered:
            raise StateError("carry_invalid", f"{subject}'s evidence covers {covered}, not {from_head}")
        if to_head == from_head:
            raise StateError("carry_invalid", "carry the evidence to a different head")
        if item["head"] != to_head:
            raise StateError("carry_invalid", f"{subject} is queued at {item['head']}, not {to_head}")
        data = _read_review_file(_review_artifact_path(directory, reference["digest"]), REVIEW_ARTIFACT_LIMIT)
        if hashlib.sha256(data).hexdigest() != reference["digest"]:
            raise StateError("carry_invalid", "the captured review artifact is damaged")
        outcomes = _review_outcomes(_review_json(data), subject, reference["candidateRef"])
        still_open = {identifier: severity for identifier, severity in
                      validate_review_outcomes(outcomes, subject, reference["candidateRef"]).items()}
        blocking = [f"{identifier} ({severity})" for identifier, severity in still_open.items() if severity in ("P0", "P1")]
        if blocking:
            raise StateError("carry_invalid", f"the captured review leaves {', '.join(blocking)} open; a carry can't approve it")
        if outcomes[-1].get("outcome") != "aligned":
            raise StateError("carry_invalid", f"the captured review's last round, round {len(outcomes)}, is not aligned")
        return append_journal(directory, kind="decision.recorded", subject=subject, lane=item.get("lane"),
                              summary=f"The lead carried {subject}'s review from {from_head} to {to_head}, "
                                      f"no new round needed: {reason.strip()}",
                              review_carry={"from": from_head, "to": to_head, "digest": reference["digest"]})


def review_observations(directory: Path, state: dict, journal: list[dict]) -> dict:
    references = {entry["subject"]: entry for entry in journal if entry["kind"] == "review.evidence"}
    latest_rounds = {entry["subject"]: entry for entry in journal if entry["kind"] == "review.round"}
    observed, budget = {}, [REVIEW_READ_BUDGET]
    items = {item["subject"]: item for item in [*state["queue"], *state.get("planned", [])]}
    queued = {item["subject"] for item in state["queue"]}
    for subject, event in references.items():
        reference = event["reviewArtifact"]
        if subject not in items or items[subject].get("runId"):
            continue
        candidate = reference["candidateRef"]
        try:
            item = items[subject]
            latest = latest_rounds.get(subject)
            # Bound to the reviewed head: a runner sync only merges the base in, under its own checks. A head the
            # lead carried the evidence to stands in for it while that carry holds.
            carry = _standing_carry(journal, subject, event) if subject in queued else None
            if carry is not None and carry["reviewCarry"]["to"] != item.get("head"):
                carry = None
            if subject in queued and item.get("head") != candidate and carry is None:
                raise StateError("review_artifact_invalid", "review candidate is stale or unknown")
            # Before hand-off there is no head: bound to the round the lane journaled last, captured after it.
            if subject not in queued and latest is None:
                raise StateError("review_artifact_invalid", "no review round is journaled for this capture")
            if latest is not None and latest["id"] > event["id"]:
                raise StateError("review_artifact_invalid", "a later review has not been captured")
            data = _read_review_file(_review_artifact_path(directory, reference["digest"]), REVIEW_ARTIFACT_LIMIT, budget)
            if hashlib.sha256(data).hexdigest() != reference["digest"]:
                raise StateError("review_artifact_invalid", "review artifact digest mismatch")
            outcomes = _review_outcomes(_review_json(data), subject, candidate)
            if subject not in queued and (latest.get("review") or {}).get("round") != len(outcomes):
                raise StateError("review_artifact_invalid", "the capture is not of the latest journaled round")
            observed[subject] = {"candidateRef": candidate, "digest": reference["digest"], "outcomes": outcomes}
            if carry is not None and item.get("head") != candidate:
                observed[subject]["carried"] = {"from": carry["reviewCarry"]["from"], "to": carry["reviewCarry"]["to"],
                                                "note": carry["summary"]}
        except (StateError, OSError, ValueError, UnicodeError, RecursionError):
            observed[subject] = {"candidateRef": candidate, "unavailable": True}
    return observed


def review_evidence(directory: Path, subject: str) -> dict:
    """Whether a subject's review evidence is captured for its reviewed head, for the lead's hand-off check."""
    document = show(directory)
    item = next((item for item in [*document["state"]["queue"], *document["state"].get("planned", [])]
                 if item["subject"] == subject), None)
    if item is None:
        raise StateError("subject_unknown", f"the record holds no work named {subject}")
    # A run keeps its own evidence, which this record can't confirm, and a capture for it is refused.
    if item.get("runId"):
        return {"subject": subject, "evidence": "run-named", "head": item.get("head"), "runId": item["runId"]}
    observed = document.get("reviewEvidence", {}).get(subject)
    rounds = len({entry["review"]["round"] for entry in document["journal"]
                  if entry["subject"] == subject and entry["kind"] == "review.round"})
    found = {"subject": subject, "head": item.get("head"), "roundsJournaled": rounds}
    if observed is None:
        return {**found, "evidence": "not-captured"}
    if observed.get("unavailable"):
        return {**found, "evidence": "unavailable"}
    if len(observed["outcomes"]) < rounds:
        return {**found, "evidence": "incomplete", "roundsCaptured": len(observed["outcomes"])}
    carried = ({"carriedFrom": observed["carried"]["from"], "carriedTo": observed["carried"]["to"]}
               if "carried" in observed else {})
    return {**found, "evidence": "captured", "roundsCaptured": len(observed["outcomes"]), **carried}


def show(directory: Path, *, between_reads: Callable[[], None] | None = None) -> dict:
    """The validated ``{state, journal, stats}`` document a renderer reads.

    ``stats`` is derived, never stored: ``delivery_stats`` folds it from the
    other two, so every view shows the same numbers.

    Both files are read under the short state lock, so the pair always comes
    from the same write. ``between_reads`` runs between the two reads, under
    the lock: a deterministic seam for concurrency tests.
    """
    if not (directory / STATE_FILE).is_file():
        read_state(directory)
    with file_lock(directory / STATE_LOCK):
        state = read_state(directory)
        if between_reads is not None:
            between_reads()
        journal = read_journal(directory)
        reviews = review_observations(directory, state, journal)
    return {**({"reviewEvidence": reviews} if reviews else {}), "state": state, "journal": journal, "stats": delivery_stats(state, journal),
            "estimates": queue_estimates(state), "status": delivery_status(state, journal),
            "focus": lane_focus(state)}


# ---------------------------------------------------------------------------
# Per-stop delivery stats, folded from the record


def _seconds(start: str | None, end: str | None) -> int | None:
    """Whole seconds from one timestamp to another; unknown when either is, or when time ran backwards."""
    if start is None or end is None:
        return None
    elapsed = (_parse_time(end, exact=True) - _parse_time(start, exact=True)).total_seconds()
    return None if elapsed < 0 else int(elapsed)


def event_time(entry: dict) -> str | None:
    """Legacy/runner entries occurred when recorded; recovery explicitly carries a source time or unknown."""
    return entry.get("occurredAt", entry["at"])


def _same_recovered_merge(entry: dict, item: dict) -> bool:
    return (entry["kind"] == "candidate.merged" and entry["subject"] == item["subject"]
            and entry.get("recovery") == {"head": item.get("syncedHead") or item.get("head"),
                                           "changeRequest": item.get("changeRequest")})


def latest_merge(journal: list[dict]) -> dict | None:
    """Latest known landing by occurrence, not by when an old merge was recovered."""
    merges = [entry for entry in journal if entry["kind"] == "candidate.merged" and event_time(entry) is not None]
    return max(merges, key=lambda entry: (_parse_time(event_time(entry), exact=True), entry["id"]), default=None)


def _lane_assignment(subject: str, first_id: int, lane_entries: list[dict]) -> dict | None:
    """The lane's assignment that led to this candidate, when the assignment did not name it.

    The lane's last assignment naming no candidate, made before this
    candidate's first event and after the lane was last assigned or handed
    another one, so a reused lane never lends one candidate's assignment to
    the next. The runner's entries for an earlier candidate do not count:
    they say nothing about what the lane was assigned.
    """
    found = None
    for entry in lane_entries:
        if entry["id"] >= first_id:
            break
        if entry["subject"] not in (None, subject) and entry["kind"] in ("lane.assigned", "candidate.handed"):
            found = None
        elif entry["kind"] == "lane.assigned":
            found = entry
    return found


def _candidate_stats(item: dict, own: list[dict], lane_entries: list[dict]) -> dict:
    rounds_by_number: dict[int, dict] = {}
    for entry in own:
        if entry["kind"] == REVIEW_KIND:
            rounds_by_number[entry["review"]["round"]] = entry["review"]  # a round recorded again replaces itself
    rounds = [rounds_by_number[number] for number in sorted(rounds_by_number)]

    assigned = next((entry for entry in own if entry["kind"] == "lane.assigned"), None)
    if assigned is None and own:
        assigned = _lane_assignment(item["subject"], own[0]["id"], lane_entries)
    handed = next((entry for entry in reversed(own) if entry["kind"] == "candidate.handed"), None)
    after_handing = [entry for entry in own if handed is not None and entry["id"] > handed["id"]]
    checked = next((entry for entry in after_handing if entry["kind"] == "check.started"), None)
    after_check = [entry for entry in after_handing if checked is not None and entry["id"] > checked["id"]]
    ended = next((entry for entry in after_check if entry["kind"] == "candidate.merged"), None)
    outcome = "merged" if ended else None
    if ended is None:
        # A stop ends the stop's run only when no check started after it: a
        # candidate re-checked after a stop is still being checked.
        last = next((entry for entry in reversed(after_check) if entry["kind"] in ("candidate.stopped", "check.started")),
                    None)
        if last is not None and last["kind"] == "candidate.stopped":
            ended, outcome = last, "stopped"
    # The total ends where the latest attempt ended, and only at a merge. A
    # merge recovered from the forge, with no check, ends an attempt nothing
    # else ended. A merge that a later hand-off superseded is not this
    # candidate's total. With no hand-off on record, the last merge stands.
    if outcome == "merged":
        merged = ended
    elif handed is None:
        merged = next((entry for entry in reversed(own) if entry["kind"] == "candidate.merged"), None)
        if merged is not None:
            ended, outcome = merged, "merged"
    elif ended is None:
        merged = next((entry for entry in after_handing if entry["kind"] == "candidate.merged"), None)
        if merged is not None:
            ended, outcome = merged, "merged"
    else:
        merged = None

    if merged is None and item.get("stage") == "merged":
        # A hand-off's append can arrive after recovery of that same head/PR.
        merged = next((entry for entry in reversed(own) if _same_recovered_merge(entry, item)), None)
        if merged is not None:
            ended, outcome = merged, "merged"

    def at(entry: dict | None) -> str | None:
        return event_time(entry) if entry else None

    late_handoff = (merged is not None and handed is not None
                    and (at(merged) is None or _seconds(at(handed), at(merged)) is None))

    first_start = min((review["startedAt"] for review in rounds), key=lambda text: _parse_time(text, exact=True),
                      default=None)
    last_finish = max((review["finishedAt"] for review in rounds), key=lambda text: _parse_time(text, exact=True),
                      default=None)
    reported = item.get("reportedRounds")
    findings = None
    if rounds:
        findings = {severity: sum(review["findings"][severity] for review in rounds) for severity in SEVERITIES}
    chosen = item.get("checkSelection")
    return {
        "assignedAt": at(assigned),
        "handedAt": at(handed),
        "checkStartedAt": at(checked),
        "endedAt": at(ended),
        "outcome": outcome,
        "buildSeconds": _seconds(at(assigned), first_start),
        "reviewSeconds": _seconds(first_start, last_finish),
        "waitSeconds": None if late_handoff else _seconds(at(handed), at(checked)),
        "checkToMergeSeconds": _seconds(at(checked), at(ended)),
        "totalSeconds": _seconds(at(assigned), at(merged)),
        "rounds": len(rounds) if rounds else reported,
        "roundsSource": "journal" if rounds else ("reported" if reported is not None else None),
        "findings": findings,
        "lastVerdicts": dict(rounds[-1]["verdicts"]) if rounds else None,
        # Only once a binding that enables lead selection has checked it, so other records read as before.
        **({"checksSelectedBy": chosen["by"]} if isinstance(chosen, dict) else {}),
    }


def median_seconds(values: list[int]) -> int | None:
    """The median of the last five values, or None with no values: no history means no estimate, never a guess."""
    recent = sorted(values[-5:])
    if not recent:
        return None
    middle = len(recent) // 2
    return recent[middle] if len(recent) % 2 else (recent[middle - 1] + recent[middle]) // 2


def queue_estimates(state: dict, at: str | None = None) -> dict:
    """Each waiting candidate's position and expected start, folded from the check history.

    Waiting means queued, in the record's order. The run in progress counts
    once, batch or not, for whatever of its median run time remains; each
    candidate ahead adds one median run. Without run history the start is
    unknown. These are estimates, never promises.
    """
    moment = _parse_time(at or now(), exact=True)
    expected = median_seconds(((state["meta"].get("checkHistory") or {}).get("runs")) or [])
    current = state["meta"]["checking"]["now"]
    remaining = 0
    if current is not None and expected is not None:
        elapsed = (moment - _parse_time(current["startedAt"], exact=True)).total_seconds()
        # A start in the future (a clock out of step) still leaves at most one run.
        remaining = min(expected, max(0, expected - int(elapsed)))
    waiting = sorted((item for item in state["queue"] if item["stage"] == "queued" and not item.get("pending")),
                     key=lambda item: (item["order"], item["subject"]))
    estimates = {}
    for position, item in enumerate(waiting, start=1):
        start = None
        if expected is not None:
            try:
                start = moment + datetime.timedelta(seconds=remaining + (position - 1) * expected)
            except OverflowError:
                start = None  # a history too long to be a run means nothing about when
            else:
                start = start.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        estimates[item["subject"]] = {"position": position, "expectedStart": start}
    return estimates


# How long a lead's status override lasts when the binding does not say.
STATUS_OVERRIDE_MAX_AGE_SECONDS = 4 * 3600
FINISHED_STEPS = ("passed", "failed", "skipped", "timed_out", "reused")


def delivery_status(state: dict, journal: list[dict], at: str | None = None) -> dict:
    """One line on where the delivery stands: the lead's override while it is fresh, or one derived from the record.

    The derived line names what is being checked and how far it has got, the
    last landing, the lanes that are blocked, and what is next.
    """
    moment = _parse_time(at or now(), exact=True)
    override = state["meta"].get("statusOverride")
    if override and moment < _parse_time(override["expiresAt"], exact=True):
        # An override says so, and until when, so a stale manual line never passes as derived state.
        return {"text": override["text"], "source": "override", "expiresAt": override["expiresAt"]}
    parts = []
    hold = state["meta"].get("hold")
    if hold:
        parts.append(f"Serving is paused: {hold['reason']}")
    elif base_awaits_repair(journal):
        parts.append("Serving is paused until the base is confirmed")
    current = state["meta"]["checking"]["now"]
    if current is not None:
        text = f"Checking {current['title']}"
        steps = [step for step in current["steps"] if step["label"] != "selecting checks"]
        if steps:
            done = sum(1 for step in steps if step["state"] in FINISHED_STEPS)
            text += f", {done} of {len(steps)} checks done"
        parts.append(text)
    landed = latest_merge(journal)
    if landed is not None:
        parts.append(f"last landed {landed['subject']} at {event_time(landed)}")
    blocked = [
        lane["id"] + (f" (waiting on {lane['waitingOn']})" if lane.get("waitingOn") else "")
        for lane in state["lanes"] if lane["state"] == "blocked"
    ]
    if blocked:
        parts.append("blocked: " + ", ".join(blocked))
    stopped = [item["subject"] for item in sorted(state["queue"], key=lambda item: (item["order"], item["subject"]))
               if item["stage"] == "stopped"]
    if stopped:
        # Stopped work waits for its lane to hand it off again; it is never "nothing in flight".
        parts.append("stopped: " + ", ".join(stopped))
    waiting = [item["subject"] for item in sorted(state["queue"], key=lambda item: (item["order"], item["subject"]))
               if item["stage"] == "queued" and not item.get("pending")]
    if waiting:
        parts.append(f"next: {waiting[0]}" + (f", then {len(waiting) - 1} more" if len(waiting) > 1 else ""))
    sources = state["meta"].get("sources") or {}
    for source in SNAPSHOT_SOURCES:
        entry = sources.get(source)
        if entry is not None and entry["status"] == "stale":
            # What the record shows from a source that stopped answering is only as new as its last read.
            parts.append(f"{source} last read at {entry['lastReadAt']}" if entry.get("lastReadAt")
                         else f"{source} never read")
    reports = state["meta"].get("sourceReports") or []
    if reports:
        # What a refresh could not settle by itself waits on the lead, so the line says so.
        first = reports[0]
        said = f"{first['subject']}: {first['note']}" if first.get("subject") else first["note"]
        parts.append(f"for the lead: {said}" + (f", then {len(reports) - 1} more" if len(reports) > 1 else ""))
    return {"text": " · ".join(parts) or "Nothing in flight", "source": "derived"}


def base_awaits_repair(journal: list[dict]) -> bool:
    """Whether the last word on the base is base.broken: the lead confirms it with base.repaired."""
    last = next((entry["kind"] for entry in reversed(journal) if entry["kind"] in ("base.broken", "base.repaired")), None)
    return last == "base.broken"


def lane_focus(state: dict) -> dict:
    """For each lane that wrote no focus of its own, one derived from its work: subject, stage, review rounds."""
    focus = {}
    for lane in state["lanes"]:
        if lane["focus"]:
            continue
        own = [item for item in state["queue"] if item["lane"] == lane["id"] and item["stage"] != "merged"]
        if own:
            item = max(own, key=lambda element: (element["order"], element["subject"]))
            rounds = item.get("reportedRounds")
            told = "rounds unknown" if rounds is None else f"{rounds} review {'round' if rounds == 1 else 'rounds'}"
            focus[lane["id"]] = f"{item['subject']}: {item['stage']}, {told}"
            continue
        planned = [item for item in state.get("planned", []) if item["lane"] == lane["id"]]
        if planned:
            focus[lane["id"]] = f"{planned[-1]['subject']}: {planned[-1]['stage']}"
    return focus


def set_status_override(directory: Path, text: str | None, max_age: int) -> dict:
    """The lead's status line, shown instead of the derived one for max_age seconds; None clears it."""

    def change(state: dict) -> None:
        if text is None:
            state["meta"]["statusOverride"] = None
            return
        set_at = now()
        try:
            expires = _parse_time(set_at, exact=True) + datetime.timedelta(seconds=max_age)
        except OverflowError:
            raise StateError("patch_invalid", f"an override cannot last {max_age} seconds") from None
        state["meta"]["statusOverride"] = {
            "text": text, "setAt": set_at, "expiresAt": expires.isoformat().replace("+00:00", "Z"),
        }

    updated, _ = _tool_write(directory, change)
    return updated


def delivery_stats(state: dict, journal: list[dict]) -> dict:
    """Per-stop stats for every queued and planned candidate, by subject.

    A pure fold of the record, in one pass over the journal. A stat whose
    events are missing is null, never zero: an older candidate with no
    assignment entry has no build time. With no journaled rounds, the round
    count is the lane's reported one. A candidate handed again follows its
    latest attempt: its hand-off, check and end are that attempt's, and its
    total runs from the assignment to that attempt's merge, null unless that
    attempt merged; a merge recovered from the forge, with no check, counts.
    Its assignment, build and review times, rounds and findings cover every
    attempt.
    """
    by_subject: dict[str, list[dict]] = {}
    by_lane: dict[str, list[dict]] = {}
    for entry in journal:
        if entry["subject"] is not None:
            by_subject.setdefault(entry["subject"], []).append(entry)
        if entry["lane"] is not None:
            by_lane.setdefault(entry["lane"], []).append(entry)
    items = [*state["queue"], *state.get("planned", [])]
    return {item["subject"]: _candidate_stats(item, by_subject.get(item["subject"], []),
                                              by_lane.get(item["lane"], []) if item.get("lane") else [])
            for item in items}


# ---------------------------------------------------------------------------
# Reconciling the record with forge and tracker snapshots

SNAPSHOT_STATES = {
    "tracker": frozenset({"backlog", "started", "completed", "canceled"}),
    "forge": frozenset({"open", "draft", "merged", "closed"}),
}


def _parse_time(text: str, *, exact: bool = False) -> datetime.datetime:
    """A schema timestamp as UTC; ``exact`` keeps its fraction of a second, to the microsecond.

    Digits beyond the sixth are dropped, never rounded, so a time never moves
    later than it was written. Times in the same microsecond compare as equal,
    even when the later one was written first; durations are whole seconds in
    any case.
    """
    whole, _, fraction = text.rstrip("Z").partition(".")
    moment = datetime.datetime.strptime(whole, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    if exact and fraction:
        moment += datetime.timedelta(microseconds=int(fraction[:6].ljust(6, "0")))
    return moment


def read_snapshot(path: Path, max_age: float, *, parent: str | None = None) -> dict:
    """A validated, fresh ``work-snapshot/1``, or a refusal."""
    try:
        snapshot = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        # ValueError covers JSON a parser refuses, such as a number too long to convert.
        raise StateError("snapshot_invalid", f"cannot read the snapshot {Path(path).name}: {error}") from None
    return check_snapshot(snapshot, Path(path).name, max_age, parent=parent)


# How many dependency problems a refusal names before it counts the rest.
DEPENDENCY_PROBLEM_LIMIT = 10


def dependency_problems(items: list[tuple[str, list[str]]], parent: str | None) -> list[str]:
    """Why work items' blockers are not a dependency list, naming the items; empty when they are.

    ``items`` are (reference, blockers) pairs. An item may not be the parent,
    be blocked by the parent, block itself, name a blocker more than once, or
    wait on itself through other items. A blocker outside the items is
    allowed. At most ``DEPENDENCY_PROBLEM_LIMIT`` problems are named, then how
    many more there are; a long cycle is named by its first items and the one
    it returns to.

    Two identical copies exist, because the delivery state tool must run on
    its own: one in agent_skills/capabilities.py for list-work results, one in
    skills/orchestrate-work/scripts/delivery_state.py for tracker snapshots.
    A parity test in tests/test_tracker_contract.py requires them to match, so
    edit both together.
    """
    problems: list[str] = []
    unnamed = 0

    def found(problem: str) -> None:
        # Past the limit a problem is only counted, so a dense graph never
        # keeps more text than the refusal shows.
        nonlocal unnamed
        if len(problems) < DEPENDENCY_PROBLEM_LIMIT:
            problems.append(problem)
        else:
            unnamed += 1

    blockers_of: dict[str, list[str]] = {}
    for reference, blockers in items:
        if parent is not None and reference == parent:
            found(f"{reference} is the parent itself")
        if parent is not None and parent in blockers:
            found(f"{reference} is blocked by its own parent {parent}")
        if reference in blockers:
            found(f"{reference} blocks itself")
        named: set[str] = set()
        repeated: dict[str, None] = {}
        for blocker in blockers:
            if blocker in named:
                repeated[blocker] = None
            named.add(blocker)
        for blocker in repeated:
            found(f"{reference} names its blocker {blocker} more than once")
        blockers_of[reference] = [blocker for blocker in dict.fromkeys(blockers) if blocker != reference]
    # A cycle through other items, found without recursion in one pass over
    # the items and blockers: each is reported once, from the item it is first
    # reached by.
    finished: set[str] = set()
    for start in blockers_of:
        if start in finished:
            continue
        path = [start]
        position = {start: 0}
        stack = [iter(blockers_of[start])]
        while stack:
            for blocker in stack[-1]:
                if blocker not in blockers_of or blocker in finished:
                    continue
                if blocker in position:
                    length = len(path) - position[blocker]
                    shown = path[position[blocker]:position[blocker] + min(length, 5)]
                    if length > 5:
                        shown.append(f"... {length - 5} more")
                    found("the blockers form a cycle, each blocked by the next: " + " -> ".join(shown + [blocker]))
                    continue
                position[blocker] = len(path)
                path.append(blocker)
                stack.append(iter(blockers_of[blocker]))
                break
            else:
                stack.pop()
                done = path.pop()
                del position[done]
                finished.add(done)
    if unnamed:
        problems.append(f"and {unnamed} more")
    return problems


def check_snapshot(snapshot: object, name: str, max_age: float, *, parent: str | None = None) -> dict:
    """``snapshot`` as a valid, fresh ``work-snapshot/1``, or a refusal naming it ``name``.

    A tracker snapshot's blockers must form a dependency list under ``parent``,
    the record's tracker parent; a forge snapshot's are ignored, so never checked.
    """
    errors = validate(snapshot, load_schema("work-snapshot/1"))
    if not errors:
        try:
            _require_unicode(snapshot, "snapshot_invalid")
        except StateError as error:
            errors.append(str(error))
    if not errors:
        allowed = SNAPSHOT_STATES[snapshot["source"]]
        errors = [f"/items/{index}/state: {item['state']} is not a {snapshot['source']} state"
                  for index, item in enumerate(snapshot["items"]) if item["state"] not in allowed]
        subjects = [item["subject"] for item in snapshot["items"]]
        if len(subjects) != len(set(subjects)):
            errors.append("/items: each subject appears once")
        if snapshot["source"] == "tracker":
            errors += [f"/items: {problem}" for problem in dependency_problems(
                [(item["subject"], item.get("blockedBy", [])) for item in snapshot["items"]], parent)]
        if "parent" in snapshot and snapshot["source"] != "tracker":
            errors.append("/parent: only a tracker snapshot carries it")
        elif "parent" in snapshot and parent is not None and snapshot["parent"]["subject"] != parent:
            errors.append(f"/parent: {snapshot['parent']['subject']} is not the record's tracker parent {parent}")
    if not errors:
        try:
            _parse_time(snapshot["takenAt"], exact=True)
        except ValueError:
            errors.append("/takenAt: not a real time")
    if not errors:
        for index, item in enumerate(snapshot["items"]):
            for field in ("status", "offeredTransitions"):
                if field in item and snapshot["source"] != "tracker":
                    errors.append(f"/items/{index}/{field}: only a tracker snapshot carries it")
            if "offeredTransitions" in item and "status" not in item:
                errors.append(f"/items/{index}/offeredTransitions: the moves offered are from a status the item names")
            offered = item.get("offeredTransitions", [])
            if len(offered) != len(set(offered)):
                errors.append(f"/items/{index}/offeredTransitions: each move appears once")
    if not errors:
        for index, item in enumerate(snapshot["items"]):
            if "mergedAt" not in item:
                continue
            if snapshot["source"] != "forge" or item["state"] != "merged":
                errors.append(f"/items/{index}/mergedAt: only a forge merge carries its occurrence time")
            try:
                if _parse_time(item["mergedAt"], exact=True) > _parse_time(snapshot["takenAt"], exact=True):
                    errors.append(f"/items/{index}/mergedAt: merge follows snapshot")
                if _parse_time(item["mergedAt"], exact=True) > _parse_time(now(), exact=True):
                    errors.append(f"/items/{index}/mergedAt: merge follows observation clock")
            except ValueError:
                errors.append(f"/items/{index}/mergedAt: not a real time")
    if errors:
        raise StateError("snapshot_invalid", f"{name}: " + "; ".join(errors))
    age = (datetime.datetime.now(datetime.timezone.utc) - _parse_time(snapshot["takenAt"])).total_seconds()
    if age < -SNAPSHOT_CLOCK_SKEW_SECONDS:
        raise StateError("snapshot_invalid", f"{name} was taken {int(-age)}s in the future: check its clock")
    if age > max_age:
        raise StateError(
            "snapshot_stale",
            f"{name} was taken {int(age)}s ago, more than {max_age:g}s: list the work again",
        )
    return snapshot


def _plan(state: dict, snapshots: list[dict], journal: list[dict]) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """The lead's changes, the merges to recover, and what to report, for one read of the state."""
    lead = {"queue": copy.deepcopy(state["queue"]), "planned": copy.deepcopy(state.get("planned", [])),
            "followups": copy.deepcopy(state["followups"])}
    changes: list[dict] = []
    reports: list[dict] = []
    recoveries: list[dict] = []
    listed: dict[str, dict] = {}

    def queued(subject: str) -> dict | None:
        return next((item for item in lead["queue"] if item["subject"] == subject), None)

    def planned(subject: str) -> dict | None:
        return next((item for item in lead["planned"] if item["subject"] == subject), None)

    def followed(subject: str) -> bool:
        return any(subject in (item["id"], item["reference"]) for item in lead["followups"])

    def change(subject: str, text: str, recovery: bool = False) -> None:
        changes.append({"subject": subject, "change": text, "recovery": recovery})

    def report(subject: str, text: str) -> None:
        reports.append({"subject": subject, "note": text})

    def where(entry: dict) -> str:
        """Where planned work stands, as a report names it."""
        return "in review" if entry["stage"] == "review" else entry["stage"]

    # The tracker first, whatever the order given: work it adds is then there
    # for the forge to link, so one pass reaches the fixed point.
    for snapshot in sorted(snapshots, key=lambda snapshot: snapshot["source"] != "tracker"):
        for item in snapshot["items"]:
            subject, word = item["subject"], item["state"]
            title = item.get("title") or subject
            if snapshot["source"] == "tracker":
                listed[subject] = item
                known = queued(subject) or planned(subject)
                if not known and not followed(subject):
                    if word == "backlog":
                        lead["followups"].append({"id": subject, "title": title, "reference": subject})
                        change(subject, "in the tracker but not the record: added as a follow-up")
                    elif word == "started":
                        # Started means under way, so it joins as building, settled in this one pass.
                        lead["planned"].append({"subject": subject, "title": title, "lane": None, "stage": "building",
                                                "trackerStarted": True})
                        change(subject, "started in the tracker but not in the record: added as planned work, building")
                    elif word == "completed":
                        report(subject, "completed in the tracker but never recorded")
                elif word == "started" and not known:
                    report(subject, "a follow-up the tracker now shows started: assign it a lane")
                elif word == "started" and planned(subject) and planned(subject)["stage"] == "planned":
                    # A lane moves its ticket to started as it begins; that alone moves the stop.
                    planned(subject).update(stage="building", trackerStarted=True)
                    change(subject, "started in the tracker: now building")
                elif word == "started" and planned(subject) and not planned(subject).get("trackerStarted"):
                    # Work a draft change request moved first: its ticket has now been started too.
                    planned(subject)["trackerStarted"] = True
                    change(subject, "started in the tracker: recorded as started")
                elif (word == "backlog" and planned(subject) and planned(subject)["stage"] in ("building", "review")
                      and planned(subject).get("trackerStarted")):
                    # Only a ticket once seen started went back; one never started is simply not yet started.
                    report(subject, f"{where(planned(subject))} in the record but back in the tracker's backlog: check it")
                elif word == "completed" and planned(subject):
                    report(subject, f"completed in the tracker but still {where(planned(subject))} in the record: check the forge")
                elif word == "canceled" and planned(subject):
                    report(subject, f"canceled in the tracker but still {where(planned(subject))} in the record")
                continue
            reference = item.get("changeRequest")
            entry = queued(subject) or planned(subject)
            if entry is None:
                if word in ("open", "draft", "merged"):
                    report(subject, f"{'an' if word == 'open' else 'a'} {word} change request for work the record does not hold")
                continue
            if (entry.get("stage") == "merged" and reference and entry.get("changeRequest")
                    and entry["changeRequest"] != reference):
                report(subject, "forge change request differs from the terminal candidate: settle identity before linking")
                continue
            if (word == "merged" and entry.get("stage") == "merged" and item.get("head")
                    and item["head"] != (entry.get("syncedHead") or entry.get("head"))):
                report(subject, "merged forge head differs from the recorded head: settle identity before recovery")
                continue
            if reference and entry.get("changeRequest") != reference:
                entry["changeRequest"] = reference
                change(subject, f"linked change request {reference}")
            if word == "open" and entry is planned(subject) and entry["stage"] in ("planned", "building"):
                entry["stage"] = "review"
                change(subject, "its change request is open: now in review")
            elif word == "draft" and entry is planned(subject) and entry["stage"] == "planned":
                # A lane's change request opens as a draft; with no tracker snapshot, that alone moves the stop.
                entry["stage"] = "building"
                change(subject, "its change request is a draft: now building")
            elif word == "closed" and entry.get("stage") != "merged":
                report(subject, "its change request closed without merging")
            elif word == "merged":
                if entry is planned(subject):
                    if "head" not in item:
                        report(subject, "merged on the forge, but the snapshot gives no head to record")
                        continue
                    # A hand-off the lead missed: the work joins the queue as the lead's...
                    lead["planned"] = [other for other in lead["planned"] if other["subject"] != subject]
                    element = {
                        "order": max([other["order"] for other in lead["queue"]] or [0]) + 1,
                        "subject": subject, "title": entry["title"], "lane": entry["lane"], "stage": "queued",
                        "head": item["head"], "syncedHead": None, "leadInputs": [], "reportedRounds": None,
                        "runId": entry.get("runId"), "checks": [],
                    }
                    if entry.get("changeRequest"):
                        element["changeRequest"] = entry["changeRequest"]
                    if entry.get("trackerStarted"):
                        element["trackerStarted"] = True
                    # Under the lead's policy it waits like any hand-off, so its recovery can say the wait was skipped.
                    if state["meta"].get("handoffsNeedAcceptance"):
                        element["pending"] = True
                    lead["queue"].append(element)
                    change(subject, "merged on the forge while still planned: added to the queue")
                    entry = element
                effective_head = entry.get("syncedHead") or entry["head"]
                if entry["stage"] == "merged" and item.get("head", effective_head) != effective_head:
                    report(subject, "merged forge head differs from the recorded head: settle identity before recovery")
                    continue
                if entry["stage"] != "merged" or not _current_merge(journal, entry):
                    # ...and the merge the runner never saw is recovered as the runner's.
                    recoveries.append({"subject": subject, "head": item.get("head") or effective_head,
                                       "changeRequest": entry.get("changeRequest"), "lane": entry.get("lane"),
                                       "occurredAt": item.get("mergedAt")})
                    change(subject, "merged on the forge but not recorded by the runner: recorded as merged", True)
                elif "pending" in entry:
                    # Left by an earlier version; the merge it waited behind is already recorded.
                    del entry["pending"]
                    change(subject, "merged and recorded, but still marked as waiting for the lead: wait cleared")
    if not lead["planned"] and "planned" not in state:
        del lead["planned"]
    changed = {key: value for key, value in lead.items() if value != state.get(key, [] if key == "planned" else None)}
    tickets = _ticket_changes(state.get("tickets", {}), listed, lead)
    if tickets:
        changed["tickets"] = tickets
        moved = {entry["subject"] for entry in changes}
        for subject in tickets:
            if subject not in moved:
                change(subject, "its tracker title or summary changed")
    return changed, recoveries, changes, reports


def _ticket_changes(tickets: dict, listed: dict[str, dict], lead: dict) -> dict:
    """A merge patch of the tracker titles and summaries the record should change.

    Kept only for work the record holds, and only when it adds to the record:
    a summary, or a title other than the record's own. A ticket the tracker no
    longer lists is left as it was.
    """
    own: dict[str, str] = {}
    for item in [*lead["queue"], *lead.get("planned", [])]:
        own[item["subject"]] = item["title"]
    for item in lead["followups"]:
        for key in (item["id"], item["reference"]):
            if key is not None:
                own.setdefault(key, item["title"])
    patch: dict = {}
    for subject, item in listed.items():
        if subject not in own:
            continue
        # A ticket listed without a title keeps the record's own; a blank summary is none.
        title = item.get("title") or own[subject]
        summary = item["summary"] if (item.get("summary") or "").strip() else None
        wanted = {"title": title, **({"summary": summary} if summary is not None else {})}
        if summary is None and title == own[subject]:
            wanted = None
        if tickets.get(subject) != wanted:
            patch[subject] = None if wanted is None else {"title": title, "summary": summary}
    return patch


def reconcile(
    directory: Path,
    snapshot_paths: list[Path],
    *,
    max_age: float = SNAPSHOT_MAX_AGE_SECONDS,
    between_read_and_write: Callable[[], None] | None = None,
) -> dict:
    """Bring the record up to date with forge and tracker snapshots.

    Lead fields are written as the lead. A merge the runner never recorded is
    written as the runner, with a journal entry saying so: a recovery path, since
    the runner records its own merges. Pinned to the version it read; when the
    record moves under it, the plan is made again from the new record.
    """
    if not snapshot_paths:
        raise StateError("snapshot_invalid", "give at least one --snapshot")
    parent = read_state(directory)["meta"].get("trackerParent")
    snapshots = [read_snapshot(Path(path), max_age, parent=parent) for path in snapshot_paths]
    seen: dict[tuple[str, str], str] = {}
    for path, snapshot in zip(snapshot_paths, snapshots):
        for item in snapshot["items"]:
            key = (snapshot["source"], item["subject"])
            if key in seen:
                raise StateError("snapshot_invalid", f"{item['subject']} is in two {snapshot['source']} snapshots, "
                                                     f"{seen[key]} and {Path(path).name}: give one view of each")
            seen[key] = Path(path).name
    # Each snapshot was checked alone; a cycle can still run across two of them.
    problems = dependency_problems([(item["subject"], item.get("blockedBy", [])) for snapshot in snapshots
                                    if snapshot["source"] == "tracker" for item in snapshot["items"]], parent)
    if problems:
        raise StateError("snapshot_invalid", "the tracker snapshots together: " + "; ".join(problems))
    finished: list[dict] = []
    held: list[dict] = []
    _finish_recovery(directory, finished, held)
    for attempt in range(1, RECONCILE_ATTEMPTS + 1):
        state = read_state(directory)
        lead, recoveries, changes, reports = _plan(state, snapshots, read_journal(directory))
        changes[:0] = finished
        reports[:0] = held
        if not lead and not recoveries:
            if finished:
                return {"ok": True, "drift": True, "changes": changes, "reports": reports,
                        "summary": f"{len(changes)} change{'s' if len(changes) != 1 else ''}"}
            return {"ok": True, "drift": False, "changes": [], "reports": reports, "summary": "no drift"}
        try:
            if lead:
                patch(directory, writer="lead", version=state["version"], changes=lead,
                      between_read_and_write=between_read_and_write)
        except StateError as error:
            if error.code == "version_stale" and attempt < RECONCILE_ATTEMPTS:
                continue
            raise
        break
    if recoveries:
        _recover(directory, recoveries, changes, reports)
    if not changes:
        return {"ok": True, "drift": False, "changes": [], "reports": reports, "summary": "no drift"}
    return {"ok": True, "drift": True, "changes": changes, "reports": reports,
            "summary": f"{len(changes)} change{'s' if len(changes) != 1 else ''}"}


def _current_merge(journal: list[dict], item: dict) -> bool:
    """Respect attempt order, except a recovered identity survives its own delayed hand-off append."""
    after_handoff = True
    for entry in reversed(journal):
        if entry["subject"] == item["subject"]:
            if _same_recovered_merge(entry, item):
                return True
            if after_handoff and entry["kind"] == "candidate.merged":
                return True
            if entry["kind"] == "candidate.handed":
                after_handoff = False
    return False


WAIT_BYPASSED = " before the lead accepted it"


def _bypass_report(subject: str) -> dict:
    return {"subject": subject, "note": f"merged{WAIT_BYPASSED}: check it with lead-accept, "
                                        "or journal your decision if lead-accept can't take it"}


def _recover(directory: Path, recoveries: list[dict], changes: list[dict], reports: list[dict]) -> None:
    """Record merges the runner missed, as the runner, only while no runner is running.

    A runner holding the queue lock may have merged and not yet recorded it;
    recording for it would race its own write and journal a false claim. So
    the queue lock is tried, not waited for, and each item is read again under
    it: a merge the runner recorded meanwhile is left alone.
    """
    def withdraw(subject: str) -> None:
        changes[:] = [change for change in changes if not (change["subject"] == subject and change["recovery"])]

    with contextlib.ExitStack() as held:
        try:
            held.enter_context(file_lock(directory / QUEUE_LOCK, timeout=0))
        except StateError as error:
            if error.code != "lock_timeout":
                raise
            for recovery in recoveries:
                withdraw(recovery["subject"])
                reports.append({"subject": recovery["subject"],
                                "note": "merged on the forge while the runner is running: reconcile again once it stops"})
            return
        # An intent left waiting for the runner is finished first, under this
        # same hold of the lock, so the next intent never overwrites it.
        try:
            pending = _read_recovery(directory)
        except StateError as error:
            reports.append({"subject": None, "note": f"{error}: set aside as {_set_aside(directory)}"})
            pending = None
        completed: list[dict] = []
        if pending is not None:
            _finish_intent(directory, pending, completed, reports)
            # Finished here after all, so no longer waiting.
            reports[:] = [report for report in reports
                          if not (report["subject"] == pending["subject"] and "waits for the queue lock" in report["note"])]
        for recovery in recoveries:
            selector = f"queue[subject={recovery['subject']}]"
            for _ in range(RECONCILE_ATTEMPTS):
                state = read_state(directory)
                item = next((item for item in state["queue"] if item["subject"] == recovery["subject"]), None)
                if item is None or (item["stage"] == "merged" and _current_merge(read_journal(directory), item)):
                    withdraw(recovery["subject"])
                    break
                if item.get("changeRequest") != recovery["changeRequest"]:
                    withdraw(recovery["subject"])
                    reports.append({"subject": item["subject"], "note": "candidate identity changed: settle change request before recovery"})
                    break
                if item["stage"] == "merged" and (item.get("syncedHead") or item["head"]) != recovery["head"]:
                    withdraw(recovery["subject"])
                    reports.append({"subject": item["subject"], "note": "recorded head changed: settle identity before recovery"})
                    break
                where = f" as change request {recovery['changeRequest']}" if recovery["changeRequest"] else ""
                bypassed = "pending" in item
                intent = {
                    "subject": recovery["subject"], "head": recovery["head"], "lane": recovery["lane"],
                    "summary": f"Recorded from a forge snapshot: merged{where} at {recovery['head']}, "
                               "not recorded by the runner" + (WAIT_BYPASSED if bypassed else ""),
                    "journalAfter": len(read_journal(directory)),
                    "occurredAt": recovery["occurredAt"],
                    "changeRequest": recovery["changeRequest"],
                }
                # The intent first: a crash after the state write and before
                # the journal entry is then finished by the next reconcile.
                _write_atomically(directory / RECOVERY_FILE, dumps(intent))
                try:
                    if item["stage"] != "merged" or "pending" in item:
                        merged = merged_item(item, syncedHead=recovery["head"])
                        written = {"queue": [merged if entry is item else entry for entry in state["queue"]]}
                        if item["stage"] != "merged":
                            written["lanes"] = lane_rows_after(directory, state, recovery["subject"])
                        patch(directory, writer="runner", version=state["version"], changes=written)
                except StateError as error:
                    (directory / RECOVERY_FILE).unlink(missing_ok=True)
                    if error.code != "version_stale":
                        raise
                    continue
                try:
                    _journal_recovery(directory, intent)
                except StateError as error:
                    if error.code != "recovery_identity":
                        raise
                    withdraw(recovery["subject"])
                    reports.append({"subject": item["subject"], "note": f"{error}: set aside as {_set_aside(directory)}"})
                    break
                (directory / RECOVERY_FILE).unlink(missing_ok=True)
                if bypassed:
                    reports.append(_bypass_report(item["subject"]))
                break
            else:
                raise StateError("version_stale", "the record kept changing while a merge was recovered")
        changes.extend(completed)


def _journal_recovery(directory: Path, intent: dict) -> dict:
    return append_journal(directory, kind="candidate.merged", subject=intent["subject"], lane=intent["lane"],
                          summary=intent["summary"], recovered=True, occurred_at=intent.get("occurredAt"),
                          recovery=({"head": intent["head"], "changeRequest": intent["changeRequest"]}
                                    if "changeRequest" in intent else None))


def _read_recovery(directory: Path) -> dict | None:
    try:
        intent = json.loads((directory / RECOVERY_FILE).read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise StateError("state_invalid", f"{RECOVERY_FILE} is not JSON") from None
    shape = {"subject": str, "head": str, "summary": str, "journalAfter": int}
    if (not isinstance(intent, dict) or not {*shape, "lane"} <= set(intent)
            or not set(intent) <= {*shape, "lane", "occurredAt", "changeRequest"}
            or not all(isinstance(intent[key], kind) and not isinstance(intent[key], bool) for key, kind in shape.items())
            or not (intent["lane"] is None or isinstance(intent["lane"], str))):
        raise StateError("state_invalid", f"{RECOVERY_FILE} is malformed")
    probe = {"id": 1, "at": now(), "kind": "candidate.merged", "subject": intent["subject"],
             "lane": intent["lane"], "summary": intent["summary"], "occurredAt": intent.get("occurredAt"),
             "recovery": {"head": intent["head"], "changeRequest": intent.get("changeRequest")}}
    if journal_errors([probe]):
        raise StateError("state_invalid", f"{RECOVERY_FILE} has an invalid occurrence time")
    return intent


def _finish_recovery(directory: Path, finished: list[dict], held: list[dict]) -> None:
    """Finish a recovery a crash interrupted, so its journal entry is never lost or written twice.

    Under the queue lock, tried and not waited for, like the recovery itself:
    when the entry was appended, the intent is only cleared; when the merge was
    recorded but not journaled, the entry is appended; when the state write
    never landed, the intent is dropped and the plan recovers the merge again.
    """
    if not (directory / RECOVERY_FILE).exists():
        return
    with contextlib.ExitStack() as locks:
        try:
            locks.enter_context(file_lock(directory / QUEUE_LOCK, timeout=0))
        except StateError as error:
            if error.code != "lock_timeout":
                raise
            held.append({"subject": _pending_subject(directory),
                         "note": "an interrupted recovery waits for the queue lock: reconcile again once it is free"})
            return
        # Read under the lock: another reconcile may have finished it, or written a newer one.
        try:
            intent = _read_recovery(directory)
        except StateError as error:
            # Set aside, never deleted: what it held is for the lead to check against the journal.
            aside = _set_aside(directory)
            held.append({"subject": None, "note": f"{error}: set aside as {aside}; check the queue "
                                                   "item it names against the journal"})
            return
        if intent is not None:
            _finish_intent(directory, intent, finished, held)


def _set_aside(directory: Path) -> str:
    """Move a malformed intent aside, never over one set aside before, and say where."""
    name, number = RECOVERY_QUARANTINE, 1
    while (directory / name).exists():
        number += 1
        name = f"{RECOVERY_QUARANTINE}.{number}"
    (directory / RECOVERY_FILE).replace(directory / name)
    return name


def _set_aside_intents(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.glob(f"{RECOVERY_QUARANTINE}*"))


def _pending_subject(directory: Path) -> str | None:
    try:
        intent = _read_recovery(directory)
    except StateError:
        return None
    return intent["subject"] if intent else None


def _finish_intent(directory: Path, intent: dict, finished: list[dict], reports: list[dict]) -> None:
    """Finish one interrupted recovery; the caller holds the queue lock."""
    journal = read_journal(directory)
    # Any merge entry for the subject since the intent, whoever wrote it: the
    # runner may have recorded the merge itself before this reconcile.
    entries = [entry for entry in journal if entry["id"] > intent["journalAfter"] and entry["kind"] == "candidate.merged"
               and entry["subject"] == intent["subject"]]
    journaled = bool(entries)
    item = next((item for item in read_state(directory)["queue"] if item["subject"] == intent["subject"]), None)
    if item is not None and item["stage"] == "merged" and (item.get("syncedHead") or item["head"]) == intent["head"]:
        if not journaled:
            try:
                _journal_recovery(directory, intent)
            except StateError as error:
                if error.code != "recovery_identity":
                    raise
                reports.append({"subject": intent["subject"], "note": f"{error}: set aside as {_set_aside(directory)}"})
                return
            finished.append({"subject": intent["subject"], "recovery": True,
                             "change": "an interrupted recovery's journal entry: appended"})
        if "pending" in item:
            _tool_write(directory, lambda state: next(entry for entry in state["queue"]
                                                      if entry["subject"] == intent["subject"]).pop("pending", None))
        # A merge the runner journaled went through the queue, so its acceptance was not skipped.
        if intent["summary"].endswith(WAIT_BYPASSED) and (not journaled or any(entry["summary"] == intent["summary"] for entry in entries)):
            reports.append(_bypass_report(intent["subject"]))
    (directory / RECOVERY_FILE).unlink(missing_ok=True)


def read_outbox(directory: Path) -> dict | None:
    """The outbox a host publisher last wrote, or None."""
    try:
        outbox = json.loads((directory / OUTBOX_DIRECTORY / OUTBOX_FILE).read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise StateError("state_invalid", f"{OUTBOX_DIRECTORY}/{OUTBOX_FILE} is not JSON") from None
    if (not isinstance(outbox, dict)
            or not all(isinstance(outbox.get(key), int) and not isinstance(outbox.get(key), bool)
                       and outbox[key] >= 0 for key in ("version", "journalThrough"))
            or not (outbox.get("publisher") is None or isinstance(outbox["publisher"], str))):
        raise StateError("state_invalid", f"{OUTBOX_DIRECTORY}/{OUTBOX_FILE} is malformed")
    return outbox


def retire_outbox(directory: Path, publisher: str, *, force: bool = False) -> dict:
    """Remove a host publisher's outbox, so it no longer caps ``ack``.

    Explicit, never implied: a render without a binding cannot tell a removed
    publisher from one it was simply not given, so only this removes an outbox.
    An outbox written before outboxes named their publisher is retired by any id.
    ``force`` retires one that cannot be read, which no render would rewrite.
    """
    with file_lock(directory / STATE_LOCK):
        read_state(directory)
        try:
            outbox = read_outbox(directory)
        except StateError:
            if not force:
                raise
            (directory / OUTBOX_DIRECTORY / OUTBOX_FILE).unlink()
            with contextlib.suppress(OSError):
                (directory / OUTBOX_DIRECTORY).rmdir()
            _note_change(directory, "outbox")
            return {"publisher": None, "version": None, "journalThrough": None, "unreadable": True}
        if outbox is None:
            raise StateError("outbox_missing", "there is no outbox to retire")
        owner = outbox.get("publisher")
        if owner is not None and owner != publisher:
            raise StateError("outbox_not_owned", f"the outbox is {owner}'s, not {publisher}'s")
        (directory / OUTBOX_DIRECTORY / OUTBOX_FILE).unlink()
        with contextlib.suppress(OSError):
            (directory / OUTBOX_DIRECTORY).rmdir()
        _note_change(directory, "outbox")
    return {"publisher": owner, "version": outbox["version"], "journalThrough": outbox["journalThrough"]}


def binding_state(path: Path) -> dict:
    """The binding's ``state`` member, for the two fields the state tool reads from it."""
    try:
        binding = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StateError("binding_invalid", f"cannot read the binding {Path(path).name}: {error}") from None
    member = binding.get("state") if isinstance(binding, dict) else None
    if not isinstance(member, dict) or not isinstance(member.get("publishers", []), list):
        raise StateError("binding_invalid", f"{Path(path).name} has no state member with a publishers list")
    for name in ("snapshotMaxAgeSeconds", "statusOverrideMaxAgeSeconds"):
        age = member.get(name)
        if age is not None and (not isinstance(age, int) or isinstance(age, bool) or age < 1):
            raise StateError("binding_invalid", f"state.{name} is a whole number of seconds, at least 1")
    refresh_interval(member)
    return member


def bound_directory(path: Path, member: dict) -> Path:
    """The state directory a binding names, as an absolute path.

    A relative ``state.directory`` is relative to the repository root: the
    folder holding ``.agents/`` when the binding lives there, as it does in a
    repository, and otherwise the folder holding the binding.
    """
    directory = member.get("directory")
    if not isinstance(directory, str) or not directory:
        raise StateError("binding_invalid", f"{Path(path).name} names no state directory")
    # The root is found from the binding's path as given, without following
    # links, so a linked .agents folder still belongs to this repository.
    binding = Path(os.path.abspath(path))
    root = binding.parent.parent if binding.parent.name == ".agents" else binding.parent
    named = Path(directory)
    try:
        return (named if named.is_absolute() else root / named).resolve()
    except (OSError, ValueError) as error:
        raise StateError("binding_invalid", f"{Path(path).name} names an unusable state directory: {error}") from None


def require_bound(path: Path, member: dict, directory: Path) -> None:
    """Refuse a binding that keeps its state somewhere other than ``directory``.

    A binding from another checkout would judge this record's outbox against
    publishers that are not this record's.
    """
    bound = bound_directory(path, member)
    shown = Path(directory).resolve()
    # The same directory under another spelling, such as another letter case
    # on a filesystem that ignores case, is still the same directory.
    with contextlib.suppress(OSError):
        if bound.exists() and shown.exists() and os.path.samefile(bound, shown):
            return
    if bound != shown:
        raise StateError(
            "binding_invalid",
            f"{Path(path).name} keeps its state in {bound}, not in {shown}, the directory being shown",
        )


def host_publishers(member: dict) -> set[str]:
    return {entry["id"] for entry in member.get("publishers", [])
            if isinstance(entry, dict) and entry.get("kind") == "host" and isinstance(entry.get("id"), str)}


# ---------------------------------------------------------------------------
# What a lane could take next: a ranking with reasons, never an assignment

# Lanes in these states hold no files another lane should avoid.
RESTING_LANE_STATES = ("idle", "done")
NEXT_LIMIT = 5


WORK_HINTS_TIMEOUT = 60
WORK_HINTS_OUTPUT_BYTES = 16 * 1024 * 1024


def check_hints(hints: object, name: str) -> dict:
    """``hints`` as a valid ``work-hints/1``, its paths normalised, or a refusal naming it ``name``.

    A path is relative to the repository and stays inside it; a folder keeps
    its trailing /. A priority is a whole number.
    """
    errors = validate(hints, load_schema("work-hints/1"))
    if not errors:
        for subject, hint in hints["items"].items():
            for field in ("files", "changedFiles"):
                paths = []
                for path in hint.get(field, []):
                    normal = posixpath.normpath(path)
                    if path.startswith("/") or normal == ".." or normal.startswith("../") or normal == ".":
                        errors.append(f"/items/{subject}/{field}: {path} is not a path inside the repository")
                    paths.append(normal + ("/" if path.endswith("/") else ""))
                if field in hint:
                    hint[field] = paths
            if hint.get("priority") is not None:
                hint["priority"] = int(hint["priority"])
    if errors:
        raise StateError("hints_invalid", f"{name}: " + "; ".join(errors))
    return hints


def read_hints(path: Path) -> dict:
    """A validated ``work-hints/1``, or a refusal."""
    try:
        hints = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        raise StateError("hints_invalid", f"cannot read the hints {Path(path).name}: {error}") from None
    return check_hints(hints, Path(path).name)


def work_hints_config(member: dict) -> dict | None:
    """The binding's ``state.workHints`` block, checked, or None when it has none."""
    config = member.get("workHints")
    if config is None:
        return None
    argv = config.get("argv") if isinstance(config, dict) else None
    timeout = config.get("timeoutSeconds", WORK_HINTS_TIMEOUT) if isinstance(config, dict) else None
    if (not isinstance(config, dict) or set(config) - {"argv", "timeoutSeconds"}
            or not (isinstance(argv, list) and argv and all(isinstance(part, str) and part for part in argv))
            or not isinstance(timeout, int) or isinstance(timeout, bool)
            or not 1 <= timeout <= TRACKER_SNAPSHOT_TIMEOUT_CEILING):
        raise StateError("binding_invalid", "state.workHints is {argv, timeoutSeconds?}: a non-empty argv of "
                                            f"strings and a whole timeout from 1 to {TRACKER_SNAPSHOT_TIMEOUT_CEILING}")
    return {"argv": argv, "timeoutSeconds": timeout}


def _fill(argv: list[str], parent: str | None, directory: Path) -> list[str]:
    """A state command's argv with ``{parent}`` and ``{stateDirectory}`` filled in."""
    return [part.replace("{parent}", parent or "").replace("{stateDirectory}", str(Path(directory).resolve()))
            for part in argv]


def hints_from_command(config: dict, parent: str | None, directory: Path) -> tuple[dict | None, str | None]:
    """The binding command's ``work-hints/1``, run like the tracker snapshot command; or None and why not."""
    if any("{parent}" in part for part in config["argv"]) and not parent:
        return None, "the hints command needs {parent}, and the record names no meta.trackerParent"
    argv = _fill(config["argv"], parent, directory)
    code, out, detail = _run_bounded(argv, config["timeoutSeconds"], WORK_HINTS_OUTPUT_BYTES)
    if code is None:
        return None, f"the hints command {detail}"
    if code != 0:
        return None, f"the hints command exited with {code}" + (f": {detail}" if detail != f"exited with {code}" else "")
    try:
        return check_hints(json.loads(out.decode("utf-8-sig")), "the hints command's output"), None
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, "the hints command printed no JSON"
    except StateError as error:
        return None, str(error)


def _overlap(first: str, second: str) -> bool:
    """Whether two declared paths can name the same file.

    Equal, one inside the other, or a glob matching the other. A glob and a
    folder (ending in /), or two globs, overlap when what precedes their first
    wildcard could share a path: kept on the side of a collision, never of a
    missed one.
    """
    a, b = first.rstrip("/"), second.rstrip("/")
    if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
        return True
    if fnmatch.fnmatchcase(a, b) or fnmatch.fnmatchcase(b, a):
        return True

    def literal(path: str) -> str | None:
        """The fixed part of a glob, or of a folder; None for a plain file."""
        wildcards = [index for index in (path.find("*"), path.find("?"), path.find("[")) if index >= 0]
        if wildcards:
            return path[:min(wildcards)]
        return path.rstrip("/") + "/" if path.endswith("/") else None

    first_fixed, second_fixed = literal(first), literal(second)
    globbed = first_fixed is not None and second_fixed is not None and ("*" in first + second or "?" in first + second
                                                                       or "[" in first + second)
    return globbed and (first_fixed.startswith(second_fixed) or second_fixed.startswith(first_fixed))


def _files(hint: dict, *, queued: bool) -> tuple[list[str], str]:
    """An item's files, and which they are.

    Queued work is compared on what its change touched, when the hints give
    any. Work not yet queued keeps what it declares, with anything its change
    has touched so far. An empty changed list hides nothing.
    """
    changed, declared = hint.get("changedFiles") or [], hint.get("files", [])
    if changed and queued:
        return changed, "changed files"
    if changed:
        return sorted({*declared, *changed}), "declared and changed files"
    return declared, "declared files"


def next_for_lane(state: dict, lane: str, snapshot: dict, hints: dict | None) -> list[dict]:
    """The planned and follow-up work ``lane`` could take next, best first, each with a one-line reason.

    Candidates are planned work that no lane, or ``lane``, holds and nobody
    has yet put in review, and the follow-ups, less anything queued and anything
    the tracker shows completed or canceled. A follow-up is the tracker's
    item named by its reference, or by its id when it has none; the hints are
    keyed the same way. They are ordered by:

    1. unblocked first: every blocker completed, canceled, or merged in the
       record. Blockers are the tracker's ``blockedBy`` and the hints'
       ``blockers``, the plan's order, so the order holds when the tracker
       has none; a blocker the snapshot does not list counts as open, and work
       the snapshot does not list comes after work it shows unblocked, its
       blockers unknown;
    2. no collision first: no file overlaps a file of work another open lane
       holds, planned or queued, not merged and not finished in the tracker.
       Files are the ones an item's
       change touches when the hints give them, else the ones it declares;
       each reason says which;
    3. priority, 1 first, then 4, then none;
    4. files ``lane`` has held before first;
    5. the record's own order.

    It reads only; the lead decides and assigns.
    """
    if not any(entry["id"] == lane for entry in state["lanes"]):
        raise StateError("lane_unknown", f"there is no lane {lane}")
    declared = {} if hints is None else hints["items"]
    tracker = {item["subject"]: item for item in snapshot["items"]}
    merged = {item["subject"] for item in state["queue"] if item["stage"] == "merged"}
    queued = {item["subject"] for item in state["queue"]}
    open_lanes = {entry["id"] for entry in state["lanes"]
                  if entry["id"] != lane and entry["state"] not in RESTING_LANE_STATES}
    held: dict[str, list[tuple[str, str]]] = {}
    finished = {subject for subject, item in tracker.items() if item["state"] in ("completed", "canceled")}
    for item in [*state.get("planned", []), *state["queue"]]:
        if item["lane"] in open_lanes and item["subject"] not in merged | finished:
            paths, kind = _files(declared.get(item["subject"], {}), queued=item["subject"] in queued)
            held.setdefault(item["lane"], []).extend((path, kind) for path in paths)
    own = [(item["subject"], path) for item in [*state.get("planned", []), *state["queue"]] if item["lane"] == lane
           for path in _files(declared.get(item["subject"], {}), queued=item["subject"] in queued)[0]]
    candidates = [(item["subject"], item["title"]) for item in state.get("planned", [])
                  if item["lane"] in (None, lane) and item["stage"] == "planned"]
    others = {item["subject"] for item in state.get("planned", []) if item["lane"] not in (None, lane)}
    candidates += [(item["reference"] or item["id"], item["title"]) for item in state["followups"]
                   if item["id"] not in others]
    ranked = []
    seen: set[str] = set()
    for order, (subject, title) in enumerate(candidates):
        if subject in seen or subject in queued or subject in others:
            continue
        seen.add(subject)
        if tracker.get(subject, {}).get("state") in ("completed", "canceled"):
            continue
        open_blockers = []
        blockers = [*tracker.get(subject, {}).get("blockedBy", []), *declared.get(subject, {}).get("blockers", [])]
        for blocker in dict.fromkeys(blockers):
            if blocker in merged or tracker.get(blocker, {}).get("state") in ("completed", "canceled"):
                continue
            open_blockers.append(blocker if blocker in tracker else f"{blocker} (not in the snapshot)")
        files, kind = _files(declared.get(subject, {}), queued=False)
        collisions = {}
        for other, paths in sorted(held.items()):
            hits = sorted({(path, theirs_kind) for path in files for theirs, theirs_kind in paths
                           if _overlap(path, theirs)})
            if hits:
                collisions[other] = hits
        owned = sorted({path for path in files for held_subject, mine in own
                        if held_subject != subject and _overlap(path, mine)})
        priority = declared.get(subject, {}).get("priority")
        unlisted = subject not in tracker
        parts = [f"blocked by {', '.join(open_blockers)}" if open_blockers
                 else "not in the snapshot, so its blockers are unknown" if unlisted else "unblocked"]
        if not files:
            parts.append("no files declared")
        elif collisions:
            parts.append(f"its {kind} collide with " + "; ".join(
                f"{other} on " + ", ".join(f"{path} ({theirs_kind})" for path, theirs_kind in hits)
                for other, hits in collisions.items()))
        else:
            parts.append(f"its {kind} collide with no open lane")
        if owned:
            parts.append(f"{lane} has worked on {', '.join(owned)}")
        parts.append(f"priority {priority}" if priority is not None else "no priority")
        ranked.append({
            "subject": subject, "title": title, "blocked": bool(open_blockers), "listed": not unlisted,
            "collidesWith": sorted(collisions), "owned": bool(owned), "priority": priority,
            "reason": f"{subject}: " + "; ".join(parts),
            "_key": (bool(open_blockers), unlisted, bool(collisions), 5 if priority is None else priority,
                     not owned, order),
        })
    ranked.sort(key=lambda entry: entry["_key"])
    for rank, entry in enumerate(ranked, start=1):
        del entry["_key"]
        entry["rank"] = rank
    return ranked


def suggest_next(directory: Path, lane: str, snapshot_path: Path, hints_path: Path | None, *,
                 max_age: float, limit: int = NEXT_LIMIT, member: dict | None = None) -> dict:
    """``next_for_lane`` from the record, a tracker snapshot and hints; it writes nothing.

    The hints come from the binding's ``workHints`` command when it declares
    one and the command succeeds, else from ``hints_path``; the result says
    which, and why the command's were not used.
    """
    config = work_hints_config(member) if member is not None else None
    snapshot = read_snapshot(Path(snapshot_path), max_age)
    if snapshot["source"] != "tracker":
        raise StateError("snapshot_invalid", f"{Path(snapshot_path).name} is not a tracker snapshot")
    state = read_state(directory)
    hints, source, note = None, "none", None
    if config is not None:
        hints, note = hints_from_command(config, state["meta"].get("trackerParent"), directory)
        source = "command" if hints is not None else source
    if hints is None and hints_path is not None:
        hints, source = read_hints(hints_path), "file"
    elif hints is not None and hints_path is not None:
        note = "the binding's command gave the hints, so the --hints file was not read"
    if hints is None:
        note = "; ".join(filter(None, [note, "no hints: files and priorities are unknown"]))
    ranked = next_for_lane(state, lane, snapshot, hints)
    result = {"ok": True, "lane": lane, "hints": source, "next": ranked[:max(limit, 0)]}
    if note is not None:
        result["hintsNote"] = note
    return result


# ---------------------------------------------------------------------------
# Forge and tracker snapshots from the repository's own commands, for a lead with no agent

SNAPSHOT_SOURCES = ("tracker", "forge")
TRACKER_SNAPSHOT_TIMEOUT = 60
TRACKER_SNAPSHOT_TIMEOUT_CEILING = 600
TRACKER_SNAPSHOT_OUTPUT_BYTES = 16 * 1024 * 1024
# How long output already written is read after the command exits.
TRACKER_SNAPSHOT_DRAIN_SECONDS = 2
# Set to a fresh value in each run's environment: what still carries it after
# the command exits was left behind by that run.
TRACKER_SNAPSHOT_MARKER = "ORCHESTRATE_SNAPSHOT_RUN"
# How far a process table's start times may run early: a boot time or a start
# printed in whole seconds.
START_SLACK_SECONDS = 1
# How many times cleanup freezes what it found and looks again, so a process
# still spawning cannot leave children the first look missed.
CLEANUP_PASSES = 5
# How often serve reads the declared sources when the binding does not say; 0 turns it off. At most a day.
REFRESH_SECONDS = 180
REFRESH_SECONDS_CEILING = 86400
SNAPSHOTS_DIRECTORY = "snapshots"


def snapshot_config(member: dict, source: str) -> dict | None:
    """The binding's ``state.<source>Snapshot`` block, checked, or None when it has none."""
    key = f"{source}Snapshot"
    config = member.get(key)
    if config is None:
        return None
    argv = config.get("argv") if isinstance(config, dict) else None
    timeout = config.get("timeoutSeconds", TRACKER_SNAPSHOT_TIMEOUT) if isinstance(config, dict) else None
    if (not isinstance(config, dict) or set(config) - {"argv", "timeoutSeconds"}
            or not (isinstance(argv, list) and argv and all(isinstance(part, str) and part for part in argv))
            or not isinstance(timeout, int) or isinstance(timeout, bool)
            or not 1 <= timeout <= TRACKER_SNAPSHOT_TIMEOUT_CEILING):
        raise StateError("binding_invalid", f"state.{key} is {{argv, timeoutSeconds?}}: a non-empty argv of "
                                            f"strings and a whole timeout from 1 to {TRACKER_SNAPSHOT_TIMEOUT_CEILING}")
    return {"argv": argv, "timeoutSeconds": timeout}


def _process_table() -> dict[int, tuple[int, int, int, float]] | None:
    """Every process as pid: (parent, group, effective uid, start in epoch seconds), or None when unreadable."""
    proc = Path("/proc")
    if (proc / "self" / "stat").is_file():
        try:
            boot = next(float(line.split()[1]) for line in (proc / "stat").read_text().splitlines()
                        if line.startswith("btime "))
            ticks = os.sysconf("SC_CLK_TCK")
        except (OSError, StopIteration, ValueError):
            return None
        table = {}
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat_line = (entry / "stat").read_text()
                fields = stat_line[stat_line.rindex(")") + 2:].split()
                uid = next(int(line.split()[2]) for line in (entry / "status").read_text().splitlines()
                           if line.startswith("Uid:"))
            except (OSError, ValueError, IndexError, StopIteration):
                continue  # gone while it was read
            table[int(entry.name)] = (int(fields[1]), int(fields[2]), uid, boot + int(fields[19]) / ticks)
        return table or None
    try:
        listing = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,pgid=,uid=,lstart="], capture_output=True,
                                 text=True, timeout=10, env={**os.environ, "LC_ALL": "C", "TZ": "UTC"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listing.returncode != 0:
        return None
    table = {}
    for line in listing.stdout.splitlines():
        parts = line.split(None, 4)
        try:
            start = datetime.datetime.strptime(" ".join(parts[4].split()), "%a %b %d %H:%M:%S %Y")
            table[int(parts[0])] = (int(parts[1]), int(parts[2]), int(parts[3]),
                                    start.replace(tzinfo=datetime.timezone.utc).timestamp())
        except (IndexError, ValueError):
            continue
    return table or None


def _process_environment(pid: int) -> list[bytes] | None:
    """A process's environment entries, or None when the system will not show them."""
    if Path("/proc/self/environ").is_file():
        try:
            return Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        except OSError:
            return None
    if sys.platform != "darwin":
        return None
    import ctypes

    # KERN_PROCARGS2 gives arguments and environment together; the marker is
    # a fresh random value, so matching it among the arguments is harmless.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                            ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    argmax = ctypes.c_int()
    size = ctypes.c_size_t(ctypes.sizeof(argmax))
    if libc.sysctl((ctypes.c_int * 2)(1, 8), 2, ctypes.byref(argmax), ctypes.byref(size), None, 0) != 0:
        return None  # CTL_KERN, KERN_ARGMAX
    buffer = ctypes.create_string_buffer(argmax.value)
    size = ctypes.c_size_t(argmax.value)
    if libc.sysctl((ctypes.c_int * 3)(1, 49, pid), 3, buffer, ctypes.byref(size), None, 0) != 0:
        return None  # CTL_KERN, KERN_PROCARGS2: gone, another user's, or hidden by the system
    raw = buffer.raw[4:size.value]
    return [entry for entry in raw[raw.find(b"\0"):].split(b"\0") if entry]


def _cleanup_selection(group: int | None, marker: bytes, started: float, table: dict | None,
                       environment: Callable[[int], list[bytes] | None], *, caller: int | None = None) -> dict:
    """What cleanup may stop once the command, the leader of ``group``, has exited.

    Only two kinds of process: a member of the command's own group, and one
    whose environment carries this run's ``marker``. Never pid 0 or 1, the
    caller or its parent chain, another user's process, or one that started
    before the run; each of those is refused and named. An unreadable process
    table selects nothing. The command itself is the caller's to reap, never
    signalled. The group may be signalled whole only when none of its members
    was refused. With ``group`` None, when the command was reaped elsewhere
    and its pid no longer holds the group id, only marked processes count.
    """
    selection = {"group": group, "selected": [], "refused": [], "unreadable": table is None, "whole_group": False}
    if table is None:
        return selection
    callers: set[int] = set()
    pid = os.getpid() if caller is None else caller
    while pid not in callers:
        callers.add(pid)
        if pid not in table:
            break
        pid = table[pid][0]
    member_refused = False
    for pid in sorted(table):
        _, pgid, owner, start = table[pid]
        if pid == group:
            continue
        in_group = group is not None and pgid == group
        if not in_group and marker not in (environment(pid) or ()):
            continue
        why = ("pid 1 or below" if pid <= 1 else "the caller or its parent" if pid in callers
               else "another user's process" if owner != os.geteuid()
               else "started before the run" if start < started - START_SLACK_SECONDS else None)
        if why:
            selection["refused"].append([pid, why])
            member_refused = member_refused or in_group
        else:
            selection["selected"].append(pid)
    selection["whole_group"] = group is not None and not member_refused
    return selection


def _describe_selection(selection: dict) -> str:
    """The selection on one line."""
    if selection["unreadable"]:
        return f"cleanup of group {selection['group']}: the process table is unreadable, nothing stopped"
    if selection["group"] is None:
        return (f"cleanup of marked processes only (the command was reaped elsewhere): "
                f"{len(selection['selected'])} selected {selection['selected']}; {len(selection['refused'])} refused")
    refused = ", ".join(f"{pid} ({why})" for pid, why in selection["refused"])
    return (f"cleanup of group {selection['group']}: {len(selection['selected'])} selected "
            f"{selection['selected']}; {len(selection['refused'])} refused" + (f": {refused}" if refused else ""))


def _exited(process: subprocess.Popen, timeout: float) -> bool:
    """Whether the command exited within ``timeout``, without reaping it.

    Unreaped, its pid stays reserved, so its group id cannot name another
    group while the cleanup signals it.
    """
    if os.name == "nt":
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        return True
    if hasattr(os, "waitid"):
        ends = time.monotonic() + timeout
        while True:
            try:
                if os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                    return True
            except ChildProcessError:
                return True
            if time.monotonic() >= ends:
                return False
            time.sleep(0.02)
    queue = select.kqueue()
    try:
        watch = select.kevent(process.pid, select.KQ_FILTER_PROC, select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                              select.KQ_NOTE_EXIT)
        return bool(queue.control([watch], 1, timeout))
    except ProcessLookupError:
        return True  # already exited
    finally:
        queue.close()


def _unreaped(pid: int) -> bool:
    """Whether the exited command is still unreaped, so its pid, and the group id with it, stay reserved.

    Without waitid, the pid must still be in the process table with this
    process as its parent, as an unreaped child is.
    """
    if hasattr(os, "waitid"):
        try:
            return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:
            return False
    table = _process_table()
    return table is not None and pid in table and table[pid][0] == os.getpid()


def _signal_selected(pids: list[int], group: int | None, marker: bytes, signum: int) -> list[int]:
    """Signal each pid only if, checked again just before, it is still in the group or still carries the marker.

    Nothing holds a single pid the way the unreaped command holds the group
    id, so a pid freed and reused between that check and the signal is the
    one window left. Returns the pids that were signalled.
    """
    signalled = []
    for pid in pids:
        with contextlib.suppress(OSError):
            if (group is not None and os.getpgid(pid) == group) or marker in (_process_environment(pid) or ()):
                os.kill(pid, signum)
                signalled.append(pid)
    return signalled


def _signal_frozen(pids: list[int], signum: signal.Signals) -> None:
    """Signal what this run froze, without reading it again: a read that fails must not leave it stopped.

    A stopped process cannot exec or exit by itself. ``_stop_leftovers`` drops a
    pid since reused, by ``_still_frozen``, before it signals here.
    """
    for pid in pids:
        with contextlib.suppress(OSError):
            os.kill(pid, signum)


def _still_frozen(frozen: dict[int, float], table: dict | None) -> dict[int, float]:
    """What was frozen, by the pid's start when frozen, less a pid this read shows started since: it is another process's.

    A pid the read lacks stays: a read can miss a live process, and a stopped one is
    left stopped by nothing else, while the signal does no harm to a pid no process holds.
    """
    return {pid: start for pid, start in frozen.items()
            if table is None or pid not in table or abs(table[pid][3] - start) <= START_SLACK_SECONDS}


def _stop_leftovers(process: subprocess.Popen, marker: bytes, started: float, *,
                    preview: Callable[[dict], None] | None, cleanup: list | None) -> None:
    """Stop what the exited command left: its group, while its unreaped pid holds the group id, and marked processes.

    What is selected is first frozen and the table read again, up to
    ``CLEANUP_PASSES`` times, so a process still spawning cannot leave
    children the first read missed; then everything frozen is killed, even
    when the passes are interrupted. A preview shows the first read only; a
    later pass may add processes spawned since, each under the same refusals.

    One last read, before anything is killed, drops a pid it shows started
    since: that pid was reused. Everything else frozen is killed and reported,
    even when that read fails or is interrupted, because a stopped process must
    not stay stopped, and the interrupt then carries on to the caller.
    """
    group = process.pid if _unreaped(process.pid) else None
    table: dict | None = None

    def select_now() -> dict:
        nonlocal table
        table = _process_table()
        return _cleanup_selection(group, marker, started, table, _process_environment)

    selection = select_now()
    if cleanup is not None:
        cleanup.append(selection)
    if preview is not None:
        preview(selection)
        return
    if selection["unreadable"]:
        return
    frozen: dict[int, float] = {}
    whole_group = selection["whole_group"]
    latest = selection
    try:
        for _ in range(CLEANUP_PASSES):
            fresh = [pid for pid in latest["selected"] if pid not in frozen]
            if not fresh:
                break
            # One at a time, so an interrupt within the pass still kills what it had frozen.
            for pid in fresh:
                start = table[pid][3]
                frozen.update((stopped, start) for stopped in _signal_selected([pid], group, marker, signal.SIGSTOP))
            latest = select_now()
            if latest["unreadable"]:
                break
            frozen = _still_frozen(frozen, table)
            whole_group = whole_group and latest["whole_group"]
            for refusal in latest["refused"]:
                if refusal not in selection["refused"]:
                    selection["refused"].append(refusal)
            # A frozen process a later read refuses is let go again, never left stopped.
            refused_now = {pid for pid, _ in latest["refused"]}
            _signal_frozen([pid for pid in frozen if pid in refused_now], signal.SIGCONT)
            frozen = {pid: start for pid, start in frozen.items() if pid not in refused_now}
    finally:
        # Never leave a frozen process behind: what was stopped is killed.
        last, interrupted = None, None
        try:
            last = _process_table()
        except KeyboardInterrupt as interrupt:
            interrupted = interrupt
        frozen = _still_frozen(frozen, last)
        if group is not None and whole_group:
            with contextlib.suppress(OSError):
                os.killpg(group, signal.SIGKILL)
        _signal_frozen(list(frozen), signal.SIGKILL)
        selection["selected"] = sorted(frozen)
        selection["whole_group"] = whole_group
        if interrupted is not None:
            raise interrupted


def _stop_group(process: subprocess.Popen) -> None:
    with contextlib.suppress(OSError):
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)


def _read_into(stream, buffer: bytearray, cap: int, keep_tail: bool, over: threading.Event,
               frozen: threading.Event, lock: threading.Lock) -> None:
    """Read ``stream`` to its end into ``buffer``: past ``cap`` stop and flag it, or keep only the last ``cap`` bytes.

    The thread holds the stream itself, never a bare descriptor, so the
    descriptor cannot be closed and reused under it. Once ``frozen`` is set
    nothing more is added, so the caller can copy the buffer.
    """
    with contextlib.suppress(OSError, ValueError):
        while True:
            data = os.read(stream.fileno(), 65536)
            if not data:
                return
            with lock:
                if frozen.is_set():
                    return
                buffer.extend(data)
                if len(buffer) > cap:
                    if not keep_tail:
                        over.set()
                        return
                    del buffer[:-cap]


def _drain(readers: list[threading.Thread], streams: tuple) -> None:
    """Read what the command had already written, for at most the drain window."""
    drained = time.monotonic() + TRACKER_SNAPSHOT_DRAIN_SECONDS
    for reader, stream in zip(readers, streams):
        reader.join(max(0.0, drained - time.monotonic()))
        if not reader.is_alive():
            stream.close()


def tracker_snapshot_config(member: dict) -> dict | None:
    """The binding's ``state.trackerSnapshot`` block, checked, or None when it has none."""
    return snapshot_config(member, "tracker")


def refresh_interval(member: dict) -> int:
    """Seconds between serve's reads of the declared sources; 0 when the binding turns them off."""
    interval = member.get("refreshSeconds", REFRESH_SECONDS)
    if not isinstance(interval, int) or isinstance(interval, bool) or not 0 <= interval <= REFRESH_SECONDS_CEILING:
        raise StateError("binding_invalid", "state.refreshSeconds is a whole number of seconds from 0, which turns "
                                            f"the refresh off, to {REFRESH_SECONDS_CEILING}")
    return interval


def _run_bounded(argv: list[str], timeout: int, limit: int, cwd: Path | None = None, *,
                 preview: Callable[[dict], None] | None = None,
                 cleanup: list | None = None) -> tuple[int | None, bytes, str]:
    """One run of an argv: its exit code (None when it never finished), stdout up to ``limit``, and why not.

    Reading stops when the command exits, after a short drain for what it had
    already written, not when its output ends: a child it leaves behind may
    hold stdout open long after the command finished. A reader still waiting
    on such a child is left behind as a daemon thread.

    However the run ends, interrupted included, what the command left running
    is then stopped (``_cleanup_selection``) before the command is reaped, and
    the selection is appended to ``cleanup``. An interrupt during its first read
    of the process table leaves them running, not stopped: nothing has been
    selected yet. With ``preview`` the selection is
    passed to it and nothing it names is signalled. A command still running
    because it overran or the run was interrupted is stopped first with its
    whole group, preview or not: the group is the new session the command was
    started in, which holds only the run's own processes. On Windows only the
    command itself is stopped, and only when it overran or was interrupted.
    """
    marker = f"{TRACKER_SNAPSHOT_MARKER}={uuid.uuid4().hex}"
    started = time.time()
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, TRACKER_SNAPSHOT_MARKER: marker.split("=", 1)[1]},
                                   cwd=cwd,
                                   **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                                      else {"start_new_session": True}))
    except OSError as error:
        return None, b"", f"could not start: {error.strerror or error}"
    out, err, over, frozen, lock = bytearray(), bytearray(), threading.Event(), threading.Event(), threading.Lock()
    readers = [
        threading.Thread(target=_read_into, args=(process.stdout, out, limit, False, over, frozen, lock), daemon=True),
        threading.Thread(target=_read_into, args=(process.stderr, err, 64 * 1024, True, over, frozen, lock),
                         daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    overran = None
    try:
        while not _exited(process, 0.1):
            if over.is_set():
                overran = f"printed more than {limit} bytes"
                break
            if time.monotonic() >= deadline:
                overran = f"timed out after {timeout}s"
                break
        if overran is None:
            _drain(readers, (process.stdout, process.stderr))
    finally:
        # Overran or interrupted: the command and its group go first.
        if overran is not None or not _exited(process, 0):
            _stop_group(process)
            _exited(process, 5)
        if os.name != "nt":
            _stop_leftovers(process, marker.encode(), started, preview=preview, cleanup=cleanup)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
    if overran is not None:
        return None, b"", overran
    # A reader still waiting on a left-behind child adds nothing from here on.
    with lock:
        frozen.set()
        output, errors = bytes(out), bytes(err)
    if over.is_set() or len(output) > limit:
        return None, b"", f"printed more than {limit} bytes"
    detail = errors.decode("utf-8", errors="replace").strip()[-300:]
    return process.returncode, output, detail or f"exited with {process.returncode}"


def _cleanup_note(cleanup: list[dict]) -> dict:
    """The run's cleanup, in the result, when it stopped, refused or could not look at anything."""
    selection = cleanup[0] if cleanup else None
    if selection is None or not (selection["selected"] or selection["refused"] or selection["unreadable"]):
        return {}
    return {"cleanup": _describe_selection(selection)}


# The state directory's own files: a snapshot is never written over one of them.
RECORD_NAMES = (STATE_FILE, JOURNAL_FILE, CHANGES_FILE, STATE_LOCK, QUEUE_LOCK, PUBLISHED_FILE,
                OUTBOX_DIRECTORY, RECOVERY_FILE)


def _refuse_record_path(directory: Path, output: Path) -> None:
    # By file identity, not by path text: a filesystem that ignores case can
    # name the state directory in a case its path does not spell.
    chain = [Path(output).resolve(), *Path(output).resolve().parents]
    below = None
    for index, ancestor in enumerate(chain):
        try:
            if os.path.samefile(ancestor, directory):
                below = chain[index - 1] if index else ancestor
                break
        except OSError:
            continue
    if below is None:
        return
    first = below.name.casefold()
    if first in {name.casefold() for name in RECORD_NAMES} or first.startswith(RECOVERY_QUARANTINE.casefold()):
        raise StateError("output_invalid", f"{output.name} is one of the record's own files: write the snapshot elsewhere")


def take_snapshot(directory: Path, member: dict, output: Path, *, source: str, max_age: float,
                  cwd: Path | None = None) -> dict:
    """Take the ``source`` snapshot with the binding's command, or keep the last one.

    Best-effort, never a gate: a command that fails, or prints anything but a
    fresh ``work-snapshot/1`` from that source, leaves ``output`` as it was and
    writes one journal note saying why; reconcile's freshness gate then refuses
    the last snapshot once it is too old. The command and whatever access it
    needs are the repository's own: the corpus runs no forge or tracker client.
    """
    config = snapshot_config(member, source)
    if config is None:
        return {"ok": True, "taken": False, "note": f"the binding declares no {source}Snapshot command"}
    _refuse_record_path(directory, Path(output))
    state = read_state(directory)
    parent = state["meta"].get("trackerParent")
    note, again = f"{source}-snapshot:", f"{source}-snapshot: taken again"
    # The journal is published, so it gets a fixed reason class; the command's
    # own words, which may carry anything it printed, stay in this result.
    reason, detail = None, None
    cleanup: list[dict] = []
    if any("{parent}" in part for part in config["argv"]) and not parent:
        reason = "the command needs {parent}, and the record names no meta.trackerParent"
    else:
        argv = _fill(config["argv"], parent, directory)
        code, out, detail = _run_bounded(argv, config["timeoutSeconds"], TRACKER_SNAPSHOT_OUTPUT_BYTES, cwd,
                                         cleanup=cleanup)
        if code is None:
            reason = detail if detail.startswith(("timed out", "printed more", "could not start")) else "it did not finish"
        elif code != 0:
            reason = f"the command exited with {code}"
        else:
            try:
                snapshot = check_snapshot(json.loads(out.decode("utf-8-sig")), "the command's snapshot", max_age,
                                          parent=parent)
                if snapshot["source"] != source:
                    raise StateError("snapshot_invalid", f"the command's snapshot is not from the {source}")
            except (UnicodeDecodeError, ValueError, RecursionError):
                reason = "the command printed no JSON"
            except StateError as error:
                reason = ("the command's snapshot is too old" if error.code == "snapshot_stale"
                          else f"the command's snapshot is not a valid {source} snapshot")
                detail = str(error)
    last = next((entry for entry in reversed(read_journal(directory)) if entry["summary"].startswith(note)), None)
    if reason is None:
        try:
            _write_atomically(Path(output), dumps(snapshot))
        except OSError as error:
            raise StateError("output_invalid", f"cannot write {Path(output).name}: {error.strerror or error}") from None
        if last is not None and last["summary"] != again:
            # Say the outage ended, so the next failure is noted again.
            append_journal(directory, kind="decision.recorded", summary=again)
        return {"ok": True, "taken": True, "output": str(output), "items": len(snapshot["items"]),
                "takenAt": snapshot["takenAt"], **_cleanup_note(cleanup)}
    summary = f"{note} {reason}; the last snapshot stands"
    if last is None or last["summary"] != summary:
        append_journal(directory, kind="decision.recorded", summary=summary)
    result = {"ok": True, "taken": False, "reason": reason, "fallback": Path(output).is_file(), **_cleanup_note(cleanup)}
    if detail and detail != reason:
        result["detail"] = detail
    return result


def tracker_snapshot(directory: Path, member: dict, output: Path, *, max_age: float) -> dict:
    """Take the tracker snapshot with the binding's command, or keep the last one."""
    return take_snapshot(directory, member, output, source="tracker", max_age=max_age)


def forge_snapshot(directory: Path, member: dict, output: Path, *, max_age: float) -> dict:
    """Take the forge snapshot with the binding's command, or keep the last one."""
    return take_snapshot(directory, member, output, source="forge", max_age=max_age)


def _last_read(path: Path) -> str | None:
    """When the snapshot kept at ``path`` was taken, or None when there is none to read."""
    try:
        taken = json.loads(path.read_bytes().decode("utf-8"))["takenAt"]
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
        return None
    return taken if isinstance(taken, str) else None


def refresh_sources(directory: Path, member: dict, *, cwd: Path | None = None) -> dict:
    """Read each source the binding declares and reconcile what was read, as serve does on its interval.

    Each snapshot is kept under ``snapshots/`` in the state directory, so a
    source that fails leaves its last one standing. ``meta.sources`` says, per
    source, whether it is observed, stale (with when it was last read) or not
    observed at all; it is written, as the runner, only when that changes, and
    reconcile writes nothing when nothing drifted, so an idle refresh leaves
    the record and the journal as they were.
    """
    bound = member.get("snapshotMaxAgeSeconds")
    max_age = float(SNAPSHOT_MAX_AGE_SECONDS if bound is None else bound)
    folder = Path(directory) / SNAPSHOTS_DIRECTORY
    statuses: dict[str, dict] = {}
    # Each source's snapshot is read into <source>.next.json and kept as
    # <source>.json only once reconcile applied it: lastReadAt is then the last
    # read the record took in, however often reconcile refuses newer ones.
    taken: list[tuple[str, Path, Path]] = []
    kept: list[Path] = []
    for source in SNAPSHOT_SOURCES:
        try:
            declared = snapshot_config(member, source) is not None
        except StateError as error:
            # A block the tool cannot run leaves the other source to be read, and its own last read standing.
            statuses[source] = {"status": "stale", "lastReadAt": _last_read(folder / f"{source}.json"),
                                "reason": str(error)}
            kept += _still_fresh(folder / f"{source}.json", max_age)
            continue
        if not declared:
            statuses[source] = {"status": "not_observed"}
            continue
        folder.mkdir(exist_ok=True)
        applied, fresh = folder / f"{source}.json", folder / f"{source}.next.json"
        result = take_snapshot(directory, member, fresh, source=source, max_age=max_age, cwd=cwd)
        if result["taken"]:
            taken.append((source, fresh, applied))
            statuses[source] = {"status": "observed"}
            continue
        statuses[source] = {"status": "stale", "lastReadAt": _last_read(applied), "reason": result["reason"]}
        kept += _still_fresh(applied, max_age)
    reconciled = None
    if taken:
        try:
            reconciled = reconcile(directory, [fresh for _, fresh, _ in taken] + kept, max_age=max_age)
        except StateError as error:
            # Read but not applied: the record has not caught up with it, so the source is stale.
            for source, _, applied in taken:
                statuses[source] = {"status": "stale", "lastReadAt": _last_read(applied),
                                    "reason": f"reconcile refused it: {error}"}
        else:
            for _, fresh, applied in taken:
                os.replace(fresh, applied)
    _set_sources(directory, statuses, None if reconciled is None else reconciled["reports"])
    return {"ok": True, "sources": statuses, "reconciled": reconciled}


def _still_fresh(applied: Path, max_age: float) -> list[Path]:
    """The last applied read, while it is fresh: it still speaks for its source, so its reports stand."""
    try:
        read_snapshot(applied, max_age)
    except StateError:
        return []
    return [applied]


def _set_sources(directory: Path, statuses: dict[str, dict], reports: list[dict] | None) -> None:
    """Write ``meta.sources`` and ``meta.sourceReports`` as the runner, only where they changed.

    A source's ``since`` marks when its status last changed. The reports are
    what the last applied reconcile left for the lead, replaced only by the
    next one: ``None`` keeps them as they are.
    """
    if reports is not None:
        # In a fixed order, so the same reports listed differently are not a change.
        reports = sorted(reports, key=lambda report: (report["subject"] or "", report["note"]))
    for _ in range(RECONCILE_ATTEMPTS):
        state = read_state(directory)
        current = state["meta"].get("sources") or {}
        merged = {}
        for source, status in statuses.items():
            kept = current.get(source)
            if kept is not None and {key: value for key, value in kept.items() if key != "since"} == status:
                merged[source] = kept
            else:
                merged[source] = {**status, "since": now()}
        assignments = {} if merged == current else {"/meta/sources": merged}
        if reports is not None and reports != state["meta"].get("sourceReports", []):
            assignments["/meta/sourceReports"] = reports
        if not assignments:
            return
        try:
            patch(directory, writer="runner", version=state["version"], assignments=assignments)
        except StateError as error:
            if error.code != "version_stale":
                raise
            continue
        return
    raise StateError("version_stale", "the record kept changing while the sources were recorded")


# ---------------------------------------------------------------------------
# Publication: what went out, so an unpublished view is visible


def read_published(directory: Path) -> dict | None:
    try:
        published = json.loads((directory / PUBLISHED_FILE).read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise StateError("state_invalid", f"{PUBLISHED_FILE} is not JSON") from None
    if (not isinstance(published, dict) or set(published) != {"version", "journalThrough", "at"}
            or not all(isinstance(published[key], int) and not isinstance(published[key], bool)
                       and published[key] >= 0 for key in ("version", "journalThrough"))
            or not isinstance(published["at"], str)):
        raise StateError("state_invalid", f"{PUBLISHED_FILE} is malformed")
    return published


def ack(directory: Path, *, version: int | None = None, through: int | None = None) -> dict:
    """Record that ``version`` and the journal through entry ``through`` were published.

    Either left out is the outbox's own: what a host publisher last wrote is what
    it published. Without an outbox, both are needed.

    Kept beside the state, not in it: an acknowledgement that wrote the state
    would make the record newer than what it acknowledges.
    """
    with file_lock(directory / STATE_LOCK):
        state = read_state(directory)
        journal = read_journal(directory)
        outbox = read_outbox(directory)
        if version is None or through is None:
            if outbox is None:
                raise StateError("ack_invalid", "there is no outbox to acknowledge: pass --version and --through, "
                                                "the version and journal entry that were published")
            version = outbox["version"] if version is None else version
            through = outbox["journalThrough"] if through is None else through
        try:
            previous = read_published(directory) or {"version": 0, "journalThrough": 0}
        except StateError:
            # An unreadable acknowledgement is replaced: the ack is its repair.
            previous = {"version": 0, "journalThrough": 0}
        last = journal[-1]["id"] if journal else 0
        problems = []
        if version > state["version"]:
            problems.append(f"version {version} is ahead of the record's {state['version']}")
        if version < max(1, previous["version"]):
            problems.append(f"version {version} is before the published {previous['version']}")
        if through > last:
            problems.append(f"journal entry {through} does not exist; the last is {last}")
        if through < previous["journalThrough"]:
            problems.append(f"journal entry {through} is before the published {previous['journalThrough']}")
        if outbox is not None and (version > outbox["version"] or through > outbox["journalThrough"]):
            hint = (f"; if {outbox['publisher']} is no longer bound, retire its outbox with outbox --retire "
                    f"{outbox['publisher']}") if outbox.get("publisher") else ""
            problems.append(
                f"the outbox holds version {outbox['version']} through entry {outbox['journalThrough']}: "
                f"acknowledge only what was published, with ack --version {outbox['version']} --through "
                f"{outbox['journalThrough']}, or ack with neither to take the outbox's own{hint}")
        elif problems and outbox is not None:  # whatever was refused, the outbox says what was published
            problems.append(f"the outbox holds version {outbox['version']} through entry {outbox['journalThrough']}")
        if problems:
            raise StateError("ack_invalid", "; ".join(problems))
        published = {"version": version, "journalThrough": through, "at": now()}
        _write_atomically(directory / PUBLISHED_FILE, dumps(published))
        _note_change(directory, "ack", version=state["version"], journal=through)
    return published


def publication(directory: Path, shown: dict, hosts: set[str] | None = None) -> dict:
    """Whether what was published, and the outbox, lag the record.

    The record is fine when only an acknowledgement or an outbox is unreadable:
    each is reported as an error and counted as a lag, never as a failure.
    """
    last = shown["journal"][-1]["id"] if shown["journal"] else 0
    version = shown["state"]["version"]
    errors = {}
    try:
        published = read_published(directory)
    except StateError as error:
        published, errors["error"] = None, str(error)
    try:
        outbox = read_outbox(directory)
    except StateError as error:
        outbox, errors["outboxError"] = None, str(error)
    through = published["journalThrough"] if published else 0
    unpublished = sum(1 for entry in shown["journal"] if entry["id"] > through)
    return {
        "publishedVersion": published["version"] if published else None,
        "journalThrough": through,
        "unpublishedEntries": unpublished,
        "outboxVersion": outbox["version"] if outbox else None,
        "outboxPublisher": outbox.get("publisher") if outbox else None,
        # A recovery a crash interrupted: its merge may be recorded and not yet journaled.
        "recoveryPending": (directory / RECOVERY_FILE).exists() or bool(_set_aside_intents(directory)),
        # Known only against a binding: an outbox whose host publisher it no longer names.
        "outboxOrphaned": None if hosts is None or outbox is None else outbox.get("publisher") not in hosts,
        "outboxLags": "outboxError" in errors or (
            outbox is not None and (outbox["version"] < version or outbox["journalThrough"] < last)),
        "lags": published is None or published["version"] < version or unpublished > 0,
        **errors,
    }


def _review_argument(value: str | None) -> dict | None:
    try:
        review = _json_argument(value, "--review")
    except StateError as error:
        raise StateError("entry_invalid", str(error)) from None
    if review is not None and not isinstance(review, dict):
        raise StateError("entry_invalid", "--review is a JSON object")
    return review


def _journal_summary(args: argparse.Namespace, review: dict | None) -> str:
    if args.summary:
        return args.summary
    if args.kind == REVIEW_KIND and review is None:
        raise StateError("entry_invalid", f"a {REVIEW_KIND} gives its review with --review")
    if args.kind != REVIEW_KIND:
        raise StateError("entry_invalid", "give --summary")
    try:
        verdicts = ", ".join(f"{lens} {verdict}" for lens, verdict in sorted(review["verdicts"].items()))
        findings = "/".join(str(review["findings"][severity]) for severity in SEVERITIES)
        number = review["round"]
        number = int(number) if isinstance(number, float) and number.is_integer() else number
        return f"Round {number}: {verdicts}; findings {findings}"
    except (KeyError, TypeError, AttributeError):
        # The entry's own checks say what is wrong with it.
        return f"Round {review.get('round', '?')}"


def _json_argument(value: str | None, name: str) -> object:
    if value is None:
        return None
    if value == "-":
        try:
            # A leading byte-order mark, as some Windows writers add, is accepted.
            text = sys.stdin.buffer.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise StateError("patch_invalid", f"{name} on stdin is not UTF-8") from None
    else:
        text = value
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise StateError("patch_invalid", f"{name} is not JSON: {error}") from None


def _handoff_command(args: argparse.Namespace) -> dict:
    if args.accept:
        return accept_handoff(args.dir, args.accept)
    if args.reject:
        if not args.reason:
            raise StateError("handoff_invalid", "say why with --reason")
        return reject_handoff(args.dir, args.reject, args.reason)
    missing = [name for name in ("lane", "subject", "head", "change_request") if not getattr(args, name)]
    if missing:
        raise StateError("handoff_invalid", "a hand-off needs " + ", ".join("--" + name.replace("_", "-") for name in missing))
    evidence = []
    for text in args.evidence or []:
        try:
            evidence.append(json.loads(text))
        except json.JSONDecodeError as error:
            raise StateError("handoff_invalid", f"--evidence is not JSON: {error}") from None
    return handoff(args.dir, lane=args.lane, subject=args.subject, head=args.head,
                   change_request=args.change_request, rounds=args.rounds, evidence=evidence, run=args.run)


def _select_command(args: argparse.Namespace) -> dict:
    if args.clear:
        if args.head or args.checks or args.reason:
            raise StateError("select_invalid", "--clear takes only --subject")
        return {"ok": True, "item": select_checks(args.dir, args.subject, binding={})}
    missing = [name for name in ("binding", "head", "checks", "reason") if not getattr(args, name)]
    if missing:
        raise StateError("select_invalid", "a selection needs " + ", ".join("--" + name for name in missing))
    try:
        binding = json.loads(Path(args.binding).read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StateError("binding_invalid", f"cannot read the binding {Path(args.binding).name}: {error}") from None
    if not isinstance(binding, dict):
        raise StateError("binding_invalid", f"{Path(args.binding).name} is not a JSON object")
    checks = _checks_argument(args.checks)
    repo = args.repo if args.repo is not None else Path.cwd()
    base_head = args.base_head or current_base_head(repo, binding)
    item = select_checks(args.dir, args.subject, binding=binding, head=args.head, checks=checks, reason=args.reason,
                         repo=repo, base_head=base_head)
    selected = [check["id"] for check in item["leadSelection"]["checks"]]
    warnings, notes = base_rerun_notices(read_state(args.dir), selected, base_head)
    return {"ok": True, "item": item, "baseHead": base_head, "warnings": warnings, "notes": notes}


def _checks_argument(value: str) -> object:
    """--checks: a JSON list, or catalogue ids separated by commas; - reads either from stdin."""
    text = value
    if value == "-":
        try:
            text = sys.stdin.buffer.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise StateError("select_invalid", "checks on stdin is not UTF-8") from None
    if text.lstrip().startswith(("[", "{", '"')):
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise StateError("select_invalid", f"checks is not JSON: {error}") from None
    ids = [name.strip() for name in text.split(",")]
    if not any(ids):
        raise StateError("select_invalid", "--checks names no check")
    if not all(ids):
        raise StateError("select_invalid", f"--checks has an empty id in {text.strip()!r}")
    return ids


# What a running `wait` returns on: the journal kinds that need the lead. A blocked lane and machine pressure also end it.
WAIT_KINDS = frozenset({"candidate.handed", "check.failed", "candidate.merged", "candidate.stopped", "base.broken", "blocker.recorded"})
# 0 is a wake and 1 an error, so a lead can tell what ended a wait left running in the background.
WAIT_EXPIRED_EXIT = 3
WAIT_TIMEOUT_SECONDS = 1800.0
WAIT_POLL_SECONDS = 15.0
WAIT_LOAD_MULTIPLE = 2.0
WAIT_MIN_FREE_BYTES = 20 * 2**30
WAIT_SWAP_GROWTH_BYTES = 4 * 2**30
WAIT_QUIET_SECONDS = 3600.0
# A held signal clears only this far inside its limit, so a value hovering at the limit doesn't wake the lead each time.
WAIT_CLEAR_MARGIN = 0.9
SWAP_UNITS = {"K": 2**10, "M": 2**20, "G": 2**30, "T": 2**40}


def _swap_from_meminfo(text: str) -> int | None:
    fields = dict(re.findall(r"(?m)^(Swap(?:Total|Free)):\s+(\d+) kB$", text))
    if len(fields) != 2:
        return None
    return (int(fields["SwapTotal"]) - int(fields["SwapFree"])) * 1024


def _swap_from_swapusage(text: str) -> int | None:
    match = re.search(r"\bused = ([0-9.]+)([KMGT])\b", text)
    return None if match is None else int(float(match.group(1)) * SWAP_UNITS[match.group(2)])


def _swap_used_bytes() -> int | None:
    """Swap in use, or None where this system offers no way to read it."""
    try:
        if sys.platform.startswith("linux"):
            return _swap_from_meminfo(Path("/proc/meminfo").read_text(encoding="ascii"))
        if sys.platform == "darwin":
            done = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=5, check=False)
            return _swap_from_swapusage(done.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def machine_reading(directory: Path) -> dict:
    """The machine's counters, each None when this system can't read it, which is never read as healthy."""
    try:
        load5 = os.getloadavg()[1]
    except (AttributeError, OSError):
        load5 = None
    try:
        free = shutil.disk_usage(directory).free
    except OSError:
        free = None
    return {"cores": os.cpu_count(), "load5": load5, "diskFreeBytes": free, "swapUsedBytes": _swap_used_bytes()}


def _machine_unavailable(reading: dict) -> list[str]:
    missing = []
    if reading["diskFreeBytes"] is None:
        missing.append("disk")
    if reading["load5"] is None or reading["cores"] is None:
        missing.append("load")
    if reading["swapUsedBytes"] is None:
        missing.append("swap")
    return missing


def _blocked_token(lane: dict) -> str:
    """Identifies one blocking, not the lane: a later blocking or a changed question is a different token."""
    question = json.dumps([lane.get("since"), lane.get("waitingOn"), lane.get("focus")], sort_keys=True)
    return f"{lane['id']}@{hashlib.sha256(question.encode('utf-8')).hexdigest()[:16]}"


def _quiet_token(lane: dict) -> str:
    """Identifies one quiet stretch: the row moving starts another."""
    stretch = json.dumps([lane.get("focusAt"), lane.get("since"), lane.get("focus")], sort_keys=True)
    return f"{lane['id']}@{hashlib.sha256(stretch.encode('utf-8')).hexdigest()[:16]}"


def _quiet_lanes(state: dict, limit: float, at: float) -> dict[str, dict]:
    """The active lanes whose row hasn't moved for more than ``limit`` seconds, by stretch, with how long."""
    quiet = {}
    for lane in state["lanes"]:
        if lane["state"] not in ("assigned", "working", "reviewing"):
            continue
        try:
            moved = max(_parse_time(lane[key], exact=True).timestamp() for key in ("focusAt", "since") if key in lane)
        except ValueError:
            # The pattern admits an impossible date, and a wait must still end with its events.
            continue
        if at - moved > limit:
            quiet[_quiet_token(lane)] = {"id": lane["id"], "focus": lane["focus"], "state": lane["state"],
                                         "waitingOn": lane["waitingOn"], "quietSeconds": int(at - moved)}
    return quiet


def wait_for_lead(
    directory: Path,
    *,
    after: int | None,
    timeout: float,
    poll: float,
    seen_blocked: list[str] | None = None,
    seen_pressure: list[str] | None = None,
    swap_baseline: int | None = None,
    load_multiple: float = WAIT_LOAD_MULTIPLE,
    min_free_bytes: int = WAIT_MIN_FREE_BYTES,
    swap_growth_bytes: int = WAIT_SWAP_GROWTH_BYTES,
    seen_quiet: list[str] | None = None,
    quiet_seconds: float = WAIT_QUIET_SECONDS,
    read_machine: Callable[[Path], dict] = machine_reading,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, dict]:
    """Read the record and the machine until something needs the lead, or the time is up. Writes nothing.

    A condition the lead has already seen stays quiet while it holds and is forgotten once it clears, so a restart
    doesn't wake on it again and its return does. The result's ``restart`` carries that across waits, with this
    wait's own limits. A quiet lane is information: it ends no wait, and rides on whatever does, once a stretch.
    """
    deadline = clock() + timeout
    held_blocked = set(seen_blocked or ())
    held_pressure = set(seen_pressure or ())
    baseline = swap_baseline
    said_unavailable = False
    while True:
        state = read_state(directory)
        entries = read_journal(directory)
        through = entries[-1]["id"] if entries else 0
        if after is None:
            after = through
        if after > through:
            raise StateError("wait_invalid", f"--after {after} is past the journal's last entry, {through}")
        events = [entry for entry in entries if entry["id"] > after and entry["kind"] in WAIT_KINDS]

        lanes = {_blocked_token(lane): lane for lane in state["lanes"] if lane["state"] == "blocked"}
        newly_blocked = sorted(set(lanes) - held_blocked)
        held_blocked = set(lanes)

        reading = read_machine(directory)
        unavailable = _machine_unavailable(reading)
        if unavailable and not said_unavailable:
            print(f"wait: {', '.join(name + ' unavailable' for name in unavailable)}", file=sys.stderr)
            said_unavailable = True
        pressure = []
        if "load" not in unavailable:
            limit = load_multiple * reading["cores"]
            load5 = reading["load5"]
            if load5 <= limit * WAIT_CLEAR_MARGIN:
                held_pressure.discard("load")
            elif load5 > limit and "load" not in held_pressure:
                held_pressure.add("load")
                pressure.append({"signal": "load", "value": load5, "limit": limit, "cores": reading["cores"]})
        if "disk" not in unavailable:
            free = reading["diskFreeBytes"]
            if free * WAIT_CLEAR_MARGIN >= min_free_bytes:
                held_pressure.discard("disk")
            elif free < min_free_bytes and "disk" not in held_pressure:
                held_pressure.add("disk")
                pressure.append({"signal": "disk", "value": free, "limit": min_free_bytes})
        if "swap" not in unavailable:
            used = reading["swapUsedBytes"]
            if baseline is None or used < baseline:
                baseline = used
            elif used - baseline > swap_growth_bytes:
                pressure.append({"signal": "swap", "value": used, "baseline": baseline, "limit": baseline + swap_growth_bytes})
                baseline = used

        remaining = deadline - clock()
        if events or newly_blocked or pressure or remaining <= 0:
            woke = "event" if events or newly_blocked else "pressure" if pressure else "expired"
            restart = ["--after", str(through), "--timeout", repr(timeout), "--poll", repr(poll),
                       "--load-multiple", repr(load_multiple), "--min-free-bytes", str(min_free_bytes),
                       "--swap-growth-bytes", str(swap_growth_bytes)]
            # The joined form, so a lane id starting with a dash still parses.
            restart += [f"--seen-blocked={token}" for token in sorted(held_blocked)]
            restart += [f"--seen-pressure={name}" for name in sorted(held_pressure)]
            quiet = _quiet_lanes(state, quiet_seconds, time.time())
            restart += ["--quiet-seconds", repr(quiet_seconds)]
            restart += [f"--seen-quiet={token}" for token in sorted(quiet)]
            if baseline is not None:
                restart += ["--swap-baseline", str(baseline)]
            return (WAIT_EXPIRED_EXIT if woke == "expired" else 0), {
                "ok": True,
                "woke": woke,
                "events": [{key: entry.get(key) for key in ("id", "kind", "lane", "subject", "summary")} for entry in events],
                "blockedLanes": [{key: lanes[token].get(key) for key in ("id", "focus", "waitingOn")} for token in newly_blocked],
                "pressure": pressure,
                "quietLanes": [quiet[token] for token in sorted(set(quiet) - set(seen_quiet or ()))],
                "machine": reading,
                "unavailable": unavailable,
                "through": through,
                "restart": restart,
            }
        sleep(min(poll, remaining))


def _add_wait_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--after", type=int, help="the journal id to start after: the last entry when left out")
    parser.add_argument("--timeout", type=float, default=WAIT_TIMEOUT_SECONDS, help="seconds before it expires")
    parser.add_argument("--poll", type=float, default=WAIT_POLL_SECONDS, help="seconds between reads")
    parser.add_argument("--seen-blocked", action="append", metavar="LANE@TOKEN", help="a blocking the lead already knows of, as restart prints it")
    parser.add_argument("--seen-pressure", action="append", choices=("load", "disk"), help="a held signal the lead already knows of")
    parser.add_argument("--seen-quiet", action="append", metavar="LANE@TOKEN", help="a quiet stretch the lead already knows of")
    parser.add_argument("--quiet-seconds", type=float, default=WAIT_QUIET_SECONDS, help="how long a row may stand still before the lane is reported quiet")
    parser.add_argument("--swap-baseline", type=int, metavar="BYTES", help="swap in use to measure growth from")
    parser.add_argument("--load-multiple", type=float, default=WAIT_LOAD_MULTIPLE, help="five-minute load, as a multiple of the cores")
    parser.add_argument("--min-free-bytes", type=int, default=WAIT_MIN_FREE_BYTES, help="free disk under the state directory")
    parser.add_argument("--swap-growth-bytes", type=int, default=WAIT_SWAP_GROWTH_BYTES, help="swap growth since the baseline")
    parser.add_argument("--output", type=Path, metavar="FILE",
                        help="also write the one result document to FILE, atomically, outside the state directory: "
                             "read it there, not from a host's capture of stdout, which may add its own lines")


def wait_arguments(arguments: list[str]) -> argparse.Namespace:
    """Read the arguments a wait's ``restart`` printed, as the command line reads them."""
    parser = argparse.ArgumentParser(add_help=False)
    _add_wait_arguments(parser)
    return parser.parse_args(arguments)


def _wait_output(args: argparse.Namespace) -> Path | None:
    """Where a wait writes its result besides stdout: a file outside the state directory, in a folder that exists."""
    if args.output is None:
        return None
    output = args.output.resolve()
    if output == args.dir.resolve() or args.dir.resolve() in output.parents:
        raise StateError("wait_invalid", "--output must be outside the state directory: a wait writes nothing to the record")
    if not output.parent.is_dir() or output.is_dir():
        raise StateError("wait_invalid", f"--output {args.output} must be a file in a folder that exists")
    return output


def _wait_command(args: argparse.Namespace) -> tuple[int, dict]:
    bounded = math.isfinite(args.timeout) and args.timeout >= 0 and math.isfinite(args.poll) and args.poll > 0
    thresholds = math.isfinite(args.load_multiple) and args.load_multiple >= 0 and args.min_free_bytes >= 0 and args.swap_growth_bytes >= 0
    if any("@" not in item for item in args.seen_blocked or ()):
        raise StateError("wait_invalid", "--seen-blocked takes the lane@token that restart prints")
    if any("@" not in item for item in args.seen_quiet or ()):
        raise StateError("wait_invalid", "--seen-quiet takes the lane@token that restart prints")
    if not math.isfinite(args.quiet_seconds) or args.quiet_seconds < 0:
        raise StateError("wait_invalid", "--quiet-seconds must be finite and not negative")
    if not bounded or not thresholds or (args.after is not None and args.after < 0):
        raise StateError("wait_invalid", "--timeout, --poll, --after and the thresholds must be finite and not negative, and --poll above zero")
    return wait_for_lead(
        args.dir,
        after=args.after,
        timeout=args.timeout,
        poll=args.poll,
        seen_blocked=args.seen_blocked,
        seen_pressure=args.seen_pressure,
        swap_baseline=args.swap_baseline,
        load_multiple=args.load_multiple,
        min_free_bytes=args.min_free_bytes,
        swap_growth_bytes=args.swap_growth_bytes,
        seen_quiet=args.seen_quiet,
        quiet_seconds=args.quiet_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True, help="the state directory")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("init", help="create an empty state and journal")
    start.add_argument("--headline", required=True)
    commands.add_parser("validate", help="validate the state and the journal")
    change = commands.add_parser("patch", help="apply a JSON merge patch, pinned to a version")
    change.add_argument("--writer", choices=WRITERS, required=True)
    change.add_argument("--lane", help="the lane a lane's write is for")
    change.add_argument("--version", type=int, required=True, help="the state version the patch was written against")
    change.add_argument("--element", help="apply the patch to one element, such as queue[subject=...]")
    change.add_argument("--patch", help="a JSON merge patch, or - to read it from stdin")
    change.add_argument(
        "--set", dest="assignments",
        help='exact values by JSON Pointer, such as {"/meta/checking/now": null}; - reads stdin',
    )
    entry = commands.add_parser("journal", help="append one journal entry")
    entry.add_argument("--kind", required=True)
    entry.add_argument("--summary", help="required, except on a review.round, whose summary is written for it")
    entry.add_argument("--review", help="a review.round's review as JSON, or - to read it from stdin")
    entry.add_argument("--lane")
    entry.add_argument("--subject")
    entry.add_argument("--writer", choices=("lead", "lane"), default="lead",
                       help="lane: a lane journals a review round of its own work")
    finished = commands.add_parser("close-finished", help="mark the close of delivery.closed entry N finished, once its archive has landed")
    finished.add_argument("--entry", type=int, required=True)
    _add_wait_arguments(commands.add_parser("wait", help="read the record and the machine until something needs the lead"))
    evidence = commands.add_parser("review-evidence",
                                   help="say whether a subject's review evidence is captured for its reviewed head")
    evidence.add_argument("--subject", required=True)
    capture = commands.add_parser("capture-review", help="retain native review outcomes for display without a run")
    capture.add_argument("--subject", required=True)
    capture.add_argument("--candidate", required=True)
    capture.add_argument("--outcome", type=Path, action="append", required=True, help="actual outcome file, once per round in order")
    hand = commands.add_parser("handoff", help="a lane hands its work to the queue, or the lead accepts or rejects it")
    decide = hand.add_mutually_exclusive_group()
    decide.add_argument("--accept", metavar="SUBJECT", help="accept a hand-off waiting for the lead")
    decide.add_argument("--reject", metavar="SUBJECT", help="reject a hand-off waiting for the lead")
    hand.add_argument("--reason", help="why a hand-off is rejected")
    hand.add_argument("--lane")
    hand.add_argument("--subject")
    hand.add_argument("--head")
    hand.add_argument("--change-request")
    hand.add_argument("--rounds", type=int)
    hand.add_argument("--run", help="the id of the run that built the work; kept from before when left out")
    hand.add_argument("--evidence", action="append",
                      help="the lane's evidence of a reusable check's run, as merge_queue.py evidence prints it; once a check")
    chosen = commands.add_parser("select", help="the lead records the checks a candidate runs at its reviewed head")
    chosen.add_argument("--subject", required=True)
    chosen.add_argument("--binding", type=Path, help="the repository binding, with selection.lead true")
    chosen.add_argument("--head", help="the candidate's reviewed head the selection is for")
    chosen.add_argument("--checks", help="catalogue ids, comma-separated, or a JSON list of checks shaped like the "
                                         "binding's alwaysRun entries, in which a string is a catalogue id; - for stdin")
    chosen.add_argument("--reason", help="why these checks cover the candidate's diff")
    chosen.add_argument("--clear", action="store_true", help="drop the selection, so the provider selects again")
    chosen.add_argument("--repo", type=Path, help="the checkout whose remote-tracking base and objects select reads, "
                                               "for the readers of the candidate's changes and the base head; the "
                                               "current directory when left out")
    chosen.add_argument("--base-head", help="the base head to warn about and diff from, instead of the checkout's "
                                            "remote-tracking base")
    headline = commands.add_parser("headline", help="the lead's status line, shown until it expires")
    told = headline.add_mutually_exclusive_group(required=True)
    told.add_argument("--text", help="the status line")
    told.add_argument("--clear", action="store_true", help="go back to the derived status line")
    headline.add_argument("--max-age", type=int, help="seconds until it expires; overrides the binding")
    headline.add_argument("--binding", type=Path, help="the repository binding, for state.statusOverrideMaxAgeSeconds")
    focused = commands.add_parser("focus", help="a lane says what it is doing")
    focused.add_argument("--lane", required=True)
    focused.add_argument("--text", required=True)
    focused.add_argument("--state", choices=("idle", "assigned", "working", "reviewing", "handed", "blocked", "done"))
    focused.add_argument("--waiting-on", help="who or what the lane waits on; an empty value clears it")
    carried = commands.add_parser("carry-review",
                                  help="the lead carries a captured review across a commit it judged needs no new round")
    carried.add_argument("--writer", required=True, choices=WRITERS)
    carried.add_argument("--subject", required=True)
    carried.add_argument("--from", dest="from_head", required=True, help="the head the evidence covers now")
    carried.add_argument("--to", dest="to_head", required=True, help="the queued head to carry it to")
    carried.add_argument("--reason", required=True, help="why the commit between them needs no new round")
    binding_run = commands.add_parser("bind-run", help="a lane binds the run building its planned work, before its hand-off")
    binding_run.add_argument("--lane", required=True)
    binding_run.add_argument("--subject", required=True)
    binding_run.add_argument("--run", required=True, help="the id of the run, in the adopter's delivery product")
    display = commands.add_parser("show", help="print the validated state and journal, and whether publication lags")
    display.add_argument("--binding", type=Path, help="the repository binding, to tell whether the outbox is orphaned")
    sync = commands.add_parser("reconcile", help="bring the record up to date with forge and tracker snapshots")
    sync.add_argument("--snapshot", action="append", type=Path, required=True, help="a work-snapshot/1 file")
    sync.add_argument("--max-age", type=float,
                      help="refuse a snapshot older than this many seconds; overrides the binding")
    sync.add_argument("--binding", type=Path, help="the repository binding, for its state.snapshotMaxAgeSeconds")
    suggest = commands.add_parser("next", help="rank the work a lane could take next, with reasons; assigns nothing")
    suggest.add_argument("--lane", required=True)
    suggest.add_argument("--snapshot", type=Path, required=True, help="a tracker work-snapshot/1 file, for blockers")
    suggest.add_argument("--hints", type=Path,
                         help="a work-hints/1 file: each item's files and priority; the fallback to the binding's command")
    suggest.add_argument("--limit", type=int, default=NEXT_LIMIT, help=f"how many to list, {NEXT_LIMIT} when absent")
    suggest.add_argument("--max-age", type=float, help="seconds a snapshot may be old; overrides the binding")
    suggest.add_argument("--binding", type=Path,
                         help="the repository binding, for state.workHints and state.snapshotMaxAgeSeconds")
    snap = commands.add_parser("tracker-snapshot", help="take the tracker snapshot with the binding's command")
    snap.add_argument("--binding", type=Path, required=True, help="the repository binding, with state.trackerSnapshot")
    snap.add_argument("--output", type=Path, required=True, help="where the snapshot is written; kept on a failure")
    snap.add_argument("--max-age", type=float, help="refuse a snapshot older than this; overrides the binding")
    forge = commands.add_parser("forge-snapshot", help="take the forge snapshot with the binding's command")
    forge.add_argument("--binding", type=Path, required=True, help="the repository binding, with state.forgeSnapshot")
    forge.add_argument("--output", type=Path, required=True, help="where the snapshot is written; kept on a failure")
    forge.add_argument("--max-age", type=float, help="refuse a snapshot older than this; overrides the binding")
    retire = commands.add_parser("outbox", help="manage a host publisher's outbox")
    retire.add_argument("--retire", required=True, metavar="PUBLISHER", help="remove this publisher's outbox")
    retire.add_argument("--force", action="store_true", help="also remove an outbox that cannot be read")
    published = commands.add_parser("ack", help="record which version and journal entries were published")
    published.add_argument("--version", type=int,
                           help="the version published: the outbox's version when left out, not the record's")
    published.add_argument("--through", type=int,
                           help="the last journal id published: the outbox's journalThrough when left out")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result: object = {"ok": True, "state": init(args.dir, args.headline)}
        elif args.command == "validate":
            show(args.dir)
            result = {"ok": True}
        elif args.command == "patch":
            if args.patch is None and args.assignments is None:
                raise StateError("patch_invalid", "give --patch, --set or both")
            if args.patch == "-" and args.assignments == "-":
                raise StateError("patch_invalid", "only one of --patch and --set can read stdin")
            result = {
                "ok": True,
                "state": patch(
                    args.dir,
                    writer=args.writer,
                    version=args.version,
                    changes=_json_argument(args.patch, "patch"),
                    selector=args.element,
                    assignments=_json_argument(args.assignments, "set"),
                    lane=args.lane,
                ),
            }
        elif args.command == "journal":
            review = _review_argument(args.review)
            result = {
                "ok": True,
                "entry": append_journal(
                    args.dir, kind=args.kind, summary=_journal_summary(args, review),
                    lane=args.lane, subject=args.subject, review=review,
                    writer="lane" if args.writer == "lane" else None,
                ),
            }
        elif args.command == "close-finished":
            result = {"ok": True, "state": close_finished(args.dir, entry=args.entry)}
        elif args.command == "headline":
            bound = binding_state(args.binding).get("statusOverrideMaxAgeSeconds") if args.binding is not None else None
            age = args.max_age if args.max_age is not None else bound or STATUS_OVERRIDE_MAX_AGE_SECONDS
            if age < 1:
                raise StateError("patch_invalid", "--max-age is at least 1 second")
            result = {"ok": True, "state": set_status_override(args.dir, None if args.clear else args.text, age)}
        elif args.command == "select":
            result = _select_command(args)
        elif args.command == "wait":
            # One run is one document: on stdout, and in --output when it is given, even when the wait fails.
            output = _wait_output(args)
            try:
                code, result = _wait_command(args)
            except StateError as error:
                code, result = 1, {"ok": False, "code": error.code, "error": str(error)}
            if output is not None:
                if isinstance(result.get("restart"), list):  # the next wait writes where this one did
                    result["restart"] = [*result["restart"], f"--output={args.output}"]
                _write_atomically(output, emitted(result))
            emit(result)
            return code
        elif args.command == "review-evidence":
            result = {"ok": True, **review_evidence(args.dir, args.subject)}
            if result["evidence"] != "captured":
                emit(result)
                return 1
        elif args.command == "capture-review":
            result = {"ok": True, "entry": capture_review(args.dir, subject=args.subject, candidate=args.candidate, outcomes=args.outcome)}
        elif args.command == "handoff":
            result = {"ok": True, "item": _handoff_command(args)}
        elif args.command == "focus":
            waiting = ... if args.waiting_on is None else (args.waiting_on or None)
            result = {"ok": True, "state": set_focus(args.dir, args.lane, args.text, state=args.state,
                                                     waiting_on=waiting)}
        elif args.command == "carry-review":
            if args.writer != "lead":
                raise StateError("writer_not_owned", "only the lead carries a review")
            result = {"ok": True, "entry": carry_review(args.dir, subject=args.subject, from_head=args.from_head,
                                                        to_head=args.to_head, reason=args.reason)}
        elif args.command == "bind-run":
            result = {"ok": True, "item": bind_run(args.dir, lane=args.lane, subject=args.subject, run=args.run)}
        elif args.command == "reconcile":
            bound = binding_state(args.binding).get("snapshotMaxAgeSeconds") if args.binding is not None else None
            max_age = args.max_age if args.max_age is not None else bound
            result = reconcile(args.dir, args.snapshot,
                               max_age=SNAPSHOT_MAX_AGE_SECONDS if max_age is None else float(max_age))
        elif args.command == "next":
            bound = binding_state(args.binding).get("snapshotMaxAgeSeconds") if args.binding is not None else None
            age = args.max_age if args.max_age is not None else bound
            result = suggest_next(args.dir, args.lane, args.snapshot, args.hints, limit=args.limit,
                                  max_age=SNAPSHOT_MAX_AGE_SECONDS if age is None else float(age),
                                  member=binding_state(args.binding) if args.binding is not None else None)
        elif args.command in ("tracker-snapshot", "forge-snapshot"):
            member = binding_state(args.binding)
            bound = member.get("snapshotMaxAgeSeconds")
            age = args.max_age if args.max_age is not None else bound
            result = take_snapshot(args.dir, member, args.output, source=args.command.split("-")[0],
                                   max_age=SNAPSHOT_MAX_AGE_SECONDS if age is None else float(age))
        elif args.command == "outbox":
            result = {"ok": True, "retired": retire_outbox(args.dir, args.retire, force=args.force)}
        elif args.command == "ack":
            result = {"ok": True, "published": ack(args.dir, version=args.version, through=args.through)}
        else:
            hosts = None
            if args.binding is not None:
                member = binding_state(args.binding)
                require_bound(args.binding, member, args.dir)
                hosts = host_publishers(member)
            shown = show(args.dir)
            result = {**shown, "publication": publication(args.dir, shown, hosts)}
    except StateError as error:
        emit({"ok": False, "code": error.code, "error": str(error)})
        return 1
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
