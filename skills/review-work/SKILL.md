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

Provide the required lens names, a positive round bound, and the completed lens
results for each round. The round bound is declared by the caller before the
first round and belongs to the delivery: every round obtained for the delivery
counts against it, across every loop, except one grace verification round
obtained at the bound, and every round already obtained is supplied to each
reduction. The workflow default is four rounds, and a repository instruction
file may declare a smaller bound; a larger bound is an operator decision
recorded before the first round. The review is bound to the release installed
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

An actionable finding is one a lens filed at P0 or P1 inside the round's scope.
A deferral recorded in evidence is not a finding. A deferral's follow-up is a
tracked item the executor records before the delivery reports done; the
deferral's evidence names what that item must say, and the lens files nothing
itself.

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

The caller emits `review.round.closed` once a round is reduced, through the
run-event command the repository's root instruction file declares, when it
declares one, naming the round, the candidate it bound, the reduced outcome, its
findings by severity, and the round's self-reported cost; where the repository
declares none, the caller proceeds silently, with no handoff and no blocker.
That emission is observability, not review state, and this workflow neither
performs it nor reads it back. The caller-selected lens list is unchanged by it.

Retain prior rounds, dissent, and typed reviewer failures in the returned value.
A later complete round may establish alignment without deleting that history.

## Handoff

Return the outcome, all supplied rounds, visible dissent, typed failures,
blockers, and the next action. Evidence remains owned by the caller. Do not
write review state, mutate delivery state, or persist review artifacts from this
workflow.
