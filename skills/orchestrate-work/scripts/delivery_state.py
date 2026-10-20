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
STATE_VERSION = "delivery-state/1"
WRITERS = ("lead", "runner")
LOCK_TIMEOUT_SECONDS = 30.0

# Arrays whose elements a patch may select, and the field that names each one.
ELEMENT_KEYS = {
    "queue": "subject",
    "lanes": "id",
    "leases": "id",
    "finish": "id",
    "followups": "id",
}
SELECTOR = re.compile(r"^(?P<array>[a-z]+)\[(?P<key>[A-Za-z]+)=(?P<value>[^\]]+)\]$")
# Fields only the runner writes. Everything else, apart from the fields the
# tool itself maintains, belongs to the lead.
RUNNER_META = ("checking", "baseHead", "releasedHead")
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
        errors.extend(f"entry {index + 1}{error}" for error in validate(entry, schema))
        if isinstance(entry, dict) and entry.get("id") != index + 1:
            errors.append(f"entry {index + 1}: ids must run in sequence from 1")
    return errors


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


def patch(
    directory: Path,
    *,
    writer: str,
    version: int,
    changes: object = None,
    selector: str | None = None,
    assignments: object = None,
    between_read_and_write: Callable[[], None] | None = None,
) -> dict:
    """Apply ``changes`` as ``writer``, pinned to ``version``.

    ``between_read_and_write`` runs after the state is read and the patch is
    prepared, and before the lock is taken for the write: a deterministic seam
    for concurrency tests.
    """
    if writer not in WRITERS:
        raise StateError("writer_invalid", f"writer must be one of {', '.join(WRITERS)}")
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
) -> dict:
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
    """The validated ``{state, journal}`` pair a renderer reads.

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
    return {"state": state, "journal": journal}


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True, help="the state directory")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("init", help="create an empty state and journal")
    start.add_argument("--headline", required=True)
    commands.add_parser("validate", help="validate the state and the journal")
    change = commands.add_parser("patch", help="apply a JSON merge patch, pinned to a version")
    change.add_argument("--writer", choices=WRITERS, required=True)
    change.add_argument("--version", type=int, required=True, help="the state version the patch was written against")
    change.add_argument("--element", help="apply the patch to one element, such as queue[subject=...]")
    change.add_argument("--patch", help="a JSON merge patch, or - to read it from stdin")
    change.add_argument(
        "--set", dest="assignments",
        help='exact values by JSON Pointer, such as {"/meta/checking/now": null}; - reads stdin',
    )
    entry = commands.add_parser("journal", help="append one journal entry")
    entry.add_argument("--kind", required=True)
    entry.add_argument("--summary", required=True)
    entry.add_argument("--lane")
    entry.add_argument("--subject")
    commands.add_parser("show", help="print the validated state and journal")
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
                ),
            }
        elif args.command == "journal":
            result = {
                "ok": True,
                "entry": append_journal(
                    args.dir, kind=args.kind, summary=args.summary, lane=args.lane, subject=args.subject
                ),
            }
        else:
            result = show(args.dir)
    except StateError as error:
        emit({"ok": False, "code": error.code, "error": str(error)})
        return 1
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
