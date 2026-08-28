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
