---
title: "Cleanup of a squash-merged story"
date: 2026-11-14
category: best-practices
module: run-delivery
problem_type: best_practice
severity: medium
tags: [cleanup, squash-merge, worktrees, close-out, forge]
applies_when: "A delivery's forge squash-merges stories and the supervisor's cleanup leaves their lane checkouts"
---

# Cleanup of a squash-merged story

## Problem

Cleanup used to count a story as merged only when every tip of the story was an ancestor of the base. These tips are
the checkout's HEAD, its local branch and its remote branch. A forge that squash-merges lands the story as a new commit
of its own, so none of the story's commits ever reaches the base.

At V26-3265's close, cleanup left all 13 lane checkouts. For each one it logged
`unmerged; not on origin/main: the checkout's HEAD (…), local claude/<KEY> (…)`. The close stayed held on
`not cleaned up: <story>`, although every change request was merged and every checkout was clean.

## Solution

Since V26-3358, cleanup asks the forge about a story whose tips are off the base. It removes the checkout, and deletes
the local branch, when both of these hold:

- the story's recorded change request is merged;
- every tip is at or below the head the forge merged.

A tip past the merged head keeps the checkout, and cleanup names it. So do uncommitted or untracked files. Cleanup
still reports a story unmerged, and leaves its checkout, in two cases:

- the forge adapter has no `get`;
- the queue row has no recorded change request.

The adoption guide's "Merge strategy" paragraph says so.

A lead on a generation without that change, or on a forge or row cleanup can't read, recovers by hand:

1. For each story the close waits on, check on the forge that its change request is merged.
2. Check that its checkout is clean: `git -C <checkout> status --porcelain` prints nothing.
3. Check that no tip is past the merged head: the checkout's HEAD, `claude/<KEY>` and `origin/claude/<KEY>` are each
   that head or an ancestor of it.
4. Remove the checkout with `git worktree remove <checkout>`, without `--force`. That way, a checkout that has changed
   since the check is refused rather than lost.
5. Let the next pass see the close. `not cleaned up` clears once the checkout is gone.

## Why This Matters

The close waits for cleanup, so one rule that misreads a merged story holds the whole delivery. The lead can't fix it
with `--force`: forcing it would discard the one checkout that really does hold unmerged work. The safe recovery is the
same check cleanup now makes: the change request is merged, the checkout is clean, and nothing is past the merged head.

## Prevention

Test cleanup on a forge that merges the way this repository's binding does. A squash fixture lands the story's tree as
a commit of its own, so no story commit is an ancestor of the base. Pin both sides:

- the merged, clean story is removed and the close follows;
- a commit past the merged head, on the checkout or on the remote branch, keeps the checkout and is named.

Keep a merge-commit case beside them, so the ancestry path still holds.

## Examples

`test_a_story_its_forge_merged_by_squash_is_cleaned_by_the_pass_and_the_close_follows` covers the supervisor's pass. It
removes the squash-merged story's checkout and the delivery closes; with the forge reporting the request open, the
checkout stays and the close waits. `test_cleanup_takes_a_story_its_forge_merged_by_squash_only_at_or_below_the_merged_head`
names a commit past the merged head, on the checkout and then on the remote branch, and keeps the checkout.

## Related

The adoption guide's "Merge strategy" paragraph, under its practices, states what cleanup does for a squashed story.
`one-lead-worktree-per-delivery-2026-11-09.md` covers the lead's own checkout.
