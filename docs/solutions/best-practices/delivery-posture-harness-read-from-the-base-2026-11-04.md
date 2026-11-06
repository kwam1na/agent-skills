---
title: "Read a merge gate's off switch from the base, and try to fake the base"
date: 2026-11-04
category: best-practices
module: delivery-posture
problem_type: best_practice
severity: high
tags: [delivery-posture, harness, merge-gate, git, review]
applies_when: "A value that turns a merge gate off is read through git from a repository a candidate can write to"
---

# Read a merge gate's off switch from the base, and try to fake the base

## Problem

A declaration that turns the gate off makes a wrongful read a wrongful pass, so reading it from
the base is only where the work starts. Athena's use of its local version since 2026-11-04 had
already needed three corrections: a tag or local branch named `origin/main` beat the short ref, a
hung fetch blocked pushes because Bun 1.1.29's `spawnSync` ignores `timeout`, and its focused
checks read the checkout, so a push of another branch had to be refused. It also needed its own
quick checks to run in place of the gate, which is why those stay the repository's.

## What git let a candidate do

The harness's reader passed its scenarios and still fell to these, each found by an adversarial
review constructing the attack:

- With no `origin` remote configured, git reads `origin` as a relative path, so a bare repository
  committed at `origin/` in the candidate's tree answered as the remote.
- From a subdirectory, git discovers a bare layout committed there as the repository.
- `rev-parse` of a full ref name that is missing falls through its lookup rules, so a tag or branch
  literally named `refs/remotes/origin/main` answers for it. `for-each-ref` matches the ref itself.
- Local replace refs change what a commit or blob reads as, while the reported tip stays true.

## Solution

Require `remote.origin.url`, pass `-c safe.bareRepository=explicit` and `GIT_NO_REPLACE_OBJECTS=1`
on every call, find refs with `for-each-ref`, and keep transport variables on network calls while
dropping the repository-selecting ones. Otherwise a machine that reaches its remote only through
`GIT_SSH_COMMAND` takes the offline path, and a stale tip decides.

## Prevention

- Name who can write each input, then build the attack for each one rather than a scenario for each
  rule. Scenarios with a real remote passed throughout.
- Make a fixture's bad value bad in one way only. A symlink whose blob isn't JSON, and an invalid
  byte that also breaks the JSON, both stayed green with their guards removed.
- A command that didn't do its work must not report that it did. `gate ok` in a run's journal, for a
  gate that never ran, read as a pass, and a later refusal was then judged against it. Compare a
  store's bytes, because comparing file names misses an append.
- An inner call can bypass or inherit a new rule. `admit` called the `gate` command directly, so a
  check placed in that command would have changed `admit`.
