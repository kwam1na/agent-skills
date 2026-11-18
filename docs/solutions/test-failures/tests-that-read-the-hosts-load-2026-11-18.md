---
title: "A test of a command that reads the host's load fails whenever the machine is busy"
date: 2026-11-18
category: test-failures
module: run-delivery
problem_type: test_failure
severity: medium
tags: [tests, lead-wait, load, flakes, merge-queue]
symptoms: "LeadListenerTests and LeadMayStopTests fail with lead-wait waking on 'pressure' instead of expiring, only while the 5-minute load is over twice the cores; they pass alone at normal load"
root_cause: "lead-wait wakes on the real host's load, disk and swap, and the tests started it with the default thresholds, so a busy machine changed what the command under test did"
resolution_type: test_fix
---

# A test of a command that reads the host's load fails whenever the machine is busy

## Problem

`lead-wait` wakes the lead on journal events and also on machine pressure: a 5-minute load over twice the cores, free
disk under a floor, or swap growth past a limit. `LeadListenerTests` in `tests/test_run_delivery_supervisor.py` and
`LeadMayStopTests` in `tests/test_run_delivery_hooks.py` start `lead-wait` and expect it to expire. They used the
default thresholds. So on a busy machine, `lead-wait` woke with `"woke": "pressure"`, exit 0, where the tests expected
`"expired"`, exit 3.

During the delivery that showed what everything waits on, three lanes, their reviewers and the merge queue ran
tests at once, and the 5-minute load reached 30 to 44 on a 10-core Mac. These cases failed in the queue whenever they ran under that load.

## Symptoms

- `AssertionError: 0 != 3` and `Tuples differ: ('pressure', []) != ('expired', [])` from `LeadListenerTests` and
  `LeadMayStopTests`, with `"load5"` over 20 in the printed result.
- The failures disappear when the case runs alone, or when the load drops.
- The queue's base rerun passed the same module later, at lower load, so it blamed the candidate. That cost one story
  two queue stops and another a counted check, each holding the queue for most of an hour.

## What Didn't Work

- **Requeuing and hoping.** A requeue only passed if the load happened to be low during that one module.
- **Holding stories for a quiet machine.** It worked once, but it cost hours, and it depended on everything else
  pausing.

## Solution

Pin every threshold the command reads from the host, so the machine can't change the test's outcome. In the
one-list Needs-you change (08163721), every wait these tests start passes `CALM`:

```python
CALM = ("--load-multiple", "1000000000", "--min-free-bytes", "0", "--swap-growth-bytes", str(2 ** 62))
```

Those are thresholds no machine reaches. Cases that test the pressure wake itself still set their own thresholds.

## Why This Works

What a test checks has to be decided by the test's inputs. Pinned thresholds take the host's load, disk and swap out
of those inputs, so `lead-wait` expires on its timeout on any machine, and the queue's verdict depends only on the
change.

## Prevention

- A test of a command that reads host pressure (load, free disk, swap, CPU count) passes thresholds that make that
  reading irrelevant, unless pressure is what the test is about.
- When a failure appears only under load and its result names a host measurement, read the measurement first.
- A queue base rerun at a different load can blame a candidate for the machine. Compare the load in both logs before
  accepting the verdict.

## Related Issues

- The one-list Needs-you change (08163721), which made these tests load-independent.
- The open reports of supervisor and hook-wait tests failing under host pressure.
- `docs/solutions/runtime-errors/a-signal-handlers-exit-is-lost-during-compile-2026-11-18.md`: a stop-wait failure
  that was first mistaken for this one.
