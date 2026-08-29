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

## Keep the Delivery Loop Observable

Before claiming `complete`, record the outcome of every kernel phase rather
than collapsing the delivery into implementation plus tests:

- name the selected execution posture and the evidence that followed it;
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
