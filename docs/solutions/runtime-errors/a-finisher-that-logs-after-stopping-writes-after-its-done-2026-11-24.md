---
title: "A finisher that logs a stop after making it writes after its processes are reported gone"
date: 2026-11-24
category: runtime-errors
module: run-delivery
problem_type: runtime_error
severity: medium
tags: [processes, ordering, hand-over, flaky, plan-preview]
symptoms: "test_adoption_hands_the_preview_over_and_its_processes_stop failed 1 run in 3 alone: one file in the preview folder changed about 0.1s after both pids were reported stopped"
root_cause: "the detached finisher signalled the live view, waited for it to go, then appended the stop to finish.log; a reader that saw both pids gone could still see that file change"
resolution_type: code_fix
---

# A finisher that logs a stop after making it writes after its processes are reported gone

## Problem

When `lead-setup` adopts a reviewed plan store, a detached finisher hands the plan preview to the delivery and stops
the preview's live server and hosted watcher. A test snapshotted the preview folder once both pids were gone and
expected nothing to change after. Sometimes `finish.log` changed about 0.1s later.

## Symptoms

- The hand-over test passed, failed, then passed when run alone at one head, and failed in full-suite runs.
- The one changed file was the finisher's own log, never the preview's record.

## What Didn't Work

- Treating it as a test flake: the order was a real property of the hand-over, so any reader waiting on the pids
  could see a late write.

## Solution

The finisher stops the live view last and writes that line to `finish.log` before signalling it, so every write
precedes the stop it describes. `handed.json` also records the finisher's own pid, so a reader can wait for the
finish itself rather than for the processes it stops.

## Why This Works

"The processes are gone" was being used as "the hand-over is done", but the finisher outlived them and kept writing.
Writing each line before the action it records, and making the finisher itself waitable, puts its last write before
the last thing a reader can observe.

## Prevention

- A process that stops others and records it writes the record before the stop, never after, and the last thing it
  stops is the thing readers wait on.
- A reader that needs "finished" waits on the finisher's own identity, not on the processes it ends.

## Related Issues

- V26-3810 (delivery V26-3775) fixed it, found by V26-3780's full-suite runs.
- `docs/solutions/test-failures/detached-fixture-supervisors-outlive-interrupted-workers-2026-11-09.md` covers detached
  children outliving their parent, from the cleanup side.
