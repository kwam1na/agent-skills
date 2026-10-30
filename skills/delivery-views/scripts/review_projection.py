"""One projection from review outcomes to the run summary's ``roundDetail``.

The line map's native review cards and run-delivery's plan review both draw a review's rounds through this module, so
the page and the producer can't drift. ``round_detail`` turns validated review outcomes, oldest first, into one entry
per round. Given each round's candidate commit, it projects a plan review: every ``fixed`` closure carries the commit
of the round that reports it closed, the revision that carried the fix, and nothing is deferred.

``fit_plan_review`` bounds what a plan file holds of an unbounded review (C4a of the plan-review contract): rounds
past 32 are folded, each entry's lists are capped, then the oldest texts shortened, then the oldest lists emptied,
until the serialized ``planReview`` is within 256 KiB. Every round's coverage, verdicts and counts, the review's state
and the fix commit of every listed closure are always kept.

Standard library only.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import views_common  # noqa: E402

SEVERITIES = ("P0", "P1", "P2", "P3")
SUMMARY_LENGTH = 280
ISSUE_LENGTH = 64
RESOLUTION_LENGTH = 2048
CONTROL_RUN = re.compile("[\u0000-\u001f\u007f]+")
CLOSURE_STATES = {"closed": "fixed", "fixed": "fixed", "withdrawn": "dropped", "dropped": "dropped",
                  "deferred": "deferred", "open": "open"}
# A shortened plan-review text keeps this many characters, its last an ellipsis.
SHORTENED = 80


def plain(value: str) -> str:
    return CONTROL_RUN.sub(" ", value)


def outcome_word(value: object) -> str | None:
    """A review outcome in the state journal's words; anything unknown is kept."""
    outcome = value if isinstance(value, str) and value != "" else None
    if outcome is None:
        return None
    if re.fullmatch(r"approve|approved|aligned", outcome, re.IGNORECASE):
        return "approve"
    if re.fullmatch(r"changes|changes[ _-]requested|request[ _-]changes", outcome, re.IGNORECASE):
        return "changes"
    return outcome


def issue(value: object) -> str | None:
    issue = value if isinstance(value, str) and value != "" else None
    return issue if issue is not None and len(issue) <= ISSUE_LENGTH and not CONTROL_RUN.search(issue) else None


def _plan_text(value: object, cap: int) -> str:
    """A text the plan contract accepts: no control character or line separator, never empty, at most `cap`."""
    said = views_common.CONTROL.sub(" ", value if isinstance(value, str) else "").strip()
    said = re.sub(r"\s{2,}", " ", said) or "(no text)"
    return said if len(said) <= cap else said[:cap - 1] + "…"


def round_detail(outcomes: list[dict], commits: dict[int, str] | None = None) -> list[dict]:
    """One `roundDetail` entry per review outcome. With `commits`, each round's candidate commit, it is a plan
    review's: fixed closures carry their closing round's commit, the round's time is when it finished, and no entry
    says it is deferred or that its details are unavailable."""
    plan = commits is not None
    rounds, states = [], {}
    for outcome in outcomes:
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
                if plan:
                    raised.append({"id": finding["id"], "lens": lens, "severity": finding["severity"],
                                   "summary": _plan_text(finding["summary"], SUMMARY_LENGTH), "state": state})
                    continue
                raised.append({"id": finding["id"], "lens": lens, "severity": finding["severity"],
                               "summary": plain(finding["summary"])[:SUMMARY_LENGTH], "state": state,
                               "deferredIssue": issue(finding.get("deferredIssue"))})
            for closure in result.get("resolvedFindings", result.get("resolutions", [])):
                state = CLOSURE_STATES[closure["state"]]
                states[closure["id"]] = "resolved" if state in ("fixed", "dropped") else "deferred" if state == "deferred" else "unresolved"
                said = closure.get("resolution", closure.get("reason", ""))
                if plan:
                    settled.append({"id": closure["id"], "lens": lens, "state": state,
                                    "fixCommit": commits[outcome["round"]] if state == "fixed" else None,
                                    "resolution": _plan_text(said, views_common.PLAN_TEXT)})
                    continue
                settled.append({"id": closure["id"], "lens": lens, "state": state,
                                "fixCommit": closure.get("fixCommit"), "deferredIssue": issue(closure.get("deferredIssue")),
                                "resolution": plain(said)[:RESOLUTION_LENGTH]})
        counts = {severity: sum(note["severity"] == severity for note in raised) for severity in SEVERITIES}
        approved = complete and outcome.get("outcome") == "aligned" and all(word == "approve" for word in verdicts.values())
        row = {"round": outcome["round"], "candidateRef": candidate, "at": None,
               "outcome": "approve" if approved else "changes" if "changes" in verdicts.values() else None,
               "findings": counts, "verdicts": verdicts, "raised": raised, "settled": settled}
        if plan:
            finished = outcome.get("finishedAt")
            row["at"] = finished if isinstance(finished, str) and views_common.TIMESTAMP.fullmatch(finished) else None
        else:
            row["detailsUnavailable"] = not complete
        rounds.append(row)
    for row in rounds:
        for raised in row["raised"]:
            raised["state"] = states[raised["id"]]
    return rounds


# Fitting a plan review to the plan file's caps


def _clipped(entry: dict) -> dict:
    return entry.setdefault("clipped", {"findings": 0, "closures": 0, "texts": False})


def _fold(rounds: list[dict]) -> dict:
    """Several rounds as one entry: the last one's verdicts, candidate, time and outcome, every round's counts summed."""
    last = rounds[-1]
    return {"round": last["round"], "fromRound": rounds[0]["round"], "candidateRef": last["candidateRef"],
            "at": last["at"], "outcome": last["outcome"], "verdicts": dict(last["verdicts"]),
            "findings": {severity: sum(row["findings"][severity] for row in rounds) for severity in SEVERITIES},
            "raised": [], "settled": [],
            "clipped": {"findings": sum(len(row["raised"]) + row.get("clipped", {}).get("findings", 0) for row in rounds),
                        "closures": sum(len(row["settled"]) + row.get("clipped", {}).get("closures", 0) for row in rounds),
                        "texts": False}}


def _shorten(holder: dict, key: str) -> bool:
    if len(holder[key]) <= SHORTENED:
        return False
    holder[key] = holder[key][:SHORTENED - 1] + "…"
    return True


def fit_plan_review(rounds: list[dict], lenses: list[str], state: str, ended_reason: str | None = None) -> dict:
    """The `planReview` a plan file holds of a review whose rounds `round_detail` projected: always within C2's caps."""
    rounds = copy.deepcopy(rounds)
    kept = views_common.PLAN_REVIEW_ROUNDS
    # 1. Fold: round 1 and the latest rounds stay entries; every round between becomes one fold.
    if len(rounds) > kept:
        rounds = [rounds[0], _fold(rounds[1:len(rounds) - (kept - 2)]), *rounds[len(rounds) - (kept - 2):]]
    # 2. Count: the worst findings and the fixed closures are listed first; the rest are counted.
    cap = views_common.PLAN_REVIEW_NOTES
    for entry in rounds:
        entry["raised"] = sorted(entry["raised"], key=lambda note: SEVERITIES.index(note["severity"]))
        entry["settled"] = sorted(entry["settled"], key=lambda note: note["state"] != "fixed")
        for key, counted in (("raised", "findings"), ("settled", "closures")):
            if len(entry[key]) > cap:
                _clipped(entry)[counted] += len(entry[key]) - cap
                entry[key] = entry[key][:cap]
    review = {"lenses": list(lenses), "state": state, **({"endedReason": ended_reason} if state == "ended" else {}),
              "rounds": rounds}

    def fits() -> bool:
        return views_common.plan_review_size(review) <= views_common.PLAN_REVIEW_BYTES

    # 3. Text: the oldest entries' resolutions, then their summaries.
    for key, notes in (("resolution", "settled"), ("summary", "raised")):
        for entry in rounds:
            if fits():
                break
            if any([_shorten(note, key) for note in entry[notes]]):
                _clipped(entry)["texts"] = True
    # 4. Lists: the oldest entries' lists are emptied until it fits; with every list empty it always does.
    for entry in rounds:
        if fits():
            break
        if entry["raised"] or entry["settled"]:
            clipped = _clipped(entry)
            clipped["findings"] += len(entry["raised"])
            clipped["closures"] += len(entry["settled"])
            entry["raised"], entry["settled"] = [], []
    return review
