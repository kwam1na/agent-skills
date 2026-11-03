---
title: "Check for a stop after the last statement that can drop one"
date: 2026-11-03
category: design-patterns
module: merge-queue
problem_type: design_pattern
severity: medium
tags: [merge-queue, signals, finalizers]
applies_when: "A long-running loop stops on a signal and rebinds objects that have finalizers"
---

# Check for a stop after the last statement that can drop one

## Problem

Python only prints what a finalizer raises. A signal handler that raises while a
finalizer runs therefore loses its exception, and the loop carries on as if no
stop had arrived. A stopping check placed before a rebind misses this case,
because rebinding the variable is what drops the previous object and runs its
finalizer.

## Solution

The handler records the stop as well as raising it. The loop checks that record
after the last statement that can drop an object, immediately before it starts
the next piece of work, and before each wait.

## Why This Matters

A stop that is printed and then ignored looks like a queue that refuses to stop.
It also lets another turn start after the operator asked for none.

## Prevention

When adding a statement to the loop, keep the check after it. Test the stop by
delivering it during the rebind, not only during the wait.

## Examples

In `merge_queue.py`, `honour_stop` reads the handler's record. `serve` calls it
through `before_turn` after building the new `Runner`, and through `sleep` before
each wait.

## Related

A stop landing in a finalizer during a turn is still honoured only after that
turn. Nothing drops a finalizer there today.
