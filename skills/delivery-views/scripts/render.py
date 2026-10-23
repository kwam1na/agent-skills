#!/usr/bin/env python3
"""The delivery views' pages, printed from the delivery record on stdin.

    python3 -B render.py line-map --config .agents/delivery-views.json --binding .agents/orchestration.json
    python3 -B render.py history --config .agents/delivery-views.json --binding .agents/orchestration.json

``line-map`` prints the live line map: one page whose style and program are
inline, loading only its two typefaces from Google Fonts. The page carries the
live opt-in marker, so the state tool's live server serves it at
``/view/<output>`` and the page follows every record write by itself; opened
as a file, it shows the record as it was rendered.

``history`` prints the delivery history: every run the configured ``runs``
command lists, read with ``runs show``, and every export in the configured
``exports`` directory, newest first, each opening the line map's run summary.
A run in both is listed once, from the product. It also opts in to the live
stream, and links back to the binding's line map.

Each page's script and styles are the skill's ``assets/`` files, inlined as they are.

A stop whose queue or planned element names a ``runId`` also shows that run's
summary, read through the configured ``runs`` command and cached per run in the
state directory. The summaries, and the links the configuration allows, are
embedded beside the record; a live page reads both again from the re-rendered
view after each push. Every value reaches the page as a text node or an
attribute set through the DOM, and the embedded blocks are escaped so no text
in them can close their script elements.

Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import fnmatch
import hashlib
import importlib.util
import json
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

import views_common  # noqa: E402
from views_common import ConfigError, RunUnavailable  # noqa: E402

# Delivery views ship over core, which owns the native observation grammar.
_state_spec = importlib.util.spec_from_file_location(
    "delivery_review_state", Path(__file__).resolve().parents[2] / "orchestrate-work" / "scripts" / "delivery_state.py")
review_state = importlib.util.module_from_spec(_state_spec)
_state_spec.loader.exec_module(review_state)


SCRIPT = "render.py"
ASSETS = Path(__file__).resolve().parent.parent / "assets"
DEFAULT_DEADLINE_SECONDS = 20
PAGE_BUDGET_BYTES = 4 * 1024 * 1024
TEXT_LIMIT_BYTES = 64 * 1024
SEVERITIES = ("P0", "P1", "P2", "P3")
ROUNDS_KEPT = 12
# How many runs are read at once; every read still shares the render's one deadline.
READ_WORKERS = 6
NOTES_KEPT = 5
FIELDS_KEPT = 4
VALUE_LENGTH = 90
REPORT_NOTES_KEPT = 50
SUMMARY_LENGTH = 280
ISSUE_LENGTH = 64
COMMIT = re.compile(r"^[0-9a-f]{40}$")
# Fields that identify or qualify an observation rather than say what it is. "Elapsed since start"
# is the view's own clock, so a cached summary would keep showing the time it was read at.
QUIET_FIELDS = frozenset({"Attempt", "Authority", "Candidate", "Cost", "Elapsed since start", "Freshness", "History",
                          "Last observed", "Lifecycle history", "Owner", "Phase"})
# The report's own states, in the run's words, for a finding the run observed no state for.
RAISED_STATES = {"open": "unresolved", "deferred": "deferred", "dropped": "resolved"}
CONTROL_RUN = re.compile("[\u0000-\u001f\u007f]+")
SAFE_INTEGER = 2 ** 53 - 1


# Values


def _is_object(value: object) -> bool:
    return isinstance(value, dict)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value != "" else None


def _count(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _number_equal(left: object, right: object) -> bool:
    """JSON number equality, which a boolean never meets."""
    return _count(left) is not None and _count(right) is not None and left == right


def _clip(value: str) -> str:
    return value[:VALUE_LENGTH - 1] + "\u2026" if len(value) > VALUE_LENGTH else value


def _plain(value: str) -> str:
    return CONTROL_RUN.sub(" ", value)


def _milliseconds(value: object) -> float | None:
    """A timestamp's epoch milliseconds, or None when it does not read as one."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00") if value.endswith("Z") else value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() * 1000


def embed(value: object) -> str:
    """The value as JSON that is safe inside a ``<script>`` element."""
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


# Run summaries


def findings_of(value: object) -> dict | None:
    """Findings by severity; None unless at least one severity is a number."""
    if not _is_object(value) or not any(_count(value.get(severity)) is not None for severity in SEVERITIES):
        return None
    return {severity: (_count(value.get(severity)) if _count(value.get(severity)) is not None else 0)
            for severity in SEVERITIES}


def outcome_word(value: object) -> str | None:
    """A review outcome in the state journal's words; anything unknown is kept."""
    outcome = _text(value)
    if outcome is None:
        return None
    if re.fullmatch(r"approve|approved|aligned", outcome, re.IGNORECASE):
        return "approve"
    if re.fullmatch(r"changes|changes[ _-]requested|request[ _-]changes", outcome, re.IGNORECASE):
        return "changes"
    return outcome


def _view_items(view: dict, section_id: str) -> list[tuple[str, dict]]:
    """A run view section's items as (label, fields), the first value of each field label winning."""
    sections = [entry for entry in view.get("sections", []) if _is_object(entry)] if isinstance(view.get("sections"), list) else []
    section = next((entry for entry in sections if entry.get("id") == section_id), None)
    items = section.get("items") if section is not None else None
    result = []
    for item in (entry for entry in items if _is_object(entry)) if isinstance(items, list) else ():
        fields: dict[str, str] = {}
        for field in item.get("fields") if isinstance(item.get("fields"), list) else ():
            if not _is_object(field):
                continue
            label, value = _text(field.get("label")), _text(field.get("value"))
            if label and value and label not in fields:
                fields[label] = value
        result.append((_text(item.get("label")) or "", fields))
    return result


def _note(label: str, fields: dict) -> dict:
    shown = [(name, value) for name, value in fields.items() if name not in QUIET_FIELDS][:FIELDS_KEPT]
    return {"label": _clip(label), "detail": " · ".join(f"{_clip(name)}: {_clip(value)}" for name, value in shown)}


def _round_number(value: object) -> int | None:
    if isinstance(value, str):
        parsed = int(value) if re.fullmatch(r"[0-9]+", value) else None
    else:
        parsed = _count(value)
    if parsed is None or parsed != int(parsed) or abs(parsed) > SAFE_INTEGER:
        return None
    return int(parsed)


def _observation(fields: dict) -> dict:
    try:
        parsed = json.loads(fields.get("Observation", ""))
    except (json.JSONDecodeError, RecursionError):
        return {}
    return parsed if _is_object(parsed) else {}


def summarize_run_view(view: object, listed: dict) -> dict | None:
    """A run view's summary for the line map, or None when the value is not a run view."""
    if not _is_object(view) or view.get("spec") != "run-view/1":
        return None
    rounds: dict[int, dict] = {}

    def round_for(number: int) -> dict:
        if number not in rounds:
            rounds[number] = {"round": number, "at": None, "outcome": None, "findings": None, "verdicts": {}}
        return rounds[number]

    for label, fields in _view_items(view, "reviews"):
        number = _round_number(fields.get("Round"))
        if number is None:
            continue
        entry = round_for(number)
        verdict = fields.get("Verdict")
        if label != "Declared review round" and label and verdict:
            entry["verdicts"][_clip(label)] = _clip(outcome_word(verdict) or verdict)
    for label, fields in _view_items(view, "evidence"):
        if label != "review.round.closed":
            continue
        closed = _observation(fields)
        number = _round_number(closed.get("round"))
        if number is None:
            continue
        entry = round_for(number)
        entry["at"] = fields.get("Reported at")
        entry["outcome"] = outcome_word(closed.get("outcome"))
        entry["findings"] = findings_of(closed.get("findings"))
    # Work still running is the latest activity as much as the attempts already finished.
    timed = [(label, fields, fields.get("Last observed")) for label, fields in
             _view_items(view, "work") + _view_items(view, "activity-history")]
    timed = [entry for entry in timed if entry[2] is not None and _milliseconds(entry[2]) is not None]
    timed.sort(key=lambda entry: _milliseconds(entry[2]))
    latest = timed[-1] if timed else None
    ordered = [rounds[number] for number in sorted(rounds)]
    activity = None
    if latest is not None:
        label, fields, at = latest
        words = " · ".join(part for part in (fields.get("Phase"), fields.get("State")) if part)
        activity = {"label": _clip(words or label), "at": at}
    return {
        "runId": _text(listed.get("runId")),
        "ticket": None,
        "open": listed.get("open") if isinstance(listed.get("open"), bool) else None,
        "status": _text(listed.get("status")),
        "startedAt": None,
        "lastAt": latest[2] if latest is not None else None,
        "durationSeconds": _count(listed.get("durationSeconds")),
        "rounds": ordered[-1]["round"] if ordered else None,
        "findings": None,
        "roundDetail": ordered[-ROUNDS_KEPT:],
        "waits": [_note(label, fields) for label, fields in _view_items(view, "waiting")[:NOTES_KEPT]],
        "work": [_note(fields.get("Phase") or label, fields) for label, fields in _view_items(view, "work")[:NOTES_KEPT]],
        "activity": activity,
    }


def _reports_section(view: object) -> list[dict]:
    if not _is_object(view) or not isinstance(view.get("sections"), list):
        return []
    section = next((entry for entry in view["sections"] if _is_object(entry) and entry.get("id") == "reports"), None)
    items = section.get("items") if section is not None else None
    return [item for item in items if _is_object(item)] if isinstance(items, list) else []


def _available(item: dict) -> bool:
    fields = item.get("fields") if isinstance(item.get("fields"), list) else []
    availability = next((field for field in fields if _is_object(field) and field.get("label") == "Availability"), None)
    return availability is not None and availability.get("value") == "referenced"


def report_references(view: object) -> dict[int, list[str | None]]:
    """A run view's round report references by round: each is the attachment to read, or None when the
    run marks it unavailable. Brief and result references are never round reports."""
    references: dict[int, list[str | None]] = {}
    for item in _reports_section(view):
        matched = re.fullmatch(r"report-round-([0-9]+)", _text(item.get("id")) or "")
        if not matched:
            continue
        artifact_id = _text(item.get("artifactId")) if _available(item) else None
        references.setdefault(int(matched.group(1)), []).append(artifact_id)
    return references


def _referenced_artifacts(view: object) -> set[str]:
    """Every attachment the run view marks referenced, whatever its report."""
    return {item["artifactId"] for item in _reports_section(view) if _text(item.get("artifactId")) and _available(item)}


def finding_states(view: object) -> dict[str, str]:
    """Each finding's latest state, from the run view's finding history in the order the run holds it."""
    states: dict[str, str] = {}
    if _is_object(view):
        for label, fields in _view_items(view, "finding-history"):
            if label and fields.get("State"):
                states[label] = fields["State"]
    return states


def _reading_bytes(reading: object) -> bytes | None:
    """A ``runs artifact --json`` reading's bytes, when they match their digest."""
    if not _is_object(reading) or not _is_object(reading.get("metadata")) or not isinstance(reading.get("base64"), str):
        return None
    try:
        data = base64.b64decode(reading["base64"])
    except (binascii.Error, ValueError):
        return None
    return data if hashlib.sha256(data).hexdigest() == reading["metadata"].get("digest") else None


def _report_from(reading: object, run_id: str | None, number: int, specs: list[str]) -> dict | None:
    """The reading as this round's report: one of the configured specs, naming this run, this round and the
    capture's own round id and candidate tree. Anything else is None."""
    data = _reading_bytes(reading)
    if data is None:
        return None
    metadata = reading["metadata"]
    try:
        report = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    if (not _is_object(report) or report.get("spec") not in specs or report.get("runId") != run_id
            or not _number_equal(report.get("round"), number) or not _number_equal(metadata.get("round"), number)
            or _text(report.get("roundId")) is None or report.get("roundId") != metadata.get("roundId")
            or _text(report.get("candidateTreeSha")) is None
            or report.get("candidateTreeSha") != metadata.get("candidateTreeSha")):
        return None
    return report


def _issue(value: object) -> str | None:
    issue = _text(value)
    return issue if issue is not None and len(issue) <= ISSUE_LENGTH and not CONTROL_RUN.search(issue) else None


def _raised(report: dict, states: dict[str, str]) -> list[dict]:
    raised = []
    for note in report.get("raised") if isinstance(report.get("raised"), list) else ():
        if not _is_object(note):
            continue
        identifier, lens = _text(note.get("id")), _text(note.get("lens"))
        severity, said = _text(note.get("severity")), _text(note.get("summary"))
        if not (identifier and lens and severity and said):
            continue
        state = states.get(identifier) or RAISED_STATES.get(_text(note.get("state")) or "") or "unresolved"
        raised.append({"id": _clip(identifier), "lens": _clip(lens), "severity": severity,
                       "summary": _plain(said)[:SUMMARY_LENGTH], "state": _clip(state),
                       "deferredIssue": _issue(note.get("deferredIssue"))})
    return raised[:REPORT_NOTES_KEPT]


def _settled(report: dict) -> list[dict]:
    settled = []
    for note in report.get("resolved") if isinstance(report.get("resolved"), list) else ():
        if not _is_object(note):
            continue
        identifier, state, fix = _text(note.get("id")), _text(note.get("state")), _text(note.get("fixCommit"))
        if identifier and state:
            settled.append({"id": _clip(identifier), "state": _clip(state),
                            "fixCommit": fix if fix and COMMIT.fullmatch(fix) else None,
                            "deferredIssue": _issue(note.get("deferredIssue"))})
    return settled[:REPORT_NOTES_KEPT]


def _brief_text(data: bytes) -> dict:
    """A brief or result as page text: at most 64 KiB, cut at a character boundary, shortened beyond."""
    shortened = len(data) > TEXT_LIMIT_BYTES
    if shortened:
        cut = TEXT_LIMIT_BYTES
        while cut > TEXT_LIMIT_BYTES - 3 and data[cut] & 0xC0 == 0x80:
            cut -= 1
        data = data[:cut]
    return {"text": data.decode("utf-8", "replace"), "shortened": shortened}


def with_round_reports(summary: dict, view: object, readings: dict[str, object], specs: list[str]) -> dict:
    """The summary with each round's report attached. ``readings`` holds each attachment's
    ``runs artifact --json`` output, or None where the read failed. Of a round's references that read
    as its report, the one whose spec comes first in ``specs`` wins; a round whose references all fail
    is marked ``detailsUnavailable``; a round with no reference is left as it is. A report's ``briefs``
    name the attachments that hold what each lens was told and returned; each becomes its text, None
    when not recorded, or ``{"unavailable": true}`` when it cannot be read."""
    references = report_references(view)
    states = finding_states(view)
    referenced = _referenced_artifacts(view)
    detail = []
    for entry in summary["roundDetail"]:
        number = entry.get("round")
        found = references.get(number) if number is not None else None
        if not found:
            detail.append(entry)
            continue
        reports = [_report_from(readings.get(artifact), summary["runId"], number, specs) for artifact in found if artifact]
        reports = [report for report in reports if report is not None]
        if not reports:
            detail.append({**entry, "detailsUnavailable": True})
            continue
        report = min(reports, key=lambda candidate: specs.index(candidate["spec"]))
        attached = {**entry, "raised": _raised(report, states), "settled": _settled(report)}
        if _is_object(report.get("briefs")):
            briefs = {}
            for lens, pair in report["briefs"].items():
                pair = pair if _is_object(pair) else {}
                briefs[lens] = {}
                for part in ("brief", "result"):
                    artifact = _text(pair.get(part))
                    if artifact is None:
                        briefs[lens][part] = None
                        continue
                    data = _reading_bytes(readings.get(artifact)) if artifact in referenced else None
                    briefs[lens][part] = _brief_text(data) if data is not None else {"unavailable": True}
            attached["briefs"] = briefs
        detail.append(attached)
    return {**summary, "roundDetail": detail}


def _artifact_ids(view: object) -> list[str]:
    """The attachments a round needs: its report references, in order, once each."""
    ids = []
    for found in report_references(view).values():
        for artifact in found:
            if artifact and artifact not in ids:
                ids.append(artifact)
    return ids


def _brief_ids(report: dict) -> list[str]:
    briefs = report.get("briefs")
    ids = []
    for pair in briefs.values() if _is_object(briefs) else ():
        for part in ("brief", "result"):
            artifact = _text(pair.get(part)) if _is_object(pair) else None
            if artifact and artifact not in ids:
                ids.append(artifact)
    return ids


# Collecting runs


def named_runs(document: object) -> list[str]:
    """The run ids the record's queue and planned elements name, once each, in order."""
    state = document.get("state") if _is_object(document) and _is_object(document.get("state")) else {}
    named = []
    for key in ("queue", "planned"):
        for item in state.get(key) if isinstance(state.get(key), list) else ():
            run_id = item.get("runId") if _is_object(item) else None
            if isinstance(run_id, str) and run_id and run_id not in named:
                named.append(run_id)
    return named


class Product:
    """The configured ``runs`` command, bounded per command and by one deadline for the whole render."""

    def __init__(self, argv: list[str], timeout: float, scrub: list[str], cwd: Path, deadline: float):
        self.argv, self.timeout, self.scrub, self.cwd, self.deadline = argv, timeout, scrub, cwd, deadline
        self.late = False

    def __call__(self, *args: str) -> object | None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.late = True
            return None
        limit = min(self.timeout, remaining)
        try:
            return views_common.run_json([*self.argv, *args], timeout=limit, scrub=self.scrub, cwd=self.cwd)
        except RunUnavailable:
            # A run with any failed read is read again next render, not kept with that failure.
            self.late = True
            return None


def _unavailable(run_id: str) -> dict:
    return {"runId": run_id, "unavailable": True}


def _read_run(product: Product, run_id: str, row: dict, specs: list[str]) -> dict | None:
    view = product("runs", "view", run_id, "--json")
    summary = summarize_run_view(view, row)
    if summary is None:
        return None
    readings = {artifact: product("runs", "artifact", run_id, artifact, "--json")
                for artifact in _artifact_ids(view)}
    # Each report's briefs and results are read once, after the reports that name them.
    for number, found in report_references(view).items():
        for artifact in found:
            report = _report_from(readings.get(artifact), summary["runId"], number, specs) if artifact else None
            for brief in _brief_ids(report) if report is not None else ():
                if brief not in readings and brief in _referenced_artifacts(view):
                    readings[brief] = product("runs", "artifact", run_id, brief, "--json")
    return with_round_reports(summary, view, readings, specs)


def native_review_summaries(document: dict) -> dict:
    """Project verified state observations into the existing cards; never invent a run."""
    observed = document.get("reviewEvidence", {})
    summaries = {}
    for subject, observation in observed.items() if isinstance(observed, dict) else ():
        if not isinstance(observation, dict) or observation.get("unavailable"):
            summaries[subject] = {"unavailable": True}
            continue
        try:
            if not isinstance(observation.get("digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", observation["digest"]):
                raise ValueError("invalid review digest")
            review_state.validate_review_outcomes(observation.get("outcomes"), subject, observation.get("candidateRef"))
            if len(embed(observation).encode("utf-8")) > review_state.REVIEW_ARTIFACT_LIMIT:
                raise ValueError("oversized review observation")
            events = [entry for entry in document.get("journal", []) if entry.get("subject") == subject
                      and entry.get("kind") in ("review.round", "review.evidence")]
            evidence = next((entry for entry in reversed(events) if entry["kind"] == "review.evidence"), None)
            boundary = max((entry.get("id", 0) for entry in events), default=0)
            if evidence is not None and (evidence.get("reviewArtifact") != {
                    "digest": observation["digest"], "candidateRef": observation["candidateRef"]}
                    or evidence.get("id", 0) != boundary):
                raise ValueError("review observation is stale")
        except (review_state.StateError, ValueError, TypeError, UnicodeError, RecursionError):
            summaries[subject] = {"unavailable": True}
            continue
        rounds, states = [], {}
        for outcome in observation.get("outcomes", []):
            raised, settled, verdicts = [], [], {}
            candidate, complete = None, True
            for entry in outcome["entries"]:
                lens, binding = entry["lens"], entry["binding"]
                candidate = binding["candidateRef"]
                if entry["state"] != "obtained":
                    verdicts[lens], complete = "unavailable", False
                    continue
                result = entry["result"]
                verdicts[lens] = outcome_word(result["outcome"])
                for finding in result["findings"]:
                    state = "deferred" if finding.get("state") == "deferred" else "unresolved"
                    states[finding["id"]] = state
                    raised.append({"id": finding["id"], "lens": lens, "severity": finding["severity"],
                                   "summary": _plain(finding["summary"])[:SUMMARY_LENGTH], "state": state,
                                   "deferredIssue": _issue(finding.get("deferredIssue"))})
                for closure in result.get("resolvedFindings", result.get("resolutions", [])):
                    state = {"closed": "fixed", "fixed": "fixed", "withdrawn": "dropped", "dropped": "dropped",
                             "deferred": "deferred", "open": "open"}[closure["state"]]
                    states[closure["id"]] = "resolved" if state in ("fixed", "dropped") else "deferred" if state == "deferred" else "unresolved"
                    settled.append({"id": closure["id"], "lens": lens, "state": state,
                                    "fixCommit": closure.get("fixCommit"), "deferredIssue": _issue(closure.get("deferredIssue")),
                                    "resolution": _plain(closure.get("resolution", closure.get("reason", "")))[:2048]})
            counts = {severity: sum(note["severity"] == severity for note in raised) for severity in SEVERITIES}
            approved = complete and outcome.get("outcome") == "aligned" and all(word == "approve" for word in verdicts.values())
            rounds.append({"round": outcome["round"], "candidateRef": candidate, "at": None,
                           "outcome": "approve" if approved else "changes" if "changes" in verdicts.values() else None,
                           "findings": counts, "verdicts": verdicts, "raised": raised, "settled": settled,
                           "detailsUnavailable": not complete})
        for row in rounds:
            for raised in row["raised"]:
                raised["state"] = states[raised["id"]]
        summaries[subject] = {"observationKey": subject + ":" + observation["digest"],
                              "digest": observation["digest"],
                              "journalId": boundary,
                              "candidateRef": observation["candidateRef"], "roundDetail": rounds,
                              "observational": True, "open": False}
    return summaries


def collect_runs(document: object, config: dict, root: Path, cache_path: Path, deadline: float) -> dict:
    """Each named run's summary, or its unavailability, keyed by run id. A run is read only when the
    inventory shows it changed since its cached summary; a run read past the deadline stays uncached."""
    named = named_runs(document)
    argv = views_common.runs_argv(config, root)
    if not named or argv is None:
        return {}
    product = Product(argv, config["runs"]["timeoutSeconds"], config["scrubEnv"], root, deadline)
    specs = config["findings"]["reportSpecs"]
    inventory = product("runs", "list", "--json")
    if not _is_object(inventory) or not isinstance(inventory.get("runs"), list):
        return {run_id: _unavailable(run_id) for run_id in named}
    rows = {row["runId"]: row for row in inventory["runs"] if _is_object(row) and isinstance(row.get("runId"), str)}
    cached = views_common.read_runs_cache(cache_path)
    kept, runs, unread = {}, {}, []
    for run_id in named:
        row = rows.get(run_id)
        if row is None:
            runs[run_id] = _unavailable(run_id)
            continue
        size = row.get("bytes") if _count(row.get("bytes")) is not None else None
        hit = cached.get(run_id)
        # A summary is reused only while the run is unchanged and the report specs it was read with still apply.
        if (size is not None and _is_object(hit) and _number_equal(hit.get("bytes"), size) and hit.get("reportSpecs") == specs
                and _is_object(hit.get("summary"))):
            # An unchanged run keeps its summary; only the inventory's own facts move with time.
            summary = {**hit["summary"], "open": row.get("open") if isinstance(row.get("open"), bool) else None,
                       "status": _text(row.get("status")), "durationSeconds": _count(row.get("durationSeconds"))}
            kept[run_id] = {"bytes": size, "reportSpecs": specs, "summary": summary}
            runs[run_id] = summary
            continue
        unread.append((run_id, row, size))

    def read(item: tuple) -> tuple:
        run_id, row, _ = item
        # Each run reads through its own product, so one run's late read never marks another's.
        own = Product(argv, config["runs"]["timeoutSeconds"], config["scrubEnv"], root, deadline)
        return _read_run(own, run_id, row, specs), own.late

    with ThreadPoolExecutor(max_workers=READ_WORKERS) as pool:
        for (run_id, _, size), (summary, late) in zip(unread, pool.map(read, unread)):
            if summary is None:
                runs[run_id] = _unavailable(run_id)
                continue
            if size is not None and not late:
                kept[run_id] = {"bytes": size, "reportSpecs": specs, "summary": summary}
            runs[run_id] = summary
    views_common.write_runs_cache(cache_path, kept)
    return {run_id: runs[run_id] for run_id in named}


def _texts(runs: dict, named: list[str]):
    """Every brief and result text slot, newest round first: (run id, round index, lens, part)."""
    for run_id in reversed(named):
        summary = runs.get(run_id)
        detail = summary.get("roundDetail") if _is_object(summary) and not summary.get("unavailable") else None
        for index in reversed(range(len(detail))) if isinstance(detail, list) else ():
            briefs = detail[index].get("briefs") if _is_object(detail[index]) else None
            for lens in briefs if _is_object(briefs) else ():
                for part in ("brief", "result"):
                    if _is_object(briefs[lens]) and _is_object(briefs[lens].get(part)) and "text" in briefs[lens][part]:
                        yield run_id, index, lens, part


def within_budget(runs: dict, named: list[str], budget: int = PAGE_BUDGET_BYTES) -> dict:
    """The runs as the page embeds them, within the page budget: run summaries first, the oldest stops'
    runs unavailable when they alone are over it; then brief and result texts from the newest round
    backwards, each one past the budget unavailable."""
    runs = copy.deepcopy(runs)
    texts = {}
    for slot in list(_texts(runs, named)):
        run_id, index, lens, part = slot
        holder = runs[run_id]["roundDetail"][index]["briefs"][lens]
        texts[slot] = holder[part]
        holder[part] = {"unavailable": True}
    size = len(embed(runs).encode("utf-8"))
    for run_id in named:
        if size <= budget:
            break
        if run_id in runs and not runs[run_id].get("unavailable"):
            # Each run is measured once; swapping it for its unavailability changes the page by the difference.
            unavailable = _unavailable(runs[run_id]["runId"]) if runs[run_id].get("runId") else {"unavailable": True}
            size -= len(embed(runs[run_id]).encode("utf-8")) - len(embed(unavailable).encode("utf-8"))
            runs[run_id] = unavailable
    for slot in _texts_order(texts, named):
        run_id, index, lens, part = slot
        if runs.get(run_id, {}).get("unavailable"):
            continue
        grown = len(embed(texts[slot]).encode("utf-8")) - len(embed({"unavailable": True}).encode("utf-8"))
        if size + grown <= budget:
            runs[run_id]["roundDetail"][index]["briefs"][lens][part] = texts[slot]
            size += grown
    return runs


def _texts_order(texts: dict, named: list[str]) -> list:
    rank = {run_id: position for position, run_id in enumerate(named)}
    return sorted(texts, key=lambda slot: (-rank.get(slot[0], 0), -slot[1]))


# Links


def page_links(document: object, runs: dict, config: dict) -> dict:
    """The links the page may open, worked out here so the page holds no link rules of its own:
    ticket ids by key, change requests by value and fix commits by hash."""
    state = document.get("state") if _is_object(document) and _is_object(document.get("state")) else {}
    keys, changes, commits = [], [], []
    meta = state.get("meta") if _is_object(state.get("meta")) else {}
    keys.append(meta.get("trackerParent"))
    for key in ("queue", "planned"):
        for item in state.get(key) if isinstance(state.get(key), list) else ():
            if _is_object(item):
                keys.append(item.get("subject"))
                changes.append(item.get("changeRequest"))
    for item in state.get("followups") if isinstance(state.get("followups"), list) else ():
        if _is_object(item):
            keys.extend((item.get("reference"), item.get("id")))
    journal = document.get("journal") if _is_object(document) else None
    for entry in journal if isinstance(journal, list) else ():
        if _is_object(entry):
            keys.append(entry.get("subject"))
    for summary in runs.values():
        for entry in summary.get("roundDetail", []) if _is_object(summary) else ():
            for note in entry.get("settled", []) if _is_object(entry) else ():
                if _is_object(note):
                    commits.append(note.get("fixCommit"))
    forges = config["forges"]
    issues = {key: href for key in keys if isinstance(key, str)
              for href in [views_common.issue_link(key, config["tracker"])] if href}
    pulls = {}
    for value in changes:
        if isinstance(value, str) and value not in pulls:
            identity = views_common.change_request_identity(value, forges)
            if identity:
                pulls[value] = {"label": "#" + identity["number"], "href": identity["href"],
                                "repository": identity["repository"]}
    fixes = {sha: href for sha in commits if isinstance(sha, str)
             for href in [views_common.commit_link(sha, forges[0] if forges else None)] if href}
    return {"issues": issues, "changes": pulls, "commits": fixes}


def _renderer_output(binding: dict, subcommand: str) -> str | None:
    """The output of the binding's ``render.py <subcommand>`` renderer, when it declares one."""
    state = binding.get("state") if _is_object(binding) else None
    for renderer in state.get("renderers", []) if _is_object(state) and isinstance(state.get("renderers"), list) else ():
        argv = renderer.get("argv") if _is_object(renderer) else None
        if (isinstance(argv, list) and subcommand in argv
                and any(isinstance(part, str) and PurePosixPath(part).name == SCRIPT for part in argv)
                and isinstance(renderer.get("output"), str)):
            return renderer["output"]
    return None


# The history


def _unknown_summary(run_id: str | None) -> dict:
    return {"runId": run_id, "ticket": None, "open": None, "status": None, "startedAt": None, "lastAt": None,
            "durationSeconds": None, "rounds": None, "findings": None, "roundDetail": []}


def summarize_run_export(exported: object) -> dict | None:
    """A run export's summary for the history, or None when the value is not a run export."""
    if not _is_object(exported) or not isinstance(exported.get("spec"), str) or not exported["spec"].startswith("delivery-run-export/"):
        return None
    summary = exported.get("summary") if _is_object(exported.get("summary")) else {}
    readout = exported.get("readout") if _is_object(exported.get("readout")) else {}
    events = [event for event in exported.get("events", []) if _is_object(event)] if isinstance(exported.get("events"), list) else []
    detail = []
    for event in events:
        if event.get("kind") != "review.round.closed":
            continue
        payload = event.get("payload") if _is_object(event.get("payload")) else {}
        detail.append({"round": _count(payload.get("round")), "at": _text(event.get("at")),
                       "outcome": outcome_word(payload.get("outcome")), "findings": findings_of(payload.get("findings"))})
    return {
        "runId": _text(exported.get("runId")),
        "ticket": _text(summary.get("ticket")),
        "open": summary.get("open") if isinstance(summary.get("open"), bool) else None,
        "status": _text(readout.get("status")),
        "startedAt": _text(summary.get("startedAt")),
        "lastAt": _text(summary.get("lastAt")),
        "durationSeconds": _count(summary.get("durationSeconds")),
        "rounds": _count(summary.get("roundsClosed")),
        "findings": findings_of(summary.get("findings")),
        "roundDetail": detail,
    }


def _branch_ticket(branch: str | None, tracker: dict | None) -> str | None:
    """The tracker key a branch name carries, found by the key pattern without its anchors, in either case."""
    if branch is None or tracker is None or not tracker.get("keyPattern"):
        return None
    pattern = tracker["keyPattern"]
    try:
        found = re.search(pattern.removeprefix("^").removesuffix("$"), branch, re.IGNORECASE)
    except re.error:
        return None
    key = found.group(0).upper() if found else None
    return key if key is not None and re.fullmatch(pattern.removeprefix("^").removesuffix("$"), key, re.IGNORECASE) else None


def _legacy_summary(record: dict, tracker: dict | None) -> dict:
    """An older delivery telemetry record (``schemaVersion: 1``), from before runs, as a summary."""
    loop = record.get("reviewLoop") if _is_object(record.get("reviewLoop")) else {}
    return {**_unknown_summary(None), "ticket": _branch_ticket(_text(record.get("branch")), tracker), "open": False,
            "status": _text(record.get("status")), "lastAt": _text(record.get("generatedAt")),
            "rounds": _count(loop.get("iterationCount")), "findings": findings_of(loop.get("findingCounts"))}


def deliveries_of(document: object) -> dict[str, dict]:
    """The deliveries the record names, by run id: its headline, tracker parent and the element's subject."""
    state = document.get("state") if _is_object(document) and _is_object(document.get("state")) else {}
    meta = state.get("meta") if _is_object(state.get("meta")) else {}
    found: dict[str, dict] = {}
    for key in ("queue", "planned"):
        for element in state.get(key) if isinstance(state.get(key), list) else ():
            run_id = _text(element.get("runId")) if _is_object(element) else None
            if run_id and run_id not in found:
                found[run_id] = {"headline": _text(meta.get("headline")), "parent": _text(meta.get("trackerParent")),
                                 "subject": _text(element.get("subject"))}
    return found


def _export_files(config: dict, root: Path) -> list[tuple[str, str]]:
    """The export directory's JSON files and their text, by name, skipping the configured patterns."""
    exports = config.get("exports")
    if exports is None:
        return []
    folder = root / exports["directory"]
    try:
        names = sorted(entry.name for entry in folder.iterdir() if entry.name.endswith(".json") and entry.is_file())
    except OSError:
        return []
    files = []
    for name in names:
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in exports["ignore"]):
            continue
        try:
            files.append((name, (folder / name).read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            files.append((name, ""))
    return files


def _history_order(row: dict) -> tuple:
    at = _milliseconds(row["summary"].get("lastAt") or row["summary"].get("startedAt"))
    return (at is None, -(at or 0), row["key"])


def collect_history(document: object, config: dict, root: Path, cache_path: Path, deadline: float, as_of: str) -> dict:
    """The history: each listed run's summary from ``runs show`` (reused from the cache while the inventory shows
    it unchanged), then each export not already listed, newest first."""
    notes: list[str] = []
    rows: dict[str, dict] = {}
    deliveries = deliveries_of(document)

    def row(key: str, source: str, summary: dict, *, file: str | None = None, label: str | None = None,
            note: str | None = None) -> None:
        rows[key] = {"key": key, "delivery": deliveries.get(summary.get("runId") or key), "source": source,
                     "file": file, "label": label, "note": note, "summary": summary}

    argv = views_common.runs_argv(config, root)
    if argv is not None:
        product = Product(argv, config["runs"]["timeoutSeconds"], config["scrubEnv"], root, deadline)
        inventory = product("runs", "list", "--json")
        readable = _is_object(inventory) and isinstance(inventory.get("runs"), list)
        if not readable:
            notes.append("The product's run list could not be read; only the tracked exports are listed."
                         if config.get("exports") else "The product's run list could not be read.")
        listed = [entry for entry in inventory["runs"] if _is_object(entry) and _text(entry.get("runId"))] if readable else []
        cached = views_common.read_runs_cache(cache_path)
        kept = {}

        def fresh(entry: dict) -> bool:
            size, hit = entry.get("bytes"), cached.get(entry["runId"])
            return (_count(size) is not None and _is_object(hit) and _number_equal(hit.get("bytes"), size)
                    and _is_object(hit.get("summary")))

        unread = [entry["runId"] for entry in listed if not fresh(entry)]
        with ThreadPoolExecutor(max_workers=READ_WORKERS) as pool:
            shown = dict(zip(unread, pool.map(lambda run_id: product("runs", "show", run_id, "--json"), unread)))
        for entry in listed:
            run_id = entry["runId"]
            size = entry.get("bytes") if _count(entry.get("bytes")) is not None else None
            if run_id not in shown:
                summary = cached[run_id]["summary"]
            else:
                summary = summarize_run_export(shown[run_id])
            if summary is not None:
                if size is not None:
                    kept[run_id] = {"bytes": size, "summary": summary}
                row(run_id, "product", {**summary, "runId": run_id})
                continue
            row(run_id, "product", {**_unknown_summary(run_id),
                                    "open": entry.get("open") if isinstance(entry.get("open"), bool) else None,
                                    "status": _text(entry.get("status")),
                                    "durationSeconds": _count(entry.get("durationSeconds"))},
                note="Its run could not be read from the product.")
        # An unreadable inventory leaves the cache as it was, so the next render need not read every run again.
        if readable:
            views_common.write_runs_cache(cache_path, kept)

    for name, body in _export_files(config, root):
        named = re.fullmatch(r"(run-[A-Za-z0-9_-]+)\.json", name)
        named = named.group(1) if named else None
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, RecursionError):
            if (named or name) not in rows:
                row(named or name, "export", _unknown_summary(named), file=name, note="This export could not be read.")
            continue
        summary = summarize_run_export(parsed)
        if summary is not None:
            key = summary["runId"] or named or name
            if key not in rows:
                row(key, "export", {**summary, "runId": summary["runId"] or named}, file=name)
        elif _is_object(parsed) and _number_equal(parsed.get("schemaVersion"), 1) and _text(parsed.get("branch")):
            row(name, "telemetry", _legacy_summary(parsed, config["tracker"]), file=name, label=_text(parsed.get("branch")))
        else:
            row(name, "export", _unknown_summary(None), file=name, note="This file is not a run export.")
    return {"asOf": as_of, "rows": sorted(rows.values(), key=_history_order), "notes": notes}


def history_within_budget(history: dict, budget: int = PAGE_BUDGET_BYTES) -> dict:
    """The history within the page budget: the oldest rows give up their summaries first."""
    history = copy.deepcopy(history)
    size = len(embed(history).encode("utf-8"))
    for row in reversed(history["rows"]):
        if size <= budget:
            break
        before = len(embed(row).encode("utf-8"))
        row["summary"] = _unknown_summary(row["summary"].get("runId"))
        row["label"], row["delivery"] = None, None
        row["note"] = "Its details are over the page's size budget."
        size -= before - len(embed(row).encode("utf-8"))
    return history


def render_history(history: dict, config: dict, *, line_map: str | None) -> str:
    view = {"lineMap": line_map, "runs": config["runs"] is not None,
            "exports": config["exports"]["directory"] if config.get("exports") else None}
    return "\n".join([
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        '<meta name="delivery-state-live">',
        "<title>Delivery history</title>",
        f'<link rel="stylesheet" href="{FONTS}">',
        f"<style>\n{_asset('history.css')}{_asset('run-summary.css')}</style>",
        "</head>",
        "<body>",
        '<main id="app"></main>',
        f'<script type="application/json" id="delivery-history">{embed(history)}</script>',
        f'<script type="application/json" id="delivery-history-view">{embed(view)}</script>',
        f"<script>\n{_asset('run-summary.js')}{_asset('history.js')}</script>",
        "</body>",
        "</html>",
        "",
    ])


# The page


def render_line_map(document: object, runs: dict, config: dict, *, theme_key: str, history: str | None) -> str:
    reviews = native_review_summaries(document)
    # One review payload budget, in stop order, regardless of its backing store.
    combined = {**{"run:" + key: value for key, value in runs.items()},
                **{"native:" + key: value for key, value in reviews.items()}}
    ordered = []
    state = document.get("state", {})
    for item in [*state.get("queue", []), *state.get("planned", [])]:
        key = "run:" + item["runId"] if item.get("runId") else "native:" + item.get("subject", "")
        if key in combined and key not in ordered:
            ordered.append(key)
    ordered.extend(key for key in combined if key not in ordered)
    bounded = within_budget(combined, ordered, PAGE_BUDGET_BYTES - 2)
    runs = {key: bounded["run:" + key] for key in runs}
    reviews = {key: bounded["native:" + key] for key in reviews}
    document = {key: value for key, value in document.items() if key != "reviewEvidence"}
    view = {
        "title": config["title"],
        "themeKey": theme_key,
        "history": history,
        "runs": config["runs"] is not None,
        "lenses": config["lenses"],
    }
    return "\n".join([
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        '<meta name="delivery-state-live">',
        f"<title>{_html_text(config['title'])}</title>",
        f'<link rel="stylesheet" href="{FONTS}">',
        f"<style>\n{_asset('line-map.css')}{_asset('run-summary.css')}</style>",
        "</head>",
        "<body>",
        THEME_SWITCH,
        '<main id="app" data-source="embedded"></main>',
        f'<script type="application/json" id="delivery-state">{embed(document)}</script>',
        f'<script type="application/json" id="delivery-runs">{embed(runs)}</script>',
        f'<script type="application/json" id="delivery-reviews">{embed(reviews)}</script>',
        f'<script type="application/json" id="delivery-links">{embed(page_links(document, runs, config))}</script>',
        f'<script type="application/json" id="delivery-view">{embed(view)}</script>',
        f"<script>\n{_asset('run-summary.js')}{_asset('line-map.js')}</script>",
        "</body>",
        "</html>",
        "",
    ])


def _asset(name: str) -> str:
    """One of the skill's page assets, inlined into the page as it is."""
    return (ASSETS / name).read_text(encoding="utf-8")


def _html_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _runs_deadline(binding: dict, subcommand: str, requested: float) -> float:
    """The runs deadline: the requested seconds, capped at this renderer's declared timeout less a margin."""
    state = binding.get("state") if _is_object(binding) else None
    renderers = state.get("renderers") if _is_object(state) else None
    for renderer in renderers if isinstance(renderers, list) else ():
        argv = renderer.get("argv") if _is_object(renderer) else None
        if isinstance(argv, list) and subcommand in argv and any(
                isinstance(part, str) and PurePosixPath(part).name == SCRIPT for part in argv):
            return views_common.runs_deadline({"state": {"renderers": [renderer]}}, SCRIPT, requested)
    return views_common.runs_deadline({}, SCRIPT, requested)


def line_map(arguments: argparse.Namespace) -> int:
    inputs = views_common.load_inputs(arguments.config, arguments.binding, state_dir=arguments.state_dir)
    try:
        document = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        print("line-map: the delivery document on stdin is not JSON", file=sys.stderr)
        return 1
    if not _is_object(document):
        print("line-map: the delivery document on stdin is not an object", file=sys.stderr)
        return 1
    seconds = _runs_deadline(inputs.binding, "line-map", arguments.deadline_seconds)
    runs = collect_runs(document, inputs.config, inputs.root, views_common.runs_cache_path(inputs.state_directory),
                        time.monotonic() + seconds)
    page = render_line_map(document, runs, inputs.config,
                           theme_key=views_common.theme_key(inputs.state_directory),
                           history=_renderer_output(inputs.binding, "history"))
    sys.stdout.write(page)
    return 0


def history(arguments: argparse.Namespace) -> int:
    inputs = views_common.load_inputs(arguments.config, arguments.binding, state_dir=arguments.state_dir)
    try:
        document = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        print("history: the delivery document on stdin is not JSON", file=sys.stderr)
        return 1
    seconds = _runs_deadline(inputs.binding, "history", arguments.deadline_seconds)
    as_of = arguments.now or datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    collected = collect_history(document, inputs.config, inputs.root,
                                views_common.runs_cache_path(inputs.state_directory).with_name("history.json"),
                                time.monotonic() + seconds, as_of)
    sys.stdout.write(render_history(history_within_budget(collected), inputs.config,
                                    line_map=_renderer_output(inputs.binding, "line-map")))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print a delivery view for the delivery record on stdin.")
    commands = parser.add_subparsers(dest="command", required=True)
    drawn = commands.add_parser("line-map", help="the live line map")
    views_common.add_input_arguments(drawn)
    drawn.add_argument("--deadline-seconds", type=float, default=DEFAULT_DEADLINE_SECONDS,
                       help="the time all runs commands share, capped at the renderer's timeout less 5 seconds")
    listed = commands.add_parser("history", help="the delivery history")
    views_common.add_input_arguments(listed)
    listed.add_argument("--deadline-seconds", type=float, default=DEFAULT_DEADLINE_SECONDS,
                        help="the time all runs commands share, capped at the renderer's timeout less 5 seconds")
    listed.add_argument("--now", help="the history's as-of time, for tests; the clock otherwise")
    arguments = parser.parse_args(argv)
    try:
        return {"line-map": line_map, "history": history}[arguments.command](arguments)
    except ConfigError as error:
        print(f"{arguments.command}: {error}", file=sys.stderr)
        return 2


# Page assets

# The pages' two faces are the only thing they load; offline, the type falls back to the system's.
FONTS = "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,300;12..96,500;12..96,700&family=Geist:wght@400;500;600&display=swap"
THEME_SWITCH = ("<div class=\"theme-switch\" role=\"group\" aria-label=\"Theme\"><button type=\"button\" data-theme-pick=\"light\" aria-label=\"Light\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0 -8 0\"/><path d=\"M3 12h1m8 -9v1m8 8h1m-9 8v1m-6.4 -15.4l.7 .7m12.1 -.7l-.7 .7m0 11.4l.7 .7m-12.1 -.7l-.7 .7\"/></svg></button><button type=\"button\" data-theme-pick=\"dark\" aria-label=\"Dark\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M12 3c.132 0 .263 0 .393 0a7.5 7.5 0 0 0 7.92 12.446a9 9 0 1 1 -8.313 -12.454z\"/></svg></button></div>")


if __name__ == "__main__":
    raise SystemExit(main())
