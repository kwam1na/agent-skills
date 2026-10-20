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

All input and output is byte-exact and independent of the platform: the
document reaches renderers and publishers as UTF-8, views are written as the
bytes a renderer produced, configuration is read as UTF-8, and the result on
stdout is ASCII JSON, so any console encoding can carry it.

Every command is bounded. Its output is read in chunks and at most
``VIEW_LIMIT_BYTES`` is kept; a renderer that writes more is recorded as
truncated and its view is not written. A command is finished when its process
exits, not when its output closes; afterwards its pipes get ``DRAIN_SECONDS``
to empty, so a child it left behind cannot hold the wrapper; a command can
therefore take up to its timeout plus ``DRAIN_SECONDS``. A renderer whose
stdout is still open then fails, because its view may be incomplete; a
publisher's output is not used, so the same does not fail it.

Each command runs in its own process group. One that outlives its timeout is
stopped together with its children, and so is every running command when the
wrapper is interrupted or terminated. Stopping is best-effort: a child that
leaves the group escapes it, and a wrapper killed outright cannot stop anything.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import contextlib
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass
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
RESERVED = (STATE_FILE, JOURNAL_FILE, STATE_LOCK, QUEUE_LOCK)
DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 600
# How much of a failing command's stderr the result keeps, in characters, and
# the bytes held to produce it.
ERROR_LIMIT = 500
ERROR_BYTES = ERROR_LIMIT * 4
# The most of one command's stdout the wrapper holds: one view's size.
VIEW_LIMIT_BYTES = 16 * 1024 * 1024
# How long a command's pipes may stay open after its process exits.
DRAIN_SECONDS = 2.0
CHUNK_BYTES = 64 * 1024


class ConfigError(Exception):
    pass


def _fold(name: str) -> str:
    # Filesystems may ignore case and normalise Unicode, so compare names
    # the way the most permissive of them would.
    return unicodedata.normalize("NFC", name).casefold()


RESERVED_FOLDED = frozenset(_fold(name) for name in RESERVED)


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
        and _fold(path.parts[0]) not in RESERVED_FOLDED
    )


def _view_target(directory: Path, output: str) -> Path:
    """Where a view may be written, or a refusal when a link would carry it
    out of the state directory or onto a file the state tool owns.

    A view replaces its target, so a link at the target itself is replaced,
    never followed; only the folders above it and the target's identity matter.
    """
    root = directory.resolve()
    target = directory / output
    parent = target.parent.resolve()
    if parent != root and root not in parent.parents:
        raise OSError(f"{output} is outside the state directory")
    # A link back to the state directory must not create a reserved file
    # that does not exist yet, such as the runner's queue lock.
    if parent == root and _fold(target.name) in RESERVED_FOLDED:
        raise OSError(f"{output} is a name the state tool owns")
    if target.exists():
        for name in RESERVED:
            reserved = directory / name
            if reserved.exists() and os.path.samefile(target, reserved):
                raise OSError(f"{output} is the state tool's {name}")
    return target


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


@dataclass
class Outcome:
    """What one command did: its exit code (None when it never finished),
    the output kept, and whether anything was cut or left open."""

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    truncated: bool
    stdout_open_after_exit: bool
    error: str | None


class _Capture(threading.Thread):
    """Drain one pipe, keeping at most ``limit`` bytes: the first ones, or the
    last ones when ``tail`` is set. Everything beyond is read and dropped, so
    the command never blocks on a full pipe and the wrapper's memory stays
    bounded."""

    def __init__(self, stream, limit: int, *, tail: bool = False) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.limit = limit
        self.tail = tail
        self.kept = bytearray()
        self.truncated = False

    def run(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            while True:
                chunk = self.stream.read1(CHUNK_BYTES)
                if not chunk:
                    self.stream.close()
                    return
                if self.tail:
                    self.kept += chunk
                    if len(self.kept) > self.limit:
                        self.truncated = True
                        del self.kept[: len(self.kept) - self.limit]
                elif len(self.kept) < self.limit:
                    room = self.limit - len(self.kept)
                    self.kept += chunk[:room]
                    self.truncated = self.truncated or len(chunk) > room
                else:
                    self.truncated = True


def _feed(stream, document: bytes) -> None:
    # A command may exit, or never read its input; neither is an error here.
    with contextlib.suppress(OSError, ValueError):
        stream.write(document)
    with contextlib.suppress(OSError, ValueError):
        stream.close()


def _process_group() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _stop(process: subprocess.Popen) -> None:
    """Stop a command and every process it started."""
    with contextlib.suppress(OSError):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()


def _execute(argv: list[str], document: bytes, timeout: int, *, needs_stdout: bool = False) -> Outcome:
    """Run one argv with the document on stdin, bounded in time and memory.

    With ``needs_stdout``, a command whose stdout is still open after it exits
    is stopped with its group, since nothing more of its output will be used.
    """
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_process_group()
        )
    except OSError as error:
        return Outcome(None, b"", b"", False, False, f"could not start: {error.strerror or error}")
    stdout = _Capture(process.stdout, VIEW_LIMIT_BYTES)
    # Enough bytes that the last ERROR_LIMIT characters survive decoding.
    stderr = _Capture(process.stderr, ERROR_BYTES, tail=True)
    feeder = threading.Thread(target=_feed, args=(process.stdin, document), daemon=True)
    for thread in (stdout, stderr, feeder):
        thread.start()
    try:
        code = process.wait(timeout)
    except subprocess.TimeoutExpired:
        _stop(process)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(DRAIN_SECONDS)
        return Outcome(None, bytes(stdout.kept), bytes(stderr.kept), stdout.truncated, False,
                       f"timed out after {timeout}s")
    except BaseException:
        # Interrupted or terminated: the command is in its own group, so the
        # signal never reached it. Stop it before giving up.
        _stop(process)
        raise
    # The process has exited; its output may still be in flight, or held open
    # by a child it left behind. Wait a bounded time for the rest.
    deadline = time.monotonic() + DRAIN_SECONDS
    for thread in (stdout, stderr):
        thread.join(max(0.0, deadline - time.monotonic()))
    stdout_open = stdout.is_alive()
    if stdout_open and needs_stdout:
        _stop(process)
    kept_err = bytes(stderr.kept)
    error = None
    if code != 0:
        detail = kept_err.decode("utf-8", errors="replace").strip()[-ERROR_LIMIT:]
        error = detail or f"exited with {code}"
    return Outcome(code, bytes(stdout.kept), kept_err, stdout.truncated, stdout_open, error)


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
        outcome = _execute(renderer["argv"], document, renderer["timeout"], needs_stdout=True)
        error = outcome.error
        entry: dict = {"id": renderer["id"], "output": renderer["output"]}
        if outcome.truncated:
            # A cut view is a broken view: keep the previous one.
            entry["truncated"] = True
            error = error or f"the view exceeded {VIEW_LIMIT_BYTES} bytes and was not written"
        elif error is None and outcome.stdout_open_after_exit:
            error = (f"the renderer's output was still open {DRAIN_SECONDS:g}s after it exited, "
                     "so the view may be incomplete and was not written")
        entry["ok"] = error is None
        if error is None:
            try:
                _write_atomically(_view_target(directory, renderer["output"]), outcome.stdout)
            except OSError as failure:
                error = f"could not write the view: {failure.strerror or failure}"
                entry["ok"] = False
        if error is not None:
            entry["error"] = error
        rendered.append(entry)

    published = []
    for publisher in publishers:
        # A publisher's output is not used; a child it leaves holding the
        # output does not change how its exit is judged.
        outcome = _execute(publisher["argv"], document, publisher["timeout"])
        entry = {"exitCode": outcome.exit_code, "id": publisher["id"], "ok": outcome.error is None}
        if outcome.error is not None:
            entry["error"] = outcome.error
        published.append(entry)

    ok = all(entry["ok"] for entry in rendered)
    return (0 if ok else 1), {"ok": ok, "publishers": published, "renderers": rendered}


def emit(value: object) -> None:
    """Write ``value`` to stdout as ASCII JSON, whatever the console encoding."""
    text = json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
    sys.stdout.flush()
    sys.stdout.buffer.write(text.encode("ascii"))
    sys.stdout.buffer.flush()


def _utf8(data: bytes, what: str) -> str:
    # A leading byte-order mark, as some Windows writers add, is accepted.
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ConfigError(f"{what} is not UTF-8") from None


def _load_config(args: argparse.Namespace) -> object:
    if args.binding is not None:
        try:
            binding = json.loads(_utf8(args.binding.read_bytes(), "the binding"))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigError(f"could not read the binding: {error}") from None
        if not isinstance(binding, dict) or "state" not in binding:
            raise ConfigError("the binding declares no state member")
        return binding["state"]
    if args.state_config is not None:
        if args.state_config == "-":
            text = _utf8(sys.stdin.buffer.read(), "the state configuration on stdin")
        else:
            text = args.state_config
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise ConfigError(f"the state configuration is not JSON: {error}") from None
    return {"renderers": [dict(entry) for entry in DEFAULT_RENDERERS], "publishers": []}


def _exit_on_signal(number: int, frame: object) -> None:
    # Let a termination unwind like an interrupt, so running commands are stopped.
    raise SystemExit(128 + number)


def main(argv: list[str] | None = None) -> int:
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            with contextlib.suppress(ValueError, OSError):
                signal.signal(getattr(signal, name), _exit_on_signal)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True, help="the state directory")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--binding", type=Path, help="a binding file; its state member is read")
    source.add_argument("--state-config", help="the state configuration as JSON, or - to read it from stdin")
    args = parser.parse_args(argv)
    try:
        config = _load_config(args)
    except ConfigError as error:
        emit({"ok": False, "code": "config_invalid", "error": str(error)})
        return 2
    code, result = render(args.dir, config)
    emit(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
