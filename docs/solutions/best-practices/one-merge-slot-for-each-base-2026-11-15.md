---
title: "Deliveries that share a base share its merge slot"
date: 2026-11-15
category: best-practices
module: orchestrate-work
problem_type: best_practice
severity: high
tags: [merge-queue, locks, worktrees, concurrency, base-moved]
applies_when: "Two or more deliveries on one machine merge into, or push to, the same base"
---

# Deliveries that share a base share its merge slot

## Problem

Each delivery's merge queue took a lock in its own state folder, `queue.lock`, for the length of a command, and
`lead-push`, `lead-sync` and `lead-upgrade-writer` took the same lock. That lock kept one delivery's commands
apart, and nothing else: with three deliveries on `main` at once, a lead push in one stopped another's
candidate with `base_moved` right after a 51-minute full-suite pass. Another delivery's merges stopped a third
twice. The leads coordinated by hand, with messages, holds and reading each other's `meta.checking`.

## Solution

A lock for each base, shared by every checkout of the repository:
`<git-common-dir>/agent-skills/merge-slot/<base>.lock`. Every linked worktree has the same common directory,
so deliveries in separate worktrees, and a command run from a subfolder, name the same file. In the base's
name `%` is written `%25` and then `/` is written `%2F`, so `integrate/x` and `integrate%2Fx` never share
one.

- **Who takes it.** Every queue command takes it before the delivery's own `queue.lock` and holds it until the
  command ends, however it ends. `lead-push`, `lead-sync` and `lead-upgrade-writer` take it the same way. One
  order for both locks everywhere means a delivery's lead command and its own queue never deadlock.
- **Waiting.** A queue command that waits past the binding's check timeout attempts nothing. Its candidate stays
  queued and `serve` tries again next turn. A lead command refuses, naming the holder's delivery.
- **Fairness.** A waiting command leaves its pid in a `waiting` folder beside the lock. A queue that has just
  merged gives a live waiter one poll before it takes the slot again.
- **Dead holders.** Only the lock decides whether the slot is held. The operating system lets a dead holder's
  lock go, so a holder file that names a dead or unrelated process is never trusted; it is for display only.

## Why This Matters

A lock in one delivery's folder guards only that delivery. The thing being protected, the base branch, is
shared by every delivery that merges into it, so the lock has to live where all of them find it, and the
repository's common directory is the one place every worktree agrees on. Moving the base under a running
check costs that check's whole time, and a full suite is the longest check there is.

## Prevention

- Put a lock where everything that touches the shared thing can see it: beside the shared resource, not beside
  one of its users.
- Take nested locks in one order everywhere. Here the order is the slot, then `queue.lock`.
- Prove a lock with two real processes. Have the holder wait on a file the test controls, stop a dead holder
  by the pid you recorded, and never trust a pid file to say a lock is held.

## Examples

Two deliveries on `main`, each with its own lead checkout as a linked worktree of one repository:

- Delivery A's check runs. Delivery B's queue waits for the slot, with its pid in
  `.git/agent-skills/merge-slot/waiting/main/`.
- A merges. B syncs onto A's merged head, checks once, and merges, with no `base_moved`, no retry and no
  carried check.
- A `lead-push` from either delivery while a check runs waits, then lands after it. With `--wait-seconds 0`
  it refuses with `slot_held`, naming the delivery that holds it.

## Related

- The orchestration guide's "Deliveries that share a base share its merge slot" and "Waiting for the slot".
- `a-stopped-base-rerun-is-not-a-broken-base-2026-11-14.md`, on another way a queue mistook something else for
  its own base moving.
