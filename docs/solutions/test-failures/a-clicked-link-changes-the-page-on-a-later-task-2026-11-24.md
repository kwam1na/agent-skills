---
title: "A clicked same-page link changes the page on a later task, so a snapshot right after click() can miss it"
date: 2026-11-24
category: test-failures
module: delivery-views
problem_type: test_failure
severity: medium
tags: [page-tests, jsdom, hashchange, flaky, load]
symptoms: "line-map-siblings 'the chapter draws a folded group per sibling' failed in 2 of 6 full page-lane runs and 3 of 24 runs under 12-way load, never alone; the snapshot still showed the Now page"
root_cause: "jsdom follows a clicked same-page link on one setTimeout(0) task and fires hashchange on another; the harness's click step snapshotted after click() returned, between the two, when the machine was busy"
resolution_type: test_fix
---

# A clicked same-page link changes the page on a later task, so a snapshot right after click() can miss it

## Problem

The page lane's `openPage` click step clicked the rail's link to `#across` and took its snapshot as soon as
`click()` returned. On a quiet machine the hash change had always landed by then. Under load it sometimes had not,
and the snapshot still showed the Now page.

## Symptoms

- One siblings page test failed intermittently in full runs and under load, and never when run alone.
- The failing snapshot showed the page the click started from, not the one it linked to.

## What Didn't Work

- Rerunning the test alone: it passed every time, so the cause stayed hidden until the run was reproduced under
  12 concurrent workers.

## Solution

In the harness's click step (`tests/delivery_views_page/support.mjs`), when the click changed `location.hash`, wait,
bounded, for that `hashchange` before taking the snapshot. Under 12 concurrent runs: 3 of 24 failed before, 0 of 48
after, and 10 consecutive full page-lane runs passed.

## Why This Works

jsdom does not navigate synchronously inside `click()`. It schedules the link's navigation on one zero-delay task and
dispatches `hashchange` on another, so the page is only in its new state after both have run. Waiting for the event
ties the snapshot to the navigation itself rather than to how fast the machine happens to be.

## Prevention

- In a page test, a step that starts navigation waits for the navigation's own event (`hashchange`, `popstate`) before
  asserting, never on `click()` returning or a fixed delay.
- A page test that fails only in full runs: reproduce it under concurrent load before changing anything.

## Related Issues

- V26-3808 (delivery V26-3775) found and fixed it.
- `docs/solutions/test-failures/page-test-ask-count-takes-the-runs-refresh-2026-11-08.md` is another page-lane timing
  trap from another angle.
