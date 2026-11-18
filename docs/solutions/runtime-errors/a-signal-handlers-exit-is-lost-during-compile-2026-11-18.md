---
title: "A signal handler's exit is lost when it lands while Python compiles a module"
date: 2026-11-18
category: runtime-errors
module: run-delivery
problem_type: runtime_error
severity: high
tags: [signals, supervisor, cpython, import, shutdown]
symptoms: "A supervisor sent SIGTERM records that it received it, then keeps passing, sleeping between passes and starting new children; a test's 30-second stop wait fails about once per class run"
root_cause: "The SIGTERM handler raises SystemExit, and CPython before 3.14 can drop an exception raised by a signal handler while it compiles a module, as on a first in-process import under -B; compile() returns normally and the exit is gone"
resolution_type: code_fix
---

# A signal handler's exit is lost when it lands while Python compiles a module

## Problem

The delivery supervisor (`lead-run`) stops on SIGTERM through a handler, `Supervisor.on_signal`. The handler records
the signal in `supervisor.json`, then calls the handler that was there before. That one raises `SystemExit`, which
unwinds the supervisor through its `finally` blocks.

Peer waits between deliveries added a step to every pass that imports `delivery_state` in-process. Under `-B` nothing is cached, so the
first pass compiles that module from source. When the TERM arrived during that compile, the handler ran and recorded
the signal, and `_terminate` raised `SystemExit` inside `compile()`. Then `compile()` returned normally, the exit was
gone, and the supervisor carried on with its passes.

A probe that raised `SystemExit` from a handler while `delivery_state.py` compiled lost it in 206 of 300 tries on
CPython 3.11.18, 245/300 on 3.9, 300/300 on 3.13 and 0/300 on 3.14. So any first in-process load is exposed, not only
the new step.

## Symptoms

- `tests.test_run_delivery_supervisor.LiveViewAddressTests.test_a_retargeted_generation_replaces_the_view_on_its_port_and_the_same_one_is_adopted`
  failed in the merge queue with "the supervisor (pid …) to stop didn't happen within 30s". It failed at a load of
  about 5, and passed alone at the story's own head.
- `supervisor.json` said the supervisor had received SIGTERM. No signal was pending or blocked in any process in the
  group.
- After the TERM, the supervisor kept sleeping between passes and started new `render_state` children. Its
  `merge_queue serve` child, started after the TERM, survived too.
- `main` without the story passed the same case. The story merged with `main` failed about once per class run.

## What Didn't Work

- **Calling it load.** The first failure came at a 5-minute load over 30, so it looked like the load-sensitive tests
  that had already stopped other stories. It recurred at a load of 5.
- **Fixing the obvious swallow.** `run_delivery.py`'s state reader wraps its `exec_module` in `except BaseException`
  and re-raises only `KeyboardInterrupt`, so it swallows a `SystemExit` too. Narrowing it left the class failing 2 runs
  in 6. That swallow is real, but it wasn't this failure.
- **Suspecting the signal never arrived.** Two processes surviving one group TERM suggested the signal was blocked.
  Stack dumps written from the handler showed it had arrived, and that it landed inside `compile()`.

## Solution

Record the signal, and act on it at a point where raising is safe. That's the pattern `merge_queue serve` and
`render_state` already used. In the peer-waits change (cdded3d4):

- `on_signal` sets `self.signalled = number` before it hands the signal on.
- `honour_signal()` calls `on_signal` again when a signal is recorded. That re-raises through the same handler, and
  `supervisor.json` isn't rewritten.
- The pass calls `honour_signal()` before each step, and the wait loop calls it each time round.

A lost exit now costs at most one step or one wait poll.

## Why This Works

The handler can still raise where Python can't deliver the exception, but the signal is no longer only an exception in
flight. It's recorded state, and the pass reads that state at points where nothing swallows a raise. The re-raise goes
through the same handler, so shutdown runs the same `finally` blocks and records the same status as an exit that
wasn't lost.

## Prevention

- Don't make a signal handler's raise the only record of a stop in a long-running process. Record the signal, and
  check the record at the loop's steps and waits.
- Treat an in-process import as a place a signal can land, especially the first one under `-B`, and especially before
  CPython 3.14.
- Never wrap a load or `exec_module` in `except BaseException` without re-raising `SystemExit` as well as
  `KeyboardInterrupt`. The state reader's swallow in `run_delivery.py` is still open.
- When a stop test fails only once a story is merged with `main`, reproduce it on that merge, not at the story's head.

## Related Issues

- The peer-waits change (cdded3d4), which made the compile likely on the first pass and carries the fix.
- The open report of supervisor tests failing a 30-second stop wait. This is a root cause of that.
- `docs/solutions/test-failures/detached-fixture-supervisors-outlive-interrupted-workers-2026-11-09.md`: a different
  way a supervisor outlives a stop.
- `docs/solutions/test-failures/tests-that-read-the-hosts-load-2026-11-18.md`: the load failures this was first
  mistaken for.
