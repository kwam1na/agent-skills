---
title: "A fixture's detached supervisor outlives a worker the test runner stops, unless the worker stops it"
date: 2026-11-09
category: test-failures
module: run-delivery
problem_type: test_failure
severity: high
tags: [tests, processes, signals, supervisor, cleanup]
symptoms: "run_delivery.py --delivery FX-1 lead-run processes running at ppid 1 for days, from a temporary release install or a case folder that no longer exists"
root_cause: "The supervisor starts in a session of its own, outside the worker's process group, so stopping the worker's group never reaches it; the case's cleanup is all that can, and an interrupt or a walk error skipped it"
resolution_type: code_fix
---

# A fixture's detached supervisor outlives a worker the test runner stops, unless the worker stops it

## Problem

`lead-init` starts the delivery's supervisor (`lead-run`) detached, with `start_new_session=True`, so that it outlives
the command that started it. Under a test, that same detachment puts it outside the test worker's process group.
`scripts/run-tests.py` stops a timed-out or interrupted worker by its group: SIGTERM, a three-second grace, then
SIGKILL. Its docstring says a child moved into a session of its own is not stopped. Each worker installs its own
SIGTERM handler, which raises `KeyboardInterrupt`. unittest re-raises that past `doCleanups`, so no `addCleanup` runs,
and the fixture's supervisor is left running at ppid 1. A process with the default disposition dies at once, with the
same result.

The case's cleanup had a second way to fail. `stop_supervisors` walked the case's folder with `Path.rglob`, and the
merge queue removes its work checkouts whenever it likes. A checkout removed mid-walk raised `FileNotFoundError`, so
the cleanup stopped nothing, which is how an adoption-guide case leaked its supervisor on a failed run.

Once leaked, the supervisor never exits. With its record removed, its whole case folder removed, or its install
removed, it keeps passing over nothing.

## Symptoms

- `run_delivery.py --delivery FX-1 lead-run` processes at ppid 1, from a `run-delivery-release-*` install or a case
  folder that no longer exists.
- Their dates match runs whose units hit the runner's time limit.
- Three leaked supervisors idle at about 0.4% CPU each. Timing a 23-test subset with and without them shows no
  slowdown (76-79 s against 79-85 s), so they leak quietly rather than failing anything.

## What Didn't Work

Relying on the case's cleanup alone: a signal or an error in the walk skips it. Relying on the supervisor's exit
alone: an interrupted worker leaves the case's folder, record and install in place, so nothing the supervisor serves
disappears.

## Solution

Two guards, each proved with the other out of the way:

- **The worker stops what it started.** The test module keeps the case folders whose supervisors are still its own
  to stop. On SIGTERM and SIGHUP, it stops each one by the pid its lock records before anything else runs, then hands
  the signal to the handler that was there before (the runner's), or ends as the signal would have. An exit hook does
  the same for a process that ends with a case still live. The cleanup's walk uses `os.walk` with an `onerror` that ignores a folder removed meanwhile, and reads
  each lock without creating it again.
- **The supervisor exits once what it serves is gone.** `Supervisor.run` checks its record and its install before each
  pass, between steps, before it logs a pass and while it waits. Once either is gone, it says so in its log, stops its
  children, and writes no record back. The record is its state file, not its folder: a removal can leave the folder
  behind, and a writer can make it again.

A process-table check finds a case's processes independently of what the case recorded: the command line names the
case's folder, or the process runs from inside it, or it names the case's install and an `--delivery FX-` key. That
check is what proves the interrupted-worker row, because the supervisor's own lock is exactly what a leak can't be
trusted to keep.

## Why This Works

The two guards cover each other's gap. An interrupted worker keeps everything the supervisor serves, so only the
worker can stop it, and it now does so on its way out. A worker killed outright, with SIGKILL and no grace, can stop
nothing. Its case folder is removed later, by the next cleanup or the system, and from then on the supervisor's own
check ends it within a pass. Proving each guard with the other out of the way is what shows both are real. The exit
rows remove the record or the install with the case's cleanup not yet run, and the interrupted-worker row keeps
everything in place.

## Prevention

- A process a test starts in a session of its own is the test process's to stop, on every way out, signals included.
- A long-running process should exit once what it serves is gone. A test fixture's cleanup is never the only guard.
- A cleanup that walks a folder other processes change must tolerate entries vanishing mid-walk.

## Related Issues

- The leaked `FX-1` supervisors found on the operator's machine on 2026-11-07.
- A later diagnosis found the walk's `FileNotFoundError` and a leaked adoption-guide supervisor.
- `docs/solutions/best-practices/stopping-a-recorded-pid-2026-11-03.md`: stop a recorded pid only while its lock is held.
