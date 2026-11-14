"""A PR reviewer's gate, read from what the forge adapter's `reviews` returns: pure functions, no forge and no clock.

A reviewer in `review.pr.reviewers` may take `gate`:
  "verdict"   met when the review's verdict says approve: the body's first line matching `**Verdict:** <value>`,
              whose first word, lowercased and stripped of punctuation, is `approve` or `approved`;
  "approval"  met when an approval's `account` is the reviewer's `account` (display names are never compared) and,
              where the approval names a `commit`, that commit is the head. Bitbucket ties no approval to a commit, so
              there an approval given at an earlier head still counts.
A gate is checked only on a current review: the caller never asks for an absent reviewer's gate.

Each gate is `{kind, met, why}`, the record's shape, and an unmet one refuses with `unmet_refusal`.

A reviewer may also take `report`, the external id of the Code Insights report it posts at the commit it reviewed.
Only the Bitbucket adapter returns `reports`; the GitHub adapter returns `reports: null`, and a reviewer with `report`
is refused there. `report_reading` classifies such a reviewer from its report rather than from timestamps:
  PASSED   current once every check passed, its text the latest matching comment posted since the head's pipeline
           started (the earliest check's `at`); with none yet it waits for the settle, then is refused;
  PENDING  (or no report yet) pending until the settle, then silent when required and absent when optional;
  FAILED   the reviewer errored: silent at once when required, absent at once when optional.
With a report, the verdict is its data item titled `Verdict` (any case), which wins over the body's line, and an
"approval" gate reads the report's result and verdict instead of the forge's approvals.
"""

from __future__ import annotations

import re
from typing import Callable

KINDS = ("approval", "verdict")
VERDICT_LINE = re.compile(r"\*\*Verdict:\*\*\s*(.+)")
APPROVING = ("approve", "approved")
REPORT_RESULTS = ("PASSED", "FAILED", "PENDING")


def same_commit(reported: object, head: str) -> bool:
    """As run_delivery's: a forge may report an abbreviated hash, of at least 7 characters."""
    return isinstance(reported, str) and len(reported) >= 7 and head.startswith(reported)


def body_verdict(body: object) -> str | None:
    """The value of the body's first `**Verdict:**` line, stripped; later lines are ignored. None when there is none."""
    for line in (body if isinstance(body, str) else "").splitlines():
        found = VERDICT_LINE.fullmatch(line)
        if found:
            return found.group(1).strip()
    return None


def verdict_says_approve(value: str) -> bool:
    words = value.split()
    return bool(words) and re.sub(r"[^\w]", "", words[0]).lower() in APPROVING


def report_verdict(report: dict | None) -> str | None:
    """The value of the report's data item titled `Verdict`, matched case-insensitively, stripped; None without one."""
    for item in (report or {}).get("data") or []:
        if isinstance(item, dict) and str(item.get("title", "")).strip().casefold() == "verdict":
            value = item.get("value")
            return "" if value is None else str(value).strip()
    return None


def verdict_gate(review: dict, report: dict | None = None) -> dict:
    """Met when the verdict says approve: the report's `Verdict`, when it has one, else the body's first line."""
    value = report_verdict(report)
    if value is not None:
        source = f"report {report.get('id')}'s Verdict"
        if verdict_says_approve(value):
            return {"kind": "verdict", "met": True, "why": f"{source} is {value!r}"}
        if not value:
            return {"kind": "verdict", "met": False, "why": f"{source} is empty"}
        return {"kind": "verdict", "met": False, "why": f"{source} is {value!r}, not approve"}
    value = body_verdict((review or {}).get("body"))
    if value is None:
        return {"kind": "verdict", "met": False, "why": "the review has no **Verdict:** line"}
    if not value:
        return {"kind": "verdict", "met": False, "why": "the review's **Verdict:** line is empty"}
    if verdict_says_approve(value):
        return {"kind": "verdict", "met": True, "why": f"the verdict is {value!r}"}
    return {"kind": "verdict", "met": False, "why": f"the verdict is {value!r}, not approve"}


def report_approval_gate(review: dict, report: dict) -> dict:
    """An "approval" gate on a reviewer with a report: the report is tied to the commit, so its result and verdict
    stand for the approval. Met when it is PASSED and its verdict, read as the verdict gate reads it, says approve."""
    if report.get("result") != "PASSED":
        return {"kind": "approval", "met": False, "why": f"report {report.get('id')} is {report.get('result')}"}
    verdict = verdict_gate(review, report)
    return {"kind": "approval", "met": verdict["met"], "why": f"report {report.get('id')} is PASSED; {verdict['why']}"}


def approval_gate(approvals: list, account: str, head: str) -> dict:
    """Met by an approval from exactly `account`, at the head where the approval names its commit."""
    mine = [a for a in approvals if isinstance(a, dict) and a.get("account") == account]
    for approval in mine:
        # An approval that names its commit, even an empty one, counts only at the head; one that names none counts.
        if "commit" not in approval or same_commit(approval["commit"], head):
            at = f" {approval['commit'][:10]}" if "commit" in approval else ""
            when = approval.get("at") or "undated"
            return {"kind": "approval", "met": True, "why": f"{account} approved{at} ({when})"}
    if mine:
        older = ", ".join(sorted({str(a.get("commit") or "")[:10] or "an unnamed commit" for a in mine}))
        return {"kind": "approval", "met": False, "why": f"{account} approved {older}, not the head {head[:10]}"}
    return {"kind": "approval", "met": False, "why": f"no approval by {account}"}


def gate(reviewer: dict, review: dict, found: dict, head: str) -> dict | None:
    """The reviewer's gate on its current review, or None when it takes none. `found` is the adapter's `reviews`,
    checked first with `members_refusal` and, for a reviewer with `report`, `reports_refusal`."""
    kind = reviewer.get("gate")
    if kind is None:
        return None
    report = head_report(found.get("reports"), reviewer["report"]) if reviewer.get("report") else None
    if kind == "verdict":
        return verdict_gate(review, report)
    if kind == "approval":
        if reviewer.get("report"):
            return report_approval_gate(review, report or {"id": reviewer["report"], "result": "absent"})
        return approval_gate(found["approvals"], reviewer["account"], head)
    raise ValueError(f"unknown gate {kind!r}")


def head_report(reports: object, external_id: str) -> dict | None:
    """The report with this external id among those at the head, the latest by `at` when the forge lists it twice.
    Anything but a list is no report here: `reports_refusal` names it."""
    mine = [r for r in (reports if isinstance(reports, list) else [])
            if isinstance(r, dict) and r.get("id") == external_id]
    return max(mine, key=lambda r: str(r.get("at") or ""), default=None)


def reports_refusal(reviewers: list[dict], found: dict) -> str | None:
    """Why the adapter's `reviews` can't serve these reviewers' reports, or None. `reports: null` is the GitHub
    adapter's: it has none, so a reviewer with `report` is refused, naming Bitbucket. Present but empty is not refused:
    no report yet is pending."""
    reporting = [r["id"] for r in reviewers if r.get("report")]
    if not reporting:
        return None
    if "reports" not in found:
        return (f"the forge adapter's `reviews` returns no `reports`, which the `report` of {', '.join(reporting)} "
                "needs: use an adapter that returns it, or drop `report`")
    reports = found["reports"]
    if reports is None:
        return (f"PR reviewer {', '.join(reporting)} takes `report`, a Code Insights report, but this forge adapter "
                "has no reports (`reports` is null, as on GitHub): only the Bitbucket adapter returns reports; "
                "use it, or drop `report`")
    if not isinstance(reports, list) or not all(
            isinstance(r, dict) and isinstance(r.get("id"), str) and r.get("result") in REPORT_RESULTS
            for r in reports):
        return ("the forge adapter's `reviews` returns `reports` that are not a list of objects, each with a string "
                "`id` and a `result` of " + ", ".join(REPORT_RESULTS))
    return None


def forge_refusal(reviewers: list[dict], adapter: object) -> str | None:
    """The doctor's refusal, from the configuration alone: the GitHub adapter has no reports, so a reviewer with
    `report` there can never be current."""
    reporting = [r["id"] for r in reviewers if r.get("report")]
    if adapter != "github" or not reporting:
        return None
    return (f"PR reviewer {', '.join(reporting)} takes `report`, a Code Insights report, but the GitHub adapter has no "
            "reports: only the Bitbucket adapter returns them; fix: drop `report`, or use Bitbucket")


def report_reading(reviewer: dict, mine: list[dict], found: dict, checks: list[dict], settled: bool,
                   when: Callable[[object], float]) -> dict:
    """One reviewer with `report`, classified from its report at the head. `mine` is the reviewer's
    matching comments, `checks` the head's (none failed: the caller refuses that first), `settled` whether the settle
    has passed, and `when` the caller's parser of the forge's times. Returns {state, review, report, why}, state one
    of current, pending, silent or absent, or {refusal} for a PASSED report whose review was never posted."""
    rid, required = reviewer["id"], reviewer.get("required", True)
    report = head_report(found.get("reports"), reviewer["report"])
    result = (report or {}).get("result")
    passed = bool(checks) and all(ch.get("state") == "passed" for ch in checks)
    if result == "FAILED":
        why = f"PR reviewer {rid} errored: its report {reviewer['report']} is FAILED"
        return {"state": "silent" if required else "absent", "review": None, "report": report, "why": why}
    if result != "PASSED" or not passed:
        if settled and result != "PASSED":
            why = (f"PR reviewer {rid}'s report {reviewer['report']} is "
                   f"{'still PENDING' if report else 'not at the head'} after the settle")
            return {"state": "silent" if required else "absent", "review": None, "report": report, "why": why}
        return {"state": "pending", "review": None, "report": report, "why": None}
    started = min(when(ch["at"]) for ch in checks)
    since = [c for c in mine if when(c["at"]) >= started]
    review = max(since, key=lambda c: when(c["at"]), default=None)
    if review is not None:
        return {"state": "current", "review": review, "report": report, "why": None}
    if not settled:
        return {"state": "pending", "review": None, "report": report, "why": None}
    return {"refusal": f"PR reviewer {rid}'s report {reviewer['report']} is PASSED, but names a review that wasn't "
                       "posted: none of its comments is from the head's pipeline (since its earliest check); have it "
                       "post its review, or push so it runs again"}


def members_refusal(reviewers: list[dict], found: dict) -> str | None:
    """Why the adapter's `reviews` can't gate these reviewers, or None. A member it doesn't return is refused; an empty
    one is not: then the gate is just unmet."""
    gated = [r["id"] for r in reviewers if r.get("gate") == "approval" and not r.get("report")]  # a report stands in
    if not gated:
        return None
    approvals = found.get("approvals")
    if approvals is None:
        return (f"the forge adapter's `reviews` returns no `approvals`, which the approval gate of "
                f"{', '.join(gated)} needs: use an adapter that returns it, or drop the gate")
    if not isinstance(approvals, list) or not all(isinstance(a, dict) for a in approvals):
        return "the forge adapter's `reviews` returns `approvals` that are not a list of objects"
    return None


def unmet_refusal(reviewer_id: str, verdict: dict, head: str) -> str:
    """The refusal for an unmet gate, the same from handoff, lead-accept and the reviewGate."""
    return (f"PR reviewer {reviewer_id}'s {verdict['kind']} gate is unmet at {head[:10]}: {verdict['why']}; "
            "fix and push, so the reviewer runs again")


def shown(verdict: dict | None) -> str:
    """The line pr-review prints beside a review: empty for a reviewer with no gate."""
    if verdict is None:
        return ""
    return f"{verdict['kind']} gate {'met' if verdict['met'] else 'UNMET'}: {verdict['why']}"


# What run_delivery's PR-review commands call: each takes their own structures and returns what to report.

def attach(read: dict, found: dict, conf: dict, head: str) -> str | None:
    """Give each current review in a classified read its gate, as `gate` beside its `review` (None for a reviewer that
    takes none). Returns why the adapter's output can't gate the reviewers, or None. The single `{author}` shape and a
    failed pipeline take no gates."""
    if conf.get("legacy") or read.get("status") == "failed":
        return None
    refused = members_refusal(conf["reviewers"], found) or reports_refusal(conf["reviewers"], found)
    if refused:
        return refused
    for reviewer in conf["reviewers"]:
        one = read["reviewers"][reviewer["id"]]
        if one["state"] == "current":
            one["gate"] = gate(reviewer, one["review"], found, head)
    return None


def live_refusals(reviewers: list[dict], live: dict[str, dict], head: str) -> list[str]:
    """The refusals for every current review, required or optional, whose gate a read of the forge finds unmet. A
    reviewer that is not current, an absent optional one among them, has no gate to check."""
    return [unmet_refusal(r["id"], live[r["id"]]["gate"], head) for r in reviewers
            if live.get(r["id"], {}).get("state") == "current" and live[r["id"]].get("gate")
            and not live[r["id"]]["gate"].get("met")]


def recorded_refusals(reviewers: list[dict], entries: dict[str, dict], head: str) -> list[str]:
    """The refusals for the recorded current reviews whose gate is unmet, or that were recorded with no gate though
    the reviewer now takes one."""
    problems = []
    for reviewer in reviewers:
        entry = entries.get(reviewer["id"]) or {}
        if entry.get("state") != "current" or not reviewer.get("gate"):
            continue
        recorded = entry.get("gate")
        if not isinstance(recorded, dict) or recorded.get("kind") != reviewer["gate"]:
            problems.append(f"PR reviewer {reviewer['id']}'s {reviewer['gate']} gate isn't in the PR review recorded "
                            f"at {head[:10]}: run pr-review again")
        elif not recorded.get("met"):
            problems.append(unmet_refusal(reviewer["id"], recorded, head))
    return problems


def brief_text(reviewers: list[dict]) -> str:
    """The lane brief's sentence on gates, or nothing when no reviewer takes one."""
    gated = [f"`{r['id']}` ({'its approval' if r['gate'] == 'approval' else 'its **Verdict:** line'})"
             for r in reviewers if r.get("gate")]
    if not gated:
        return ""
    return ("   Some reviewers gate the hand-off: " + "; ".join(gated) + ". `pr-review` shows each gate beside its "
            "review; with one unmet, `handoff`, the lead's acceptance and the merge queue refuse, so fix what the "
            "reviewer found and push, and it runs again.\n")


def stops_at_once(reviewers: dict[str, dict]) -> bool:
    """Whether a classified read ends the wait though a check is still pending: a required reviewer is silent, which
    before the settle only an errored report makes it."""
    return any(r.get("state") == "silent" for r in reviewers.values())


def silent_reasons(read: dict) -> str | None:
    """The silent refusal's reason when a silent reviewer has one of its own (its report FAILED, or still PENDING
    after the settle), else None and the refusal says the reviewer posted nothing after the pipeline went green. A
    reviewer silent without a reason of its own beside one with a reason is named too: that only happens after the
    settle, when the pipeline is green."""
    silent = {rid: r for rid, r in read.get("reviewers", {}).items() if r.get("state") == "silent"}
    reasons = [r["why"] for r in silent.values() if r.get("why")]
    if not reasons:
        return None
    quiet = [rid for rid, r in silent.items() if not r.get("why")]
    return "; ".join(reasons + ([f"{', '.join(quiet)} posted nothing since the pipeline went green"] if quiet else []))


def unread_when_stopped(entry: dict) -> bool:
    """Whether a recorded reviewer is `pending`: not yet in when another's errored report stopped the read at once.
    On a hand-off blocked on that, acceptance leaves it to the lead's decision rather than refusing it."""
    return entry.get("state") == "pending"


def recorded_report(state: dict) -> dict | None:
    """The record's `report` for a reviewer read from its report: its id, result and time at the head."""
    report = state.get("report")
    return {k: report.get(k) for k in ("id", "result", "at")} if report else None
