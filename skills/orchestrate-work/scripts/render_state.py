#!/usr/bin/env python3
"""Render a delivery's validated state into its views, then publish it.

Run with ``python3 -B``. The state and journal are validated first; when
either is invalid nothing runs. A renderer is any argv that reads the
validated ``{state, journal}`` document on stdin and writes one view on
stdout. A publisher is any argv that receives the same document and puts it
somewhere a view reads. The wrapper only reads the state: views are pure
functions of it, so one can be replaced without touching the state. The two
built-in views, markdown and static-page, run inside the wrapper instead of as
commands: they are held to the same view limit, and a timeoutSeconds given for
one is not used.

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
leaves the group escapes it, a wrapper killed outright cannot stop anything,
and on Windows the children of a command that has already exited are no longer
reachable through it.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import contextlib
import json
import os
import re
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


# The built-in markdown view. State text is shown as text: links, markup and
# raw HTML are neutralised. Only characters that can change what a CommonMark
# or GitHub-flavoured renderer shows are escaped, so the raw file stays readable.

# Anywhere in a value: escapes, code, emphasis, strikethrough, links, raw HTML,
# entities, tables and GitHub's math. Their presence also covers block quotes
# and tables at the start of a line.
_MARKDOWN_INLINE = frozenset("\\`*_~[]<>&|$")
# At the start of a value, which may start a line or a list item's content:
# headings and list markers, only when a space follows or the value ends there,
# since only then do they open a block.
_MARKDOWN_MARKER_START = frozenset("#-+")
_THEMATIC_DASHES = re.compile(r"-(?:[ \t]*-){2,}[ \t]*")


def _markdown_escape(text: str) -> str:
    out = []
    for index, character in enumerate(text):
        after = text[index + 1 : index + 2]
        if (
            character in _MARKDOWN_INLINE
            or (index == 0 and character in _MARKDOWN_MARKER_START and _opens_block(text, _run(text, character)))
            # A value of dashes alone would be a thematic break on its own line.
            or (index == 0 and character == "-" and _THEMATIC_DASHES.fullmatch(text) is not None)
            # An image opener; its bracket is escaped too.
            or (character == "!" and after == "[")
            # GitHub's bare autolinks: a scheme's "://", "www." and e-mail.
            or (character == ":" and after == "/")
            or (character == "." and text[max(0, index - 3) : index].lower() == "www")
            or character == "@"
        ):
            out.append("\\" + character)
        else:
            out.append(character)
    escaped = "".join(out)
    # An ordered-list marker: digits and "." or ")" at the start.
    digits = len(text) - len(text.lstrip("0123456789"))
    if 0 < digits and text[digits : digits + 1] in {".", ")"} and _opens_block(text, digits + 1):
        escaped = text[:digits] + "\\" + escaped[digits:]
    # A trailing run of "#" after a space would close a heading and vanish.
    run = len(escaped) - len(escaped.rstrip("#"))
    if run and (run == len(escaped) or escaped[-run - 1] in " \t"):
        escaped = escaped[:-run] + "\\" + escaped[-run:]
    return escaped


def _run(text: str, character: str) -> int:
    """The length of a heading's "#" run; other markers are one character."""
    return len(text) - len(text.lstrip("#")) if character == "#" else 1


def _opens_block(text: str, index: int) -> bool:
    """A block marker ending before ``index`` is followed by space or nothing.

    Any Unicode space or format character counts: some renderers accept one.
    """
    return (
        index >= len(text)
        or text[index].isspace()
        or unicodedata.category(text[index]) in {"Zs", "Cf"}
    )


def _markdown_text(value: object) -> str:
    """One inert line of Markdown for a state value."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    # A line break in state text cannot start a new block; leading space
    # cannot open an indented code block.
    flat = "".join(
        " " if unicodedata.category(character) in {"Cc", "Zl", "Zp"} else character for character in raw
    ).strip()
    return _markdown_escape(flat) or "none"


def _markdown_number(value: object) -> str:
    """A record's own number, written so no renderer renumbers it as a list."""
    return f"{_markdown_text(value)}\\."


def _markdown_section(title: str, rows: list[str], empty: str) -> list[str]:
    return [f"## {title}", "", *(rows or [empty]), ""]


def render_markdown(document: dict) -> str:
    state = document["state"]
    meta = state["meta"]
    lines = [
        f"# {_markdown_text(meta['headline'])}",
        "",
        f"Updated {_markdown_text(meta['updatedAt'])} · state version {_markdown_text(state['version'])}",
        f"Base head {_markdown_text(meta['baseHead'])} · released head {_markdown_text(meta['releasedHead'])}",
        "",
    ]

    checking = meta["checking"]
    rows = []
    if checking["now"] is not None:
        now = checking["now"]
        rows.append(f"- Now: {_markdown_text(now['subject'])}, {_markdown_text(now['title'])}, since {_markdown_text(now['startedAt'])}")
        rows.extend(f"  - {_markdown_text(step['label'])}: {_markdown_text(step['state'])}" for step in now["steps"])
    if checking["next"]:
        rows.append("- Next: " + ", ".join(_markdown_text(subject) for subject in checking["next"]))
    lines += _markdown_section("Checking", rows, "Nothing is being checked.")

    rows = []
    for item in sorted(state["queue"], key=lambda entry: (entry["order"], entry["subject"])):
        # A bullet, not an ordered list: the order is the record's, not Markdown's.
        rows.append(
            f"- {_markdown_number(item['order'])} {_markdown_text(item['subject'])}: {_markdown_text(item['title'])}, {_markdown_text(item['stage'])}"
            f" · lane {_markdown_text(item['lane'])} · head {_markdown_text(item['head'])}"
        )
        rows.extend(f"  - {_markdown_text(check['id'])}: {_markdown_text(check['state'])}" for check in item["checks"])
    lines += _markdown_section("Queue", rows, "The queue is empty.")

    rows = [
        f"- {_markdown_text(lane['id'])}: {_markdown_text(lane['title'])}, {_markdown_text(lane['state'])}"
        f" · focus {_markdown_text(lane['focus'])} · waiting on {_markdown_text(lane['waitingOn'])} · since {_markdown_text(lane['since'])}"
        for lane in state["lanes"]
    ]
    lines += _markdown_section("Lanes", rows, "No lanes.")

    rows = [f"- {'met' if item['met'] else 'open'}: {_markdown_text(item['text'])}" for item in state["finish"]]
    lines += _markdown_section("Finish line", rows, "No finish line recorded.")

    rows = [f"- {_markdown_text(item['title'])} · {_markdown_text(item['reference'])}" for item in state["followups"]]
    lines += _markdown_section("Follow-ups", rows, "No follow-ups.")

    rows = [f"- {_markdown_text(lease['id'])}: {_markdown_text(lease['holder'])} since {_markdown_text(lease['since'])}" for lease in state["leases"]]
    lines += _markdown_section("Leases", rows, "No leases held.")

    rows = [
        f"- {_markdown_number(entry['id'])} {_markdown_text(entry['at'])} {_markdown_text(entry['kind'])}"
        f" · lane {_markdown_text(entry['lane'])} · subject {_markdown_text(entry['subject'])}: {_markdown_text(entry['summary'])}"
        for entry in reversed(document["journal"])
    ]
    lines += _markdown_section("Journal", rows, "The journal is empty.")
    return "\n".join(lines).rstrip("\n") + "\n"


# The built-in static page. It embeds the state as escaped JSON and draws it
# through text content only, making no external loads.

_PAGE_ESCAPES = {
    "<": "\\u003c",
    ">": "\\u003e",
    "&": "\\u0026",
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}

_PAGE_STYLE = """
:root { color-scheme: light dark; --ink: #1d1d1f; --muted: #6e6e73; --line: #d2d2d7; --page: #ffffff; --soft: #f5f5f7; }
@media (prefers-color-scheme: dark) {
  :root { --ink: #f5f5f7; --muted: #a1a1a6; --line: #3a3a3c; --page: #161617; --soft: #222224; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink); font: 15px/1.5 system-ui, sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 32px 16px 64px; overflow-wrap: anywhere; }
h1 { font-size: 28px; line-height: 1.2; margin: 0 0 8px; }
h2 { font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); margin: 32px 0 8px; }
ul, ol { list-style: none; margin: 0; padding: 0; }
li { border-top: 1px solid var(--line); padding: 10px 0; }
.meta, .detail, .empty { color: var(--muted); font-size: 13px; }
.note { background: var(--soft); border-radius: 6px; padding: 8px 12px; margin: 16px 0 0; font-size: 13px; }
.tag { display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 0 8px; margin-left: 8px; font-size: 12px; }
.steps li { border: 0; padding: 2px 0 2px 16px; }
""".strip()

# The page's program. It never parses markup: every value reaches the page
# through textContent on elements it creates itself.
_PAGE_PROGRAM = """
(function () {
  "use strict";
  var embedded = JSON.parse(document.getElementById("delivery-state").textContent);
  var app = document.getElementById("app");

  function el(tag, text, className) {
    var element = document.createElement(tag);
    if (className) { element.className = className; }
    if (text !== undefined) { element.textContent = text; }
    return element;
  }
  function show(value) {
    if (value === null || value === undefined) { return "none"; }
    if (typeof value === "string") { return value; }
    if (typeof value === "boolean") { return value ? "yes" : "no"; }
    return JSON.stringify(value);
  }
  function list(tag, items, empty, draw) {
    if (!items.length) { return el("p", empty, "empty"); }
    var element = el(tag);
    items.forEach(function (item) { element.appendChild(draw(item)); });
    return element;
  }
  function row(title, detail, tag) {
    var item = el("li");
    var head = el("div", title);
    if (tag !== undefined) { head.appendChild(el("span", show(tag), "tag")); }
    item.appendChild(head);
    if (detail) { item.appendChild(el("div", detail, "detail")); }
    return item;
  }
  function isArray(value) { return Array.isArray(value); }
  function isObject(value) { return value !== null && typeof value === "object" && !isArray(value); }

  function usable(candidate) {
    return isObject(candidate) && isObject(candidate.state) && isArray(candidate.journal) &&
      candidate.state.schemaVersion === embedded.state.schemaVersion &&
      isObject(candidate.state.meta) && isObject(candidate.state.meta.checking) &&
      isArray(candidate.state.meta.checking.next) &&
      ["lanes", "queue", "finish", "followups", "leases"].every(function (key) { return isArray(candidate.state[key]); });
  }

  function build(doc, note) {
    var state = doc.state;
    var meta = state.meta;
    var parts = [];
    parts.push(el("h1", show(meta.headline)));
    parts.push(el("p", "Updated " + show(meta.updatedAt) + " \\u00b7 state version " + show(state.version) +
      " \\u00b7 base " + show(meta.baseHead) + " \\u00b7 released " + show(meta.releasedHead), "meta"));
    if (note) { parts.push(el("p", note, "note")); }

    parts.push(el("h2", "Checking"));
    var now = meta.checking.now;
    if (now) {
      var current = el("div");
      current.appendChild(el("div", show(now.subject) + ": " + show(now.title)));
      current.appendChild(el("div", "Since " + show(now.startedAt), "detail"));
      var steps = list("ul", now.steps, "No steps yet.", function (step) {
        return el("li", show(step.label) + ": " + show(step.state));
      });
      if (now.steps.length) { steps.className = "steps"; }
      current.appendChild(steps);
      parts.push(current);
    } else {
      parts.push(el("p", "Nothing is being checked.", "empty"));
    }
    if (meta.checking.next.length) {
      parts.push(el("p", "Next: " + meta.checking.next.map(show).join(", "), "detail"));
    }

    var queue = state.queue.slice().sort(function (a, b) { return a.order - b.order; });
    parts.push(el("h2", "Queue"));
    parts.push(list("ol", queue, "The queue is empty.", function (item) {
      var checks = item.checks.map(function (check) { return show(check.id) + " " + show(check.state); }).join(", ");
      return row(show(item.order) + ". " + show(item.subject) + ": " + show(item.title),
        "Lane " + show(item.lane) + " \\u00b7 head " + show(item.head) + (checks ? " \\u00b7 " + checks : ""), item.stage);
    }));

    parts.push(el("h2", "Lanes"));
    parts.push(list("ul", state.lanes, "No lanes.", function (lane) {
      return row(show(lane.id) + ": " + show(lane.title),
        "Focus " + show(lane.focus) + " \\u00b7 waiting on " + show(lane.waitingOn) + " \\u00b7 since " + show(lane.since), lane.state);
    }));

    parts.push(el("h2", "Finish line"));
    parts.push(list("ul", state.finish, "No finish line recorded.", function (item) {
      return row(show(item.text), "", item.met ? "met" : "open");
    }));

    parts.push(el("h2", "Follow-ups"));
    parts.push(list("ul", state.followups, "No follow-ups.", function (item) {
      return row(show(item.title), show(item.reference));
    }));

    parts.push(el("h2", "Leases"));
    parts.push(list("ul", state.leases, "No leases held.", function (lease) {
      return row(show(lease.id) + ": " + show(lease.holder), "Since " + show(lease.since));
    }));

    parts.push(el("h2", "Journal"));
    parts.push(list("ol", doc.journal.slice().reverse(), "The journal is empty.", function (entry) {
      return row(show(entry.summary),
        show(entry.at) + " \\u00b7 lane " + show(entry.lane) + " \\u00b7 subject " + show(entry.subject), entry.kind);
    }));
    return parts;
  }

  function draw(doc, source, note) {
    app.replaceChildren.apply(app, build(doc, note));
    app.setAttribute("data-source", source);
  }
  /* The answer to one live document: "shown", "unusable" (a different state
     version or shape), or "failed" (it passed the checks but could not be
     drawn). Every failure leaves the embedded state on screen. */
  function drawLive(doc) {
    try {
      if (!usable(doc)) { return "unusable"; }
      draw(doc, "live");
      return "shown";
    } catch (error) {
      return "failed";
    }
  }
  var notes = {
    unusable: "Showing the saved state: the live store did not match this page's state version.",
    failed: "Showing the saved state: the live store's answer could not be shown.",
    unread: "Showing the saved state: the live store could not be read."
  };
  function fallBack(reason) { draw(embedded, "embedded", notes[reason]); }

  draw(embedded, "embedded");
  var store = globalThis.deliveryStateStore;
  if (isObject(store) && typeof store.read === "function") {
    /* A pushed update is newer than whatever read() returns, so once one is
       shown the read's answer is ignored. */
    var pushed = false;
    Promise.resolve().then(function () { return store.read(); }).then(function (doc) {
      if (pushed) { return; }
      var result = drawLive(doc);
      if (result !== "shown") { fallBack(result); }
    }, function () {
      if (!pushed) { fallBack("unread"); }
    });
    /* A store that cannot subscribe still serves the read above. */
    try {
      if (typeof store.subscribe === "function") {
        store.subscribe(function (doc) {
          if (drawLive(doc) === "shown") { pushed = true; }
        });
      }
    } catch (error) {
      /* Nothing to undo: a document already pushed stays shown. */
    }
  }
})();
""".strip()


def _page_embed(document: object) -> str:
    """The document as JSON that cannot close or break out of its script element."""
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for raw, escaped in _PAGE_ESCAPES.items():
        text = text.replace(raw, escaped)
    return text


def render_static_page(document: dict) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Delivery state</title>\n"
        f"<style>\n{_PAGE_STYLE}\n</style>\n"
        "</head>\n"
        "<body>\n"
        '<main id="app" data-source="embedded"></main>\n'
        f'<script type="application/json" id="delivery-state">{_page_embed(document)}</script>\n'
        f"<script>\n{_PAGE_PROGRAM}\n</script>\n"
        "</body>\n"
        "</html>\n"
    )


BUILTINS = {
    "markdown": render_markdown,
    "static-page": render_static_page,
}


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
        builtin = entry.get("builtin")
        if "builtin" in entry:
            if builtin not in BUILTINS:
                raise ConfigError(f"{where}: builtin must be one of {', '.join(sorted(BUILTINS))}")
            argv = None
        else:
            argv = _argv(entry["argv"], where)
        output = entry.get("output")
        if not _safe_output(output):
            raise ConfigError(f"{where}: output must be a relative path inside the state directory")
        if output in outputs:
            raise ConfigError(f"{where}: output {output} is already written by another renderer")
        outputs.add(output)
        renderers.append({"id": entry["id"], "argv": argv, "builtin": builtin, "output": output,
                          "timeout": _timeout(entry, where)})
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
    try:
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
        # The process has exited; its output may still be in flight, or held
        # open by a child it left behind. Wait a bounded time for the rest.
        deadline = time.monotonic() + DRAIN_SECONDS
        for thread in (stdout, stderr):
            thread.join(max(0.0, deadline - time.monotonic()))
        stdout_open = stdout.is_alive()
        if stdout_open and needs_stdout:
            _stop(process)
    except BaseException:
        # Interrupted or terminated at any point: the command is in its own
        # group, so the signal never reached it. Stop it before giving up.
        _stop(process)
        raise
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


def _render_builtin(name: str, document: bytes) -> Outcome:
    """Draw a built-in view in this process, held to the same view limit."""
    try:
        view = BUILTINS[name](json.loads(document.decode("utf-8"))).encode("utf-8")
    except Exception as error:  # noqa: BLE001 - a view must never stop the others
        return Outcome(None, b"", b"", False, False, f"the built-in {name} view failed: {error}")
    if len(view) > VIEW_LIMIT_BYTES:
        return Outcome(0, view[:VIEW_LIMIT_BYTES], b"", True, False, None)
    return Outcome(0, view, b"", False, False, None)


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
        if renderer["builtin"] is not None:
            outcome = _render_builtin(renderer["builtin"], document)
        else:
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
