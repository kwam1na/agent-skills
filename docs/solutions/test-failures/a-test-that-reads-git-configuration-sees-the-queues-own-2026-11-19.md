---
title: "A test that reads git configuration sees the merge queue's own setting and fails only in the queue"
date: 2026-11-19
category: test-failures
module: run-delivery
problem_type: test_failure
severity: medium
tags: [tests, git, merge-queue, environment, hooks]
symptoms: "Two ToolCommitHookTests cases pass in the lane and fail in the merge queue at assert_configuration_kept, with `git config --get-all core.hooksPath` returning a second path under the queue's work folder"
root_cause: "The merge queue runs every check under its hooks bypass by passing core.hooksPath through GIT_CONFIG_COUNT, GIT_CONFIG_KEY_0 and GIT_CONFIG_VALUE_0, and git reads that environment configuration in every command the test starts"
resolution_type: test_fix
---

# A test that reads git configuration sees the merge queue's own setting and fails only in the queue

## Problem

The merge queue runs a candidate's checks with its hooks bypass on. It passes the bypass to every git a check starts
through the environment: `GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0=core.hooksPath` and `GIT_CONFIG_VALUE_0` naming an
empty folder under its work folder. Git reads environment configuration in every command, as if it were set on the
command line. So a test that reads or depends on git configuration sees the queue's value as well as its fixture's.

In V26-3656, V26-3654's `tests/test_run_delivery_tool_commits.py` checked that a fixture repository's own
`core.hooksPath` was unchanged after the tool committed. It passed in the lane and failed in the queue.

## Symptoms

- `AssertionError: '<fixture hooks>\n<queue work folder>/…/hooks' != '<fixture hooks>'` from
  `assert_configuration_kept`, in the queue's log only.
- The same module passes in the lane's gate and in a plain local run.
- After the candidate fails, the queue's base re-run of the check can't reproduce it, because the base doesn't have the
  failure's cause or, for a new module, the test at all.

## What Didn't Work

- Running the module locally again: the lane's shell has no `GIT_CONFIG_*` variables, so it passes every time.

## Solution

Make the test independent of configuration passed through the environment:

- Read only the repository's own file: `git config --local --get-all core.hooksPath`, never a bare `--get-all`.
- Strip `GIT_CONFIG_COUNT`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_KEY_<n>` and `GIT_CONFIG_VALUE_<n>` from the
  environment the fixture's git and the command under test run with, unless the case is about them.
- Add a case that sets them on purpose, so the behaviour under the queue is pinned: the V26-3654 case passes a
  failing hooks folder through `GIT_CONFIG_*`, shows the tool's own `-c core.hooksPath` still wins, and shows a plain
  commit given the same environment does run those hooks.

Before handing off a test that touches git configuration, run it once the way the queue does:

```bash
GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/tmp/queue-hooks \
  .venv/bin/python scripts/run-tests.py -p test_<module>.py
```

## Why This Works

`--local` reads one file, the repository's `.git/config`, so it can't see command-line or environment
configuration. Clearing the variables gives every git the test starts the same configuration in the lane and in the
queue. The deliberate case keeps the test honest about the queue's real environment instead of only avoiding it.

## Prevention

- A lane whose change starts git or reads its configuration runs that module once with the queue's `GIT_CONFIG_*`
  variables exported before hand-off.
- A test asserts on a repository's own configuration with `--local`, or on the effective configuration only when that
  is the point, with the environment it expects set explicitly.

## Related Issues

- `docs/solutions/test-failures/tests-that-read-the-hosts-load-2026-11-18.md`: another test that passed in the lane and
  failed in the queue because it read the machine it ran on.
- V26-3654 (#427), and its queue stop at journal entry 70 of delivery V26-3656.
