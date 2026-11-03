---
name: review-work
description: Review a delivery candidate through caller-selected lenses and return a bounded convergence outcome.
---

# Review Work

## Purpose

Reduce independent, evidence-bearing lens results into one portable review
outcome. Repository or host policy selects the lenses and obtains the review
evidence. This workflow does not name reviewers, invoke tools, or replace a
repository's review authority.

## Inputs

Provide the required lens names, a positive delivery-review round bound, and the
completed lens results for each round. This workflow bounds delivery review;
plan review remains outside this reduction and may be unbounded. The bound is
declared by the caller before the first delivery-review round and belongs to the
delivery. Every counted delivery-review round obtained is supplied to each
reduction, and every obtained pass, including a grace or reopened pass, remains
in the supplied history. The last counted delivery-review round is round N. When the workflow
permits it, the single grace verification round is round N+1 with declared bound
N and remains outside the count. A reopened pass preserves the logical round's
number and kind and does not create another counted round. The workflow default
is four rounds, and a repository instruction file may declare a smaller bound;
a larger bound is an operator decision recorded before the first round. The
review is bound to the release installed
when the delivery's first round is acquired: a release installed mid-review is
recorded and does not invalidate rounds already carried, and the delivery's
closing record names the bound release's `releaseId` and `archiveSha256`.

Every aligned or changes-requested result includes
caller-owned evidence. A changes-requested result also names its actionable
findings. A failed result names the failure.

Use only the lenses required by the adopting repository and the candidate's
risk. Do not turn the core contract into a universal reviewer list or scoring
policy.

## Convergence

P0 and P1 findings are blocking and are never deferred. A P2 or P3 finding may
be deferred only when it is actionable, nonblocking, and names its tracked
follow-up item. Under `review.green/1` that exception is limited to `expansion`
scope. An `in_contract` P2 or P3 deferral requires the consumer to declare
`review.green/2` in the review obligation's accepted payload specs; its evidence
producer must then select `review.green/2`. That version admits `in_contract`
and `expansion` deferrals under the same rule, never `adjacent` findings. A
deferral recorded in evidence is not an open finding. A deferral's follow-up is
a tracked item the executor records before the delivery reports done; the
deferral's evidence names what that item must say, and the lens files nothing
itself. One tracked item may carry several deferrals, one item per shippable
outcome. Where no tracker is configured, those follow-up items are the
actionable tracking handoff in the executor's delivery result rather than a
blocker.

- `aligned` means every required lens in the latest round has evidence and no
  actionable findings, each lens having discharged every carried finding of its
  own by that lens's closure report, by that lens's withdrawal with its reason,
  or, for a finding it filed as a deferral, by that deferral.
- `unresolved` keeps dissent visible and asks the caller to resolve findings and
  run another round within the declared bound.
- `blocked` names missing evidence, missing or failed lenses, malformed results,
  or unresolved findings at the round bound.

`blocked` at the round bound is terminal: the caller records a typed blocker
`review.loop-bound-reached` naming the open findings and does not run another
round.

A lens result may mark some of its own findings as late: findings that lens
raised against lines unchanged since its own most recent result-carrying round
before this one. A late finding is still a finding and is reduced exactly like
any other, so the mark never changes the outcome of a round. The mark is
visible dissent about the lens rather than about the candidate, and the
returned value carries every marked finding with the lens and the round that
filed it, so a lens's misses stay readable across the delivery. A late finding
a lens recorded as a deferral rather than as a finding is marked late in that
deferral, and neither the returned value nor the count below carries it.

The caller emits `review.round.closed` once a round is reduced, through the
run-event command the repository's root instruction file declares, when it
declares one, naming the round, the candidate it bound, the reduced outcome,
its findings by severity, and the round's self-reported cost; where the
repository declares none, the caller
proceeds silently, with no handoff and no blocker. That emission is
observability, not review state, and this workflow neither performs it nor
reads it back. The caller-selected lens list is unchanged by it. The shared observation
contract below retains late-finding counts in captured reduction output when
the runtime's closed event schema has no such member; it never invents a field.

When the host reports no cost, record `coverage: unreported`; do not invent zero.
Name `reportedBy`. If only part is measured, preserve the reported unit and
total with partial coverage rather than implying a complete cost. Use the
installed product's supported cost shape; this accounting does not affect
whether the review evidence is valid.

Retain prior rounds, dissent, and typed reviewer failures in the returned value.
A later complete round may establish alignment without deleting that history.

## Retain the actual reduction for observation

The caller follows the shared `$execute-work/references/run-observation-contract.md`:
capture this reducer's actual structured return before cleanup, retain earlier
dissent and available interrupted output, then emit the supported closed-round
fields. Accounting outside that event schema remains in the captured reduction.
Project finding dispositions only from supplied originating reviewer/reducer
results, never infer closure from a later zero count. Retain reported units and
coverage unchanged. Observation does not alter this reducer, its supplied round
history, bound/grace interpretation or finding authority.

## Handoff

Return the outcome, all supplied rounds, visible dissent, typed failures,
blockers, and the next action. Evidence remains owned by the caller. Do not
write review state, mutate delivery state, or persist review artifacts from this
workflow.
