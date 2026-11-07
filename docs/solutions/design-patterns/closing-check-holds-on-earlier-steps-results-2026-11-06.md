---
title: "Make a closing check hold on what an earlier step should have left"
date: 2026-11-06
category: design-patterns
module: run-delivery
problem_type: design_pattern
severity: medium
tags: [run-delivery, supervisor, close-out, races]
applies_when: "A loop's pass runs several steps, each reading a shared record afresh, and a last step ends the loop"
---

# Make a closing check hold on what an earlier step should have left

## Problem

A supervisor pass runs its steps in order: cleanup, then the finish lines, and
then the close check. Each step reads the record afresh, and another process,
the merge queue, writes that record at the same time. A merge that lands after
cleanup's read but before the close check's read is visible only to the close
check. The finish line is met and the lane is idle, so the pass closes. Closing
ends the passes, so no later cleanup runs, and the merged story's checkout and
branch outlive the delivery.

## Solution

The step that ends the loop holds on the result it relies on, not on the
earlier step having run. `close_holds` holds the close with "not cleaned up:
<story>" while any merged story of the delivery still has its lane checkout. The
next pass's cleanup runs before its close check, so the delivery closes one pass
later with nothing left behind.

## Why This Matters

The window is a few milliseconds between two reads, so the failure appears
rarely, and mostly under load. It passed 20 runs in a row on a machine at load
averages of 6 to 14 before the fix. A rare failure in a step that ends a loop
is not retried: the leftover is permanent for the adopter.

## Prevention

When a step ends a loop or a delivery, list what the earlier steps of the same
pass were meant to leave, and make the ending step check those results itself.
Prove a suspected window between two reads deterministically: add a local hook,
never committed, that holds the first step until the concurrent write lands. Then
pin the fix with a row that reaches the same state by another path. Here, cleanup
refusing a checkout with an untracked file leaves the checkout in place at the
close check, with no timing involved.

## Examples

In `run_delivery.py`, `Supervisor.one_pass` runs the `cleanup`, `finish`,
`publish` and `needs` steps, each with its own `load_state()`, and then
`close_holds`. In `tests/test_run_delivery_supervisor.py`,
`test_a_merged_story_whose_checkout_is_still_there_holds_the_close` pins the
hold. The adoption guide's end-to-end row is
`test_the_guide_takes_an_empty_repository_to_a_closed_delivery`, and its
"the lane's checkout outlived its merge" assertion is what caught the race.

## Related

`lane_moved_off` decides when cleanup may remove a lane's checkout. That
decision is separate from this one: the close waits for cleanup's result,
whichever reason cleanup had to wait.
