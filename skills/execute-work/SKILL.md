---
name: execute-work
description: Execute a bounded plan incrementally, preserve unrelated work, run repository sensors, and hand off evidence without overstating completion.
---

# Execute Work

## Purpose

Carry an approved plan through implementation and verification. Apply
`$compound-delivery-kernel` for the shared delivery posture. Preserve the plan's
scope, finish line, dependencies, test scenarios, sensors, approvals, and
execution posture.

## Start from Repository Authority

Read the adopting repository's instructions and current working state before
editing. Preserve unrelated tracked and untracked changes. Use the repository's
named commands and sensors; generic examples never replace a repository rule.

Do not begin implementation while a required dependency is incomplete. If the
request is diagnosis-only, stop after evidence-backed diagnosis. If the request
is plan-only, do not edit code.

## Work Incrementally

- For `characterization-first`, capture existing behavior before changing it.
- For `test-first`, prove the intended test fails before implementing the
  smallest behavior slice that makes it pass.
- For `sensor-only`, change only the declared non-behavioral surface.
- After each coherent slice, run the closest relevant sensor and preserve its
  result as evidence.
- Keep partial progress explicit. Do not broaden scope to make an adjacent idea
  fit the current delivery.

Before choosing a change surface from a failing sensor, confirm that its failing
assertion represents the repository's intended behavior rather than incidental
sensor mechanics. Read the assertion's stated contract and, when the distinction
is unclear, the smallest relevant history or neighboring evidence. If the
product already satisfies the intended behavior, correct the smallest sensor
defect without weakening that behavior. Do not reshape valid product behavior
solely to satisfy an accidental formatting or extraction constraint.

When work becomes blocked, retain completed work and evidence, name the blocker,
and provide the approval or decision handoff needed to continue.

## Bound the Review Loop

Before the first review round, state the bound in effect for the delivery,
quoting the repository instruction that sets it if any, and state the lenses.
Never declare a second bound for the same delivery. The lens set is declared
before the first round and held unchanged for the delivery: every declared lens
is selected in every round, no lens is added after the first round, and dropping
a lens disposes of nothing it filed.

For each verification round, supply each lens its own retained entry from every
prior round in which it was selected, result-less entries included, and, for
each of the two references its delta spans, a pair of the exact `candidateRef`
text (its `opaque` member) and the revision the repository instructions declare
it resolves to, and nothing else.

After each round, record a disposition for every finding, each citing the lens:
closed only by citing the closure report of the lens that filed it in a named
later round; deferred only where that lens itself recorded the deferral, with
its follow-up; declined only where that lens withdrew the finding, with its
stated reason; or open, named in the blocker when the bound is reached. The
executor closes, defers, and declines nothing on its own judgement.

Reaching the bound is terminal in all three of its forms. With dissent open,
record the typed blocker `review.loop-bound-reached` naming the open findings,
report `partial`, and stop. Without dissent but with a repair, rebase, or fix
still required that would change the candidate, record the same blocker naming
the unreviewed change, report `partial`, and stop. With the latest round blocked
by a failed acquisition and no round left, record the same blocker naming the
failed lens, report `partial`, and stop. Before the bound a failed acquisition
is not terminal: obtain the next round within the remaining bound, and that
round is the latest the reducer judges.

## Keep the Delivery Loop Observable

Before claiming `complete`, record the outcome of every kernel phase rather
than collapsing the delivery into implementation plus tests:

- name the selected execution posture and the evidence that followed it;
- name the review round bound in effect for the delivery and the rounds obtained
  against it, and record a disposition for every finding, each citing the lens;
- record the review outcome, who or what produced it, and the evidence reviewed;
  when no repository- or host-selected independent review is available, say so
  and retain the bounded acceptance-criteria review instead of implying
  independent review occurred; and
- record the compounding outcome and its reason or next action.

A missing posture, review, or compounding outcome leaves the delivery `partial`.
An unavailable review blocks completion only when repository authority, host
policy, or the approved plan requires that review.

## Verify the Finish Line

Run the smallest honest targeted sensors before broader repository gates. An
unavailable optional sensor is reported; an unavailable or failed required
sensor blocks completion. Required approvals must also be recorded before the
work can be complete.

Use these result categories:

- `in-progress` when execution has begun without a completed slice;
- `partial` when useful work or evidence exists but the finish line remains
  incomplete;
- `blocked` when a dependency, required sensor, approval, or explicit blocker
  prevents progress or a completion claim; and
- `complete` only when the full scope and finish line are satisfied, required
  sensors pass, and approval handoffs are complete.

## Resolve Optional Tracking

When tracking is selected, use only neutral status, evidence, and
close-or-handoff operations with stable idempotency keys. Consume only
normalized outcomes. If no tracker is configured, continue the core execution,
perform no tracking mutation, and include the actionable tracking handoff in the
result.

## Handoff

Report the result category, execution posture, completed scope and finish-line
items, sensor results, retained evidence, review provenance and outcome,
compounding outcome, unresolved blockers, pending approvals, and any optional
operation that was not performed. Never claim completion from progress alone or
from a passing sensor set that does not cover the declared finish line.
