#!/usr/bin/env python3
"""Render a validated ``{state, journal}`` document as Markdown.

Reads the document on stdin and writes the view on stdout. State text is
untrusted: every ASCII punctuation character in it is backslash-escaped, so
no link, image, raw HTML, autolink or emphasis can form, and every line or
paragraph separator becomes a space, so no text can open a new block.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import string
import unicodedata


PUNCTUATION = frozenset(string.punctuation)


def text(value: object) -> str:
    """One inert line of Markdown for a state value."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    out = []
    for character in raw:
        if character in PUNCTUATION:
            out.append("\\" + character)
        elif unicodedata.category(character) in {"Cc", "Zl", "Zp"}:
            out.append(" ")
        else:
            out.append(character)
    return "".join(out).strip() or "none"


def section(title: str, rows: list[str], empty: str) -> list[str]:
    return [f"## {title}", "", *(rows or [empty]), ""]


def render(document: dict) -> str:
    state = document["state"]
    meta = state["meta"]
    lines = [
        f"# {text(meta['headline'])}",
        "",
        f"Updated {text(meta['updatedAt'])} · state version {text(state['version'])}",
        f"Base head {text(meta['baseHead'])} · released head {text(meta['releasedHead'])}",
        "",
    ]

    checking = meta["checking"]
    rows = []
    if checking["now"] is not None:
        now = checking["now"]
        rows.append(f"- Now: {text(now['subject'])}, {text(now['title'])}, since {text(now['startedAt'])}")
        rows.extend(f"  - {text(step['label'])}: {text(step['state'])}" for step in now["steps"])
    if checking["next"]:
        rows.append("- Next: " + ", ".join(text(subject) for subject in checking["next"]))
    lines += section("Checking", rows, "Nothing is being checked.")

    rows = []
    for item in sorted(state["queue"], key=lambda entry: (entry["order"], entry["subject"])):
        rows.append(
            f"{text(item['order'])}. {text(item['subject'])}: {text(item['title'])}, {text(item['stage'])}"
            f" · lane {text(item['lane'])} · head {text(item['head'])}"
        )
        rows.extend(f"   - {text(check['id'])}: {text(check['state'])}" for check in item["checks"])
    lines += section("Queue", rows, "The queue is empty.")

    rows = [
        f"- {text(lane['id'])}: {text(lane['title'])}, {text(lane['state'])}"
        f" · focus {text(lane['focus'])} · waiting on {text(lane['waitingOn'])} · since {text(lane['since'])}"
        for lane in state["lanes"]
    ]
    lines += section("Lanes", rows, "No lanes.")

    rows = [f"- {'met' if item['met'] else 'open'}: {text(item['text'])}" for item in state["finish"]]
    lines += section("Finish line", rows, "No finish line recorded.")

    rows = [f"- {text(item['title'])} · {text(item['reference'])}" for item in state["followups"]]
    lines += section("Follow-ups", rows, "No follow-ups.")

    rows = [f"- {text(lease['id'])}: {text(lease['holder'])} since {text(lease['since'])}" for lease in state["leases"]]
    lines += section("Leases", rows, "No leases held.")

    rows = [
        f"- {text(entry['id'])}. {text(entry['at'])} {text(entry['kind'])}"
        f" · lane {text(entry['lane'])} · subject {text(entry['subject'])}: {text(entry['summary'])}"
        for entry in reversed(document["journal"])
    ]
    lines += section("Journal", rows, "The journal is empty.")
    return "\n".join(lines).rstrip("\n") + "\n"


def main() -> int:
    document = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    sys.stdout.buffer.write(render(document).encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
