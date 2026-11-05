---
title: "Derive merge hook context at the merge boundary"
date: 2026-10-24
category: design-patterns
module: merge-queue
problem_type: design_pattern
severity: medium
tags: [merge-queue, hooks]
applies_when: "A repository hook has to tell a single candidate's check from a batch's"
---

# Derive merge hook context at the merge boundary

## Problem

A repository hook may need to distinguish checks of one candidate from checks
of a combined batch. The durable queue stage does not prove which path reached
the hook, and an environment flag can outlive the call that justified it.

## Solution

Derive `checkMode` at `Runner.merge` from the presence of its `batch_synced`
argument: `single` for None, `batch` otherwise. Supply it only after the existing
candidate and base checks, to the ready and merge hooks. Keep expected-head and
tree verification intact.

The binding accepts `{checkMode}` as a whole placeholder element. Refuse a
missing value or anything other than the two exact enum strings when it is
expanded. A hook that runs earlier has no mode to consume. Keep binding shape
compatibility; this requires no new queue state field or schema version.

## Why This Matters

The mode is read from the call that is merging, so it can't be stale the way a
stored stage or an inherited environment flag can. It describes the path; it does
not assert a fresh passing check or waive the repository's admission rules.

## Prevention

Take context a hook acts on from the boundary that produced it, not from state
another step wrote earlier. Expand a placeholder only to a value from a closed
set, and refuse anything else rather than passing it through.

## Examples

Use real single-candidate and multi-member batch fixtures to check the arguments
received by both hooks. Also cover unavailable and malformed contexts, embedded
placeholder literals, and hooks without the new placeholder.

## Related

- [Preserve source time during merge recovery](merge-occurrence-and-observation-2026-10-23.md),
  the queue's other rule for keeping what a record claims tied to what was observed.
