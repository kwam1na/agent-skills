---
title: "Preserve source time during merge recovery"
date: 2026-10-23
category: design-patterns
module: merge-queue
problem_type: design_pattern
severity: medium
tags: [merge-queue, recovery, journal]
applies_when: "A queue's state and its journal disagree about a merge"
---

# Preserve source time during merge recovery

## Problem

A queue can say merged while its append-only journal has no merge event. This
can happen when an external merge is recorded as a state patch, or a crash
separates state and journal writes. Reconciliation must inspect both records.

## Solution

Recovery observes an old event now. Keep the journal's `at` as append time and
carry the verified forge `mergedAt` as `occurredAt`. Explicit null means that
recovery has no source time; absence retains the old journal and normal runner
semantics.

An idempotent recovery uses the queue lock and durable recovery intent, preserves
review events, and checks for a merge after the latest hand-off before appending.
Retain the recovered head and change-request identity too: hand-off state and
its journal append can straddle recovery, so journal order alone cannot tell a
delayed same-attempt hand-off from new work. Reject conflicting retained heads
and future source occurrences before writing an intent or changing state.

Display landing times and compute durations from occurrence. If a hand-off was
recorded after the actual merge, its wait and check duration are unknown, not
zero or the delay until recovery. Sort the last known landing by occurrence,
since an older merge can be observed after a newer one.

## Why This Matters

Rewriting append time would hide when recovery actually happened. Keeping the two
times apart lets a reader see both when a merge happened and when the record learned
of it, and keeps durations from absorbing the delay until recovery.

## Prevention

A snapshot limited to one repository cannot discover a merge in another.
Supply a fresh, verified source snapshot for that merge through reconciliation;
do not invent run history or edit the live journal. Source aggregation remains
separate from timestamp recovery.

A later pull request for the same subject must not relabel a merged candidate.
Reconciliation reports a different retained change request before linking or
recovering it; the lead settles candidate identity through supported state operations.

Revalidate recovered identity inside the journal append state lock, including
crash completion; a concurrent correction quarantines the stale intent and
reports it without a false merge event. Keep lifetime duration statistics
separate from the current-attempt assignment shown in a building detail.

## Examples

- An external merge recorded as a state patch: recovery appends the merge event
  with `at` set to now and `occurredAt` set to the forge's `mergedAt`.
- A hand-off recorded after the actual merge: its wait and check duration read as
  unknown, not zero.
- An older merge observed after a newer one: the last known landing is still the
  newer one, because landings sort by occurrence.

## Related

- [Derive merge hook context at the merge boundary](merge-hook-check-mode-2026-10-24.md)
- [Preserve reviewer reports without inventing a run](runless-review-observations-2026-10-23.md),
  which likewise records what was observed without synthesizing history.
