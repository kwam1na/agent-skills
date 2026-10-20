#!/usr/bin/env python3
"""Render a validated ``{state, journal}`` document as one self-contained page.

Reads the document on stdin and writes the page on stdout. The page embeds
the document as JSON with ``<``, ``>``, ``&``, U+2028 and U+2029 escaped, draws
everything through text content only, and loads nothing from anywhere else.

A hosting environment may provide a live store as a global
``deliveryStateStore`` object with ``read()``, which returns the document or a
promise of it, and an optional ``subscribe(callback)``. The page shows a live
document only when its ``schemaVersion`` matches the embedded one and it can be
drawn; otherwise it keeps showing the embedded state.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json


ESCAPES = {
    "<": "\\u003c",
    ">": "\\u003e",
    "&": "\\u0026",
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}

STYLE = """
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
PROGRAM = """
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
    if (typeof store.subscribe === "function") {
      store.subscribe(function (doc) {
        if (drawLive(doc) === "shown") { pushed = true; }
      });
    }
    Promise.resolve().then(function () { return store.read(); }).then(function (doc) {
      if (pushed) { return; }
      var result = drawLive(doc);
      if (result !== "shown") { fallBack(result); }
    }, function () {
      if (!pushed) { fallBack("unread"); }
    });
  }
})();
""".strip()


def embed(document: object) -> str:
    """The document as JSON that cannot close or break out of its script element."""
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for raw, escaped in ESCAPES.items():
        text = text.replace(raw, escaped)
    return text


def render(document: dict) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Delivery state</title>\n"
        f"<style>\n{STYLE}\n</style>\n"
        "</head>\n"
        "<body>\n"
        '<main id="app" data-source="embedded"></main>\n'
        f'<script type="application/json" id="delivery-state">{embed(document)}</script>\n'
        f"<script>\n{PROGRAM}\n</script>\n"
        "</body>\n"
        "</html>\n"
    )


def main() -> int:
    document = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    sys.stdout.buffer.write(render(document).encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
