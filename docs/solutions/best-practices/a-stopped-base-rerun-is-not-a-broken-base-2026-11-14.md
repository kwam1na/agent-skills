---
title: "A stopped base rerun is not a broken base"
date: 2026-11-14
category: best-practices
module: orchestrate-work
problem_type: best_practice
severity: high
tags: [merge-queue, base-rerun, signals, exit-codes, base-broken]
applies_when: "A lead stops a merge queue's check, or the runner, while a failing check re-runs on the base"
---

# A stopped base rerun is not a broken base

## Problem

When a candidate's check fails, the merge queue re-runs that check on the base to tell
whether the failure is the base's own. The runner read any non-zero exit of that re-run
as a failure. A lead who stopped the re-run, by its pid or by stopping the runner, got
`base_broken: <check> also fails on main at <head>` (or whatever the base is called)
right after the stop, a `base.broken` entry, and a queue that held every candidate
until someone journaled `base.repaired`. Nothing was wrong with the base.

## Solution

The runner now classifies the re-run first. It reads as stopped when the process died
by a signal, exited 129 up to 128 plus the platform's highest signal number, or ended
because the runner itself was interrupted. An exit of 128, one above that range, and a
timeout are still failures. A check that exits 137 with no signal sent cannot be told
from a stopped one, and reads as stopped. The bound below covers that case.

A stopped re-run journals `base.rerun.stopped` in place of `base.rerun.finished`. It
records no verdict in `meta.baseReruns` and journals no `base.broken`, and the queue
stays free. The candidate goes back to `queued` and is checked again without the lead,
once for each pair of candidate head and base head. A second stop at the same pair stops
the candidate as `base_rerun_stopped`, journaled as `candidate.stopped`, which the
lead's wait wakes on. `base.rerun.stopped` alone wakes no wait.

A lead on a generation without the fix recovers by hand. If `base.broken` names the
check that was just stopped, and `base.rerun.finished` follows the stop with no failing
tests of its own, the base was never judged:

1. Confirm the base by hand: run that check on a fresh checkout of the base head and
   let it finish.
2. If it passes, journal `base.repaired`, naming the stop as the cause, and re-queue the
   candidate it stopped. If it fails, the base really is broken: follow the guide's
   base-broken rules.
3. Don't stop a base re-run again until the fix is applied. Stop the runner between
   turns instead.

## Why This Matters

A stopped process has no verdict, but its exit status looks like one. A check killed by
a signal returns a negative code to Python. One that traps the signal and exits, as
`scripts/run-tests.py` does, returns 128 plus the signal's number: 130 for SIGINT, 143
for SIGTERM. A runner stopped mid-re-run never sees an exit at all. All three read as
"also fails" to a test of `returncode != 0`. A broken base holds the whole queue, so one
stop that reads as a failure costs every lane, not just the one candidate.

## Prevention

A test of a stopped run has to stop a real process. Have the fixture check write its own
pid, and signal that pid alone. Never signal by pattern. Cover the plain death, the
trapped exits 130 and 143, the runner's own interruption, a stop during a flake retry and
an in-range exit with no signal sent. Beside those, pin that exits of 1, 128 and 255 and
a timeout still break the base, so the classification can't widen to every non-zero
exit.

## Examples

A lead stopped a full-suite run to change the queue's budget, with the operator's
approval. The run it stopped was the base re-run. The queue journaled `base.broken` and
held every candidate. Clearing it took a recorded decision, a `base.repaired` and a
re-queue.

## Related

The orchestration guide's "A stopped base re-run" rule, under "What the record shows about checks", states
the classification. Its reason table says what the lead does for `base_rerun_stopped`.
`stopping-a-recorded-pid-2026-11-03.md` covers stopping a process by the pid you
recorded.
