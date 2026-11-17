---
title: "A reviewer that changes files gets its own checkout"
date: 2026-11-17
category: best-practices
module: review-work
problem_type: best_practice
severity: medium
tags: [review, worktrees, mutation, integration-review]
applies_when: "Reviewers are started by hand, outside run-delivery's review-prepare, on a candidate another reviewer or a lane also reads"
---

# A reviewer that changes files gets its own checkout

## Problem

A lead reviewed its own integration work with two lenses and started both in the
integration worktree. The adversarial-testing lens proves a test can fail by changing
the code under it, running the test, and putting the code back. While it does, the tree
is not the candidate. The other lens was reading the same files, and the lane was still
committing there.

## Solution

Give each reviewer a detached checkout of its own at the exact candidate before it
starts, and name that checkout in its brief:

```bash
git worktree add --detach <folder> <candidate commit>
```

A reviewer that only reads gets one too. It costs seconds, and it means the commit the
reviewer names in its result is the commit it read. Remove each checkout when the round
ends.

## Why This Matters

A review's result is about one commit. A shared tree that another process changes
breaks that without leaving a trace: a finding can describe a mutant, or a lens can
approve text that was never committed. Nothing fails, so nobody is told.

`review-prepare` already does this for a story's rounds, one private checkout per lens,
reset to each round's candidate. The gap is every review started another way, and the
lead's integration review is the usual one.

## Prevention

- Start no reviewer in a worktree anything else reads or writes: not the lane's, not
  the lead's, not another reviewer's.
- Put the checkout's path and the candidate's commit in the brief, and have the result
  name both.
- Where the round can go through `review-prepare`, use it rather than starting
  reviewers by hand.

## Examples

Round 1 of an integration review started both lenses in the integration worktree. The
outcome lens was moved to its own checkout mid-round, once the mutations were noticed.
Round 2 gave each lens a detached checkout at the round's candidate from the start, and
both results named that commit.

## Related

`one-lead-worktree-per-delivery-2026-11-09.md` is the same rule for leads: a checkout
has one writer.
