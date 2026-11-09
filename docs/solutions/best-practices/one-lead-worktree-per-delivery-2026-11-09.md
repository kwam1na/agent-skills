---
title: "One lead worktree per delivery when deliveries share a machine"
date: 2026-11-09
category: best-practices
module: run-delivery
problem_type: best_practice
severity: medium
tags: [lead, worktrees, lead-plan, parallel-deliveries]
applies_when: "Two or more deliveries' leads run on one machine against the same base"
---

# One lead worktree per delivery when deliveries share a machine

## Problem

Three deliveries ran on one machine against `main`. Each lead committed onto the operator's checkout's local
`main`, and one lead's untracked file blocked another's `plan-review prepare`. Two leads moved to worktrees of
their own on `lead/<delivery>` branches, since git checks a branch out in one worktree only. From there
`lead-plan approval` refused by branch name, so one lead recorded an approval by editing the delivery file by
hand, outside the plan's change record.

## Solution

Give each delivery's lead its own worktree on its own branch, made from the base on the remote and fast-forwarded
with `git merge --ff-only origin/<base>`. `lead-plan change` and `lead-plan approval` accept that worktree when
its HEAD is the base's tip, or ahead of it with only that delivery's own delivery file and plan commits. They read
the worktree's own delivery file, rather than the base on the remote that a checkout off the base reads, commit
there, and print the push, `git push origin HEAD:<base>`. A worktree behind the base, diverged from it, ahead with
any other commit, or dirty is refused, naming the reason. A story branch stays a lane's.

## Why This Matters

A lead checkout's job is to hold the base plus that lead's own pending plan commits. The branch name was a proxy
for that, and it fails as soon as two leads need the base at once. Checking the relation to the base, and what
the commits ahead change, keeps another lead's unpushed work out of the push the command prints.

## When to Apply

- Before a second delivery's lead starts on a machine where another delivery's lead already works.
- Whenever a lead command refuses by branch name in a checkout that holds the base's tip.
