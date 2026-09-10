---
name: plan-work
description: Create a bounded implementation plan with an explicit finish line, dependencies, execution posture, repository sensors, and approval handoffs.
---

# Plan Work

## Purpose

Turn an approved outcome into the smallest plan that can be executed without
re-deriving scope. Apply `$compound-delivery-kernel` for the shared delivery
posture. This workflow plans only; it does not edit implementation files or run
the planned work.

## Discover Repository Authority

Read the adopting repository's instructions before choosing paths, commands,
or sensors. Repository rules and named sensors outrank generic examples. Keep
repository-specific commands in the plan as discovered inputs rather than
making them part of this workflow.

## Bound the Plan

Record:

- the requested outcome and explicit in-scope work;
- clear out-of-scope boundaries;
- an observable finish line;
- real dependencies and their order;
- representative test scenarios;
- required approval handoffs;
- the smallest honest repository sensors; and
- one execution posture: `test-first`, `characterization-first`, or
  `sensor-only`.

Use `characterization-first` when existing or unclear behavior must be captured
before it changes. Use `test-first` for a new behavior with a clear expected
outcome. Use `sensor-only` only when no runtime behavior changes.

If scope or the finish line is unresolved, return a concise handoff naming the
missing decision. Do not hide an assumption that would materially change the
outcome. Plans contain decisions and evidence targets, not implementation code
or command choreography.

## Review the Plan Without a Round Bound

Plan review carries no round bound: it runs until its lenses align unless the
operator ends it by decision, and no round count ends it. Plan review is
separate from the delivery review that `$review-work` bounds; the absence of a
count for plan review changes nothing about delivery review. Leaving plan
review unbounded is a recorded decision rather than an omission, so a later
plan review does not re-derive the question.

A plan review that is not converging may end by operator decision rather than
by count; termination is not guaranteed. One sufficient signal is a round after
the first that raises a finding not raised before while leaving the number of
open findings no lower than the round before it. Surfacing that signal to the
operator is the caller's responsibility; supplying the channel is outside this
workflow's scope: a plan-review round is realized outside this workflow, which
neither carries a channel to the operator nor assumes one. The caller running the loop is what
puts each round in front of the operator who may end it, and where the caller
surfaces nothing the loop still runs to alignment with no one positioned to end
it early.

In place of a bound, every lens reviewing a plan optimizes for the lowest number
of rounds: every finding a lens can raise against the plan as it stands is
raised in the current round, not held for a later one. A lens that withholds a
finding it could already state spends a round the plan did not need. Each
plan-review lens carries this mandate in its own round brief, so a lens holding
only its brief can act on it.

## Plan Extension Pins and Shared Resources

For each unit that extends a registry, enum, or counted collection, search the
existing tests for literal identifiers, arrays, and counts that will become
stale. Assign those test-pin updates to the unit or derive them beforehand;
include the affected assertions in its test disposition inventory.

Distinguish a production-logic boundary from a test-pin boundary. For example,
"no production logic change under the worker directory; related test pins may
be re-derived" permits the expected assertion update without widening the
feature. Preserve any stricter boundary the caller explicitly imposed.

For parallel units and review lenses, list shared ports, browser sessions,
databases, and caches alongside file ownership. Allocate separate resources or
name the repository or host mechanism that serializes their use, including its
owner and release point. Serialize only the sensor that needs the resource.
Record sensors whose assertions compare to the merge base, and require a fresh
base check before their final validation; upstream movement invalidates the
earlier result according to that sensor's contract.

## Resolve Optional Operations

Tracking is optional unless repository authority or the user makes it required.
When selected, use only the neutral tracker operations and normalized outcomes
declared by the delivery router. If tracking is unavailable, finish the plan,
report that no tracking mutation occurred, and give an actionable handoff.

Treat specialized external sensors the same way: an unavailable optional sensor
is reported honestly; an unavailable required sensor remains visible as a
completion blocker.

## Handoff

Return the normalized scope, non-goals, finish line, dependencies, scenarios,
posture, sensors, approval handoffs, and any unavailable optional operations.
The plan is ready when execution can start without inventing coverage or success
criteria.
