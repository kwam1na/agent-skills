---
title: "A quickened page test's ask count took the runs refresh's fetch as one of the plan's"
date: 2026-11-08
category: test-failures
module: delivery-views
problem_type: test_failure
severity: high
tags: [tests, page-lane, jsdom, timers, flaky]
symptoms: "A plan-chapter page test failed on CI's Ubuntu job on 3 of 5 runs: the snapshot at the last `{ asked: n }` still showed the old plan's outcome"
root_cause: "The page's runs refresh fetches the same address three seconds after a push, and the harness ran that timer in real time, so on a slow runner it became one of the fetches `asked` counts and took the answer meant for the plan"
resolution_type: code_fix
---

# A quickened page test's ask count took the runs refresh's fetch as one of the plan's

## Problem

The page-behaviour lane runs the line map's plan-chapter tests with
`quicker: 100`, which speeds up the page's timers. The harness exempted 3000 ms
timers, so that the runs refresh a push schedules would stay out of the way. Its
comment said "every fetch a quickened test sees is the plan's". That holds only
while a test ends within three seconds of its push. A test with seventeen asks
runs for about a second on a laptop and can run past three seconds on a loaded
runner.

## Symptoms

- "a render that never holds the named plan ends in the out-of-date note, until
  one does" failed in `snaps[4]`, the snapshot at `{ asked: 17 }`: it expected the
  changed plan's outcome and got the old one.
- It passed locally and failed on 3 of 5 Ubuntu runs on the default branch,
  including a run whose only changes were delivery files.

## What Didn't Work

- Reading it as an answer that lands after its snapshot. The harness's fetch
  answers through promises the page chains with `then`, so an answer the server
  has already given is taken in before the step's own wait ends. An 80 ms stall in
  every step of the failing test passed three runs out of three.

## Solution

- `tests/delivery_views_page/support.mjs` now holds the 3000 ms timer under
  `quicker` and never runs it, as it already did `setInterval`. `asked` then
  counts the plan's asks alone, however long the test runs.
- The failing test has a `RUNS_DUE` step, a 3.1 second stall. Without the hold it
  reproduces CI's exact assertion every time, because the runs refresh comes due
  between the 16th and 17th plan asks and takes the 17th answer. With the hold it
  passes.
- A `holding(answer, ...holds)` server holds the chosen asks open until a
  `release` step. Three tests use it, so that a snapshot taken late can't show an
  ask that came due after it.

## Why This Works

`fetched` records every fetch of the page's address, and the harness can't tell
the runs refresh from a plan ask, because both ask for the same address with the
same options. Removing the only other fetcher from a quickened page makes the
count mean what the tests read it as. Holding an ask open makes a snapshot's
lateness irrelevant: the page can't take an answer the server hasn't given yet.

## Prevention

- `{ asked: n }` is not a readiness wait. It says the page has asked n times, and
  that every answer already given has been taken in. It says nothing about the
  ask after it. Any snapshot that an ask coming due could change needs that ask
  held, here with `holding` or `downThenHeld`.
- To find which tests can lose that race, run the plan tests against a copy of
  the harness whose step wait is 80 ms longer. That models a late snapshot. A
  stall placed before the ask-wait does not, because the page's timers fire
  during the wait anyway.
- A count of fetches is only as good as the list of the page's fetchers. A new
  fetch of the page's own address has to be held under `quicker`, or counted
  separately.

## Related Issues

- An earlier fix held the retry after a failed ask, with `downThenHeld` and an
  80 ms `STALL`, for four tests whose snapshot came late. That is the same family
  of race, but a different trigger from this one.
