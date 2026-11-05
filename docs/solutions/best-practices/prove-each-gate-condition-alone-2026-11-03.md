---
title: "Prove each condition of a gate on its own, with the gate still shut"
date: 2026-11-03
category: best-practices
module: lead-supervisor
problem_type: best_practice
severity: high
tags: [tests, mutation, close-condition, gates]
applies_when: "A gate opens only when several conditions all hold, and its tests reach it only from the happy path"
---

# Prove each condition of a gate on its own, with the gate still shut

## Problem

A gate that opens only when several conditions all hold, such as a close that waits until every story is merged,
every finish line is met, every lane is done and nothing is blocked, is easy to test only from the happy path. Each
case settles everything and checks that the gate opens, or opens one failure and settles it again in the same pass.
Every one of those cases still passes when one condition is deleted from the gate, because no case ever reaches the
gate with that condition as the only thing holding it.

## Solution

Prove each condition in its own step:

- bring the subject to the point where nothing else holds the gate;
- open exactly one condition;
- run one evaluation and assert both that this condition is named as the holder and that the gate stayed shut;
- close that condition and move to the next.

## Why This Matters

A condition no test isolates can be deleted with the whole suite still green, and the gate then opens while that
condition still holds.

## Prevention

Two more things keep such a test honest. Any step whose evaluation would find nothing holding opens the gate, which
ends the test early, so keep one independent hold open while you probe a condition that can't be isolated otherwise.
Where a condition clears in two ways, such as a held runner's blocker cleared by its hold lifting or by a later
repair, prove each way with the other absent, or a mutant that keeps only one of them survives.

## Examples

In the supervisor's first review, four of the close condition's holds could be removed with the whole suite still
green: an unmerged story, an unmet finish line, a busy lane, and a blocked or waiting lane. The supervisor could have
archived a delivery and stopped with work still open.

## Related

- [Stop a recorded pid only while its lock is held, and test it in a session of its own](stopping-a-recorded-pid-2026-11-03.md),
  from the same supervisor work.
