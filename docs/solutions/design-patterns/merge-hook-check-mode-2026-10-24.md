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

A repository hook may need to distinguish checks of one candidate from checks
of a combined batch. The durable queue stage does not prove which path reached
the hook, and an environment flag can outlive the call that justified it.

Derive `checkMode` at `Runner.merge` from the presence of its `batch_synced`
argument: `single` for None, `batch` otherwise. Supply it only after the existing
candidate and base checks, to the ready and merge hooks. Keep expected-head and
tree verification intact. The mode describes the path; it does not assert a
fresh passing check or waive the repository's admission rules.

The binding accepts `{checkMode}` as a whole placeholder element. Refuse a
missing value or anything other than the two exact enum strings when it is
expanded. A hook that runs earlier has no mode to consume. Keep binding shape
compatibility; this requires no new queue state field or schema version.

Use real single-candidate and multi-member batch fixtures to check the arguments
received by both hooks. Also cover unavailable and malformed contexts, embedded
placeholder literals, and hooks without the new placeholder.
