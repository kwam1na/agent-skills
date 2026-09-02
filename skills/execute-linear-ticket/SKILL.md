---
name: execute-linear-ticket
description: Use when work already exists in the tracker and the caller wants implementation rather than new decomposition.
---

# Executing Tracked Work

Carry an existing tracked item through implementation, review, and closure. Use
`$create-linear-ticket` first when the work is not yet tracked or still needs
decomposition.

`$execute-work` owns implementation; this skill adds the tracker record around
it. Do not restate execution rules that `$execute-work` already owns.

## Delivery Posture

Apply `$compound-delivery-kernel` throughout. The tracker owns the work record,
the repository owns sensors, and the skills system owns the delivery workflow.
Preserve the item's execution posture: behavior changes start with a failing
test or a characterization capture, not implementation.

## When to Use

- The caller asks to work on a specific tracked item.
- The caller asks to continue a backlog that is already tracked.
- The work includes implementation plus tracker hygiene such as status changes,
  evidence, and closure.

Do not use this skill when:
- the work needs planning or decomposition first
- the work is not tracked yet
- the task is unrelated to a tracked workflow

## Tracker Boundary

Reach the tracker only through the neutral operations that
`$linear-tracker-adapter` maps. That adapter owns properties resolution,
outcome normalization, and mutation safety; this skill supplies no
provider-specific resolution of its own.

- `resolve-context` establishes the item and its context before any mutation.
- `update-status` moves the item as execution progresses.
- `attach-evidence` records progress, validation evidence, and change-set links.
- `close-or-handoff` ends the item as delivered or as an explicit handoff.

Carry a stable idempotency key per mutation. When an outcome is not confirmed,
stop and report it; never repeat the mutation. Consume only the normalized
outcome, never a raw host payload.

This skill has no operation for rewriting an existing item's body or labels.
Record new information as evidence instead of editing the item body.

If the tracker capability is absent, unconfigured, or blocked, continue the
complete execution path and report every tracking mutation that was omitted.

## Red Flags

- "The change set is open, so this is done."
- "I'll update the tracker at the end."
- "The review loop hit its cap, so I should stop even though the next fix is
  obvious."
- "The checks are probably fine" or "the review comments are minor."
- "Local validation passed, so remote parity is guaranteed."
- "The final suite passed, so test-first happened."
- "The work landed, so there is nothing left to teach the system."
- "I noticed something adjacent, so I should silently expand this item."
- "The mutation may not have landed, so I should send it again."

## Workflow

### 1. Pick Up The Item

- Read the item first through `resolve-context`.
- Capture title, scope, acceptance criteria, test scenarios, and relevant
  relationships.
- Capture execution posture, expected repository sensors, and compounding
  opportunity when present; infer them from the item and repository context when
  absent.
- Move the item to the in-progress status with `update-status` when work begins.
- When continuing a backlog, choose the next item by explicit dependencies
  first, then implementation leverage.

### 2. Implement

- Run implementation through `$execute-work` with the item's execution posture,
  finish line, and expected sensors.
- Keep changes aligned to the item's outcome; do not bundle unrelated cleanup.
- When new work is discovered that should not expand the current item, apply the
  kernel's proactive-item rules and record it as a separate item through
  `$create-linear-ticket`.

### 3. Keep the Record Current

- Use `attach-evidence` at meaningful progress, when the change set opens, and
  when the status changes.
- Include the branch, the current commit, the execution posture, the validation
  run, major scope decisions, and the pending compounding decision.
- Use `update-status` when the item is implemented and waiting on review.
- For coordinated batches, attach the shared change-set reference to every
  included item.

### 4. Review

- Obtain review through `$obtain-review` and converge through `$review-work`.
- Treat unresolved actionable findings and any check that is not green as
  blocking; fix, revalidate, and re-review.
- Attach the converged review outcome as evidence.

### 5. Compound The Learning

- Decide whether the work taught the system something reusable through
  `$compound-learning`.
- Record a follow-up item when the learning is a concrete missing sensor,
  missing coverage, or tooling gap; include the source evidence and why it is
  separate from the current item.
- Record `No durable learning` only when the change is local, obvious, and
  unlikely to recur.
- Include the compounding decision in the final evidence and the handoff.

### 6. Close The Loop

- Use `close-or-handoff` once the finish line is satisfied, attaching the final
  evidence, validation results, and compounding decision.
- If the finish line is not satisfied, use `close-or-handoff` as an explicit
  handoff that names the exact blocker and the unresolved checklist.
- Leave the local checkout tidy and free of the temporary worktree or branch
  created for the item.
- If a tracker mutation returned an unconfirmed outcome, report it as
  outstanding rather than assuming it landed.

## Output

- `Resolved Context`: the references returned by `resolve-context`
- `Execution Posture`: the posture actually followed and its evidence
- `Sensor Results`: the sensors run and their outcomes
- `Review Outcome`: provenance and convergence result
- `Compounding Decision`: the outcome and its reason or next action
- `Tracker Record`: the statuses, evidence, and closure that were recorded
- `Omitted Tracking`: any mutation that was not performed, and why
- `Blockers`: unresolved blockers and pending approvals
