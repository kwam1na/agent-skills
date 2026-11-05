---
title: "Stop a recorded pid only while its lock is held, and test it in a session of its own"
date: 2026-11-03
category: best-practices
module: lead-supervisor
problem_type: best_practice
severity: high
tags: [processes, signals, locks, tests]
applies_when: "A command stops a process by a pid it recorded earlier, or a test exercises that path"
---

# Stop a recorded pid only while its lock is held, and test it in a session of its own

## Problem

A command that stops another process by a pid read from a file trusts that file.

## Solution

Signal the recorded pid only while a live process holds the lock, which `flock` with `LOCK_NB` shows, and refuse a
pid that isn't an integer above 1.

To confirm a stopped process is gone, check both that the lock is free and that `os.kill(pid, 0)` raises
`ProcessLookupError`.

## Why This Matters

A pid in a lock nobody holds may have exited long ago, and the number may now belong to an unrelated process. A pid
of 0 or 1, or one that isn't a number, sends the signal to the caller's own process group or to init.

## Prevention

A child that has exited but hasn't been reaped is a zombie, and `kill(pid, 0)` still succeeds on it. In production
the stopped process's parent has exited, so init reaps it. In a test that starts the stand-in with `Popen`, nothing
reaps it until the test calls `wait`. The command under test then waits out its whole deadline and reports the
process as still running. Reap the stand-in on a thread as soon as it exits, as its real parent would.

Run the command under test with `start_new_session=True` whenever it may signal a process group. If a regression
loosens the pid guard, `killpg(0, ...)` then stops only that command, and the test fails on its exit code. Without the
separate session, the signal stops the whole test run.

## Examples

To prove the command confirms the process is gone, use a stand-in that ignores SIGTERM. A stand-in that dies on the
first signal passes even when the confirmation and the SIGKILL fallback are missing. Have it record a chosen pid,
including 0, and pair it with an unheld lock that names a live process the test started and checks is left
running.

## Related

- [Prove each condition of a gate on its own, with the gate still shut](prove-each-gate-condition-alone-2026-11-03.md),
  from the same supervisor work.
