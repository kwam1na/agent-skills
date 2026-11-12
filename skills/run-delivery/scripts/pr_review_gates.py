"""A PR reviewer's gate, read from what the forge adapter's `reviews` returns: pure functions, no forge and no clock.

A reviewer in `review.pr.reviewers` may take `gate`:
  "verdict"   met when the review's verdict says approve: the body's first line matching `**Verdict:** <value>`,
              whose first word, lowercased and stripped of punctuation, is `approve` or `approved`;
  "approval"  met when an approval's `account` is the reviewer's `account` (display names are never compared) and,
              where the approval names a `commit`, that commit is the head. Bitbucket ties no approval to a commit, so
              there an approval given at an earlier head still counts.
A gate is checked only on a current review: the caller never asks for an absent reviewer's gate.

Each gate is `{kind, met, why}`, the record's shape, and an unmet one refuses with `unmet_refusal`.
"""

from __future__ import annotations

import re

KINDS = ("approval", "verdict")
VERDICT_LINE = re.compile(r"\*\*Verdict:\*\*\s*(.+)")
APPROVING = ("approve", "approved")


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


def verdict_gate(review: dict) -> dict:
    value = body_verdict(review.get("body"))
    if value is None:
        return {"kind": "verdict", "met": False, "why": "the review has no **Verdict:** line"}
    if not value:
        return {"kind": "verdict", "met": False, "why": "the review's **Verdict:** line is empty"}
    if verdict_says_approve(value):
        return {"kind": "verdict", "met": True, "why": f"the verdict is {value!r}"}
    return {"kind": "verdict", "met": False, "why": f"the verdict is {value!r}, not approve"}


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
    checked first with `members_refusal`."""
    kind = reviewer.get("gate")
    if kind is None:
        return None
    if kind == "verdict":
        return verdict_gate(review)
    if kind == "approval":
        return approval_gate(found["approvals"], reviewer["account"], head)
    raise ValueError(f"unknown gate {kind!r}")


def members_refusal(reviewers: list[dict], found: dict) -> str | None:
    """Why the adapter's `reviews` can't gate these reviewers, or None. A member it doesn't return is refused; an empty
    one is not: then the gate is just unmet."""
    gated = [r["id"] for r in reviewers if r.get("gate") == "approval"]
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
    refused = members_refusal(conf["reviewers"], found)
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
