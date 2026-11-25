---
title: "A listing stop is cleared by ownership, not a base merge"
date: 2026-11-16
category: best-practices
module: orchestrate-work
problem_type: best_practice
severity: medium
tags: [merge-queue, source-conflict, provenance, regenerable-listings, lane-ownership]
applies_when: "The merge queue stops a candidate with source_conflict saying it changed the outputs a regenerable listing lists"
---

# A listing stop is cleared by ownership, not a base merge

## Problem

A story that adds a skill file adds an output to `provenance.lock.json`. When the
lane does not solely own the lock, the merge queue stops the candidate at its first
sync:

```
source_conflict: the candidate changed the outputs provenance.lock.json lists
(added skills/hive-knowledge-adapter/SKILL.md, ...); resolve it by hand, taking the
candidate's side, and hand off again
```

The message reads like a merge conflict against a moved base, and the obvious answer
is to authorize a base merge. That doesn't clear the stop. The lane merges the base,
takes its side of the lock, reruns the writer, its gates and a review round, and
hands off again. Then the queue stops it with the same message.

## Solution

Read the stop as an ownership question. `check_shared_outputs` in the queue skips
the listing check for any listed file that the candidate's lane alone owns. Without
that ownership, `unowned_listing_changes` compares, through `listing_change`, the
candidate's listed outputs with the merge base's and stops on any difference. A base merge moves the merge base, but the
candidate still adds the same outputs, so the difference is still there.

When the story is meant to add or remove listed outputs, as its plan says, grant its
lane the listing and requeue the same head:

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py \
  --delivery KEY lead-own --lane LANE --grant provenance.lock.json
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py \
  --delivery KEY lead-accept --requeue STORY
```

The queue then regenerates the digests during sync and runs the candidate's checks.
That requeue needs no new hand-off and no new review round. Move the grant back
afterwards if another lane needs the lock.

When the story was not meant to change the listing, the stop is a real finding.
Send it back to the lane.

## Why This Matters

The wrong path is expensive and looks like progress. In the knowledge-library
delivery, the hive-mined adapter story stopped at 17:17. Then came a base merge
authorization, a lane turn, a fourth review round that found nothing, a re-hand-off,
and the same stop at 17:33. The grant and the requeue took under a minute, and the
story merged at 17:58. The review evidence also gained a round that exists only
because of a misdiagnosis.

## Prevention

- At `lead-setup`, give a story that adds or removes skill files its lane's claim on
  the listing it changes. The plan's `Units and order` table can already say which
  stories add outputs.
- Before authorizing a base merge for a `source_conflict`, check whether the message
  names a listing ("changed the outputs ... lists"). A listing stop and a conflict
  outside the regenerable paths are different stops.
- A queue that merged a candidate's new outputs whenever the base changed only
  digests would remove this stop in the common case. Until it does, ownership is how
  through.

## Examples

The knowledge-library delivery's journal shows the whole path: the first stop, the
base merge authorization, review round 4, the second stop, the decision
`lane-2 owns provenance.lock.json`, the requeue, and the merge.

## Related

`one-merge-slot-for-each-base-2026-11-15.md` covers the queue's sync model.
The run-delivery skill's "Lane ownership" section describes `lead-own`.
