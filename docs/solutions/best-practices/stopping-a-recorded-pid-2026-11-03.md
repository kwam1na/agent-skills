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
pid that isn't an integer above 1. That refuses -1, 0 and 1, and a JSON `true` too, which Python reads as an integer
equal to 1.

To confirm a stopped process is gone, check both that the lock is free and that `os.kill(pid, 0)` raises
`ProcessLookupError`.

## Why This Matters

A pid in a lock nobody holds may have exited long ago, and the number may now belong to an unrelated process. The
small numbers aren't processes at all. `kill(0, sig)` signals the caller's own process group, `kill(1, sig)` signals
init, and `kill(-1, sig)` signals every process the caller may signal, which for an ordinary user is all of their
own. `killpg` reads 0 as the caller's own group too. A pid that isn't an integer, such as a string or `null` read from
the file, signals nothing: `os.kill` raises `TypeError`, so without the guard the command crashes mid-stop instead.

## Prevention

A child that has exited but hasn't been reaped is a zombie, and `kill(pid, 0)` still succeeds on it. In production
the stopped process's parent has usually exited already, so the process was reparented to init, or to the nearest
subreaper such as launchd or a container's init, which normally reaps it within moments of its exit. Until then
`kill(pid, 0)` still succeeds, so poll for it until a deadline rather than checking once. In a test that starts the
stand-in with `Popen`, the test is the parent, and nothing reaps the stand-in until the test calls `wait`. The command
under test then waits out its whole deadline and reports the process as still running. Reap the stand-in on a thread as
soon as it exits, as its real parent would.

Start the stand-in with `start_new_session=True` too. A command that stops by group, as `killpg(pid, sig)` does,
finds no group to signal when the stand-in shares the test's group, so it falls back to `kill` or fails, and the test
never exercises the group path it claims to.

Run the command under test with `start_new_session=True` whenever it may signal a process group. If a regression
loosens the pid guard, `killpg(0, ...)` then stops only that command, and the test fails on its exit code. Without the
separate session, the signal stops the whole test run. A separate session doesn't contain -1 or 1, so never let a test
send them for real. Test the guard against those in the process itself, with `os.kill` and `os.killpg` patched, and
assert that nothing was sent, beside a control that a real-looking pid is signalled through the same patches.

## Examples

To prove the command confirms the process is gone, use a stand-in that ignores SIGTERM. A stand-in that dies on the
first signal passes even when the confirmation and the SIGKILL fallback are missing. Have it record a chosen pid,
including 0, and pair it with an unheld lock that names a live process the test started and checks is left
running.

## Related

- [Prove each condition of a gate on its own, with the gate still shut](prove-each-gate-condition-alone-2026-11-03.md),
  from the same supervisor work.
