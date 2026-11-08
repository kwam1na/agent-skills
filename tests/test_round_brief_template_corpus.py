from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]

SKILL = "skills/obtain-review/SKILL.md"
TEMPLATE = "skills/obtain-review/references/round-brief-template.md"
# run-delivery's filled copy of the same contract, which it renders for each lens.
RUN_DELIVERY_BRIEF = "skills/run-delivery/templates/round-brief.md"

MEMBER_SENTENCE = re.compile(r"its members are (.+?)\.\s")
QUOTED_MEMBER = re.compile(r"`([^`]+)`")
TEMPLATE_MEMBER = re.compile(r"^## (.+)$", re.MULTILINE)
ROUND_STATEMENT = re.compile(
    r"^- `([^`]+)`: (.*?)(?=\n- `|\n\n)", re.MULTILINE | re.DOTALL
)

REQUIRED_SKILL_PHRASES = (
    "The brief is [the round brief template]"
    "(references/round-brief-template.md) filled in, not composed",
    "An executor realizing a lens fills the template's members rather than"
    " composing the brief's shape from this text",
)

SKILL_MEMBER_PHRASES = {
    "Binding tuple": "Obtain the trusted comparison context from the harness",
    "Round and bound": "The brief uses exactly one of these round statements",
    "Round kind and scope": (
        "The brief also states the round kind and its resulting scope"
    ),
    "Fewest-rounds mandate": "The brief carries the mandate",
    "Carry-forward entries": (
        "For a verification round the caller supplies each lens"
    ),
    "Candidate reference resolutions": (
        "The caller also supplies, for each of the two `candidateRef` values"
    ),
    "Charter": (
        "Its `Charter` member contains the selected charter verbatim inside the brief"
    ),
    "Result shape": (
        "`review-lens-result/1`: `outcome`, `findings`, `evidence`"
    ),
    "Observation binding and selected output": (
        "Every selected lens runs in its own clean checkout of the prepared"
        " candidate tree"
    ),    "Checks this lens runs": (
        "The brief's `Checks this lens runs` member carries this rule"
    ),
}

REQUIRED_MEMBERS = tuple(SKILL_MEMBER_PHRASES)
ROUND_STATEMENTS = ("ordinary counted", "grace", "reopened", "unbounded")

REQUIRED_TEMPLATE_PHRASES = (
    "Fill every member below",
    "`review-lens-result/1`",
    "`lateFindings`",
    "round k of N",
    "round N+1 with declared bound N",
    "reopened round k",
    "unbounded round k",
    "supplied verbatim",
)

MEMBER_BODY_PHRASES = {
    "Binding tuple": (
        "prepared candidate tree SHA",
        "Opaque references are reproduced exactly",
    ),
    "Round and bound": (
        "Use exactly one of these statements",
        "round N+1 with declared bound N",
    ),
    "Round kind and scope": (
        "assessment round",
        "verification round",
    ),
    "Fewest-rounds mandate": (
        "conscious effort to find everything the lens can in this round",
        "Round N, the last counted round",
    ),
    "Carry-forward entries": (
        "every prior round in which this lens was selected",
        "result-less state where the acquisition failed",
        "most recent result-carrying entry",
    ),
    "Candidate reference resolutions": (
        "For each of the two `candidateRef` values the delta spans",
        "matches each pair by exact text",
    ),
    "Charter": (
        "The selected lens's charter, supplied verbatim inside this brief",
    ),
    "Result shape": (
        "reviewed tree SHA and the successful start and end identity checks",
        "When this lens was realized by the host convention",
    ),
    "Observation binding and selected output": (
        "own clean checkout of the prepared candidate tree",
        "Repeat both checks after the review",
    ),    "Checks this lens runs": (
        "never the repository's full suite",
        "scoped sensor commands",
        "test files in scope for this round",
    ),
}


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.index(marker) + len(marker)
    end = text.find("\n## ", start)
    return text[start:] if end == -1 else text[start:end]


def _round_statements(text: str, heading: str) -> dict[str, str]:
    return {
        label: " ".join(body.split())
        for label, body in ROUND_STATEMENT.findall(_section(text, heading))
    }


# Every place obtain-review's template and run-delivery's rendered briefs say a thing differently, each with why:
# (section, the template's items, run-delivery's items, the reason). An item is a sentence or a whole fenced block.
# run-delivery's side is what run_delivery.py renders, taken together across every round it renders, so drift in its
# rendering code shows here as well as drift in either template. A sentence added to one side only, a shared one
# removed from either, or a fenced block changed in one, makes the briefs differ where nothing declares it: make the
# other side match, or declare the difference here with its reason.
DIVERGENCES = (
    (
        'Binding tuple',
        (
            'The trusted comparison context, obtained from the harness and never from a reviewer result:',
        ),
        (),
        (
            'Addressed to the executor who fills the template; run-delivery reads the tuple from the installed '
            'release itself.'
        ),
    ),
    (
        'Binding tuple',
        (
            '`<exact opaque text>`',
            '`<lens identifier>`',
            '`<positive integer>`',
            '`<tree SHA from the preparation receipt>`',
            '`<value>`',
        ),
        (
            '`1`',
            '`2`',
            '`5`',
            '`<candidate>`',
            '`<graph>` (SHA-256 of workflows/delivery-v1.json in the installed release)',
            '`<tree>`',
            '`EX-1.plan`',
            '`EX-1`',
            '`lens.example`',
        ),
        "The template's placeholders, which run-delivery fills with the round's own values.",
    ),
    (
        'Round and bound',
        (
            'Fill k with `<round>` and N with `<declared bound>` wherever they apply.',
            'Use exactly one of these statements:',
        ),
        (),
        'Instructions to whoever fills the template; run-delivery renders only the statement that applies.',
    ),
    (
        'Round and bound',
        (
            '`ordinary counted`: round k of N, counted against the declared bound;',
        ),
        (
            '`ordinary counted`: round 1 of 4, counted against the declared bound.',
            '`ordinary counted`: round 2 of 4, counted against the declared bound.',
        ),
        'The ordinary counted statement, filled with the round and the bound and ended as a sentence.',
    ),
    (
        'Round and bound',
        (
            '`grace`: round N+1 with declared bound N, outside the count;',
        ),
        (
            '`grace`: round 5 with declared bound 4, outside the count.',
        ),
        'The grace statement, filled with the round and the bound and ended as a sentence.',
    ),
    (
        'Round and bound',
        (
            '`unbounded`: unbounded round k, with no bound supplied or invented.',
        ),
        (
            '`unbounded`: unbounded round 1, with no bound supplied or invented.',
            '`unbounded`: unbounded round 2, with no bound supplied or invented.',
        ),
        'The unbounded statement, filled with the round and ended as a sentence.',
    ),
    (
        'Round and bound',
        (
            "`reopened`: reopened round k, preserving that round's number and naming why it reopened; or",
        ),
        (),
        "run-delivery never reopens a round: a prepared round is reduced, or restarted with the lead's say-so.",
    ),
    (
        'Round kind and scope',
        (
            'State exactly one kind and its scope:',
        ),
        (),
        'Instructions to whoever fills the template; run-delivery renders only the kind that applies.',
    ),
    (
        'Round kind and scope',
        (
            'The whole subject is the delivered diff for delivery review or the plan for plan review.',
            (
                "`assessment round`: this lens's first result-carrying round; review the whole subject and carry no "
                'prior entries or candidate resolution pairs.'
            ),
        ),
        (
            (
                "`assessment round`: this lens's first result-carrying round; review the whole plan, "
                '`docs/plans/example.md` in your checkout, and carry no prior entries or candidate resolution '
                'pairs.'
            ),
            (
                "`assessment round`: this lens's first result-carrying round; review the whole subject, the "
                'delivered diff `git diff <merge base>..<candidate>` in your checkout, and carry no prior entries '
                'or candidate resolution pairs.'
            ),
        ),
        (
            'The assessment statement, naming its subject itself: the plan for a plan review, the delivered diff '
            'otherwise.'
        ),
    ),
    (
        'Round kind and scope',
        (
            (
                '`verification round`: re-check every carried finding and its approval condition against the delta '
                'this lens derives itself from its most recent result-carrying entry and this round, then review '
                'that delta; the executor adds no list.'
            ),
        ),
        (
            (
                '`verification round`: re-check every carried finding and its approval condition against the delta '
                'you derive yourself (`git diff <prior candidate>..<candidate>`), then review that delta.'
            ),
            (
                "`verification round`: re-check every carried finding and its approval condition against the plan's "
                'revision, the delta you derive yourself (`git diff <prior candidate>..<candidate>`), then review '
                'that delta.'
            ),
        ),
        (
            'The verification statement, naming the delta the lens derives as a git diff; run-delivery adds no list '
            'either.'
        ),
    ),
    (
        'Round kind and scope',
        (
            'A grace round is a verification round.',
        ),
        (),
        'run-delivery renders a grace round as a verification round, so it states the result, not the rule.',
    ),
    (
        'Round kind and scope',
        (
            'A reopened round keeps its original kind and scope.',
        ),
        (),
        "run-delivery never reopens a round: a prepared round is reduced, or restarted with the lead's say-so.",
    ),
    (
        'Round kind and scope',
        (
            (
                'The approval conditions add to the carried-finding re-check and the late-finding marking; they '
                'replace neither.'
            ),
        ),
        (),
        (
            "run-delivery's verification round asks for the re-check here and the approval conditions in Result "
            'shape, so neither stands in for the other.'
        ),
    ),
    (
        'Carry-forward entries',
        (
            (
                "A delivery's fixed lens set makes that every prior logical round; another loop requires no "
                'nonexistent entry for a round in which this lens was not selected.'
            ),
            (
                "A replay's active entry replaces its superseded entry here, while the superseded entry remains "
                'retained in delivery history.'
            ),
            'Nothing else, nothing narrower.',
            (
                'Supply one active retained entry from every prior round in which this lens was selected: binding '
                'tuple and result together where the state is `obtained`, and binding tuple with its result-less '
                'state where the acquisition failed.'
            ),
            'These entries are contiguous across the rounds in which this lens was selected.',
        ),
        (),
        'Rules for whoever assembles the entries; run-delivery assembles them from its own round files.',
    ),
    (
        'Carry-forward entries',
        (
            'An assessment round carries none.',
        ),
        (
            'None: this is an assessment round, which carries no prior entries.',
        ),
        'The same rule, stated for the assessment round it renders.',
    ),
    (
        'Carry-forward entries',
        (
            (
                'The lens re-derives its carried set from these entries and derives the delta itself from the '
                "`candidateRef` of its most recent result-carrying entry and this round's, so a round in which its "
                'acquisition failed is spanned rather than skipped.'
            ),
        ),
        (),
        "run-delivery's verification statement has the lens derive the delta itself from the two commits it names.",
    ),
    (
        'Carry-forward entries',
        (
            '`<entry>`',
        ),
        (
            '`{"binding":{"round":1},"state":"obtained","result":{"outcome":"aligned"}}`',
        ),
        "The template's entry placeholder, filled with the prior round's retained entry.",
    ),
    (
        'Carry-forward entries',
        (
            'round 2:',
        ),
        (),
        "The template's second example row; run-delivery lists only rounds that happened.",
    ),
    (
        'Candidate reference resolutions',
        (
            (
                'For each of the two `candidateRef` values the delta spans, a pair of the exact `candidateRef` text '
                "(its `opaque` member) and the revision the adopting repository's instructions declare it resolves "
                'to:'
            ),
            '`<exact opaque text>` resolves to `<revision>`',
        ),
        (
            '`<candidate>` resolves to git commit `<candidate>`',
            '`<prior candidate>` resolves to git commit `<prior candidate>`',
        ),
        "The template's rule and placeholder, filled with the two commits the delta spans.",
    ),
    (
        'Candidate reference resolutions',
        (
            'An assessment round spans no delta and needs no resolution pair.',
        ),
        (
            'None: an assessment round spans no delta and needs no resolution pair.',
        ),
        'The same rule, stated for the assessment round it renders.',
    ),
    (
        'Candidate reference resolutions',
        (
            'The lens matches each pair by exact text, never by parsing.',
        ),
        (),
        "run-delivery's references are commits that resolve to themselves, so there is no opaque text to match.",
    ),
    (
        'Charter',
        (
            'The executor composes nothing about the charter itself.',
            "The selected lens's charter, supplied verbatim inside this brief.",
        ),
        (),
        'Addressed to the executor; run-delivery puts the charter verbatim in the fence below.',
    ),
    (
        'Result shape',
        (
            'Return `review-lens-result/1` with `outcome`, `findings`, `evidence`, and the optional `lateFindings`.',
        ),
        (
            'Return `review-lens-result/1` as one JSON object, and nothing else, as your final message:',
            (
                '```json {"schemaVersion": "review-lens-result/1", "lensId": "lens.example", "subjectRef": "EX-1", '
                '"candidateRef": "<candidate>", "round": 1, "outcome": "aligned | changes-requested", "findings": '
                '[{"id": "example-r1-1", "severity": "P0|P1|P2|P3", "summary": "...", "location": "path:line", '
                '"failureScenario": "...", "proposedChange": "..."}], "evidence": ["..."]}```'
            ),
            (
                '```json {"schemaVersion": "review-lens-result/1", "lensId": "lens.example", "subjectRef": "EX-1", '
                '"candidateRef": "<candidate>", "round": 2, "outcome": "aligned | changes-requested", "findings": '
                '[{"id": "example-r2-1", "severity": "P0|P1|P2|P3", "summary": "...", "location": "path:line", '
                '"failureScenario": "...", "proposedChange": "..."}], "evidence": ["..."]}```'
            ),
            (
                '```json {"schemaVersion": "review-lens-result/1", "lensId": "lens.example", "subjectRef": "EX-1", '
                '"candidateRef": "<candidate>", "round": 5, "outcome": "aligned | changes-requested", "findings": '
                '[{"id": "example-r5-1", "severity": "P0|P1|P2|P3", "summary": "...", "location": "path:line", '
                '"failureScenario": "...", "proposedChange": "..."}], "evidence": ["..."]}```'
            ),
            (
                '```json {"schemaVersion": "review-lens-result/1", "lensId": "lens.example", "subjectRef": '
                '"EX-1.plan", "candidateRef": "<candidate>", "round": 1, "outcome": "aligned | changes-requested", '
                '"findings": [{"id": "example-r1-1", "severity": "P0|P1|P2|P3", "summary": "...", "location": '
                '"path:line", "failureScenario": "...", "proposedChange": "..."}], "evidence": ["..."]}```'
            ),
            (
                '```json {"schemaVersion": "review-lens-result/1", "lensId": "lens.example", "subjectRef": '
                '"EX-1.plan", "candidateRef": "<candidate>", "round": 2, "outcome": "aligned | changes-requested", '
                '"findings": [{"id": "example-r2-1", "severity": "P0|P1|P2|P3", "summary": "...", "location": '
                '"path:line", "failureScenario": "...", "proposedChange": "..."}], "evidence": ["..."]}```'
            ),
        ),
        (
            "run-delivery asks for the result as the lens's only output and shows its exact shape, filled for the "
            'round.'
        ),
    ),
    (
        'Result shape',
        (
            'Only `changes-requested` carries nonempty findings; both carry evidence.',
        ),
        (
            'Only `changes-requested` carries open P0 or P1 findings; both carry evidence.',
        ),
        (
            "The same rule, read for run-delivery's results, which carry deferred P2 and P3 findings beside the "
            'open ones.'
        ),
    ),
    (
        'Result shape',
        (
            "`findings`: only P0 and P1 findings inside the round's scope are actionable.",
        ),
        (
            'P0 and P1 are actionable, and a P0 is never deferred.',
            'P0 and P1 are actionable, and a plan review never defers them.',
            (
                'P2 and P3 are deferrable: file each with `"state": "deferred"` and `"deferredIssue": "<what its '
                'follow-up item must say>"`, and so is that P1, which also lists its id in `lateFindings`.'
            ),
            (
                'P2 and P3 notes belong in `evidence`, never in `findings`: a plan review has no deferral path, so '
                'a result carries no `state` or `deferredIssue` on any finding.'
            ),
            '`findings`: every finding, with a unique id starting `example-r1-` and one severity, P0 to P3.',
            '`findings`: every finding, with a unique id starting `example-r2-` and one severity, P0 to P3.',
            '`findings`: every finding, with a unique id starting `example-r5-` and one severity, P0 to P3.',
        ),
        "run-delivery states its story and plan reviews' severity rules, with finding ids its review-reduce checks.",
    ),
    (
        'Result shape',
        (
            (
                'A late finding the out-of-delta rule keeps out of `findings` is marked late in the deferral that '
                'records it instead.'
            ),
        ),
        (),
        (
            'How a late deferral is recorded, not what the rule is: run-delivery keeps the deferred P1 in `findings` '
            'with `"state": "deferred"` and lists its id in `lateFindings`, as its severities say.'
        ),
    ),
    (
        'Result shape',
        (
            (
                'End `evidence` with the approval conditions: for each finding this result raises, deferrals '
                'included, whether the change that finding proposes, adopted as written, resolves it.'
            ),
            (
                'When this lens was realized by the host convention, also record the realization basis '
                '`host-convention`; otherwise record the harness-supplied basis.'
            ),
            (
                '`evidence`: the reviewed tree SHA and the successful start and end identity checks; the re-check '
                'of every carried finding of this lens, deferrals included, each reported closed, open, raised with '
                'its new severity, withdrawn with its reason, or still deferred; for delivery review, the '
                "deferrable findings, each with what its follow-up item must say, that item's outcome, the "
                "deferral's identifier, and this lens; and the model class the lens ran under."
            ),
        ),
        (
            "A deferred finding doesn't block `aligned`.",
            (
                'End `evidence` with the approval conditions: for each finding you raise, whether its proposed '
                'change, adopted as written, resolves it.'
            ),
            (
                '`evidence` (strings, required): the reviewed tree SHA `<tree>` and the successful start and end '
                'identity checks; the re-check of every carried finding; the model class you ran under; the '
                'realization basis `host-convention`.'
            ),
        ),
        (
            "The same evidence and approval conditions, addressed to the lens; run-delivery's lenses run only by "
            'host convention.'
        ),
    ),
    (
        'Result shape',
        (
            (
                'Approval conditions are report text inside `evidence`; the result shape does not change, and '
                'nothing enforces them.'
            ),
        ),
        (),
        'Explains the approval conditions to whoever reads the template; the lens needs only the rule.',
    ),
    (
        'Result shape',
        (
            'A lens carries none on its assessment round.',
            "`lateFindings`: each names one of this same result's own `findings` that the lens raised late.",
        ),
        (
            (
                "`lateFindings`: ids of this result's own findings you raised late (against lines unchanged since "
                'your previous round); omit when none.'
            ),
            '`lateFindings`: none on an assessment round; omit it.',
        ),
        'The same late-finding rule, rendered for the round.',
    ),
    (
        'Result shape',
        (),
        (
            (
                "Every finding this lens raised in an earlier round that is still open must appear (one deferred "
                "earlier needn't); one closed in an earlier round may be listed again or left out."
            ),
            'A deferral (P2/P3 only) also carries `deferredIssue`.',
            'Every carried finding must appear; a plan review defers none.',
            (
                'One entry for every finding this lens raised in an earlier round and has not closed, `{"id", '
                '"state": "closed|fixed|withdrawn|open", "resolution": "..."}`.'
            ),
            (
                'One entry per finding, `{"id", "state": "closed|fixed|withdrawn|deferred|open", "resolution": '
                '"..."}`.'
            ),
            '`resolvedFindings`: required.',
        ),
        (
            "run-delivery's own resolvedFindings record, which its review-reduce checks; the template carries "
            'findings forward as entries.'
        ),
    ),
    (
        'Observation binding and selected output',
        (
            'Capture actual review, dissent, clarification and available partial-output bytes before cleanup.',
            'Name a superseded attempt only for an actual replacement.',
            (
                'Permission observations never grant authority; reviewer verdict and acquisition completion remain '
                'distinct.'
            ),
            'Record the host handle and any progress/permission signal limitation; do not parse transcripts.',
            'Return stable artifact/report references or an explicit unavailable reason.',
            (
                'Supply actual run ID, candidate tree from prepared context, activity ID, attempt ID, '
                'roundId/round/lensId, stable event IDs, and selected bounded output paths.'
            ),
            'Use the shared `$execute-work/references/run-observation-contract.md`.',
        ),
        (
            (
                'Host: a native subagent; no progress or permission signals are exposed to you; record that '
                'limitation in evidence.'
            ),
            (
                'Run ID: none (the repository declares no run-event command). activityId `review-EX-1.plan`, '
                'attemptId `1`, roundId `EX-1.plan-r1`, round `1`, lensId `lens.example`.'
            ),
            (
                'Run ID: none (the repository declares no run-event command). activityId `review-EX-1.plan`, '
                'attemptId `1`, roundId `EX-1.plan-r2`, round `2`, lensId `lens.example`.'
            ),
            (
                'Run ID: none (the repository declares no run-event command). activityId `review-EX-1`, attemptId '
                '`1`, roundId `EX-1-r1`, round `1`, lensId `lens.example`.'
            ),
            (
                'Run ID: none (the repository declares no run-event command). activityId `review-EX-1`, attemptId '
                '`1`, roundId `EX-1-r2`, round `2`, lensId `lens.example`.'
            ),
            (
                'Run ID: none (the repository declares no run-event command). activityId `review-EX-1`, attemptId '
                '`1`, roundId `EX-1-r5`, round `5`, lensId `lens.example`.'
            ),
        ),
        (
            'Addressed to the executor recording the run; run-delivery records no run events and says so in the Run '
            'ID line.'
        ),
    ),
    (
        'Observation binding and selected output',
        (
            (
                'Run this lens in its own clean checkout of the prepared candidate tree, separate from the executor '
                'and every other lens.'
            ),
        ),
        (
            'Run every command there, never in any other checkout.',
            'Your private checkout:',
            (
                "`<checkout>` (detached at `<candidate>`, prepared with the repository's own prepare commands, so "
                'its tests can run there).'
            ),
        ),
        'The same private checkout, named for the lens.',
    ),
    (
        'Observation binding and selected output',
        (
            (
                'A failed check fails the acquisition; do not return a lens result from a checkout that moved or '
                'stayed dirty.'
            ),
            (
                'Before the lens reads the checkout, verify that its tree SHA equals the prepared candidate tree '
                'SHA and its status is clean.'
            ),
            'Repeat both checks after the review.',
            (
                "Temporary probes are permitted only in this private checkout, never in the executor's checkout or "
                'another shared checkout, and must be removed before the ending checks.'
            ),
        ),
        (
            (
                'Before reading: verify `git rev-parse HEAD^{tree}` equals `<tree>` and `git status --porcelain` is '
                'empty.'
            ),
            'If either fails, report that instead of a result.',
            'Repeat both after the review.',
            (
                'Temporary probes (e.g. mutants) are permitted only in this private checkout and must be reverted '
                '(`git checkout -- .` and removing any file you added) before the ending checks.'
            ),
        ),
        'The same identity checks, probe rule and failure, as the commands the lens runs.',
    ),
    (
        'Observation binding and selected output',
        (),
        (
            'Do NOT spawn subagents.',
            'Do not push, commit, or touch any checkout other than yours.',
        ),
        "run-delivery's lenses are subagents of a lane's session, so they spawn none and touch no other checkout.",
    ),
    (
        'Checks this lens runs',
        (
            (
                'A lens needing evidence beyond that set names each additional test file, one path at a time, with '
                'what it is evidence for, and never widens a run to a directory.'
            ),
            (
                "A lens that mutates or injects faults limits both to the candidate's changed lines and the code "
                'that calls them, and says so in its evidence.'
            ),
            (
                "The lens runs the adopting repository's scoped check set in its own checkout, never the "
                "repository's full suite: the full suite runs once, outside the round, at the delivery's tail."
            ),
        ),
        (
            (
                'Evidence beyond that set names each additional test file, one path at a time, with what it is '
                'evidence for; never widen a run to a directory.'
            ),
            (
                "Mutation and fault injection stay on the candidate's changed lines and the code that calls them, "
                'and your `evidence` says so.'
            ),
            (
                "Run the scoped check set below in your checkout, never the repository's full suite: the full suite "
                "runs once, outside the round, at the delivery's tail."
            ),
        ),
        'The same rules, addressed to the lens.',
    ),
    (
        'Checks this lens runs',
        (
            'The executor fills both, per round:',
            '`<commands>`',
            '`<paths>`',
            'scoped sensor commands:',
            'test files in scope for this round:',
        ),
        (
            '`tests/test_example.py`.',
            'scoped sensor commands: none; a plan review runs no checks, since the plan is the whole subject.',
            'scoped sensor commands: the sensors the plan unit names, in the context refs below.',
            'test files in scope for this round: none.',
            (
                'test files in scope for this round: the tests among the files `git diff <merge base>..<candidate>` '
                'changes, and the tests that cover the rest:'
            ),
            (
                'test files in scope for this round: the tests among the files `git diff <prior '
                'candidate>..<candidate>` changes, and the tests that cover the rest:'
            ),
        ),
        (
            "The template's placeholders, filled per round: the plan unit's sensors and the files the diff changes, "
            'or none for a plan review.'
        ),
    ),
)


# What only one kind of review renders, with why: (kind, section, its items, the reason). Taken together the two kinds
# could stand in for each other, so each kind's renders must match this exactly: a line run_delivery.py drops from one
# kind while the other still renders it shows here.
KIND_ONLY = (
    (
        'story review',
        'Binding tuple',
        (
            '`5`',
            '`EX-1`',
        ),
        "A story is keyed by its ticket, and only a story's review reaches a grace round.",
    ),
    (
        'plan review',
        'Binding tuple',
        (
            '`EX-1.plan`',
        ),
        "A plan review is keyed by its delivery's plan.",
    ),
    (
        'story review',
        'Round and bound',
        (
            '`grace`: round 5 with declared bound 4, outside the count.',
            '`ordinary counted`: round 1 of 4, counted against the declared bound.',
            '`ordinary counted`: round 2 of 4, counted against the declared bound.',
        ),
        "A story's review is bounded, so its rounds are counted, then grace.",
    ),
    (
        'plan review',
        'Round and bound',
        (
            '`unbounded`: unbounded round 1, with no bound supplied or invented.',
            '`unbounded`: unbounded round 2, with no bound supplied or invented.',
        ),
        'A plan review is unbounded.',
    ),
    (
        'story review',
        'Round kind and scope',
        (
            (
                "`assessment round`: this lens's first result-carrying round; review the whole subject, the "
                'delivered diff `git diff <merge base>..<candidate>` in your checkout, and carry no prior entries '
                'or candidate resolution pairs.'
            ),
            (
                '`verification round`: re-check every carried finding and its approval condition against the delta '
                'you derive yourself (`git diff <prior candidate>..<candidate>`), then review that delta.'
            ),
        ),
        "A story's subject is its delivered diff.",
    ),
    (
        'plan review',
        'Round kind and scope',
        (
            (
                "`assessment round`: this lens's first result-carrying round; review the whole plan, "
                '`docs/plans/example.md` in your checkout, and carry no prior entries or candidate resolution '
                'pairs.'
            ),
            (
                "`verification round`: re-check every carried finding and its approval condition against the plan's "
                'revision, the delta you derive yourself (`git diff <prior candidate>..<candidate>`), then review '
                'that delta.'
            ),
        ),
        "A plan review's subject is the plan.",
    ),
    (
        'story review',
        'Result shape',
        (
            "A deferred finding doesn't block `aligned`.",
            (
                "Every finding this lens raised in an earlier round that is still open must appear (one deferred "
                "earlier needn't); one closed in an earlier round may be listed again or left out."
            ),
            'A deferral (P2/P3 only) also carries `deferredIssue`.',
            (
                'One entry per finding, `{"id", "state": "closed|fixed|withdrawn|deferred|open", "resolution": '
                '"..."}`.'
            ),
            'P0 and P1 are actionable, and a P0 is never deferred.',
            (
                'P2 and P3 are deferrable: file each with `"state": "deferred"` and `"deferredIssue": "<what its '
                'follow-up item must say>"`, and so is that P1, which also lists its id in `lateFindings`.'
            ),
            (
                '```json {"schemaVersion": "review-lens-result/1", "lensId": "lens.example", "subjectRef": "EX-1", '
                '"candidateRef": "<candidate>", "round": 1, "outcome": "aligned | changes-requested", "findings": '
                '[{"id": "example-r1-1", "severity": "P0|P1|P2|P3", "summary": "...", "location": "path:line", '
                '"failureScenario": "...", "proposedChange": "..."}], "evidence": ["..."]}```'
            ),
            (
                '```json {"schemaVersion": "review-lens-result/1", "lensId": "lens.example", "subjectRef": "EX-1", '
                '"candidateRef": "<candidate>", "round": 2, "outcome": "aligned | changes-requested", "findings": '
                '[{"id": "example-r2-1", "severity": "P0|P1|P2|P3", "summary": "...", "location": "path:line", '
                '"failureScenario": "...", "proposedChange": "..."}], "evidence": ["..."]}```'
            ),
            (
                '```json {"schemaVersion": "review-lens-result/1", "lensId": "lens.example", "subjectRef": "EX-1", '
                '"candidateRef": "<candidate>", "round": 5, "outcome": "aligned | changes-requested", "findings": '
                '[{"id": "example-r5-1", "severity": "P0|P1|P2|P3", "summary": "...", "location": "path:line", '
                '"failureScenario": "...", "proposedChange": "..."}], "evidence": ["..."]}```'
            ),
            '`findings`: every finding, with a unique id starting `example-r5-` and one severity, P0 to P3.',
        ),
        "A story's review defers P2 and P3 and keys its result by the ticket, up to a grace round.",
    ),
    (
        'plan review',
        'Result shape',
        (
            'Every carried finding must appear; a plan review defers none.',
            (
                'One entry for every finding this lens raised in an earlier round and has not closed, `{"id", '
                '"state": "closed|fixed|withdrawn|open", "resolution": "..."}`.'
            ),
            'P0 and P1 are actionable, and a plan review never defers them.',
            (
                'P2 and P3 notes belong in `evidence`, never in `findings`: a plan review has no deferral path, so '
                'a result carries no `state` or `deferredIssue` on any finding.'
            ),
            (
                '```json {"schemaVersion": "review-lens-result/1", "lensId": "lens.example", "subjectRef": '
                '"EX-1.plan", "candidateRef": "<candidate>", "round": 1, "outcome": "aligned | changes-requested", '
                '"findings": [{"id": "example-r1-1", "severity": "P0|P1|P2|P3", "summary": "...", "location": '
                '"path:line", "failureScenario": "...", "proposedChange": "..."}], "evidence": ["..."]}```'
            ),
            (
                '```json {"schemaVersion": "review-lens-result/1", "lensId": "lens.example", "subjectRef": '
                '"EX-1.plan", "candidateRef": "<candidate>", "round": 2, "outcome": "aligned | changes-requested", '
                '"findings": [{"id": "example-r2-1", "severity": "P0|P1|P2|P3", "summary": "...", "location": '
                '"path:line", "failureScenario": "...", "proposedChange": "..."}], "evidence": ["..."]}```'
            ),
        ),
        "A plan review defers nothing and keys its result by the delivery's plan.",
    ),
    (
        'story review',
        'Observation binding and selected output',
        (
            (
                'Run ID: none (the repository declares no run-event command). activityId `review-EX-1`, attemptId '
                '`1`, roundId `EX-1-r1`, round `1`, lensId `lens.example`.'
            ),
            (
                'Run ID: none (the repository declares no run-event command). activityId `review-EX-1`, attemptId '
                '`1`, roundId `EX-1-r2`, round `2`, lensId `lens.example`.'
            ),
            (
                'Run ID: none (the repository declares no run-event command). activityId `review-EX-1`, attemptId '
                '`1`, roundId `EX-1-r5`, round `5`, lensId `lens.example`.'
            ),
        ),
        "Each kind's Run ID line names its own subject and rounds.",
    ),
    (
        'plan review',
        'Observation binding and selected output',
        (
            (
                'Run ID: none (the repository declares no run-event command). activityId `review-EX-1.plan`, '
                'attemptId `1`, roundId `EX-1.plan-r1`, round `1`, lensId `lens.example`.'
            ),
            (
                'Run ID: none (the repository declares no run-event command). activityId `review-EX-1.plan`, '
                'attemptId `1`, roundId `EX-1.plan-r2`, round `2`, lensId `lens.example`.'
            ),
        ),
        "Each kind's Run ID line names its own subject and rounds.",
    ),
    (
        'story review',
        'Checks this lens runs',
        (
            '`tests/test_example.py`.',
            'scoped sensor commands: the sensors the plan unit names, in the context refs below.',
            (
                'test files in scope for this round: the tests among the files `git diff <merge base>..<candidate>` '
                'changes, and the tests that cover the rest:'
            ),
            (
                'test files in scope for this round: the tests among the files `git diff <prior '
                'candidate>..<candidate>` changes, and the tests that cover the rest:'
            ),
        ),
        "A story's lens runs the plan unit's sensors over the files its diff changes.",
    ),
    (
        'plan review',
        'Checks this lens runs',
        (
            'scoped sensor commands: none; a plan review runs no checks, since the plan is the whole subject.',
            'test files in scope for this round: none.',
        ),
        'A plan review runs no checks, since the plan is the whole subject.',
    ),
)

# Sections only run-delivery's brief has, each filled whole from one placeholder by run_delivery.py.
RUN_DELIVERY_ONLY_SECTIONS = (
    "In-app evidence so far",
    "Context refs (caller-retained material: ticket contract and plan unit)",
)


def _brief_sections(text: str) -> dict[str, str]:
    parts = re.split(r"(?m)^## (.+)$", text)
    return dict(zip(parts[1::2], parts[2::2]))


FENCE = re.compile(r"```.*?```", re.DOTALL)


def _items(body: str) -> list[str]:
    """A section as compared items, whitespace-normalized: its prose split at list items, paragraphs and sentence
    ends, and each fenced block whole, in place."""
    fences = []

    def mark(match: re.Match) -> str:
        fences.append("```" + " ".join(match.group(0).strip("`").split()) + "```")
        return f"\n\n\x00{len(fences) - 1}\x00\n\n"

    found = []
    for item in re.split(r"(?m)^\s*-\s+|\n\s*\n", FENCE.sub(mark, body)):
        item = " ".join(item.split())
        if re.fullmatch(r"\x00\d+\x00", item):
            found.append(fences[int(item.strip("\x00"))])
        elif item:
            found += [part for part in re.split(r"(?<=[.;:])\s+(?=[A-Z`(])", item) if part]
    return found


_TOOL = None


def _tool():
    """run_delivery.py, loaded once without writing bytecode, for rendering briefs the way a delivery does."""
    global _TOOL
    if _TOOL is None:
        spec = importlib.util.spec_from_file_location("run_delivery_for_round_briefs",
                                                      ROOT / "skills/run-delivery/scripts/run_delivery.py")
        module = importlib.util.module_from_spec(spec)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = previous
        _TOOL = module
    return _TOOL


def _render(brief_template: str) -> list[str]:
    """run-delivery's brief as run_delivery.py renders it from `brief_template`, for every round it writes: a story's
    assessment, verification and grace rounds, and a plan review's assessment and verification rounds. Only what a
    delivery would read from its release, settings, review folder and git is stood in for."""
    tool = _tool()
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "templates").mkdir()
        (root / "templates/round-brief.md").write_text(brief_template, encoding="utf-8")
        (root / "charter.md").write_text("<charter text, verbatim>\n", encoding="utf-8")
        (root / "context.md").write_text("<context refs>\n", encoding="utf-8")
        config = types.SimpleNamespace(lenses={"lens.example": ("example", str(root / "charter.md"))}, bound=4,
                                       plan="docs/plans/example.md", delivery="EX-1", plan_store=None)
        release = {name: "<value>" for name in ("releaseId", "profile", "archiveSha256", "metadataSha256")}
        prior = [{"binding": {"round": 1}, "state": "obtained", "result": {"outcome": "aligned"}}]
        story = ("EX-1", "lens.example", "<candidate>", "<tree>", "<merge base>", Path("<checkout>"))
        with mock.patch.multiple(tool, TOOL=root, cfg=lambda: config, release_tuple=lambda: release,
                                 graph_digest=lambda: "<graph>", review_dir=lambda key, root=None: Path(folder),
                                 in_app_summary=lambda key: "<in-app evidence>",
                                 git=lambda *args, **kwargs: "tests/test_example.py",
                                 plan_review_subject=lambda c: "EX-1.plan"):
            key, lens, candidate, tree, merge_base, checkout = story
            return [
                tool.brief(key, 1, lens, candidate, tree, merge_base, checkout, [], None),
                tool.brief(key, 2, lens, candidate, tree, merge_base, checkout, prior, "<prior candidate>"),
                tool.brief(key, 5, lens, candidate, tree, merge_base, checkout, prior, "<prior candidate>"),
                tool.plan_brief(config, 1, lens, candidate, tree, checkout, [], None),
                tool.plan_brief(config, 2, lens, candidate, tree, checkout, prior, "<prior candidate>"),
            ]


def _divergence(template: str, renders: list[str]) -> dict[str, tuple[set[str], set[str]]]:
    """For each template section, the items only the template has and the items only run-delivery's rendered briefs
    have, those taken together across every round it renders."""
    found = {}
    for heading, body in _brief_sections(template).items():
        theirs = {item for render in renders for item in _items(_brief_sections(render).get(heading, ""))}
        mine = set(_items(body))
        found[heading] = (mine - theirs, theirs - mine)
    return found


class RoundBriefTemplateCorpusTests(unittest.TestCase):
    """Pin the shipped round brief template against the skill text that names it."""

    def test_template_ships_beside_the_skill(self) -> None:
        self.assertTrue((ROOT / TEMPLATE).is_file(), TEMPLATE)

    def test_skill_text_points_at_the_template(self) -> None:
        normalized = " ".join(_read(SKILL).split())
        for phrase in REQUIRED_SKILL_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, normalized)

    def test_template_carries_its_declared_phrases(self) -> None:
        normalized = " ".join(_read(TEMPLATE).split())
        for phrase in REQUIRED_TEMPLATE_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, normalized)

    def test_member_sets_match_in_both_directions(self) -> None:
        normalized_skill = " ".join(_read(SKILL).split())
        sentence = MEMBER_SENTENCE.search(normalized_skill + " ")
        self.assertIsNotNone(sentence, "the skill text names no template members")
        named = QUOTED_MEMBER.findall(sentence.group(1))
        self.assertTrue(named, "the member sentence quotes no member")
        self.assertEqual(len(named), len(set(named)), named)
        self.assertEqual(set(named), set(REQUIRED_MEMBERS))

        shipped = TEMPLATE_MEMBER.findall(_read(TEMPLATE))
        self.assertTrue(shipped, "the template declares no member heading")
        self.assertEqual(len(shipped), len(set(shipped)), shipped)
        self.assertEqual(set(shipped), set(REQUIRED_MEMBERS))

        self.assertEqual(
            set(named) - set(shipped),
            set(),
            "the skill text names a member the template omits",
        )
        self.assertEqual(
            set(shipped) - set(named),
            set(),
            "the template ships a member the skill text does not name",
        )

        for member, phrase in SKILL_MEMBER_PHRASES.items():
            with self.subTest(member=member, phrase=phrase):
                self.assertIn(phrase, normalized_skill)

    def test_round_statement_sets_match_in_both_directions(self) -> None:
        skill_statements = _round_statements(_read(SKILL), "The round brief")
        template_statements = _round_statements(_read(TEMPLATE), "Round and bound")
        self.assertEqual(tuple(skill_statements), ROUND_STATEMENTS)
        self.assertEqual(tuple(template_statements), ROUND_STATEMENTS)
        self.assertEqual(skill_statements, template_statements)

    def test_each_required_member_carries_its_normative_body(self) -> None:
        template = _read(TEMPLATE)
        matches = list(TEMPLATE_MEMBER.finditer(template))
        bodies: dict[str, str] = {}
        for index, match in enumerate(matches):
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(template)
            )
            bodies[match.group(1)] = " ".join(template[match.end() : end].split())

        self.assertEqual(set(MEMBER_BODY_PHRASES), set(REQUIRED_MEMBERS))
        for member, phrases in MEMBER_BODY_PHRASES.items():
            for phrase in phrases:
                with self.subTest(member=member, phrase=phrase):
                    self.assertIn(phrase, bodies[member])


class RoundBriefDriftTests(unittest.TestCase):
    """obtain-review's template and run-delivery's brief, as run_delivery.py renders it, are two copies of one
    contract: neither drifts alone, and every place they differ says why."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.template, cls.brief = _read(TEMPLATE), _read(RUN_DELIVERY_BRIEF)
        cls.renders = _render(cls.brief)

    def declared(self) -> dict[str, tuple[set[str], set[str]]]:
        found: dict[str, tuple[set[str], set[str]]] = {}
        for heading, ours, theirs, _ in DIVERGENCES:
            mine, filled = found.setdefault(heading, (set(), set()))
            mine.update(ours)
            filled.update(theirs)
        return found

    def assertDeclared(self, found: dict[str, tuple[set[str], set[str]]]) -> None:
        declared = self.declared()
        for heading, (only_template, only_brief) in found.items():
            with self.subTest(section=heading):
                expected = declared.get(heading, (set(), set()))
                self.assertEqual(only_template, expected[0],
                                 "obtain-review's template differs from run-delivery's brief where nothing declares it")
                self.assertEqual(only_brief, expected[1], "run-delivery's rendered brief differs from obtain-review's "
                                                          "template where nothing declares it")

    def test_run_delivery_carries_every_template_section_and_only_its_declared_extras(self) -> None:
        # Raw template bodies, fences included, so no rule can sit in a section the comparison doesn't walk.
        # Above the first section run-delivery's brief carries only its title, so no rule sits outside every section.
        preamble = self.brief[:self.brief.index("\n## ")]
        self.assertRegex(preamble, r"^# Round brief: [^\n]*\n*$")
        self.assertEqual(re.sub(r"\{\{\w+\}\}", "", preamble.splitlines()[0]), "# Round brief: ,  round ")
        extras = set(_brief_sections(self.brief)) - set(_brief_sections(self.template))
        self.assertEqual(set(_brief_sections(self.template)) - set(_brief_sections(self.brief)), set())
        self.assertEqual(extras, set(RUN_DELIVERY_ONLY_SECTIONS))
        for heading in RUN_DELIVERY_ONLY_SECTIONS:
            with self.subTest(extra=heading):
                self.assertRegex(_brief_sections(self.brief)[heading].strip(), r"^\{\{\w+\}\}$")

    def test_the_briefs_differ_only_where_declared(self) -> None:
        self.assertEqual(len(self.renders), 5)
        found = _divergence(self.template, self.renders)
        self.assertEqual(set(self.declared()) - set(found), set(), "a declared section isn't in the template")
        self.assertDeclared(found)

    def test_each_kind_of_review_renders_what_it_shares_with_the_other(self) -> None:
        # Taken together, one kind of review could stand in for the other: a line run_delivery.py drops from a story's
        # brief would still be found in a plan review's. So what each kind renders alone is declared, exactly.
        declared = {(kind, heading): set(items) for kind, heading, items, _ in KIND_ONLY}
        kinds = {"story review": self.renders[:3], "plan review": self.renders[3:]}
        for heading in _brief_sections(self.template):
            carried = {kind: {item for render in renders for item in _items(_brief_sections(render)[heading])}
                       for kind, renders in kinds.items()}
            for kind, other in (("story review", "plan review"), ("plan review", "story review")):
                with self.subTest(section=heading, kind=kind):
                    self.assertEqual(carried[kind] - carried[other], declared.get((kind, heading), set()),
                                     f"a {kind} renders, or no longer renders, a line the other kind does")
        for kind, heading, items, reason in KIND_ONLY:
            with self.subTest(kind=kind, section=heading):
                self.assertIn(kind, kinds)
                self.assertTrue(items)
                self.assertRegex(reason, r"^[A-Za-z`].{19,199}$")

    def test_every_difference_says_why(self) -> None:
        seen: set[tuple[str, str, str]] = set()
        for heading, ours, theirs, reason in DIVERGENCES:
            with self.subTest(section=heading, reason=reason[:50]):
                self.assertTrue(ours or theirs, "an entry declares nothing")
                self.assertRegex(reason, r"^[A-Za-z`].{19,199}$", "a reason is one line, of 20 to 200 characters")
                self.assertNotIn("\n", reason)
                for side, items in (("template", ours), ("brief", theirs)):
                    for item in items:
                        self.assertNotIn((heading, side, item), seen, "an item is declared twice")
                        seen.add((heading, side, item))

    def test_the_rules_both_carry_are_compared(self) -> None:
        # The stopping rule nearly shipped in one brief only: it is a shared sentence, so it is compared.
        rendered = {item for render in self.renders for item in _items(_brief_sections(render)["Result shape"])}
        shared = [item for item in _items(_brief_sections(self.template)["Result shape"]) if item in rendered]
        self.assertIn("A lens is read-only, files no tracked item itself, and cannot return `failed`.", shared)
        self.assertTrue(any(item.startswith("It stops only processes it started") for item in shared))
        mandate = _items(_brief_sections(self.template)["Fewest-rounds mandate"])
        self.assertTrue(mandate)
        self.assertEqual(_divergence(self.template, self.renders)["Fewest-rounds mandate"], (set(), set()))
        # The charter's fence is compared too: the template's placeholder is what run-delivery puts there.
        self.assertIn("```<charter text, verbatim>```", _items(_brief_sections(self.template)["Charter"]))

    def test_removing_any_shared_sentence_from_either_brief_is_seen(self) -> None:
        baseline = _divergence(self.template, self.renders)
        checked = 0
        for heading, ours in _brief_sections(self.template).items():
            theirs = _brief_sections(self.brief)[heading]
            rendered = {item for render in self.renders for item in _items(_brief_sections(render)[heading])}
            for sentence in [s for s in _items(ours) if s in rendered and s in _items(theirs)]:
                for side in ("template", "brief"):
                    with self.subTest(section=heading, side=side, sentence=sentence[:60]):
                        body = ours if side == "template" else theirs
                        # Cut the item alone, leaving the section's lists and paragraphs as they were.
                        cut = re.sub(r"\s+".join(map(re.escape, sentence.split())), "", body, count=1)
                        self.assertNotEqual(cut, body)
                        if side == "template":
                            changed = _divergence(self.template.replace(ours, cut), self.renders)
                        else:
                            changed = _divergence(self.template, _render(self.brief.replace(theirs, cut)))
                        self.assertNotEqual(changed, baseline)
                        # It is now the uncut side's alone, and named as such.
                        self.assertIn(sentence, changed[heading][1 if side == "template" else 0])
                        checked += 1
        self.assertGreater(checked, 40)

    def test_a_fenced_block_changed_in_either_brief_is_seen(self) -> None:
        baseline = _divergence(self.template, self.renders)
        checked = 0
        for side, text in (("template", self.template), ("brief", self.brief)):
            for heading, body in _brief_sections(text).items():
                if heading in RUN_DELIVERY_ONLY_SECTIONS:
                    continue
                for fence in FENCE.findall(body):
                    with self.subTest(side=side, section=heading, fence=fence[:40]):
                        edited = fence[:-3] + "an added rule\n```"
                        changed_text = text.replace(fence, edited, 1)
                        if side == "template":
                            changed = _divergence(changed_text, self.renders)
                        else:
                            changed = _divergence(self.template, _render(changed_text))
                        self.assertNotEqual(changed, baseline)
                        named = changed[heading][0 if side == "template" else 1]
                        self.assertTrue(any("an added rule" in item for item in named))
                        checked += 1
        # The charter's fence in both, and run-delivery's result shape.
        self.assertGreaterEqual(checked, 3)


if __name__ == "__main__":
    unittest.main()
