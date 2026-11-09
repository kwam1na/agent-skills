---
title: "A ready-wait that accepts any listener on the port runs its check against another run's server"
date: 2026-11-09
category: test-failures
module: run-delivery
problem_type: test_failure
severity: high
tags: [tests, ports, concurrency, leases, subprocess, in-app]
symptoms: "the adoption-guide case's adapter-test failed its last case, loaded, with URLError Errno 61 Connection refused, after its first six cases had passed, only in suites that ran at the same time as others on the same commit"
root_cause: "adapter-test and app-check waited until the port answered, never asking whether the answer came from the serve they had started; under a lease of their own, two runs on one port both found it free, one serve lost the bind and died silently, and that run checked against the other's server until it stopped"
resolution_type: code_fix
---

# A ready-wait that accepts any listener on the port runs its check against another run's server

## Problem

`adapter-test` and `app-check` each start the repository's serve adapter on the binding's `inApp.port`, then wait for
the port to answer before running the check. They probe the port before starting the serve, and refuse if something
already answers there. The lease (`inApp.lock`) is meant to keep everyone else off the port while they run.

The wait asked only whether the port answered, not who answered. The lease is a lock on a path, not on the port. So
two runs whose locks are different paths can both hold "the" lease for one port. Both probe the port, find it free
and start their serves. One serve binds; the other fails with `Address already in use` and exits. Its output went to
`/dev/null`, so nothing said so. Its run's wait then saw the port answer, from the other run's server, and ran its
check against that.

## Symptoms

- The adoption-guide case failed on the guide's `adapter-test` block with `loaded: no JSON report (... URLError:
  <urlopen error [Errno 61] Connection refused>)`. `loaded` is the last of seven cases, and the six before it passed.
- It failed only in full suites that ran at the same time as others on the same commit. Each suite reached the case at
  nearly the same moment, and a run of one alone passed.
- The six passing cases were the clue. The adapter fixture's page and stamp are the same in every run, so a check
  against the other run's server passes until that server stops. The next case then finds nothing listening.

## What Didn't Work

- Reading it as a slow first listen under load. A slow start would fail the first case, or the wait would give up
  with "didn't start listening". Here the server was there for six cases and then went away.
- Breaking out of the wait when the serve exits. `app-check` already did that. But in the race the other run's server
  often answers first, while the losing serve is still alive or just about to fail its bind, so the wait breaks on the
  port, not on the exit.

## Solution

Both commands now start their serve through one function, `start_serve`, which settles who answered before the
check runs:

- The serve's output goes to `<lock>.serve.log`, beside the lease's lock, written by one holder at a time.
- Once the port answers, it names the listeners (`lsof`). A listener in the serve's own process group is this run's,
  including a serve that runs its server as a child, as `npm run serve` does. Any other listener isn't.
- If the listener isn't this run's, it gives the serve a few seconds. Two things can happen in that time. If the serve
  exits of the lost bind, the refusal can name the exit. If the other listener lets the port go and the serve takes it,
  the listener is this run's after all, and the check goes ahead. Otherwise it refuses. The refusal names the listener,
  says whether this run's serve exited (with its code) or is still running, and ends with the serve log's tail. Then
  it stops what it started.
- A serve that exits before anything answers is refused with its exit code and log tail, instead of "didn't start
  listening".
- Where the host can't name listeners, the serve must still be running two seconds after the port answers to be taken
  as the listener. A serve that lost the bind dies of it well within that.

The adoption-guide case gives the adopter a free port, as a new pinned substitution. That case's lease is its own,
because its lock is under the case's own `HOME`, so a fixed port was shared with every suite on the machine that reached
the case at once.

## Why This Works

Ownership of the port is now checked, not inferred from the probe that came before the serve started. The race
between the probe and the bind is still there, but losing it now ends in a refusal that names the other server and
this serve's own error. No check passes or fails against someone else's build.

## Prevention

- A wait for a process you started to listen should check that the listener is that process's, not just that the
  port is open. A stranger's listener answers the same.
- Don't send a helper's output to `/dev/null`. Keep it where a refusal can quote it.
- A lock keeps users of a port apart only when they share its path. Tests that give each case its own `HOME`, and so
  its own lock, must also give each case its own port.
- To prove a race, coordinate both sides with marker files rather than timing. The race rows make the first serve
  bind only once the second has started, so the second always finds the port free and then loses it. The first run's
  check also waits for the case's release, so its server stays up until the second run has finished. Without that,
  the winner could finish and let the port go first, and the loser would then win it.

## Related Issues

- `http-server-name-lookup-stalls-macos-runners-2026-11-07.md`: the same serve adapter's slow bind on macOS runners.
