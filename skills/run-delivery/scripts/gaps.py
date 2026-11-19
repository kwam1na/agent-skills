"""Gaps noted as they happen: what `note-gap` journals as `gap.noted`, and where the close-out routes each class.

A gap is friction met while delivering: in the workflow (the installed release's skills and tools), in the
repository, or in the host the agents run in. Each note is one `gap.noted` entry. A note that repeats an open gap,
by its text casefolded with whitespace collapsed or by `--same-as`, is journalled as an occurrence of it, never as a
new gap. Matching is exact: the listing `note-gap` prints is there so the agent picks `--same-as` itself.

A gap is open from its note until the close-out routes it: repo gaps become follow-ups, workflow gaps go into a
report beside the archive, grouped by release, and every gap is listed in the friction stub.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

KIND = "gap.noted"
CLASSES = ("workflow", "repo", "host")
FOLDER = "gaps"
HEADINGS = {"workflow": "Workflow", "repo": "Repo", "host": "Host"}


class Refused(Exception):
    """A note the command refuses, journalling nothing."""


def normalized(text: str) -> str:
    """The text a match compares: casefolded, with every run of whitespace one space, and none at either end."""
    return " ".join(text.split()).casefold()


def notes(journal: list[dict]) -> list[dict]:
    """Every gap.noted entry, gaps and occurrences, in journal order."""
    return sorted((e for e in journal if e.get("kind") == KIND), key=lambda e: e["id"])


def closes(journal: list[dict]) -> list[int]:
    return sorted(e["id"] for e in journal if e.get("kind") == "delivery.closed")


def since(journal: list[dict], reopening: tuple[str, ...]) -> int:
    """The id after which a gap is this close's to route, or open: the close before the one that closes the delivery
    now, or, while no close stands, the latest one. A delivery reopened by new work after a close starts afresh."""
    standing = None
    for entry in sorted(journal, key=lambda e: e["id"]):
        if entry.get("kind") == "delivery.closed":
            standing = entry["id"]
        elif entry.get("kind") in reopening:
            standing = None
    earlier = [n for n in closes(journal) if standing is None or n < standing]
    return earlier[-1] if earlier else 0


def window(journal: list[dict], reopening: tuple[str, ...]) -> list[dict]:
    """The notes this close routes, or that are open now."""
    after = since(journal, reopening)
    return [e for e in notes(journal) if e["id"] > after]


def gaps(journal: list[dict], reopening: tuple[str, ...]) -> list[dict]:
    """The gaps in the window, each with its occurrences: {entry, count, occurrences}."""
    found = window(journal, reopening)
    roots = {e["id"]: {"entry": e, "occurrences": []} for e in found if "occurrenceOf" not in e["data"]}
    for entry in found:
        root = entry["data"].get("occurrenceOf")
        if root in roots:
            roots[root]["occurrences"].append(entry)
    return [{**gap, "count": 1 + len(gap["occurrences"])} for gap in roots.values()]


def matching(found: list[dict], cls: str, release: str | None) -> list[dict]:
    """The open gaps a new note of this class may repeat: a workflow gap's on the same release, any other class's on
    any release, since those match on class alone."""
    return [gap for gap in found if gap["entry"]["data"]["class"] == cls
            and (cls != "workflow" or gap["entry"]["data"].get("release") == release)]


def occurrence_of(journal: list[dict], reopening: tuple[str, ...], cls: str, text: str, release: str | None,
                  same_as: int | None) -> int | None:
    """The gap a new note repeats, or None for a new gap. `--same-as N` names it outright, and must name a gap.noted
    of the same class (an occurrence names its gap); otherwise an open gap whose text matches exactly."""
    if same_as is not None:
        named = next((e for e in journal if e.get("id") == same_as), None)
        if named is None or named.get("kind") != KIND:
            raise Refused(f"--same-as {same_as}: journal entry {same_as} is "
                          f"{'not in the record' if named is None else 'a ' + str(named.get('kind'))}, not a {KIND}")
        if named["data"]["class"] != cls:
            raise Refused(f"--same-as {same_as}: journal entry {same_as} is a {named['data']['class']} gap, "
                          f"not a {cls} gap")
        return named["data"].get("occurrenceOf", named["id"])
    wanted = normalized(text)
    hit = next((gap for gap in matching(gaps(journal, reopening), cls, release)
                if normalized(gap["entry"]["data"]["text"]) == wanted), None)
    return None if hit is None else hit["entry"]["id"]


def listing(journal: list[dict], reopening: tuple[str, ...], cls: str, release: str | None) -> list[str]:
    """The open gaps a note of this class may repeat, one line each with its journal id, for `--same-as`."""
    return [f"  {gap['entry']['id']}: {gap['entry']['data']['text']}"
            + (f" (noted {gap['count']} times)" if gap["count"] > 1 else "")
            for gap in matching(gaps(journal, reopening), cls, release)]


def next_id(journal: list[dict]) -> int:
    return max((e["id"] for e in journal), default=0) + 1


def copy_evidence(record: Path, entry_id: int, source: Path) -> dict:
    """Copy the evidence file into the record's gaps/ folder under the entry's id; its path relative to the record,
    and its sha256. A folder that already holds this id is another note's, so the note is refused."""
    if not source.is_file():
        raise Refused(f"--evidence {source}: no such file")
    folder = record / FOLDER / str(entry_id)
    if folder.exists():
        raise Refused(f"{folder} already exists: another note is being written; run it again")
    folder.mkdir(parents=True)
    target = folder / source.name
    shutil.copyfile(source, target)
    return {"path": target.relative_to(record).as_posix(), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def drop_evidence(record: Path, entry_id: int) -> None:
    """Remove a copy whose note was never journalled."""
    shutil.rmtree(record / FOLDER / str(entry_id), ignore_errors=True)


def followups(found: list[dict], delivery: str) -> list[dict]:
    """One follow-up per repo gap, as a deferred finding's: its story the gap's subject (the delivery's key when it
    named none), its finding gap-<journal id>, its title the gap's text."""
    return [{"story": gap["entry"].get("subject") or delivery, "finding": f"gap-{gap['entry']['id']}",
             "title": gap["entry"]["data"]["text"]}
            for gap in found if gap["entry"]["data"]["class"] == "repo"]


def workflow_report(found: list[dict], delivery: str, archive: Path, state_name: str) -> str | None:
    """`<archive>.workflow-gaps.md`: the workflow gaps grouped by release, each with its text, why, command,
    occurrence count and evidence path in the archive. None when there are none."""
    workflow = [gap for gap in found if gap["entry"]["data"]["class"] == "workflow"]
    if not workflow:
        return None
    lines = [f"# {delivery}: workflow gaps", "",
             "Gaps in the workflow this delivery ran, noted as they happened, for the workflow's maintainers. "
             "Sending this report anywhere is the adopter's call.", ""]
    releases: dict[str, list[dict]] = {}
    for gap in workflow:
        releases.setdefault(gap["entry"]["data"].get("release") or "release not recorded", []).append(gap)
    for release, listed in releases.items():
        generation = listed[0]["entry"]["data"].get("generation")
        lines += [f"## {release}" + (f" (generation {generation})" if generation else ""), ""]
        for gap in listed:
            data = gap["entry"]["data"]
            lines += [f"### {data['text']}", "", f"- Journal entry: {gap['entry']['id']}", f"- Why: {data['why']}",
                      f"- Noted: {gap['count']} {'time' if gap['count'] == 1 else 'times'}"]
            if data.get("command"):
                lines.append(f"- Command: `{data['command']}`")
            if data.get("evidence"):
                lines.append(f"- Evidence: {archive / state_name / data['evidence']['path']} "
                             f"(sha256 {data['evidence']['sha256']})")
            lines.append("")
    return "\n".join(lines)


def friction_section(found: list[dict]) -> list[str]:
    """The friction stub's "Gaps noted": every gap by class, for compound-learning."""
    lines = ["## Gaps noted", ""]
    if not found:
        return lines + ["- (none)", ""]
    for cls in CLASSES:
        listed = [gap for gap in found if gap["entry"]["data"]["class"] == cls]
        if not listed:
            continue
        lines += [f"### {HEADINGS[cls]}", ""]
        for gap in listed:
            data = gap["entry"]["data"]
            lines.append(f"- {data['text']} (journal entry {gap['entry']['id']}"
                         + (f", noted {gap['count']} times" if gap["count"] > 1 else "") + f"): {data['why']}")
        lines.append("")
    return lines
