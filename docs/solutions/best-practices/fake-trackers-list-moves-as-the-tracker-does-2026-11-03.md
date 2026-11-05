---
title: "Fake a tracker's moves as the tracker lists them, not as its workflow defines them"
date: 2026-11-03
category: best-practices
module: tracker-publisher
problem_type: best_practice
severity: medium
tags: [tests, tracker, fake-server, jira]
applies_when: "A test fake stands in for a tracker whose moves depend on the ticket's current status"
---

# Fake a tracker's moves as the tracker lists them, not as its workflow defines them

## Problem

A tracker publisher has to tell a move a person already made apart from a move
that isn't offered. It needs each move's target status for that. Jira's
`GET issue/{key}/transitions` lists only the moves out of the ticket's current
status. `includeUnavailableTransitions=true` adds the moves whose conditions
fail, never the moves from other statuses. A global move is hidden once the
ticket is already in its target status. So when a ticket has already been moved
on, the move the publisher was about to make isn't listed at all, and its
target is unknown.

## Solution

The fix is in two parts:
- The fakes list only what the tracker lists.
- A move's target comes from the tracker when the tracker lists it. Otherwise
  it comes from `tracker.transitions.targets`, and `doctor` names every
  configured move that has no target.

## Why This Matters

Against real Jira, a move someone else already made would have been recorded as
not offered and shown to the lead as work to do by hand. The adapter-contract
test couldn't catch it, because the second fake was just as lenient.

## Prevention

When a contract field such as a move's target status can be missing in the real
service, write the fake so that it is missing there too, and test the
publisher's behaviour without it.

Review practice from the same unit: a lens that proves tests red by mutating
code must do it in a private export of the candidate (`git archive <head>`),
never in the lane's worktree. A mutation applied in place spoils every sensor
the lane or another reviewer runs in the meantime.

## Examples

The first fake Jira listed the whole workflow from every status, marking each
move offered or not. Against it, the "already made by someone else" scenario
passed.

## Related

- [Prove each condition of a gate on its own, with the gate still shut](prove-each-gate-condition-alone-2026-11-03.md),
  another way a test can pass while the case it names never happens.
