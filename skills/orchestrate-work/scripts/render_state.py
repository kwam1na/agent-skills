#!/usr/bin/env python3
"""Render a delivery's validated state into its views, then publish it.

Run with ``python3 -B``. The state and journal are validated first; when
either is invalid nothing runs. A renderer is any argv that reads the
validated ``{state, journal}`` document on stdin and writes one view on
stdout. A publisher is any argv that receives the same document and puts it
somewhere a view reads. The wrapper only reads the state: views are pure
functions of it, so one can be replaced without touching the state.

Exit codes: 0 when every renderer succeeded, 1 when a renderer failed, 2 when
the state, the journal or the configuration was refused. A failing publisher
is recorded in the result and never changes the exit code.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

from delivery_state import JOURNAL_FILE, QUEUE_LOCK, STATE_FILE, STATE_LOCK, StateError, dumps, show  # noqa: E402


RENDERERS = Path(__file__).resolve().parent / "renderers"
BUILTINS = {
    "markdown": RENDERERS / "markdown.py",
    "static-page": RENDERERS / "static_page.py",
}
DEFAULT_RENDERERS = (
    {"id": "markdown", "builtin": "markdown", "output": "delivery.md"},
    {"id": "static-page", "builtin": "static-page", "output": "delivery.html"},
)
# The files the state tool and the runner own; no view may replace them.
RESERVED = frozenset({STATE_FILE, JOURNAL_FILE, STATE_LOCK, QUEUE_LOCK})
DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 600
# How much of a failing command's stderr the result keeps.
ERROR_LIMIT = 500


class ConfigError(Exception):
    pass


def _safe_output(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    windows = PureWindowsPath(value)
    return (
        not path.is_absolute()
        and path.as_posix() == value
        and ".." not in path.parts
        and "." not in path.parts
        and not windows.drive
        and not windows.is_absolute()
        and value not in RESERVED
    )


def _argv(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(part, str) and part for part in value):
        raise ConfigError(f"{where}: argv must be a non-empty array of strings")
    return list(value)


def _timeout(entry: dict, where: str) -> int:
    value = entry.get("timeoutSeconds", DEFAULT_TIMEOUT_SECONDS)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_TIMEOUT_SECONDS:
        raise ConfigError(f"{where}: timeoutSeconds must be a whole number from 1 to {MAX_TIMEOUT_SECONDS}")
    return value


def _entries(config: dict, name: str) -> list[dict]:
    value = config.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ConfigError(f"{name} must be an array of objects")
    ids = [item.get("id") for item in value]
    for index, identifier in enumerate(ids):
        if not isinstance(identifier, str) or not identifier:
            raise ConfigError(f"{name}[{index}]: id must be a non-empty string")
    if len(ids) != len(set(ids)):
        raise ConfigError(f"{name}: ids must be unique")
    return value


def parse_config(config: object) -> tuple[list[dict], list[dict]]:
    """The renderers and publishers a ``state`` configuration declares."""
    if not isinstance(config, dict):
        raise ConfigError("the state configuration must be an object")
    allowed = {"directory", "renderers", "publishers"}
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise ConfigError(f"unknown state configuration fields: {', '.join(unknown)}")
    renderers = []
    outputs: set[str] = set()
    for index, entry in enumerate(_entries(config, "renderers")):
        where = f"renderers[{index}]"
        unknown = sorted(set(entry) - {"id", "builtin", "argv", "output", "timeoutSeconds"})
        if unknown:
            raise ConfigError(f"{where}: unknown fields: {', '.join(unknown)}")
        if ("builtin" in entry) == ("argv" in entry):
            raise ConfigError(f"{where}: give exactly one of builtin and argv")
        if "builtin" in entry:
            if entry["builtin"] not in BUILTINS:
                raise ConfigError(f"{where}: builtin must be one of {', '.join(sorted(BUILTINS))}")
            argv = [sys.executable, "-B", str(BUILTINS[entry["builtin"]])]
        else:
            argv = _argv(entry["argv"], where)
        output = entry.get("output")
        if not _safe_output(output):
            raise ConfigError(f"{where}: output must be a relative path inside the state directory")
        if output in outputs:
            raise ConfigError(f"{where}: output {output} is already written by another renderer")
        outputs.add(output)
        renderers.append({"id": entry["id"], "argv": argv, "output": output, "timeout": _timeout(entry, where)})
    publishers = []
    for index, entry in enumerate(_entries(config, "publishers")):
        where = f"publishers[{index}]"
        unknown = sorted(set(entry) - {"id", "argv", "timeoutSeconds"})
        if unknown:
            raise ConfigError(f"{where}: unknown fields: {', '.join(unknown)}")
        publishers.append({"id": entry["id"], "argv": _argv(entry.get("argv"), where), "timeout": _timeout(entry, where)})
    return renderers, publishers


def _execute(argv: list[str], document: bytes, timeout: int) -> tuple[int | None, bytes, str | None]:
    """Run one argv with the document on stdin: its exit code, stdout and error."""
    try:
        completed = subprocess.run(argv, input=document, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, b"", f"timed out after {timeout}s"
    except OSError as error:
        return None, b"", f"could not start: {error.strerror or error}"
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[-ERROR_LIMIT:]
        return completed.returncode, completed.stdout, detail or f"exited with {completed.returncode}"
    return 0, completed.stdout, None


def _write_atomically(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def render(directory: Path, config: object) -> tuple[int, dict]:
    try:
        renderers, publishers = parse_config(config)
    except ConfigError as error:
        return 2, {"ok": False, "code": "config_invalid", "error": str(error)}
    try:
        document = dumps(show(directory)).encode("utf-8")
    except StateError as error:
        return 2, {"ok": False, "code": error.code, "error": str(error)}

    rendered = []
    for renderer in renderers:
        code, view, error = _execute(renderer["argv"], document, renderer["timeout"])
        entry: dict = {"id": renderer["id"], "ok": error is None, "output": renderer["output"]}
        if error is None:
            try:
                _write_atomically(directory / renderer["output"], view)
            except OSError as failure:
                error = f"could not write the view: {failure.strerror or failure}"
                entry["ok"] = False
        if error is not None:
            entry["error"] = error
        rendered.append(entry)

    published = []
    for publisher in publishers:
        code, _, error = _execute(publisher["argv"], document, publisher["timeout"])
        entry = {"exitCode": code, "id": publisher["id"], "ok": error is None}
        if error is not None:
            entry["error"] = error
        published.append(entry)

    ok = all(entry["ok"] for entry in rendered)
    return (0 if ok else 1), {"ok": ok, "publishers": published, "renderers": rendered}


def _load_config(args: argparse.Namespace) -> object:
    if args.binding is not None:
        try:
            binding = json.loads(args.binding.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigError(f"could not read the binding: {error}") from None
        if not isinstance(binding, dict) or "state" not in binding:
            raise ConfigError("the binding declares no state member")
        return binding["state"]
    if args.state_config is not None:
        text = sys.stdin.read() if args.state_config == "-" else args.state_config
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise ConfigError(f"the state configuration is not JSON: {error}") from None
    return {"renderers": [dict(entry) for entry in DEFAULT_RENDERERS], "publishers": []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True, help="the state directory")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--binding", type=Path, help="a binding file; its state member is read")
    source.add_argument("--state-config", help="the state configuration as JSON, or - to read it from stdin")
    args = parser.parse_args(argv)
    try:
        config = _load_config(args)
    except ConfigError as error:
        sys.stdout.write(dumps({"ok": False, "code": "config_invalid", "error": str(error)}))
        return 2
    code, result = render(args.dir, config)
    sys.stdout.write(dumps(result))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
