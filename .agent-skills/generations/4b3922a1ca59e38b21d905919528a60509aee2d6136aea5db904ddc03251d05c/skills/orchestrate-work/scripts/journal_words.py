#!/usr/bin/env python3
"""Turn one journal entry into its words, from the registry of kinds.

Run with ``python3 -B``. The registry, ``references/journal-kinds.json``, is the
only place a kind is described: its facts, its links, its altitude and tone, its
label, its words and its explanation. Every tool that reads a kind's description
reads it through ``REGISTRY_FILE``, which a test may point at a copy.

A kind's ``head`` and ``line`` are templates. ``{name}`` is a field of the
entry's ``data``, or its ``lane`` or ``subject``, ``{name.member}`` a member of a
typed field such as a base's ``ref``, and ``{name:form}`` shows it in one of a
closed set of forms:

- ``commit``: a commit, shortened to its first seven characters;
- ``lane``: a lane's name, so ``lane-1`` reads "Lane 1" and ``lead`` "Lead";
- ``count:noun`` or ``count:noun|nouns``: a number, or a list's length, with
  its noun: "0 rounds", "1 round", "2 rounds";
- ``list`` or ``list:form``: a list joined as "a, b and c", each item in the
  given form, and "none" when empty;
- ``duration``: seconds as "45s", "4m 10s", "13m", "1h 43m" or "3d";
- ``ticket``: a ticket by its key, read from a tracker link when it is one.

A part in square brackets is left out when a field it uses is absent, an
empty list or zero: there is nothing in it to say. A head or
line may instead be ``{"by": field, "cases": {value: template}, "otherwise":
template}``, a variant chosen by that field's value. There is no other logic.

Reads JSON entries on stdin, one per line, and prints each one's words as JSON.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import re
from pathlib import Path

REGISTRY_FILE = Path(__file__).resolve().parent.parent / "references" / "journal-kinds.json"
FORMS = ("commit", "lane", "count", "list", "duration", "ticket")
# A field reference: a name, then an optional form and its argument.
FIELD = re.compile(r"\{([A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)*)(?::([a-z]+)(?::([^{}\[\]]+))?)?\}")
TICKET = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+")


class WordsError(ValueError):
    """A template, or the entry it is given, that the words cannot be made from."""


def load_registry(path: Path | None = None) -> dict:
    """The registry at ``path``, or at ``REGISTRY_FILE`` as it stands when called."""
    return json.loads(Path(REGISTRY_FILE if path is None else path).read_bytes().decode("utf-8"))


def words(entry: dict, registry: dict | None = None) -> dict:
    """``{"head", "line"}`` for one entry, in its kind's words."""
    return traced(entry, registry)[0]


def traced(entry: dict, registry: dict | None = None) -> tuple[dict, list[tuple]]:
    """The entry's words, and the trace of what made them.

    The trace lists, for each of ``head`` and ``line``, the variant chosen, each
    bracketed part kept or left out, and each form used with the value it was
    given, so a test can say which parts and forms its vectors reach.
    """
    registry = load_registry() if registry is None else registry
    kind = registry["kinds"].get(entry.get("kind"))
    if kind is None:
        raise WordsError(f"no kind named {entry.get('kind')!r} in the registry")
    fields = {**(entry.get("data") or {}), "lane": entry.get("lane"), "subject": entry.get("subject")}
    trace: list[tuple] = []
    made = {}
    for part in ("head", "line"):
        template = kind["words"][part]
        if isinstance(template, dict):
            value = lookup(fields, template["by"])
            key = _case_key(value)
            if key in template["cases"]:
                trace.append((part, "variant", key))
                template = template["cases"][key]
            else:
                trace.append((part, "variant", None))
                template = template["otherwise"]
        made[part] = _fill(template, fields, part, trace)
    return made, trace


def variants(template: object) -> list[str | None]:
    """The variant keys a head or line offers, with None for its ``otherwise``."""
    return [*template["cases"], None] if isinstance(template, dict) else []


def templates(template: object) -> list[str]:
    """Every template string a head or line holds."""
    return [*template["cases"].values(), template["otherwise"]] if isinstance(template, dict) else [template]


def brackets(template: str) -> list[str]:
    """The bracketed parts of one template string, in order."""
    found, depth, start = [], 0, 0
    for index, character in enumerate(template):
        if character == "[":
            if depth:
                raise WordsError(f"a bracketed part holds another: {template!r}")
            depth, start = 1, index
        elif character == "]":
            if not depth:
                raise WordsError(f"a bracket closes that never opened: {template!r}")
            depth = 0
            found.append(template[start + 1:index])
    if depth:
        raise WordsError(f"a bracket never closes: {template!r}")
    return found


def references(template: str) -> list[tuple[str, str | None, str | None]]:
    """Each ``{name:form:argument}`` a template string uses."""
    return [match.groups() for match in FIELD.finditer(template)]


def lookup(fields: dict, name: str) -> object:
    """A field by its dotted name, or None when it or any member on the way is absent."""
    value: object = fields
    for part in name.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _said(value: object) -> bool:
    """Whether a bracketed part has something to say of a value: not absent, an empty list or zero."""
    return value is not None and value != [] and (isinstance(value, bool) or value != 0)


def _case_key(value: object) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int)):
        return str(value)
    return None


def _fill(template: str, fields: dict, part: str, trace: list[tuple]) -> str:
    out, at = [], 0
    for index, inner in enumerate(brackets(template)):
        start = template.index("[" + inner + "]", at)
        out.append(_substitute(template[at:start], fields, part, trace))
        kept = all(_said(lookup(fields, name)) for name, _, _ in references(inner))
        trace.append((part, "bracket", template, index, kept))
        if kept:
            out.append(_substitute(inner, fields, part, trace))
        at = start + len(inner) + 2
    out.append(_substitute(template[at:], fields, part, trace))
    return "".join(out)


def _substitute(text: str, fields: dict, part: str, trace: list[tuple]) -> str:
    def one(match: re.Match) -> str:
        name, form, argument = match.groups()
        value = lookup(fields, name)
        if value is None:
            raise WordsError(f"{part} uses {name}, which the entry does not have")
        shown = show(value, form, argument)
        if form is not None:
            trace.append((part, "form", form, _edge(form, value)))
        return shown
    return FIELD.sub(one, text)


def show(value: object, form: str | None, argument: str | None = None) -> str:
    """One value in one form."""
    if form is None:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise WordsError(f"{value!r} needs a form to be shown")
        return _number(value) if not isinstance(value, str) else value
    if form == "commit":
        return _text(value)[:7]
    if form == "lane":
        lane = _text(value)
        if lane == "lead":
            return "Lead"
        numbered = re.fullmatch(r"lane-([0-9]+)", lane)
        if numbered:
            return "Lane " + numbered.group(1)
        name = lane[len("lane-"):] if lane.startswith("lane-") else lane
        return name[:1].upper() + name[1:] + " lane"
    if form == "count":
        value = len(value) if isinstance(value, list) else value
        if isinstance(value, bool) or not isinstance(value, int) or not argument:
            raise WordsError(f"a count is a whole number, or a list, with its noun: {value!r}")
        singular, _, plural = argument.partition("|")
        return f"{value} {singular if value == 1 else plural or singular + 's'}"
    if form == "list":
        if not isinstance(value, list):
            raise WordsError(f"a list is a list: {value!r}")
        items = [show(item, argument) for item in value]
        if not items:
            return "none"
        return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
    if form == "duration":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise WordsError(f"a duration is a number of seconds: {value!r}")
        return duration(value)
    if form == "ticket":
        text = _text(value)
        if "://" in text:
            keys = TICKET.findall(text)
            return keys[-1] if keys else text
        return text
    raise WordsError(f"no form named {form!r}")


def duration(total: float) -> str:
    """Seconds as the live page shows them."""
    total = int(total + 0.5)
    if total < 60:
        return f"{total}s"
    minutes = total // 60
    if minutes < 10:
        return f"{minutes}m" + (f" {total % 60}s" if total % 60 else "")
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h" + (f" {minutes % 60}m" if minutes % 60 else "")
    return f"{hours // 24}d"


def _edge(form: str, value: object) -> object:
    """What the trace keeps of a form's value: the edge it sits at."""
    if form == "count":
        return min(len(value) if isinstance(value, list) else value, 2)
    if form == "list":
        return min(len(value), 3)
    if form == "duration":
        return "under a minute" if value < 60 else "over an hour" if value > 3600 else "minutes"
    return None


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise WordsError(f"{value!r} is not text")
    return value


def _number(value: int | float) -> str:
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)


def main() -> int:
    registry = load_registry()
    for line in sys.stdin.buffer.read().decode("utf-8").splitlines():
        if line.strip():
            print(json.dumps(words(json.loads(line), registry), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
