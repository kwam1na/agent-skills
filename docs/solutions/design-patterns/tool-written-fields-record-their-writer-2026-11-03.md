---
title: "Record who wrote a field when a tool writes on a person's behalf"
date: 2026-11-03
category: design-patterns
module: delivery-state
problem_type: design_pattern
severity: medium
tags: [delivery-state, merge-queue, lane-rows]
applies_when: "A tool writes into a field that a person also writes, and a rule reads that field's freshness"
---

# Record who wrote a field when a tool writes on a person's behalf

## Problem

Once the merge queue wrote a lane row's focus, a rule reading "the lane spoke
recently" could not tell the queue's words from the lane's. The queue's own write
read as the lane moving on, so a lane's second merge left its row stale.

## Solution

A small field only the tool can set says who wrote the words. The state tool keeps
`focusBy` and `focusAt` for every writer and replaces anything a writer puts there.
`focusAt` moves when the words change or a lane says them again, and
`focusBy: "runner"` says the queue wrote them.

## Why This Matters

Without it, each new rule about freshness has to guess at the writer from the text
or from timing, and the guess fails on the tool's own write.

## Prevention

Give the runner the fewest fields that do the job, and check per field. Here it can
write `focus`, `state`, `waitingOn` and `since`, and can't add, remove or reorder
rows. Test a second merge in a row, not only the first.

## Examples

A lane's `focus` command takes over from the queue's words and ends the queue's
`waitingOn` unless it sets its own.

## Related

Ending the queue's `waitingOn` is tied to `focusBy`, not to who set `waitingOn`. A
lane's `patch --set /focus` drops `focusBy` and keeps the queue's `waitingOn`, and a
later `focus` then leaves it unless it passes `--waiting-on`. The documented `focus` route and `patch --patch`
both end it.

Importing a `TestCase` class into a test module makes `unittest` run its whole suite
again from there. Take the methods from the class, or import the module.
