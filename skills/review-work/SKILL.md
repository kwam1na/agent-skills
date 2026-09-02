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
counts against it, across every loop, and every round already obtained is
supplied to each reduction. The workflow default is three rounds, and a
repository instruction file may declare a smaller bound; a larger bound is an
operator decision recorded before the first round.

Every aligned or changes-requested result includes
caller-owned evidence. A changes-requested result also names its actionable
findings. A failed result names the failure.

Use only the lenses required by the adopting repository and the candidate's
risk. Do not turn the core contract into a universal reviewer list or scoring
policy.

## Convergence

An actionable finding is one a lens filed at P0 or P1 inside the round's scope.
A deferral recorded in evidence is not a finding.

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

Retain prior rounds, dissent, and typed reviewer failures in the returned value.
A later complete round may establish alignment without deleting that history.

## Handoff

Return the outcome, all supplied rounds, visible dissent, typed failures,
blockers, and the next action. Evidence remains owned by the caller. Do not
write review state, mutate delivery state, or persist review artifacts from this
workflow.
