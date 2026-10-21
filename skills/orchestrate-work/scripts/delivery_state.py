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
import contextlib
import copy
import datetime
import json
import os
import re
import signal
import subprocess
import tempfile
import time
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
STATE_VERSION = "delivery-state/1"
WRITERS = ("lead", "runner", "lane")
# What a lane writes about itself, through the tool.
LANE_FIELDS = ("focus", "state", "waitingOn", "since")
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
RUNNER_META = ("checking", "baseHead", "releasedHead", "checkHistory")
RUNNER_QUEUE = ("stage", "checks", "syncedHead")
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
    "items", "minLength", "minimum", "pattern", "anyOf", "$ref", "$defs",
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


def emit(value: object) -> None:
    """Write ``value`` to stdout as ASCII JSON, whatever the console encoding."""
    text = json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
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
    meta_before, meta_after = before.get("meta", {}), after.get("meta", {})
    if meta_before.get("updatedAt") != meta_after.get("updatedAt"):
        violations.append("/meta/updatedAt is maintained by the tool")
    if writer == "runner":
        for field in set(before) | set(after):
            if field in TOOL_OWNED or field in ("meta", "queue"):
                continue
            if before.get(field) != after.get(field):
                violations.append(f"/{field} belongs to the lead")
        for field in set(meta_before) | set(meta_after):
            if field not in RUNNER_META and field != "updatedAt" and meta_before.get(field) != meta_after.get(field):
                violations.append(f"/meta/{field} belongs to the lead")
        old, new = before.get("queue", []), after.get("queue", [])
        if [item.get("subject") for item in old] != [item.get("subject") for item in new]:
            violations.append("/queue elements are added, removed and ordered by the lead")
        else:
            for item_before, item_after in zip(old, new):
                for field in set(item_before) | set(item_after):
                    if field not in RUNNER_QUEUE and item_before.get(field) != item_after.get(field):
                        violations.append(f"/queue[subject={item_before.get('subject')}]/{field} belongs to the lead")
        return violations
    for field in RUNNER_META:
        if meta_before.get(field) != meta_after.get(field):
            violations.append(f"/meta/{field} belongs to the runner")
    old = {item.get("subject"): item for item in before.get("queue", [])}
    for item in after.get("queue", []):
        previous = old.get(item.get("subject"))
        for field in RUNNER_QUEUE:
            expected = previous.get(field) if previous is not None else QUEUE_START[field]
            if item.get(field) != expected:
                violations.append(f"/queue[subject={item.get('subject')}]/{field} belongs to the runner")
    return violations


def lane_violations(before: dict, after: dict, lane: str) -> list[str]:
    """What a lane changed beyond its own lane's focus, state and waiting-on: anything at all is refused."""
    if lane not in [entry.get("id") for entry in before.get("lanes", [])]:
        return [f"there is no lane {lane}"]
    expected = copy.deepcopy(after)
    for entry in expected.get("lanes", []):
        if entry.get("id") == lane:
            previous = next(item for item in before["lanes"] if item.get("id") == lane)
            for field in LANE_FIELDS:
                if field in previous:
                    entry[field] = previous[field]
    if expected != before:
        return [f"a lane writes only its own lane's {', '.join(LANE_FIELDS[:3])}"]
    return []


def _stamp_lane(before: dict, after: dict, lane: str) -> None:
    """The tool keeps since: it moves when the lane's state changes, and only then."""
    old = next(entry for entry in before["lanes"] if entry["id"] == lane)
    new = next(entry for entry in after["lanes"] if entry["id"] == lane)
    if "state" in new:
        new["since"] = now() if new["state"] != old.get("state") else old.get("since")


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
    return updated, outcome


def _set_lane_state(state: dict, lane: str, lane_state: str) -> None:
    for entry in state["lanes"]:
        if entry["id"] == lane and entry["state"] != lane_state:
            entry.update(state=lane_state, since=now())


def handoff(
    directory: Path,
    *,
    lane: str,
    subject: str,
    head: str,
    change_request: str,
    rounds: int | None = None,
) -> dict:
    """A lane hands its reviewed work to the queue, and the hand-off is journaled.

    The subject is planned in the lane, or queued in it and waiting or stopped,
    for a hand-off at a new head. Work in flight is never handed off again.
    """

    def change(state: dict) -> dict:
        if lane not in [entry["id"] for entry in state["lanes"]]:
            raise StateError("writer_not_owned", f"there is no lane {lane}")
        queued = next((item for item in state["queue"] if item["subject"] == subject), None)
        if queued is not None:
            if queued["lane"] != lane:
                raise StateError("writer_not_owned", f"{subject} belongs to {queued['lane']}, not {lane}")
            if queued["stage"] not in ("queued", "stopped"):
                raise StateError("handoff_invalid", f"{subject} is {queued['stage']}; hand it off once it stops")
            queued.update(stage="queued", head=head, syncedHead=None, checks=[], changeRequest=change_request)
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
                "syncedHead": None, "leadInputs": [], "reportedRounds": None, "runId": None, "checks": [],
                "changeRequest": change_request,
            }
            state["queue"].append(item)
        if rounds is not None:
            item["reportedRounds"] = rounds
        # The lead's own policy, from the record, so no caller can leave it out; only the lead clears the wait.
        if state["meta"].get("handoffsNeedAcceptance"):
            item["pending"] = True
        _set_lane_state(state, lane, "handed")
        return copy.deepcopy(item)

    _, item = _tool_write(directory, change)
    waiting = "; waiting for the lead to accept it" if item.get("pending") else ""
    rounds_text = f" after {rounds} review rounds" if rounds is not None else ""
    append_journal(directory, kind="candidate.handed", lane=lane, subject=subject,
                   summary=f"{lane} handed {subject} at {head} ({change_request}){rounds_text}{waiting}")
    return item  # type: ignore[return-value]


def _pending(state: dict, subject: str) -> dict:
    item = next((entry for entry in state["queue"] if entry["subject"] == subject), None)
    if item is None or not item.get("pending"):
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
        state.setdefault("planned", []).append(back)
        if item["lane"] is not None:
            _set_lane_state(state, item["lane"], "working")
        return back

    _, back = _tool_write(directory, change)
    append_journal(directory, kind="decision.recorded", subject=subject, lane=back["lane"],  # type: ignore[index]
                   summary=f"Rejected the hand-off of {subject}: {reason}")
    return back  # type: ignore[return-value]


def set_focus(directory: Path, lane: str, text: str, *, state: str | None = None,
              waiting_on: object = ...) -> dict:
    """A lane says what it is doing, as itself."""
    changes: dict = {"focus": text}
    if state is not None:
        changes["state"] = state
    if waiting_on is not ...:
        changes["waitingOn"] = waiting_on
    current = read_state(directory)
    return patch(directory, writer="lane", lane=lane, version=current["version"], changes=changes,
                 selector=f"lanes[id={lane}]")


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
        else:
            violations = ownership_violations(current, updated, writer)
            if violations:
                raise StateError("field_not_owned", "; ".join(sorted(set(violations))))
        updated["version"] = version + 1
        if isinstance(updated.get("meta"), dict):
            updated["meta"]["updatedAt"] = now()
        errors = state_errors(updated)
        if errors:
            raise StateError("patch_invalid", "; ".join(errors))
        _require_unicode(updated, "patch_invalid")
        _write_atomically(directory / STATE_FILE, dumps(updated))
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
        read_state(directory)
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
        errors = journal_errors([*entries, entry])
        if errors:
            raise StateError("entry_invalid", "; ".join(errors))
        _require_unicode(entry, "entry_invalid")
        line = json.dumps(entry, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        complete = _journal_text(directory)[1]
        with open(directory / JOURNAL_FILE, "ab") as stream:
            # Drop the remains of an append that never finished.
            stream.truncate(complete)
            stream.write(line.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
    return entry


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
    return {"state": state, "journal": journal, "stats": delivery_stats(state, journal),
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
    elif ended is None:
        merged = next((entry for entry in after_handing if entry["kind"] == "candidate.merged"), None)
        if merged is not None:
            ended, outcome = merged, "merged"
    else:
        merged = None

    def at(entry: dict | None) -> str | None:
        return entry["at"] if entry else None

    first_start = min((review["startedAt"] for review in rounds), key=lambda text: _parse_time(text, exact=True),
                      default=None)
    last_finish = max((review["finishedAt"] for review in rounds), key=lambda text: _parse_time(text, exact=True),
                      default=None)
    reported = item.get("reportedRounds")
    findings = None
    if rounds:
        findings = {severity: sum(review["findings"][severity] for review in rounds) for severity in SEVERITIES}
    return {
        "assignedAt": at(assigned),
        "handedAt": at(handed),
        "checkStartedAt": at(checked),
        "endedAt": at(ended),
        "outcome": outcome,
        "buildSeconds": _seconds(at(assigned), first_start),
        "reviewSeconds": _seconds(first_start, last_finish),
        "waitSeconds": _seconds(at(handed), at(checked)),
        "checkToMergeSeconds": _seconds(at(checked), at(ended)),
        "totalSeconds": _seconds(at(assigned), at(merged)),
        "rounds": len(rounds) if rounds else reported,
        "roundsSource": "journal" if rounds else ("reported" if reported is not None else None),
        "findings": findings,
        "lastVerdicts": dict(rounds[-1]["verdicts"]) if rounds else None,
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
FINISHED_STEPS = ("passed", "failed", "skipped", "timed_out")


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
    landed = next((entry for entry in reversed(journal) if entry["kind"] == "candidate.merged"), None)
    if landed is not None:
        parts.append(f"last landed {landed['subject']} at {landed['at']}")
    blocked = [
        lane["id"] + (f" (waiting on {lane['waitingOn']})" if lane.get("waitingOn") else "")
        for lane in state["lanes"] if lane["state"] == "blocked"
    ]
    if blocked:
        parts.append("blocked: " + ", ".join(blocked))
    waiting = [item["subject"] for item in sorted(state["queue"], key=lambda item: (item["order"], item["subject"]))
               if item["stage"] == "queued" and not item.get("pending")]
    if waiting:
        parts.append(f"next: {waiting[0]}" + (f", then {len(waiting) - 1} more" if len(waiting) > 1 else ""))
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
        expires = _parse_time(set_at, exact=True) + datetime.timedelta(seconds=max_age)
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


def read_snapshot(path: Path, max_age: float) -> dict:
    """A validated, fresh ``work-snapshot/1``, or a refusal."""
    try:
        snapshot = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        # ValueError covers JSON a parser refuses, such as a number too long to convert.
        raise StateError("snapshot_invalid", f"cannot read the snapshot {Path(path).name}: {error}") from None
    return check_snapshot(snapshot, Path(path).name, max_age)


def check_snapshot(snapshot: object, name: str, max_age: float) -> dict:
    """``snapshot`` as a valid, fresh ``work-snapshot/1``, or a refusal naming it ``name``."""
    errors = validate(snapshot, load_schema("work-snapshot/1"))
    if not errors:
        allowed = SNAPSHOT_STATES[snapshot["source"]]
        errors = [f"/items/{index}/state: {item['state']} is not a {snapshot['source']} state"
                  for index, item in enumerate(snapshot["items"]) if item["state"] not in allowed]
        subjects = [item["subject"] for item in snapshot["items"]]
        if len(subjects) != len(set(subjects)):
            errors.append("/items: each subject appears once")
    if not errors:
        try:
            _parse_time(snapshot["takenAt"], exact=True)
        except ValueError:
            errors.append("/takenAt: not a real time")
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


def _plan(state: dict, snapshots: list[dict]) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """The lead's changes, the merges to recover, and what to report, for one read of the state."""
    lead = {"queue": copy.deepcopy(state["queue"]), "planned": copy.deepcopy(state.get("planned", [])),
            "followups": copy.deepcopy(state["followups"])}
    changes: list[dict] = []
    reports: list[dict] = []
    recoveries: list[dict] = []

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

    # The tracker first, whatever the order given: work it adds is then there
    # for the forge to link, so one pass reaches the fixed point.
    for snapshot in sorted(snapshots, key=lambda snapshot: snapshot["source"] != "tracker"):
        for item in snapshot["items"]:
            subject, word = item["subject"], item["state"]
            title = item.get("title") or subject
            if snapshot["source"] == "tracker":
                known = queued(subject) or planned(subject)
                if not known and not followed(subject):
                    if word == "backlog":
                        lead["followups"].append({"id": subject, "title": title, "reference": subject})
                        change(subject, "in the tracker but not the record: added as a follow-up")
                    elif word == "started":
                        lead["planned"].append({"subject": subject, "title": title, "lane": None, "stage": "planned"})
                        change(subject, "started in the tracker but not in the record: added as planned work")
                    elif word == "completed":
                        report(subject, "completed in the tracker but never recorded")
                elif word == "started" and not known:
                    report(subject, "a follow-up the tracker now shows started: assign it a lane")
                elif word == "completed" and planned(subject):
                    report(subject, "completed in the tracker but still planned in the record: check the forge")
                elif word == "canceled" and planned(subject):
                    report(subject, "canceled in the tracker but still planned in the record")
                continue
            reference = item.get("changeRequest")
            entry = queued(subject) or planned(subject)
            if entry is None:
                if word in ("open", "draft", "merged"):
                    report(subject, f"{'an' if word == 'open' else 'a'} {word} change request for work the record does not hold")
                continue
            if reference and entry.get("changeRequest") != reference:
                entry["changeRequest"] = reference
                change(subject, f"linked change request {reference}")
            if word == "open" and entry is planned(subject) and entry["stage"] == "planned":
                entry["stage"] = "review"
                change(subject, "its change request is open: now in review")
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
                        "runId": None, "checks": [],
                    }
                    if entry.get("changeRequest"):
                        element["changeRequest"] = entry["changeRequest"]
                    lead["queue"].append(element)
                    change(subject, "merged on the forge while still planned: added to the queue")
                    entry = element
                if entry["stage"] != "merged":
                    # ...and the merge the runner never saw is recovered as the runner's.
                    recoveries.append({"subject": subject, "head": item.get("head") or entry["head"],
                                       "changeRequest": entry.get("changeRequest"), "lane": entry.get("lane")})
                    change(subject, "merged on the forge but not recorded by the runner: recorded as merged", True)
    if not lead["planned"] and "planned" not in state:
        del lead["planned"]
    changed = {key: value for key, value in lead.items() if value != state.get(key, [] if key == "planned" else None)}
    return changed, recoveries, changes, reports


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
    snapshots = [read_snapshot(Path(path), max_age) for path in snapshot_paths]
    seen: dict[tuple[str, str], str] = {}
    for path, snapshot in zip(snapshot_paths, snapshots):
        for item in snapshot["items"]:
            key = (snapshot["source"], item["subject"])
            if key in seen:
                raise StateError("snapshot_invalid", f"{item['subject']} is in two {snapshot['source']} snapshots, "
                                                     f"{seen[key]} and {Path(path).name}: give one view of each")
            seen[key] = Path(path).name
    finished: list[dict] = []
    held: list[dict] = []
    _finish_recovery(directory, finished, held)
    for attempt in range(1, RECONCILE_ATTEMPTS + 1):
        state = read_state(directory)
        lead, recoveries, changes, reports = _plan(state, snapshots)
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
        if pending is not None:
            _finish_intent(directory, pending, changes)
            # Finished here after all, so no longer waiting.
            reports[:] = [report for report in reports
                          if not (report["subject"] == pending["subject"] and "waits for the queue lock" in report["note"])]
        for recovery in recoveries:
            selector = f"queue[subject={recovery['subject']}]"
            for _ in range(RECONCILE_ATTEMPTS):
                state = read_state(directory)
                item = next((item for item in state["queue"] if item["subject"] == recovery["subject"]), None)
                if item is None or item["stage"] == "merged":
                    withdraw(recovery["subject"])
                    break
                where = f" as change request {recovery['changeRequest']}" if recovery["changeRequest"] else ""
                intent = {
                    "subject": recovery["subject"], "head": recovery["head"], "lane": recovery["lane"],
                    "summary": f"Recorded from a forge snapshot: merged{where} at {recovery['head']}, "
                               "not recorded by the runner",
                    "journalAfter": len(read_journal(directory)),
                }
                # The intent first: a crash after the state write and before
                # the journal entry is then finished by the next reconcile.
                _write_atomically(directory / RECOVERY_FILE, dumps(intent))
                try:
                    patch(directory, writer="runner", version=state["version"], selector=selector,
                          changes={"stage": "merged", "syncedHead": recovery["head"]})
                except StateError as error:
                    (directory / RECOVERY_FILE).unlink(missing_ok=True)
                    if error.code != "version_stale":
                        raise
                    continue
                _journal_recovery(directory, intent)
                (directory / RECOVERY_FILE).unlink(missing_ok=True)
                break
            else:
                raise StateError("version_stale", "the record kept changing while a merge was recovered")


def _journal_recovery(directory: Path, intent: dict) -> dict:
    return append_journal(directory, kind="candidate.merged", subject=intent["subject"], lane=intent["lane"],
                          summary=intent["summary"])


def _read_recovery(directory: Path) -> dict | None:
    try:
        intent = json.loads((directory / RECOVERY_FILE).read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise StateError("state_invalid", f"{RECOVERY_FILE} is not JSON") from None
    shape = {"subject": str, "head": str, "summary": str, "journalAfter": int}
    if (not isinstance(intent, dict) or set(intent) != {*shape, "lane"}
            or not all(isinstance(intent[key], kind) and not isinstance(intent[key], bool) for key, kind in shape.items())
            or not (intent["lane"] is None or isinstance(intent["lane"], str))):
        raise StateError("state_invalid", f"{RECOVERY_FILE} is malformed")
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
            _finish_intent(directory, intent, finished)


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


def _finish_intent(directory: Path, intent: dict, finished: list[dict]) -> None:
    """Finish one interrupted recovery; the caller holds the queue lock."""
    journal = read_journal(directory)
    # Any merge entry for the subject since the intent, whoever wrote it: the
    # runner may have recorded the merge itself before this reconcile.
    journaled = any(entry["id"] > intent["journalAfter"] and entry["kind"] == "candidate.merged"
                    and entry["subject"] == intent["subject"] for entry in journal)
    item = next((item for item in read_state(directory)["queue"] if item["subject"] == intent["subject"]), None)
    if not journaled and item is not None and item["stage"] == "merged" and item["syncedHead"] == intent["head"]:
        _journal_recovery(directory, intent)
        finished.append({"subject": intent["subject"], "recovery": True,
                         "change": "an interrupted recovery's journal entry: appended"})
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
            return {"publisher": None, "version": None, "journalThrough": None, "unreadable": True}
        if outbox is None:
            raise StateError("outbox_missing", "there is no outbox to retire")
        owner = outbox.get("publisher")
        if owner is not None and owner != publisher:
            raise StateError("outbox_not_owned", f"the outbox is {owner}'s, not {publisher}'s")
        (directory / OUTBOX_DIRECTORY / OUTBOX_FILE).unlink()
        with contextlib.suppress(OSError):
            (directory / OUTBOX_DIRECTORY).rmdir()
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
    age = member.get("snapshotMaxAgeSeconds")
    if age is not None and (not isinstance(age, int) or isinstance(age, bool) or age < 1):
        raise StateError("binding_invalid", "state.snapshotMaxAgeSeconds is a whole number of seconds, at least 1")
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
# A tracker snapshot from the repository's own command, for a lead with no agent

TRACKER_SNAPSHOT_TIMEOUT = 60
TRACKER_SNAPSHOT_TIMEOUT_CEILING = 600
TRACKER_SNAPSHOT_OUTPUT_BYTES = 16 * 1024 * 1024
TRACKER_SNAPSHOT_NOTE = "tracker-snapshot:"
TRACKER_SNAPSHOT_TAKEN = "tracker-snapshot: taken again"


def tracker_snapshot_config(member: dict) -> dict | None:
    """The binding's ``state.trackerSnapshot`` block, checked, or None when it has none."""
    config = member.get("trackerSnapshot")
    if config is None:
        return None
    argv = config.get("argv") if isinstance(config, dict) else None
    timeout = config.get("timeoutSeconds", TRACKER_SNAPSHOT_TIMEOUT) if isinstance(config, dict) else None
    if (not isinstance(config, dict) or set(config) - {"argv", "timeoutSeconds"}
            or not (isinstance(argv, list) and argv and all(isinstance(part, str) and part for part in argv))
            or not isinstance(timeout, int) or isinstance(timeout, bool)
            or not 1 <= timeout <= TRACKER_SNAPSHOT_TIMEOUT_CEILING):
        raise StateError("binding_invalid", "state.trackerSnapshot is {argv, timeoutSeconds?}: a non-empty argv of "
                                            f"strings and a whole timeout from 1 to {TRACKER_SNAPSHOT_TIMEOUT_CEILING}")
    return {"argv": argv, "timeoutSeconds": timeout}


def _run_bounded(argv: list[str], timeout: int, limit: int) -> tuple[int | None, bytes, str]:
    """One run of an argv: its exit code (None when it never finished), stdout up to ``limit``, and why not."""
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                                      else {"start_new_session": True}))
    except OSError as error:
        return None, b"", f"could not start: {error.strerror or error}"
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=2)
        return None, b"", f"timed out after {timeout}s"
    except BaseException:
        # Interrupted: stop the command and its children before giving up.
        with contextlib.suppress(OSError):
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        raise
    if len(out) > limit:
        return None, b"", f"printed more than {limit} bytes"
    detail = err.decode("utf-8", errors="replace").strip()[-300:]
    return process.returncode, out, detail or f"exited with {process.returncode}"


# The state directory's own files: a snapshot is never written over one of them.
RECORD_NAMES = (STATE_FILE, JOURNAL_FILE, STATE_LOCK, QUEUE_LOCK, PUBLISHED_FILE, OUTBOX_DIRECTORY, RECOVERY_FILE)


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


def tracker_snapshot(directory: Path, member: dict, output: Path, *, max_age: float) -> dict:
    """Take the tracker snapshot with the binding's command, or keep the last one.

    Best-effort, never a gate: a command that fails, or prints anything but a
    fresh ``work-snapshot/1`` from the tracker, leaves ``output`` as it was and
    writes one journal note saying why; reconcile's freshness gate then refuses
    the last snapshot once it is too old. The command and whatever access it
    needs are the repository's own.
    """
    config = tracker_snapshot_config(member)
    if config is None:
        return {"ok": True, "taken": False, "note": "the binding declares no trackerSnapshot command"}
    _refuse_record_path(directory, Path(output))
    state = read_state(directory)
    parent = state["meta"].get("trackerParent")
    # The journal is published, so it gets a fixed reason class; the command's
    # own words, which may carry anything it printed, stay in this result.
    reason, detail = None, None
    if any("{parent}" in part for part in config["argv"]) and not parent:
        reason = "the command needs {parent}, and the record names no meta.trackerParent"
    else:
        argv = [part.replace("{parent}", parent or "") for part in config["argv"]]
        code, out, detail = _run_bounded(argv, config["timeoutSeconds"], TRACKER_SNAPSHOT_OUTPUT_BYTES)
        if code is None:
            reason = detail if detail.startswith(("timed out", "printed more", "could not start")) else "it did not finish"
        elif code != 0:
            reason = f"the command exited with {code}"
        else:
            try:
                snapshot = check_snapshot(json.loads(out.decode("utf-8-sig")), "the command's snapshot", max_age)
                if snapshot["source"] != "tracker":
                    raise StateError("snapshot_invalid", "the command's snapshot is not from the tracker")
            except (UnicodeDecodeError, ValueError, RecursionError):
                reason = "the command printed no JSON"
            except StateError as error:
                reason = ("the command's snapshot is too old" if error.code == "snapshot_stale"
                          else "the command's snapshot is not a valid tracker snapshot")
                detail = str(error)
    last = next((entry for entry in reversed(read_journal(directory))
                 if entry["summary"].startswith(TRACKER_SNAPSHOT_NOTE)), None)
    if reason is None:
        try:
            _write_atomically(Path(output), dumps(snapshot))
        except OSError as error:
            raise StateError("output_invalid", f"cannot write {Path(output).name}: {error.strerror or error}") from None
        if last is not None and last["summary"] != TRACKER_SNAPSHOT_TAKEN:
            # Say the outage ended, so the next failure is noted again.
            append_journal(directory, kind="decision.recorded", summary=TRACKER_SNAPSHOT_TAKEN)
        return {"ok": True, "taken": True, "output": str(output), "items": len(snapshot["items"])}
    summary = f"{TRACKER_SNAPSHOT_NOTE} {reason}; the last snapshot stands"
    if last is None or last["summary"] != summary:
        append_journal(directory, kind="decision.recorded", summary=summary)
    result = {"ok": True, "taken": False, "reason": reason, "fallback": Path(output).is_file()}
    if detail and detail != reason:
        result["detail"] = detail
    return result


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


def ack(directory: Path, *, version: int, through: int) -> dict:
    """Record that ``version`` and the journal through entry ``through`` were published.

    Kept beside the state, not in it: an acknowledgement that wrote the state
    would make the record newer than what it acknowledges.
    """
    with file_lock(directory / STATE_LOCK):
        state = read_state(directory)
        journal = read_journal(directory)
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
        outbox = read_outbox(directory)
        if outbox is not None and (version > outbox["version"] or through > outbox["journalThrough"]):
            hint = (f"; if {outbox['publisher']} is no longer bound, retire its outbox with outbox --retire "
                    f"{outbox['publisher']}") if outbox.get("publisher") else ""
            problems.append(
                f"the outbox holds version {outbox['version']} through entry {outbox['journalThrough']}: "
                f"acknowledge only what was published{hint}")
        if problems:
            raise StateError("ack_invalid", "; ".join(problems))
        published = {"version": version, "journalThrough": through, "at": now()}
        _write_atomically(directory / PUBLISHED_FILE, dumps(published))
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
    return handoff(args.dir, lane=args.lane, subject=args.subject, head=args.head,
                   change_request=args.change_request, rounds=args.rounds)


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
    display = commands.add_parser("show", help="print the validated state and journal, and whether publication lags")
    display.add_argument("--binding", type=Path, help="the repository binding, to tell whether the outbox is orphaned")
    sync = commands.add_parser("reconcile", help="bring the record up to date with forge and tracker snapshots")
    sync.add_argument("--snapshot", action="append", type=Path, required=True, help="a work-snapshot/1 file")
    sync.add_argument("--max-age", type=float,
                      help="refuse a snapshot older than this many seconds; overrides the binding")
    sync.add_argument("--binding", type=Path, help="the repository binding, for its state.snapshotMaxAgeSeconds")
    snap = commands.add_parser("tracker-snapshot", help="take the tracker snapshot with the binding's command")
    snap.add_argument("--binding", type=Path, required=True, help="the repository binding, with state.trackerSnapshot")
    snap.add_argument("--output", type=Path, required=True, help="where the snapshot is written; kept on a failure")
    snap.add_argument("--max-age", type=float, help="refuse a snapshot older than this; overrides the binding")
    retire = commands.add_parser("outbox", help="manage a host publisher's outbox")
    retire.add_argument("--retire", required=True, metavar="PUBLISHER", help="remove this publisher's outbox")
    retire.add_argument("--force", action="store_true", help="also remove an outbox that cannot be read")
    published = commands.add_parser("ack", help="record which version and journal entries were published")
    published.add_argument("--version", type=int, required=True)
    published.add_argument("--through", type=int, required=True, help="the last journal id published")
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
        elif args.command == "headline":
            bound = binding_state(args.binding).get("statusOverrideMaxAgeSeconds") if args.binding is not None else None
            age = args.max_age if args.max_age is not None else bound or STATUS_OVERRIDE_MAX_AGE_SECONDS
            if age < 1:
                raise StateError("patch_invalid", "--max-age is at least 1 second")
            result = {"ok": True, "state": set_status_override(args.dir, None if args.clear else args.text, age)}
        elif args.command == "handoff":
            result = {"ok": True, "item": _handoff_command(args)}
        elif args.command == "focus":
            waiting = ... if args.waiting_on is None else (args.waiting_on or None)
            result = {"ok": True, "state": set_focus(args.dir, args.lane, args.text, state=args.state,
                                                     waiting_on=waiting)}
        elif args.command == "reconcile":
            bound = binding_state(args.binding).get("snapshotMaxAgeSeconds") if args.binding is not None else None
            max_age = args.max_age if args.max_age is not None else bound
            result = reconcile(args.dir, args.snapshot,
                               max_age=SNAPSHOT_MAX_AGE_SECONDS if max_age is None else float(max_age))
        elif args.command == "tracker-snapshot":
            member = binding_state(args.binding)
            bound = member.get("snapshotMaxAgeSeconds")
            age = args.max_age if args.max_age is not None else bound
            result = tracker_snapshot(args.dir, member, args.output,
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
