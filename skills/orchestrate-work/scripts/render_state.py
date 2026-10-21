#!/usr/bin/env python3
"""Render a delivery's validated state into its views, then publish it.

Run with ``python3 -B``. The state and journal are validated first; when
either is invalid nothing runs. A renderer is any argv that reads the
validated ``{state, journal, stats}`` document on stdin and writes one view on
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

An interrupt, termination or hangup ends the wrapper non-zero and stops every
running command; it never reports an interrupted render as a success:

- from the script's first statement until its result is printed, it exits
  with 128 plus the signal number and an ``interrupted`` result;
- after the result is printed, the result stands but the exit code is still
  128 plus the signal number, or the signal itself ends the wrapper.

A signal the parent set to be ignored, as for a command started in the
background, stays ignored. A signal during the interpreter's own startup,
before this script's first statement runs, is outside the wrapper's reach.

Only the first signal unwinds the work; a second one, such as a repeated
interrupt, is recorded and never interrupts the stopping of commands.
"""

from __future__ import annotations

import signal
import sys

sys.dont_write_bytecode = True

# Run as the wrapper process, record a terminating signal from this point on,
# before the imports below, so a signal arriving while they load is never
# swallowed by Python's default handling. A signal the parent set to be
# ignored, such as an interrupt for a command started in the background, stays
# ignored. Nothing here changes for a caller that imports this module.
_received: list[int] = []
_ignored: set[int] = set()


def _record(number: int, frame: object) -> None:
    if not _received:
        _received.append(number)


if __name__ == "__main__":
    for _name in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(signal, _name):
            _number = getattr(signal, _name)
            if signal.getsignal(_number) is signal.SIG_IGN:
                _ignored.add(_number)
            else:
                signal.signal(_number, _record)
    import os as _os

    _step = _os.environ.get("ORCHESTRATE_RENDER_INTERRUPT_AT", "")
    if _step.startswith("loading:"):
        for _signal_name in _step.partition(":")[2].split(","):
            _os.kill(_os.getpid(), getattr(signal, _signal_name))

import argparse
import contextlib
import datetime
import http.server
import ipaddress
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from delivery_state import (  # noqa: E402
    CHANGES_FILE, JOURNAL_FILE, OUTBOX_DIRECTORY, OUTBOX_FILE, PUBLISHED_FILE, QUEUE_LOCK, RECOVERY_FILE,
    RECOVERY_QUARANTINE, STATE_FILE, STATE_LOCK, StateError,
    dumps, read_published, show,
)


DEFAULT_RENDERERS = (
    {"id": "markdown", "builtin": "markdown", "output": "delivery.md"},
    {"id": "static-page", "builtin": "static-page", "output": "delivery.html"},
)
# The files the state tool and the runner own; no view may replace them.
RESERVED = (STATE_FILE, JOURNAL_FILE, CHANGES_FILE, STATE_LOCK, QUEUE_LOCK, PUBLISHED_FILE, OUTBOX_DIRECTORY,
            RECOVERY_FILE, RECOVERY_QUARANTINE)
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


def _duration(seconds: object) -> str:
    """A stat's duration for people; the page's program writes it the same way."""
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds // 60 % 60:02d}m"
    return f"{seconds // 86400}d {seconds // 3600 % 24:02d}h"


def _elapsed(start: object, end: object) -> int | None:
    """Whole seconds between two record timestamps, or None when either is missing."""
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    # Whole seconds of each timestamp, as the page counts them.
    moments = [datetime.datetime.strptime(text.rstrip("Z").partition(".")[0], "%Y-%m-%dT%H:%M:%S") for text in (start, end)]
    return max(0, int((moments[1] - moments[0]).total_seconds()))


def _step_text(step: dict, as_of: str) -> str:
    """A check step with its timing, as of the record's last write; the page counts on from its own clock."""
    text = f"{step['label']}: {step['state']}"
    if "finishedAt" in step and "startedAt" in step:
        return text + f" · took {_duration(_elapsed(step['startedAt'], step['finishedAt']))}"
    if step["state"] == "running" and "startedAt" in step:
        elapsed = _elapsed(step["startedAt"], as_of)
        expected = step.get("expectedSeconds")
        if not isinstance(expected, int):
            return text + f" · {_duration(elapsed)}, no expected time yet"
        over = ", over the expected time" if elapsed is not None and elapsed > expected else ""
        return text + f" · {_duration(elapsed)} of an expected {_duration(expected)}{over}"
    return text


def _check_text(check: dict) -> str:
    """A queue row's check with its failing tests and log; the page's program writes the same line."""
    text = f"{check['id']}: {check['state']}"
    if check.get("failures"):
        text += " · failing " + ", ".join(check["failures"])
    if check.get("log"):
        text += f" · log {check['log']}"
    return text


def _estimate_text(estimate: dict) -> str:
    """A waiting candidate's place in the queue; the page's program writes the same line."""
    start = estimate.get("expectedStart")
    when = f"expected to start around {start}" if start else "start time unknown"
    return f"position {estimate['position']} · {when} (an estimate)"


def _stats_text(stats: dict) -> str:
    """One line of a candidate's stats; the page's program writes the same line."""
    rounds = stats.get("rounds")
    if rounds is None:
        review = "rounds unknown"
    elif stats.get("roundsSource") == "reported":
        review = f"{rounds} {'round' if rounds == 1 else 'rounds'} (reported)"
    else:
        findings = stats.get("findings") or {}
        counts = "/".join(str(findings.get(severity, "?")) for severity in ("P0", "P1", "P2", "P3"))
        review = f"{rounds} {'round' if rounds == 1 else 'rounds'}, findings P0/P1/P2/P3 {counts}"
    return (f"build {_duration(stats.get('buildSeconds'))} · review {_duration(stats.get('reviewSeconds'))}"
            f" · {review} · wait {_duration(stats.get('waitSeconds'))}"
            f" · check to merge {_duration(stats.get('checkToMergeSeconds'))} · total {_duration(stats.get('totalSeconds'))}")


def _status_text(status: object) -> str | None:
    """The status line; the page's program writes the same line."""
    if not isinstance(status, dict) or not isinstance(status.get("text"), str):
        return None
    if status.get("source") == "override":
        return f"Status: {status['text']} (set by the lead, until {status.get('expiresAt')})"
    return f"Status: {status['text']}"


def _status_lines(status: object) -> list[str]:
    text = _status_text(status)
    return [] if text is None else [_markdown_text(text), ""]


def render_markdown(document: dict) -> str:
    state = document["state"]
    meta = state["meta"]
    lines = [
        f"# {_markdown_text(meta['headline'])}",
        "",
        *_status_lines(document.get("status")),
        f"Updated {_markdown_text(meta['updatedAt'])} · state version {_markdown_text(state['version'])}",
        f"Base head {_markdown_text(meta['baseHead'])} · released head {_markdown_text(meta['releasedHead'])}",
        "",
    ]

    checking = meta["checking"]
    rows = []
    if checking["now"] is not None:
        now = checking["now"]
        rows.append(f"- Now: {_markdown_text(now['subject'])}, {_markdown_text(now['title'])}, since {_markdown_text(now['startedAt'])}")
        rows.extend(f"  - {_markdown_text(_step_text(step, meta['updatedAt']))}" for step in now["steps"])
    if checking["next"]:
        rows.append("- Next: " + ", ".join(_markdown_text(subject) for subject in checking["next"]))
    lines += _markdown_section("Checking", rows, "Nothing is being checked.")

    stats = document.get("stats") or {}
    estimates = document.get("estimates") or {}

    def change_request(item: dict) -> str:
        reference = item.get("changeRequest")
        return "" if reference is None else f" · change request {_markdown_text(reference)}"

    def stats_row(item: dict, indent: str) -> list[str]:
        found = stats.get(item["subject"])
        return [f"{indent}- stats: {_markdown_text(_stats_text(found))}"] if isinstance(found, dict) else []

    rows = []
    for item in sorted(state["queue"], key=lambda entry: (entry["order"], entry["subject"])):
        # A bullet, not an ordered list: the order is the record's, not Markdown's.
        rows.append(
            f"- {_markdown_number(item['order'])} {_markdown_text(item['subject'])}: {_markdown_text(item['title'])}, {_markdown_text(item['stage'])}"
            f" · lane {_markdown_text(item['lane'])} · head {_markdown_text(item['head'])}{change_request(item)}"
        )
        rows.extend(stats_row(item, "  "))
        if isinstance(estimates.get(item["subject"]), dict):
            rows.append(f"  - {_markdown_text(_estimate_text(estimates[item['subject']]))}")
        rows.extend(f"  - {_markdown_text(_check_text(check))}" for check in item["checks"])
    lines += _markdown_section("Queue", rows, "The queue is empty.")

    rows = []
    for item in state.get("planned", []):
        rows.append(f"- {_markdown_text(item['subject'])}: {_markdown_text(item['title'])}, {_markdown_text(item['stage'])}"
                    f" · lane {_markdown_text(item['lane'])}{change_request(item)}")
        rows.extend(stats_row(item, "  "))
    lines += _markdown_section("Planned", rows, "Nothing planned outside the queue.")

    derived = document.get("focus") or {}

    def lane_focus(lane: dict) -> str:
        if not lane["focus"] and isinstance(derived.get(lane["id"]), str):
            return _markdown_text(derived[lane["id"]]) + " (derived)"
        return _markdown_text(lane["focus"])

    rows = [
        f"- {_markdown_text(lane['id'])}: {_markdown_text(lane['title'])}, {_markdown_text(lane['state'])}"
        f" · focus {lane_focus(lane)} · waiting on {_markdown_text(lane['waitingOn'])} · since {_markdown_text(lane['since'])}"
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
.status { position: fixed; top: 8px; right: 12px; margin: 0; font-size: 12px; color: var(--muted); }
.status.down { color: var(--ink); }
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
    if (typeof value === "number" && Number.isInteger(value)) {
      /* A whole number in full, never in exponent form, however large. */
      return value === 0 ? "0" : value.toLocaleString("en-US", { useGrouping: false, maximumFractionDigits: 0 });
    }
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
  function duration(seconds) {
    if (typeof seconds !== "number" || Math.floor(seconds) !== seconds) { return "unknown"; }
    function two(value) { return (value < 10 ? "0" : "") + value; }
    if (seconds < 60) { return seconds + "s"; }
    if (seconds < 3600) { return Math.floor(seconds / 60) + "m " + two(seconds % 60) + "s"; }
    if (seconds < 86400) { return Math.floor(seconds / 3600) + "h " + two(Math.floor(seconds / 60) % 60) + "m"; }
    return Math.floor(seconds / 86400) + "d " + two(Math.floor(seconds / 3600) % 24) + "h";
  }
  function seconds(text) { return Math.floor(Date.parse(text) / 1000); }
  function elapsed(start, end) {
    if (typeof start !== "string" || typeof end !== "string") { return null; }
    var span = seconds(end) - seconds(start);
    return isNaN(span) ? null : Math.max(0, span);
  }
  /* A live page counts a running step on from its own clock, never from before the record's last write. */
  function liveNow(asOf) {
    var at = new Date(Math.max(Date.now(), Date.parse(asOf) || 0));
    return isNaN(at.getTime()) ? asOf : at.toISOString();
  }
  function stepText(step, asOf) {
    var text = show(step.label) + ": " + show(step.state);
    if (typeof step.finishedAt === "string" && typeof step.startedAt === "string") {
      return text + " \\u00b7 took " + duration(elapsed(step.startedAt, step.finishedAt));
    }
    if (step.state === "running" && typeof step.startedAt === "string") {
      var spent = elapsed(step.startedAt, asOf);
      if (typeof step.expectedSeconds !== "number") {
        return text + " \\u00b7 " + duration(spent) + ", no expected time yet";
      }
      return text + " \\u00b7 " + duration(spent) + " of an expected " + duration(step.expectedSeconds) +
        (spent !== null && spent > step.expectedSeconds ? ", over the expected time" : "");
    }
    return text;
  }
  function checkText(check) {
    var text = show(check.id) + ": " + show(check.state);
    if (isArray(check.failures) && check.failures.length) { text += " \\u00b7 failing " + check.failures.map(show).join(", "); }
    if (typeof check.log === "string") { text += " \\u00b7 log " + check.log; }
    return text;
  }
  function estimateText(estimate) {
    var when = typeof estimate.expectedStart === "string" ? "expected to start around " + estimate.expectedStart : "start time unknown";
    return "position " + show(estimate.position) + " \\u00b7 " + when + " (an estimate)";
  }
  function statsText(stats) {
    var review;
    if (stats.rounds === null || stats.rounds === undefined) {
      review = "rounds unknown";
    } else if (stats.roundsSource === "reported") {
      review = show(stats.rounds) + (stats.rounds === 1 ? " round" : " rounds") + " (reported)";
    } else {
      var findings = isObject(stats.findings) ? stats.findings : {};
      review = show(stats.rounds) + (stats.rounds === 1 ? " round" : " rounds") + ", findings P0/P1/P2/P3 " + ["P0", "P1", "P2", "P3"].map(function (severity) {
        return severity in findings ? show(findings[severity]) : "?";
      }).join("/");
    }
    return "build " + duration(stats.buildSeconds) + " \\u00b7 review " + duration(stats.reviewSeconds) +
      " \\u00b7 " + review + " \\u00b7 wait " + duration(stats.waitSeconds) +
      " \\u00b7 check to merge " + duration(stats.checkToMergeSeconds) + " \\u00b7 total " + duration(stats.totalSeconds);
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
    if (isObject(doc.status) && typeof doc.status.text === "string") {
      parts.push(el("p", "Status: " + doc.status.text +
        (doc.status.source === "override" ? " (set by the lead, until " + show(doc.status.expiresAt) + ")" : ""), "status"));
    }

    parts.push(el("h2", "Checking"));
    var now = meta.checking.now;
    if (now) {
      var current = el("div");
      current.appendChild(el("div", show(now.subject) + ": " + show(now.title)));
      current.appendChild(el("div", "Since " + show(now.startedAt), "detail"));
      var steps = list("ul", now.steps, "No steps yet.", function (step) {
        return el("li", stepText(step, liveNow(meta.updatedAt)));
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

    /* Stats are derived by the state tool; a live document without them still draws. */
    var stats = isObject(doc.stats) ? doc.stats : {};
    var estimates = isObject(doc.estimates) ? doc.estimates : {};
    function changeRequest(item) {
      return item.changeRequest === undefined || item.changeRequest === null ? "" : " \\u00b7 change request " + show(item.changeRequest);
    }
    function withStats(entry, item) {
      var found = Object.prototype.hasOwnProperty.call(stats, item.subject) ? stats[item.subject] : null;
      if (isObject(found)) { entry.appendChild(el("div", statsText(found), "detail")); }
      return entry;
    }

    var queue = state.queue.slice().sort(function (a, b) { return a.order - b.order; });
    parts.push(el("h2", "Queue"));
    parts.push(list("ol", queue, "The queue is empty.", function (item) {
      var checks = item.checks.map(function (check) { return show(check.id) + " " + show(check.state); }).join(", ");
      var entry = withStats(row(show(item.order) + ". " + show(item.subject) + ": " + show(item.title),
        "Lane " + show(item.lane) + " \\u00b7 head " + show(item.head) + changeRequest(item) + (checks ? " \\u00b7 " + checks : ""),
        item.stage), item);
      var estimate = Object.prototype.hasOwnProperty.call(estimates, item.subject) ? estimates[item.subject] : null;
      if (isObject(estimate)) { entry.appendChild(el("div", estimateText(estimate), "detail")); }
      item.checks.forEach(function (check) {
        if ((isArray(check.failures) && check.failures.length) || typeof check.log === "string") {
          entry.appendChild(el("div", checkText(check), "detail"));
        }
      });
      return entry;
    }));

    parts.push(el("h2", "Planned"));
    parts.push(list("ul", isArray(state.planned) ? state.planned : [], "Nothing planned outside the queue.", function (item) {
      return withStats(row(show(item.subject) + ": " + show(item.title), "Lane " + show(item.lane) + changeRequest(item),
        item.stage), item);
    }));

    parts.push(el("h2", "Lanes"));
    var derivedFocus = isObject(doc.focus) ? doc.focus : {};
    parts.push(list("ul", state.lanes, "No lanes.", function (lane) {
      var focus = !lane.focus && typeof derivedFocus[lane.id] === "string" ? derivedFocus[lane.id] + " (derived)" : show(lane.focus);
      return row(show(lane.id) + ": " + show(lane.title),
        "Focus " + focus + " \\u00b7 waiting on " + show(lane.waitingOn) + " \\u00b7 since " + show(lane.since), lane.state);
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
  /* A live store says whether its stream is connected; the page says so quietly. */
  var indicator = document.getElementById("live-status");
  if (isObject(store) && typeof store.onStatus === "function" && indicator) {
    store.onStatus(function (connected) {
      indicator.hidden = false;
      indicator.className = connected ? "status live" : "status down";
      indicator.textContent = connected ? "Live" : "Disconnected: showing the last state received";
    });
  }
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


# The live adapter: the same page, fed by the live command's event stream. It
# loads nothing from anywhere but the server that served the page.
_LIVE_ADAPTER = """
(function () {
  "use strict";
  var listeners = [];
  var statuses = [];
  function status(connected) { statuses.forEach(function (fn) { fn(connected); }); }
  var source = new EventSource("/events");
  source.addEventListener("document", function (event) {
    var doc;
    try { doc = JSON.parse(event.data); } catch (error) { return; }
    status(true);
    listeners.forEach(function (fn) { fn(doc); });
  });
  source.addEventListener("error", function () { status(false); });
  globalThis.deliveryStateStore = {
    read: function () { return fetch("/document").then(function (answer) { return answer.json(); }); },
    subscribe: function (fn) { listeners.push(fn); },
    onStatus: function (fn) { statuses.push(fn); }
  };
})();
""".strip()


def render_static_page(document: dict, *, live: bool = False) -> str:
    adapter = f"<script>\n{_LIVE_ADAPTER}\n</script>\n" if live else ""
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
        '<p id="live-status" class="status" hidden></p>\n'
        f'<script type="application/json" id="delivery-state">{_page_embed(document)}</script>\n'
        f"{adapter}"
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
        # Malformed recovery intents are set aside under numbered names too.
        and not _fold(path.parts[0]).startswith(_fold(RECOVERY_QUARANTINE))
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
    allowed = {"directory", "renderers", "publishers", "snapshotMaxAgeSeconds", "trackerSnapshot"}
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise ConfigError(f"unknown state configuration fields: {', '.join(unknown)}")
    age = config.get("snapshotMaxAgeSeconds")
    if age is not None and (not isinstance(age, int) or isinstance(age, bool) or age < 1):
        raise ConfigError("snapshotMaxAgeSeconds must be a whole number of seconds, at least 1")
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
        if "kind" in entry:
            # A host publisher runs nothing: the lead publishes its outbox with the host's own tool.
            if entry["kind"] != "host" or set(entry) != {"id", "kind"}:
                raise ConfigError(f"{where}: a publisher with a kind is {{id, kind: host}} and nothing else")
            if any(publisher.get("kind") == "host" for publisher in publishers):
                # Every host publisher writes the one outbox; a second would overwrite the first.
                raise ConfigError(f"{where}: a state configuration has at most one host publisher")
            publishers.append({"id": entry["id"], "kind": "host"})
            continue
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


# The signals that end the wrapper. Each is recorded when it arrives, and while
# work is in progress it also unwinds that work, so running commands stop.
# How long a command is waited for between checks for a recorded signal.
WAIT_SLICE_SECONDS = 0.2
TERMINATING = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name))
_unwinding = True
# A test seam: "<point>:<SIGNAME>[,<SIGNAME>...][;<point>:...]" delivers those
# signals to the wrapper itself at each named point, so every interrupt window
# can be tested without relying on timing; "<SIGNAME>!" records one without
# raising, as when a raise is swallowed. Unset, it does nothing.
INTERRUPT_SEAM = "ORCHESTRATE_RENDER_INTERRUPT_AT"


class Interrupted(BaseException):
    """A terminating signal arrived while work was in progress."""

    def __init__(self, number: int) -> None:
        super().__init__(number)
        self.number = number


def _on_signal(number: int, frame: object) -> None:
    global _unwinding
    if not _received:
        _received.append(number)
    if _unwinding:
        # Only the first signal unwinds, so a second one never lands inside
        # the stopping of commands the first one started.
        _unwinding = False
        raise Interrupted(number)


def _defer() -> None:
    """Hold unwinding back while a step that cannot be interrupted runs.

    Only on the main thread, where signals land: a helper thread's step must
    never stop the main thread from unwinding. A helper thread learns of a
    signal from the recorded one, when it next waits.
    """
    global _unwinding
    if threading.current_thread() is threading.main_thread():
        _unwinding = False


def _resume() -> None:
    """Unwind now if a signal arrived while unwinding was held back."""
    global _unwinding
    if threading.current_thread() is not threading.main_thread():
        return
    _unwinding = True
    if _received:
        _unwinding = False
        raise Interrupted(_received[0])


def _seam(point: str) -> None:
    for step in os.environ.get(INTERRUPT_SEAM, "").split(";"):
        named, _, signal_names = step.partition(":")
        if named == point:
            for name in (signal_names or "SIGINT").split(","):
                if name.endswith("!"):
                    # A raise swallowed where Python cannot raise: the signal
                    # is recorded and nothing unwinds.
                    _record(getattr(signal, name[:-1]), None)
                else:
                    os.kill(os.getpid(), getattr(signal, name))


def _process_group() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _stop(process: subprocess.Popen) -> None:
    """Stop a command and every process it started."""
    _seam("stopping")
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
    # A signal inside Popen, once the child is forked, would escape with the
    # child running and never stopped, so unwinding waits until it is known.
    _defer()
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_process_group()
        )
        # The child exists but nothing stops it yet: the window a signal
        # inside Popen falls into.
        _seam("spawning")
    except OSError as error:
        _resume()
        return Outcome(None, b"", b"", False, False, f"could not start: {error.strerror or error}")
    try:
        _resume()
        _seam("spawn")
        stdout = _Capture(process.stdout, VIEW_LIMIT_BYTES)
        # Enough bytes that the last ERROR_LIMIT characters survive decoding.
        stderr = _Capture(process.stderr, ERROR_BYTES, tail=True)
        feeder = threading.Thread(target=_feed, args=(process.stdin, document), daemon=True)
        for thread in (stdout, stderr, feeder):
            thread.start()
        try:
            _seam("wait")
            code = _wait(process, timeout)
        except subprocess.TimeoutExpired:
            _stop(process)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(DRAIN_SECONDS)
            return Outcome(None, bytes(stdout.kept), bytes(stderr.kept), stdout.truncated, False,
                           f"timed out after {timeout}s")
        # The process has exited; its output may still be in flight, or held
        # open by a child it left behind. Wait a bounded time for the rest.
        _seam("drain")
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


def _wait(process: subprocess.Popen, timeout: float) -> int:
    """Wait for a command, checking between short slices for a recorded signal.

    A signal's raise can be swallowed where Python cannot raise, such as in a
    finalizer; the recorded signal still ends the wait at the next slice.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            return process.wait(max(0.0, min(WAIT_SLICE_SECONDS, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            if _received:
                raise Interrupted(_received[0]) from None
            if time.monotonic() >= deadline:
                raise


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


def _write_outbox(directory: Path, publisher_id: str, shown: dict) -> dict:
    """Leave the state and every journal entry not yet acknowledged for the lead to publish."""
    entry: dict = {"id": publisher_id, "kind": "host"}
    try:
        published = read_published(directory)
    except StateError as error:
        return {**entry, "ok": False, "error": str(error)}
    through = published["journalThrough"] if published else 0
    journal = [item for item in shown["journal"] if item["id"] > through]
    # Readers ignore fields they do not know, so later projections can join these.
    outbox = {"version": shown["state"]["version"], "journalThrough": journal[-1]["id"] if journal else through,
              "publisher": publisher_id, "state": shown["state"], "journal": journal, "stats": shown["stats"]}
    relative = f"{OUTBOX_DIRECTORY}/{OUTBOX_FILE}"
    try:
        _write_atomically(directory / OUTBOX_DIRECTORY / OUTBOX_FILE, dumps(outbox).encode("utf-8"))
    except OSError as failure:
        return {**entry, "ok": False, "error": f"could not write the outbox: {failure.strerror or failure}"}
    return {**entry, "ok": True, "outbox": relative, "entries": len(journal), "version": outbox["version"],
            "journalThrough": outbox["journalThrough"]}


def render(directory: Path, config: object) -> tuple[int, dict]:
    try:
        renderers, publishers = parse_config(config)
    except ConfigError as error:
        return 2, {"ok": False, "code": "config_invalid", "error": str(error)}
    try:
        shown = show(directory)
        document = dumps(shown).encode("utf-8")
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
        if publisher.get("kind") == "host":
            published.append(_write_outbox(directory, publisher["id"], shown))
            continue
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


def _interrupted(number: int) -> tuple[int, dict]:
    return 128 + number, {"code": "interrupted", "ok": False, "signal": signal.Signals(number).name}


def _watched() -> tuple[int, ...]:
    """The terminating signals the wrapper acts on: not those the parent ignored.

    An ignored signal is never blocked or read as pending, since some systems
    keep a blocked one pending even while it is ignored.
    """
    return tuple(number for number in TERMINATING if number not in _ignored)


def _pending() -> int | None:
    """A terminating signal held back by the block, if one arrived."""
    if not hasattr(signal, "sigpending"):
        return None
    pending = signal.sigpending()
    return next((number for number in _watched() if number in pending), None)


def _let_handlers_run() -> None:
    # CPython runs a pending Python-level handler when a function is entered
    # (and at loop iterations), so a signal another thread took just before
    # the block has its handler run by this call, where it only records it.
    for _ in range(2):
        pass


# ---------------------------------------------------------------------------
# The live view: re-render on every record write, and push to open pages

LIVE_POLL_SECONDS = 0.25
LIVE_KEEPALIVE_SECONDS = 15.0
# An HTML view opts in to the live stream with this in its head.
LIVE_OPT_IN = '<meta name="delivery-state-live">'


class LiveView:
    """The latest valid document, re-read and re-rendered when the record changes."""

    def __init__(self, directory: Path, config: object) -> None:
        self.directory = Path(directory)
        self.config = config
        self.changed = threading.Condition()
        self.generation = 0
        self.document: dict | None = None
        self.encoded = b""
        self.fingerprint: tuple | None = None
        self.stopping = threading.Event()

    def watch_key(self) -> tuple:
        keys = []
        for name in (CHANGES_FILE, STATE_FILE, JOURNAL_FILE):
            try:
                stat = (self.directory / name).stat()
                keys.append((stat.st_size, stat.st_mtime_ns))
            except OSError:
                keys.append(None)
        return tuple(keys)

    def read(self) -> dict | None:
        """The record, or None while it cannot be read: half-written, unreadable, or not yet there."""
        try:
            return show(self.directory)
        except (StateError, OSError, ValueError):
            return None

    def refresh(self) -> bool | None:
        """Publish the record if it moved, then re-render the views; None when it could not be read."""
        shown = self.read()
        if shown is None:
            return None
        encoded = json.dumps(shown, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
        # The whole document, so a record replaced at the same version still counts as moved.
        fingerprint = encoded
        if fingerprint == self.fingerprint:
            return False
        # Pages first: they need only the record, never the rendered files, so
        # no slow view delays them.
        with self.changed:
            self.document, self.encoded, self.fingerprint = shown, encoded, fingerprint
            self.generation += 1
            self.changed.notify_all()
        with contextlib.suppress(Exception):
            render(self.directory, _views_only(self.config))
        return True

    def watch(self) -> None:
        seen = None
        try:
            _seam("watch-start")
            while not self.stopping.wait(LIVE_POLL_SECONDS):
                key = self.watch_key()
                if key == seen:
                    continue
                try:
                    # Settled only once the record read cleanly; otherwise look again next tick.
                    if self.refresh() is not None:
                        seen = key
                except Exception:  # noqa: BLE001 - the watcher outlives any one bad read
                    continue
        except BaseException:
            # Interrupted: stop serving with it, rather than leave pages on a dead stream.
            self.stop()
            raise

    def stop(self) -> None:
        self.stopping.set()
        with self.changed:
            self.changed.notify_all()


def _live_handler(view: LiveView, accepts: Callable[[str], bool]) -> type:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the base class's name
            return

        def _plain(self, code: int, body: bytes, kind: str = "text/plain; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - the base class's name
            # The record is only for pages on the host and port it serves: a
            # page from anywhere else that renamed itself to reach it is refused.
            if not accepts(self.headers.get("Host") or ""):
                self._plain(403, b"refused: this view serves only its own host and port\n")
                return
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                with view.changed:
                    document = view.document
                if document is None:
                    self._plain(503, b"the record is not readable yet\n")
                    return
                page = render_static_page(document, live=True).encode("utf-8")
                self._plain(200, page, "text/html; charset=utf-8")
            elif path == "/document":
                with view.changed:
                    encoded = view.encoded
                self._plain(200 if encoded else 503, encoded or b"{}", "application/json")
            elif path == "/events":
                self._events()
            elif path.startswith("/view/"):
                self._view(path[len("/view/"):])
            else:
                self._plain(404, b"not found\n")

        def _view(self, name: str) -> None:
            outputs = {renderer["output"] for renderer in _live_renderers(view.config)}
            if name not in outputs or not name.endswith(".html"):
                self._plain(404, b"not found\n")
                return
            try:
                page = (view.directory / name).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                self._plain(503, b"the view is not rendered yet\n")
                return
            if LIVE_OPT_IN not in page:
                self._plain(404, b"this view does not opt in to the live stream\n")
                return
            page = page.replace(LIVE_OPT_IN, LIVE_OPT_IN + f"\n<script>\n{_LIVE_ADAPTER}\n</script>", 1)
            self._plain(200, page.encode("utf-8"), "text/html; charset=utf-8")

        def _events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            sent = -1
            try:
                while not view.stopping.is_set():
                    with view.changed:
                        if view.generation == sent:
                            view.changed.wait(LIVE_KEEPALIVE_SECONDS)
                        generation, encoded = view.generation, view.encoded
                    if view.stopping.is_set():
                        break
                    if generation != sent and encoded:
                        # Only the latest is sent: a burst of writes reaches a page as one update.
                        self.wfile.write(b"event: document\ndata: " + encoded + b"\n\n")
                        sent = generation
                    else:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    return Handler


def _views_only(config: object) -> object:
    """The configuration with no publishers: the live view re-renders views, and publishing stays the lead's."""
    if config is None:
        return {"renderers": [dict(entry) for entry in DEFAULT_RENDERERS], "publishers": []}
    return {**config, "publishers": []} if isinstance(config, dict) else config


def _live_renderers(config: object) -> list[dict]:
    try:
        renderers, _ = parse_config(config)
    except ConfigError:
        return []
    return renderers


class _LiveServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


class _LiveServer6(_LiveServer):
    address_family = socket.AF_INET6


def _is_loopback(name: str) -> bool:
    """Whether a host names this machine's loopback: localhost, 127.0.0.0/8, ::1, or ::ffff:127.x."""
    if name.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def _is_wildcard(name: str) -> bool:
    """Whether a host would listen on every address: empty, or naming or resolving to an unspecified one."""
    if not name.strip():
        return True
    try:
        return ipaddress.ip_address(name).is_unspecified
    except ValueError:
        pass
    try:
        found = socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return False  # the bind then fails on its own
    return any(ipaddress.ip_address(entry[4][0].split("%")[0]).is_unspecified for entry in found)


def _split_host(header: str) -> tuple[str, str] | None:
    """A Host header's host and port, or None when it has no port."""
    if header.startswith("["):
        host, _, rest = header[1:].partition("]")
        return (host, rest[1:]) if rest.startswith(":") else None
    host, colon, port = header.rpartition(":")
    return (host, port) if colon and host else None


def _live_hosts(host: str, port: int) -> Callable[[str], bool]:
    """Which Host headers a request may carry.

    The bound host and port; for a loopback bind, any loopback name on that
    port, so a page opened as localhost or 127.0.0.1 is served alike.
    """
    bound_loopback = _is_loopback(host)

    def accepts(header: str) -> bool:
        parts = _split_host(header.strip())
        if parts is None or parts[1] != str(port):
            return False
        name = parts[0]
        return name.lower() == host.lower() or (bound_loopback and _is_loopback(name))

    return accepts


def serve_live(directory: Path, config: object, *, host: str, port: int, announce: Callable[[str], None]) -> None:
    """Serve the live view until interrupted. Renders on every record change; never writes the record."""
    view = LiveView(directory, config)
    view.refresh()
    server = (_LiveServer6 if ":" in host else _LiveServer)((host, port), _live_handler(view, lambda header: False))
    if _is_wildcard(str(server.server_address[0])):
        # A name that only resolved to every address once bound: never serve on it.
        server.server_close()
        raise OSError("the live view serves this machine only; bind a loopback address")
    bound = server.server_address[1]
    shown_host = f"[{host}]" if ":" in host else host
    server.RequestHandlerClass = _live_handler(view, _live_hosts(host, bound))
    watcher = threading.Thread(target=view.watch, name="live-watch", daemon=True)
    serving = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, name="live-serve",
                               daemon=True)
    watcher.start()
    serving.start()
    try:
        # The address alone: a browser opens it as it is.
        announce(f"{shown_host}:{bound}")
        while serving.is_alive() and not view.stopping.is_set():
            serving.join(0.5)
            # A signal whose raise was swallowed is still recorded: unwind on it.
            if _received:
                raise Interrupted(_received[0])
    finally:
        view.stop()
        server.shutdown()
        server.server_close()
        # A view command the watcher started is stopped by it, on the recorded signal, within a wait slice.
        watcher.join(5)


def _live_from_args(argv: list[str]) -> tuple[int, dict]:
    parser = argparse.ArgumentParser(prog="render_state.py live",
                                     description="serve the delivery's views live, re-rendered on every write")
    parser.add_argument("--dir", type=Path, required=True, help="the state directory")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--binding", type=Path, help="a binding file; its state member is read")
    source.add_argument("--state-config", help="the state configuration as JSON, or - to read it from stdin")
    parser.add_argument("--host", default="127.0.0.1",
                        help="the address to listen on; anything but this machine's loopback must be named")
    parser.add_argument("--port", type=int, default=0, help="the port; 0 picks a free one")
    args = parser.parse_args(argv)
    if _is_wildcard(args.host):
        # Every address at once would put the record in front of the network;
        # hosting it beyond this machine is the adopter's to arrange.
        return 2, {"ok": False, "code": "live_refused",
                   "error": "the live view serves this machine only; bind a loopback address"}
    try:
        config = _load_config(args)
        parse_config(config)
    except ConfigError as error:
        return 2, {"ok": False, "code": "config_invalid", "error": str(error)}
    try:
        show(args.dir)
    except StateError as error:
        return 2, {"ok": False, "code": error.code, "error": str(error)}

    def announce(address: str) -> None:
        line = json.dumps({"ok": True, "live": address}, ensure_ascii=True, separators=(",", ":")) + "\n"
        sys.stdout.buffer.write(line.encode("ascii"))
        sys.stdout.flush()

    try:
        serve_live(args.dir, config, host=args.host, port=args.port, announce=announce)
    except OSError as error:
        return 2, {"ok": False, "code": "live_unavailable",
                   "error": f"cannot listen on {args.host}:{args.port}: {error.strerror or error}"}
    return 0, {"ok": True, "live": "stopped"}


def _render_from_args(argv: list[str] | None) -> tuple[int, dict]:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["live"]:
        return _live_from_args(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True, help="the state directory")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--binding", type=Path, help="a binding file; its state member is read")
    source.add_argument("--state-config", help="the state configuration as JSON, or - to read it from stdin")
    args = parser.parse_args(argv)
    try:
        config = _load_config(args)
    except ConfigError as error:
        return 2, {"ok": False, "code": "config_invalid", "error": str(error)}
    code, result = render(args.dir, config)
    _seam("finished")
    return code, result


def main(argv: list[str] | None = None) -> int:
    """Render as a caller in this process would: no signal handling changes."""
    code, result = _render_from_args(argv)
    emit(result)
    return code


def _main_process(argv: list[str] | None = None) -> int:
    """Render as the wrapper process, settling the exit code against signals."""
    global _unwinding
    _unwinding = True
    try:
        _seam("start")
        for number in TERMINATING:
            if number in _ignored:
                continue
            with contextlib.suppress(ValueError, OSError):
                signal.signal(number, _on_signal)
        # A signal recorded before the work began ends it before it starts.
        if _received:
            _unwinding = False
            raise Interrupted(_received[0])
        code, result = _render_from_args(argv)
    except Interrupted as error:
        code, result = _interrupted(error.number)
    except KeyboardInterrupt:
        # Python's own handler, before the wrapper's was installed.
        code, result = _interrupted(signal.SIGINT)
    # Settle the exit code. From here a signal no longer unwinds anything: it
    # is recorded, or held back by the block and read from the pending set.
    _unwinding = False
    if hasattr(signal, "pthread_sigmask"):
        signal.pthread_sigmask(signal.SIG_BLOCK, _watched())
    _let_handlers_run()
    number = _received[0] if _received else _pending()
    if number is not None:
        code, result = _interrupted(number)
    emit(result)
    _seam("exit")
    # Checked again here and once more in _exit: either alone catches a signal
    # held back since the block; together they leave no gap before the exit.
    if number is None:
        number = _pending()
        if number is not None:
            code = 128 + number
    return code


def _exit(code: int) -> None:
    """End the process with ``code``, never reporting success after a signal.

    Every terminating signal takes its default action from here on, so one
    arriving now ends the process by the signal. One that arrived before is
    either held back by the block or was taken by another thread, whose
    handler then runs at the next step here and records it; either way it
    turns the exit code non-zero. Only a clean exit unblocks the signals, so a
    signal still pending then ends the process by itself.

    One window remains, nanoseconds wide and only while a helper thread is
    alive: a signal that thread takes during the reset of its own handler can
    be discarded by CPython itself.
    """
    with contextlib.suppress(OSError, ValueError):
        sys.stdout.flush()
        sys.stderr.flush()
    for number in TERMINATING:
        if number in _ignored:
            continue
        with contextlib.suppress(ValueError, OSError):
            signal.signal(number, signal.SIG_DFL)
    _let_handlers_run()
    if code == 0:
        number = _received[0] if _received else _pending()
        if number is not None:
            code = 128 + number
    if code == 0 and hasattr(signal, "pthread_sigmask"):
        signal.pthread_sigmask(signal.SIG_UNBLOCK, _watched())
    os._exit(code)


if __name__ == "__main__":
    _exit(_main_process())
