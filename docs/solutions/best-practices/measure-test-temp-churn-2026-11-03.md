---
title: "Measure a test suite's temp churn with a real observer"
date: 2026-11-03
category: best-practices
module: test-suite
problem_type: best_practice
severity: medium
tags: [tests, tmpdir, fsevents, git]
applies_when: "A test suite's temporary files load the machine, or a fixture change claims to reduce them"
---

# Measure a test suite's temp churn with a real observer

## Problem

The suite wrote about 850,000 files per run, which loaded the machine and its file
event daemon. Most of that wasn't what the fixtures meant to write.

## Solution

Count with an observer rather than a guess. `scripts/measure-test-writes.py` gives
each test unit its own `TMPDIR` under one fresh root and counts FSEvents there. A
short run needs a marker file written and seen at both ends, because a stream starts
and delivers late.

The volume came from:

- Root copies that carried every environment. `tests/corpus_copy.py` copies the
  corpus without them.
- `git init` copying the sample hooks unless `GIT_TEMPLATE_DIR` is empty.
- `git fast-import` unpacking below `fastimport.unpackLimit`, 100 by default, so a
  small seed becomes loose objects and every local clone links each one.
- `git clone` rewriting its config about eight times, so writing it once saves real
  volume across hundreds of repositories. `tests/quiet_git.py` holds these.

## Why This Matters

Measuring before and after on one tree is what makes the ratio mean anything. A base
that moves during a delivery takes the ratio with it: this work read 49.4% at one
tree and 49.66% at the next, against different totals.

## Prevention

A copy shared across tests has to compare the whole tree on each hand-over and
unlink before writing back, or a hard link carries the bytes elsewhere. Comparison
can't see a process or open file left running, so the guarantee covers what a test
wrote before it ended.

A refusal test that calls `main()` in-process must patch the run, or a lost refusal
runs the suite inside itself.

## Examples

macOS's `mktemp -d` ignores `TMPDIR`, so those writes go unseen by this measure.

No sensor guards the ratio, so later tests can move it unnoticed.

## Related

`AGENTS.md` carries the standing rule to use the helpers.
