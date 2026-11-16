#!/usr/bin/env python3
"""Tell a delivery's story from its journal, as a ``story/1`` document.

Run with ``python3 -B``. The narrator reads a journal of facts, the registry of
kinds, and, when it has them, the record's state and the plan, and writes the
story a person reads to follow the delivery: a digest, chapters, the beats
inside them, and the threads that gather each setback. It calls no model, no
network and no clock, so the same record always tells the same story, and the
story holds instants and the durations between two entries, never a time
measured to now. ``references/story.schema.json`` describes the document.

- **An entry's instant** is its ``occurredAt`` when it has one, and its ``at``
  otherwise. Order, chapters and costs all use it.
- **Chapters.** Each ``candidate.merged`` ends a chapter named for the piece
  that landed. The entries before the first landing are "Getting started", and
  those after the last are "Finishing" while the delivery is open and
  "Finished" once it has closed.
- **Threads.** An entry of a kind the registry marks as opening a thread opens
  one, unless the registry names a fact that keeps it from opening one, such as
  a stop that left its piece in the queue, or a thread already holds it. The thread gathers every entry linked to its opening through
  ``because`` and ``answers``, either way and at any distance, and closes at the
  first entry that answers its opening; its cost is the time between the two.
  One with no closing is open, and carries the instant it opened and no cost.
- **Beats.** A beat is one entry, except that a batch's starts are one beat, a
  run of bookkeeping is one beat, and a thread at the headline altitude is one
  beat telling its cause, its opening, what it held up and its ending. A
  person's decision is a beat of its own at every altitude, even in a thread.
- **Altitudes.** A beat lists the altitudes it shows at: ``headline``,
  ``story`` and ``everything``. A kind's altitude comes from the registry; a
  thread's entries show from the story altitude down, where the thread's own
  beat stands for them at the headline.

Reads the record document a renderer is given, ``{"state", "journal"}`` and
optionally ``"plan"``, on stdin, and prints its story as JSON.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import calendar
import importlib.util
import json
import re
from pathlib import Path

SPEC = "story/1"
ALTITUDES = ("headline", "story", "everything")
MERGE_KIND = "candidate.merged"
CLOSE_KIND = "delivery.closed"
DECISION_KIND = "decision.recorded"
STOP_KIND = "candidate.stopped"
ASSIGN_KIND = "lane.assigned"
HANDOFF_KIND = "candidate.handed"
BATCH_KIND = "check.started"
FINISH_KIND = "finish.met"
# Kinds that name a piece of the delivery by their subject.
PIECE_KINDS = (ASSIGN_KIND, "story.added", HANDOFF_KIND, MERGE_KIND, STOP_KIND)
# Work that, journaled after the close, means the delivery has reopened.
WORK_KINDS = (ASSIGN_KIND, HANDOFF_KIND, BATCH_KIND)
INSTANT = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]+)?Z$")
NUMBERS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")


def _sibling(name: str):
    # Loaded by its own path, so a copy of this script beside another release's reads its own registry.
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# What describes each kind: read through journal_words.REGISTRY_FILE, which a test may replace.
journal_words = _sibling("journal_words")


# Until the sentence is removed, a record may hold entries with a sentence and no facts, and one kind that is the
# sentence's bridge only. Such an entry is told by its sentence, under its kind's label, and opens no thread.
BRIDGE_KINDS = {"review.evidence": {"altitude": "bookkeeping", "tone": "note", "label": "Review evidence", "opensThread": False}}


# What each tone means to a reader, in the order a legend lists them. A setback's sheet is named by its tone's word.
TONES = [("landed", "Landed"), ("ready", "Ready to check"), ("decision", "Decision"), ("problem", "Setback"), ("note", "Other update")]


class NarratorError(ValueError):
    """A record the story cannot be told from."""


def _kind(entry: dict, registry: dict) -> dict:
    return registry["kinds"].get(entry["kind"]) or BRIDGE_KINDS[entry["kind"]]


def _facts(entry: dict) -> bool:
    return isinstance(entry.get("data"), dict)


def _said(entry: dict, registry: dict) -> dict:
    """An entry's words: its kind's, from its facts, or, for an entry with only a sentence, its label and sentence."""
    if _facts(entry):
        return journal_words.words(entry, registry)
    return {"head": _kind(entry, registry)["label"], "line": entry.get("summary") if isinstance(entry.get("summary"), str) else ""}


def narrate(journal: list, state: dict | None = None, plan: dict | None = None, registry: dict | None = None) -> dict:
    """The ``story/1`` document of one record's journal."""
    registry = journal_words.load_registry() if registry is None else registry
    entries = _ordered(journal, registry)
    by_id = {entry["id"]: entry for entry in entries}
    closed = _closed(entries)
    pieces = _pieces(entries, state, plan)
    titles = _titles(entries, state, plan)
    threads, member_of = _threads(entries, by_id, registry)
    chapters = _chapters(entries, closed)
    finishes = _finishes(state, plan)

    chapter_of = {entry: index for index, chapter in enumerate(chapters) for entry in chapter["entries"]}
    for thread in threads:
        thread["chapter"] = chapters[chapter_of[thread["opening"]]]["id"]
    beats = []
    for index, chapter in enumerate(chapters):
        made = _entry_beats(chapter["entries"], by_id, member_of, registry, finishes)
        for thread in threads:
            if chapter_of[thread["opening"]] == index:
                made.append(_thread_beat(thread, by_id, registry))
        made.sort(key=lambda beat: (_seconds(beat["at"]), beat["first"], beat["form"] != "thread"))
        for beat in made:
            beat["id"] = f"b{len(beats) + 1}"
            beat["chapter"] = chapter["id"]
            del beat["first"]
            if beat["form"] == "thread":
                next(thread for thread in threads if thread["id"] == beat["thread"])["beat"] = beat["id"]
            beats.append(beat)
        chapter["beats"] = [beat["id"] for beat in made]

    landed = list(dict.fromkeys(entry["subject"] for entry in entries if entry["kind"] == MERGE_KIND and entry["subject"]))
    for index, chapter in enumerate(chapters):
        begins = _seconds(_instant(by_id[chapters[index - 1]["entries"][-1]])) if index else None
        chapter["standing"] = [thread["id"] for thread in threads if begins is not None and chapter_of[thread["opening"]] < index
                               and (thread["closedAt"] is None or _seconds(thread["closedAt"]) > begins)]
        _word_chapter(chapter, chapters, index, by_id, threads, pieces, landed, titles, state, closed, registry)
    for chapter in chapters:
        del chapter["first"]
    return {
        "schemaVersion": SPEC,
        "state": "closed" if closed else "open",
        "digest": _digest(entries, threads, pieces, landed, closed),
        "chapters": chapters,
        "beats": beats,
        "threads": threads,
        "tones": [{"tone": tone, "word": word} for tone, word in TONES],
    }


# The journal


def _ordered(journal: object, registry: dict) -> list[dict]:
    if not isinstance(journal, list):
        raise NarratorError("a journal is a list of entries")
    for position, entry in enumerate(journal, 1):
        if (not isinstance(entry, dict) or not isinstance(entry.get("id"), int) or isinstance(entry.get("id"), bool)
                or not isinstance(entry.get("kind"), str) or not isinstance(entry.get("at"), str)):
            raise NarratorError(f"entry {position} has no id, kind and at")
        if entry["kind"] not in registry["kinds"] and entry["kind"] not in BRIDGE_KINDS:
            raise NarratorError(f"entry {entry['id']}: no kind named {entry['kind']} in the registry")
        _seconds(_instant(entry))
        if not isinstance(entry.get("data", {}), dict):
            raise NarratorError(f"entry {entry['id']}: its data is not an object")
    return sorted(journal, key=lambda entry: (_seconds(_instant(entry)), entry["id"]))


def _instant(entry: dict) -> str:
    """An entry's instant: when it happened, when that is known, and when it was journaled otherwise."""
    occurred = entry.get("occurredAt")
    return occurred if isinstance(occurred, str) else entry["at"]


def _seconds(text: str) -> float:
    found = INSTANT.match(text) if isinstance(text, str) else None
    if not found:
        raise NarratorError(f"{text!r} is not an instant")
    year, month, day, hour, minute, second = (int(part) for part in found.groups()[:6])
    fraction = float(found.group(7)) if found.group(7) else 0.0
    return calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0)) + fraction


def _between(start: str, end: str) -> int:
    return round(_seconds(end) - _seconds(start))


def _closed(entries: list[dict]) -> bool:
    closes = [index for index, entry in enumerate(entries) if entry["kind"] == CLOSE_KIND]
    return bool(closes) and not any(entry["kind"] in WORK_KINDS for entry in entries[closes[-1] + 1:])


def _pieces(entries: list[dict], state: dict | None, plan: dict | None) -> list[str]:
    """Every piece of the delivery: the record's, the plan's, then any the journal names."""
    found = []
    state = state if isinstance(state, dict) else {}
    for key in ("queue", "planned"):
        found.extend(item.get("subject") for item in state.get(key) or [] if isinstance(item, dict))
    if isinstance(plan, dict):
        found.extend(unit.get("subject") for unit in plan.get("units") or [] if isinstance(unit, dict) and unit.get("removed") is not True)
    found.extend(entry["subject"] for entry in entries if entry["kind"] in PIECE_KINDS)
    return list(dict.fromkeys(subject for subject in found if isinstance(subject, str) and subject))


def _titles(entries: list[dict], state: dict | None, plan: dict | None) -> dict:
    titles = {}
    for entry in entries:
        if entry["kind"] in (ASSIGN_KIND, "story.added") and isinstance(entry.get("data", {}).get("title"), str):
            titles.setdefault(entry["subject"], entry["data"]["title"])
    sources = []
    if isinstance(plan, dict):
        sources.extend(plan.get("units") or [])
    if isinstance(state, dict):
        sources.extend((state.get("queue") or []) + (state.get("planned") or []))
    for item in sources:
        if isinstance(item, dict) and isinstance(item.get("title"), str) and item["title"].strip():
            titles[item.get("subject")] = item["title"]
    return titles


# Threads


def _opens(entry: dict, registry: dict) -> bool:
    """Whether an entry opens a thread: its kind's ``opensThread`` is true, or ``{"unless": field}`` and that field isn't."""
    opens = _kind(entry, registry)["opensThread"]
    if not _facts(entry):
        return False
    if isinstance(opens, dict):
        return entry.get("data", {}).get(opens.get("unless")) is not True
    return opens is True


def _kept_from_opening(entry: dict, registry: dict) -> bool:
    """An entry of a kind that opens a thread, kept from opening one by its own facts: a stop that left its piece queued."""
    return bool(_kind(entry, registry)["opensThread"]) and _facts(entry) and not _opens(entry, registry)


def _threads(entries: list[dict], by_id: dict, registry: dict) -> tuple[list[dict], dict]:
    """Each setback's thread, and the thread each entry belongs to."""
    linked = {entry["id"]: set() for entry in entries}
    for entry in entries:
        for target in list(entry.get("because") or []) + list(entry.get("answers") or []):
            if target in linked:
                linked[entry["id"]].add(target)
                linked[target].add(entry["id"])
    member_of, threads = {}, []
    for entry in entries:
        if entry["id"] in member_of or not _opens(entry, registry):
            continue
        name = f"t{len(threads) + 1}"
        held, waiting = {entry["id"]}, [entry["id"]]
        while waiting:
            for other in linked[waiting.pop()]:
                if other not in held and other not in member_of:
                    held.add(other)
                    waiting.append(other)
        members = [item for item in entries if item["id"] in held]
        for item in members:
            member_of[item["id"]] = name
        closing = next((item for item in members if entry["id"] in (item.get("answers") or [])), None)
        opened = _instant(entry)
        threads.append({
            "id": name,
            "chapter": None,
            "beat": None,
            "opening": entry["id"],
            "closing": closing["id"] if closing else None,
            "openedAt": opened,
            "closedAt": _instant(closing) if closing else None,
            "cost": _between(opened, _instant(closing)) if closing else None,
            "state": "closed" if closing else "open",
            "entries": [item["id"] for item in members],
            "cause": _cause(entry, by_id, registry),
            "heldUp": list(dict.fromkeys(item["subject"] for item in members
                                         if item["kind"] == STOP_KIND and item["id"] != entry["id"] and item["subject"])),
        })
    return threads, member_of


def _cause(opening: dict, by_id: dict, registry: dict) -> list[int]:
    """What went wrong first: the problems the opening happened because of, at any distance, or its direct causes."""
    direct = [target for target in opening.get("because") or [] if target in by_id]
    seen, waiting, problems = set(direct), list(direct), []
    while waiting:
        entry = by_id[waiting.pop()]
        if _kind(entry, registry)["tone"] == "problem":
            problems.append(entry)
        for target in entry.get("because") or []:
            if target in by_id and target not in seen:
                seen.add(target)
                waiting.append(target)
    chosen = problems or [by_id[target] for target in direct]
    return [entry["id"] for entry in sorted(chosen, key=lambda entry: (_seconds(_instant(entry)), entry["id"]))]


# Chapters and beats


def _chapters(entries: list[dict], closed: bool) -> list[dict]:
    chapters, current = [], []

    def chapter(form: str, held: list[dict], subject: str | None = None) -> None:
        chapters.append({"id": f"c{len(chapters) + 1}", "form": form, "subject": subject, "pieceTitle": None,
                         "at": _instant(held[-1] if form != "start" else held[0]), "entries": [entry["id"] for entry in held],
                         "first": held[0], "beats": [], "standing": [], "notLanded": [], "words": None})

    for entry in entries:
        if entry["kind"] == MERGE_KIND:
            if not chapters and current:
                chapter("start", current)
                current = []
            chapter("landing", current + [entry], entry["subject"])
            current = []
        else:
            current.append(entry)
    if current:
        chapter("finish" if chapters or closed else "start", current)
    return chapters


def _altitudes(entry: dict, thread: str | None, registry: dict) -> list[str]:
    altitude = _kind(entry, registry)["altitude"]
    if entry["kind"] == DECISION_KIND:
        # A person's decision, with its reason; a sentence-only one was the old record's catch-all.
        return list(ALTITUDES) if _facts(entry) else ["story", "everything"]
    if _kept_from_opening(entry, registry):
        altitude = "story"
    if altitude == "bookkeeping":
        return ["everything"]
    if altitude == "story" or thread is not None:
        return ["story", "everything"]
    return list(ALTITUDES)


def _finishes(state: dict | None, plan: dict | None) -> dict:
    """Each finish line by its id, as a reader knows it: the plan's label, else the record's own words for it."""
    named = {}
    for source, key in ((state, "text"), (state, "label"), (plan, "text"), (plan, "label")):
        for line in (source or {}).get("finish") or [] if isinstance(source, dict) else []:
            if isinstance(line, dict) and isinstance(line.get("id"), str):
                said = line.get(key)
                named[line["id"]] = said if isinstance(said, str) and said.strip() else named.get(line["id"])
    return named


def _entry_beats(ids: list[int], by_id: dict, member_of: dict, registry: dict, finishes: dict) -> list[dict]:
    beats = []
    for entry in (by_id[name] for name in ids):
        thread = member_of.get(entry["id"])
        kind = _kind(entry, registry)
        batch = entry.get("data", {}).get("batch") if entry["kind"] == BATCH_KIND else None
        last = beats[-1] if beats else None
        if (last and last["thread"] == thread and kind["altitude"] == "bookkeeping" and last["group"] == "bookkeeping"):
            last["held"].append(entry)
            continue
        # Finish lines met together, journaled one after another with nothing between, are told as one; a line written
        # only as a sentence keeps its sentence.
        met = "finish" if entry["kind"] == FINISH_KIND and _facts(entry) else None
        if met and last and last["thread"] == thread and last["group"] == met:
            last["held"].append(entry)
            continue
        if batch:
            # A batch's starts are one beat. Looking back, the search passes over the same batch's starts that another
            # thread holds, each its own beat, and stops at anything else, so a batch run again is a beat of its own.
            joined = None
            for earlier in reversed(beats):
                if earlier["group"] != ("batch", tuple(batch)):
                    break
                if earlier["thread"] == thread:
                    joined = earlier
                    break
            if joined is not None:
                joined["held"].append(entry)
                continue
        group = "bookkeeping" if kind["altitude"] == "bookkeeping" else ("batch", tuple(batch)) if batch else met
        beats.append({"group": group, "thread": thread, "held": [entry]})
    return [_beat(beat, registry, finishes) for beat in beats]


def _finish_label(entry: dict, finishes: dict) -> str:
    return finishes.get(entry.get("data", {}).get("finish")) or "A finish line"


def _beat(beat: dict, registry: dict, finishes: dict) -> dict:
    held = beat["held"]
    first = held[0]
    kind = _kind(first, registry)
    if len(held) == 1 and first["kind"] == FINISH_KIND and _facts(first):
        # A finish line is named for the reader by its label, never by its id.
        words = _said({**first, "data": {**first["data"], "finish": _finish_label(first, finishes)}}, registry)
        form, label, tone, kind_name = "entry", kind["label"], kind["tone"], first["kind"]
    elif len(held) == 1:
        form, words, label, tone, kind_name = "entry", _said(first, registry), kind["label"], kind["tone"], first["kind"]
    elif first["kind"] == FINISH_KIND:
        met = [entry.get("data", {}).get("finish") for entry in held]
        every = len(finishes) == len(held) and set(met) == set(finishes)
        head = "Every finish line was met" if every else f"{_number(len(held)).capitalize()} finish lines were met"
        form, label, tone, kind_name = "finish", kind["label"], kind["tone"], first["kind"]
        words = {"head": head, "line": journal_words.show([_finish_label(entry, finishes) for entry in held], "list")}
    elif beat["group"] == "bookkeeping":
        counted = {}
        for entry in held:
            label_ = _kind(entry, registry)["label"]
            counted[label_] = counted.get(label_, 0) + 1
        form, label, tone, kind_name = "bookkeeping", "Bookkeeping", "note", None
        words = {"head": "Bookkeeping", "line": journal_words.show([f"{name} ({count})" for name, count in counted.items()], "list")}
        # A run folds; opened, it lists each of its entries in that entry's own words.
        items = [{"entry": entry["id"], "words": _said(entry, registry)} for entry in held]
    else:
        form, label, tone, kind_name = "batch", kind["label"], kind["tone"], first["kind"]
        line = _said({**first, "subject": None}, registry)["line"]
        words = {"head": f"Checks started on {_number(len(beat['group'][1]))} pieces together", "line": line}
    return {"id": None, "chapter": None, "form": form, "altitudes": _altitudes(first, beat["thread"], registry), "at": _instant(first),
            "entries": [entry["id"] for entry in held], "thread": beat["thread"], "kind": kind_name, "tone": tone, "label": label,
            "words": words, "explain": kind.get("explain") if form != "bookkeeping" else None,
            "items": items if form == "bookkeeping" else [], "first": first["id"]}


def _thread_beat(thread: dict, by_id: dict, registry: dict) -> dict:
    opening = by_id[thread["opening"]]
    stops = [entry for entry in (by_id[name] for name in thread["entries"])
             if entry["kind"] == STOP_KIND and entry["id"] != opening["id"] and entry["subject"] in thread["heldUp"]]
    told = set(thread["cause"]) | {opening["id"]} | {entry["id"] for entry in stops}
    if thread["closing"] is not None:
        told.add(thread["closing"])
    said = _said(opening, registry)
    # Its parts, each a sentence of its own, for a view to set apart; and the same parts as one line.
    cause = _sentence(_said(by_id[thread["cause"][0]], registry)["head"]) if thread["cause"] else None
    held_up = f"It held up {journal_words.show(thread['heldUp'], 'list')}." if thread["heldUp"] else None
    ending = (_sentence("It ended when " + _lower(_said(by_id[thread["closing"]], registry)["head"])) if thread["closing"] is not None
              else "It still stands.")
    # The opening's own line: why it happened, in its own words, when nothing earlier caused it, and its detail otherwise.
    own = _sentence(said["line"]) if said["line"].strip() else None
    thread["told"] = {"cause": cause, "said": own, "heldUp": held_up, "ending": ending}
    line = [part for part in (cause, own, held_up, ending) if part]
    thread["words"] = {"head": "A setback: " + _lower(said["head"]), "line": " ".join(line)}
    ordered = sorted(told, key=lambda name: (_seconds(_instant(by_id[name])), name))
    return {"id": None, "chapter": None, "form": "thread", "altitudes": ["headline"], "at": thread["openedAt"], "entries": ordered,
            "thread": thread["id"], "kind": opening["kind"], "tone": "problem", "label": dict(TONES)["problem"], "words": dict(thread["words"]),
            "explain": _kind(opening, registry).get("explain"), "items": [], "first": opening["id"]}


# Words


def _word_chapter(chapter: dict, chapters: list[dict], index: int, by_id: dict, threads: list[dict], pieces: list[str],
                  landed: list[str], titles: dict, state: dict | None, closed: bool, registry: dict) -> None:
    held = [by_id[name] for name in chapter["entries"]]
    # A setback opened in an earlier chapter: still standing at this chapter's end, or ended inside it, with when.
    ends = _seconds(_instant(held[-1]))
    standing = []
    for name in chapter["standing"]:
        thread = next(t for t in threads if t["id"] == name)
        opened = _said(by_id[thread["opening"]], registry)["head"]
        if thread["closedAt"] is not None and _seconds(thread["closedAt"]) <= ends:
            standing.append({"thread": name, "text": opened, "until": thread["closedAt"]})
        else:
            standing.append({"thread": name, "text": _sentence("A setback still stands: " + _lower(opened)), "until": None})
    if chapter["form"] == "landing":
        merge = held[-1]
        chapter["pieceTitle"] = titles.get(merge["subject"])
        earlier = [entry for entry in by_id.values() if _seconds(_instant(entry)) <= _seconds(_instant(merge)) and entry["subject"] == merge["subject"]]
        picked = [entry for entry in earlier if entry["kind"] == ASSIGN_KIND]
        handed = [entry for entry in earlier if entry["kind"] == HANDOFF_KIND]
        parts = []
        if picked:
            parts.append(journal_words.duration(_between(_instant(min(picked, key=_key)), _instant(merge))) + " after it was picked up")
        rounds = max(handed, key=_key).get("data", {}).get("rounds") if handed else None
        if isinstance(rounds, int) and rounds > 0:
            parts.append("after " + journal_words.show(rounds, "count", "review round"))
        line = ", ".join(parts)
        chapter["words"] = {"head": _said(merge, registry)["head"] if _facts(merge) else f"{merge['subject']} landed", "line": _sentence(line[:1].upper() + line[1:]) if line else "",
                            "standing": standing}
        return
    if chapter["form"] == "start":
        assigned = [entry for entry in held if entry["kind"] == ASSIGN_KIND]
        lanes = list(dict.fromkeys(entry["lane"] for entry in assigned if entry["lane"]))
        subjects = list(dict.fromkeys(entry["subject"] for entry in assigned if entry["subject"]))
        line = ""
        if subjects:
            who = journal_words.show(lanes[0], "lane") if len(lanes) == 1 else f"{_number(len(lanes)).capitalize()} lanes" if lanes else "The lanes"
            line = f"{who} picked up {journal_words.show(subjects, 'list')}."
        chapter["words"] = {"head": "Getting started", "line": line, "standing": standing}
        return
    waiting = [piece for piece in pieces if piece not in landed]
    parts = []
    if closed:
        finish = [line for line in (state or {}).get("finish") or [] if isinstance(line, dict)] if isinstance(state, dict) else []
        met = [line for line in finish if line.get("met") is True]
        if finish and len(met) == len(finish):
            parts.append("Every finish line was met.")
        elif finish:
            parts.append(f"{_number(len(met)).capitalize()} of {_number(len(finish))} finish lines were met.")
        if waiting:
            parts.append(f"{journal_words.show(waiting, 'list')} never landed.")
        chapter["notLanded"] = waiting
    elif waiting:
        parts.append(f"{journal_words.show(waiting, 'list')} {'is' if len(waiting) == 1 else 'are'} still to land.")
    chapter["words"] = {"head": "Finished" if closed else "Finishing", "line": " ".join(parts), "standing": standing}


def _digest(entries: list[dict], threads: list[dict], pieces: list[str], landed: list[str], closed: bool) -> dict:
    started = _instant(entries[0]) if entries else None
    closing = [entry for entry in entries if entry["kind"] == CLOSE_KIND] if closed else []
    closed_at = _instant(closing[-1]) if closing else None
    took = _between(started, closed_at) if closed_at else None
    done = [thread for thread in threads if thread["state"] == "closed"]
    standing = [{"thread": thread["id"], "openedAt": thread["openedAt"]} for thread in threads if thread["state"] == "open"]
    decisions = sum(1 for entry in entries if entry["kind"] == DECISION_KIND and _facts(entry))
    cost = sum(thread["cost"] for thread in done)
    if not landed:
        head = "Nothing landed." if closed else "Nothing has landed yet."
    else:
        count = (f"{_number(len(landed)).capitalize()} of {_number(len(pieces))} pieces" if len(pieces) > len(landed)
                 else f"{_number(len(landed)).capitalize()} {'piece' if len(landed) == 1 else 'pieces'}")
        head = f"{count} landed " + (f"in {journal_words.duration(took)}." if took is not None else "so far.")
    line = []
    if done:
        line.append(f"{_number(len(done)).capitalize()} {'setback' if len(done) == 1 else 'setbacks'} cost {journal_words.duration(cost)}.")
    if standing:
        line.append(f"{_number(len(standing)).capitalize()} {'setback still stands' if len(standing) == 1 else 'setbacks still stand'}.")
    if not threads:
        line.append("No setbacks.")
    if decisions:
        line.append(f"The lead made {_number(decisions)} {'decision' if decisions == 1 else 'decisions'}.")
    return {"landed": len(landed), "pieces": len(pieces), "startedAt": started, "closedAt": closed_at, "took": took,
            "setbacks": {"closed": len(done), "cost": cost, "standing": standing}, "decisions": decisions,
            "words": {"head": head, "line": " ".join(line)}}


def _key(entry: dict) -> tuple:
    return (_seconds(_instant(entry)), entry["id"])


def _number(value: int) -> str:
    return NUMBERS[value] if 0 <= value < len(NUMBERS) else str(value)


def _lower(text: str) -> str:
    """A head as it reads mid-sentence: "The main line broke" as "the main line broke", a ticket left as it is."""
    return text[:1].lower() + text[1:] if len(text) > 1 and text[1].islower() else text


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?" else text + "."


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Print the story of the record document on stdin.").parse_args(argv)
    try:
        document = json.loads(sys.stdin.read())
        if not isinstance(document, dict) or not isinstance(document.get("journal"), list):
            raise NarratorError("the document is a record: an object with a journal")
        story = narrate(document["journal"], state=document.get("state"), plan=document.get("plan"))
    except (ValueError, KeyError, TypeError) as error:
        print(f"narrator: {error}", file=sys.stderr)
        return 2
    print(json.dumps(story, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
