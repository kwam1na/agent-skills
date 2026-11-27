---
title: "Reproduce a redraw's scroll jump in jsdom with a crude box model that clamps the window"
date: 2026-11-27
category: test-failures
module: delivery-views
problem_type: test_failure
severity: medium
tags: [page-tests, jsdom, scroll, layout, redraw]
symptoms: "The live view's scroll jumped on every 30-second tick in a browser, but every page test passed: jsdom reports scrollY 0 and every box as zero height, so a redraw can't move the window there"
root_cause: "A browser clamps the reader's place to what the page can hold at each layout read; a redraw that measures while part of the page is briefly shorter moves the window. jsdom does no layout, so nothing clamps and the bug can't show"
resolution_type: test_fix
---

# Reproduce a redraw's scroll jump in jsdom with a crude box model that clamps the window

## Problem

The line map lost the reader's place on each 30-second tick, a resize and a
pushed update: a reader scrolled to the end of the line was moved up by a few
hundred pixels. In jsdom nothing has a size and `scrollY` never moves, so no
page test could fail on it, and a fix had nothing to prove itself against.

## Symptoms

- In a browser, a tick moves the window although nothing above the reader grew.
- Every page test passes on the buggy tree. A test that sets `scrollY` and reads
  it back after a redraw gets the same number, because jsdom never clamps it.

## What Didn't Work

- Stubbing `scrollY` as a plain number. The redraw never moves it, so the test
  passes on the bug.
- Asserting that the redraw calls `scrollTo` with the old value. That pins how
  the fix works, not what the reader sees, and the bug itself makes no call to
  catch.

## Solution

The page lane's harness (`tests/delivery_views_page/support.mjs`) takes a
`layout: { height? }` option that lays the page out crudely:

- Each shown element is a 10-pixel box in document order, holding its shown
  descendants, and nothing inside `hidden` takes room.
- The window is `height` tall, 300 by default.
- Every layout read (`scrollY`, `scrollHeight`, `getBoundingClientRect`,
  `offsetHeight`) first clamps the reader's place to what the page then holds,
  as a browser's does, and `scrollTo` moves it.
- A `MutationObserver` marks the boxes stale, so a read after a change lays the
  page out again.

With it, the bug reproduced: on the base tree a tick moved `scrollY` from 1810
to 1560 in `line-map-place.test.mjs`. The fix's tests were then written red
first.

## Why This Works

The jump comes from one browser behaviour, clamping at each layout read, and
not from real geometry. A model with fixed box sizes keeps that behaviour and
nothing else, so a redraw that empties and refills a chapter shortens the page
mid-draw exactly as a browser's would, and the window moves for the same reason.

## Prevention

- When a page bug lives in layout or scrolling, model the one browser behaviour
  that causes it before writing the fix, and show the test red on the base.
- Reach for the `layout` option before writing another scroll stub; later slices
  of the same delivery reused it for one-move navigation counts and redraw tests.
- Keep the model crude. Exact pixel sizes would pin the page's markup, while
  document order and `hidden` are all a scroll bug needs.

## Related Issues

- `docs/solutions/test-failures/a-clicked-link-changes-the-page-on-a-later-task-2026-11-24.md`,
  another jsdom behaviour that differs from a browser's.
- Plan: `docs/plans/2026-11-26-live-view-page-controller.md`, the reader's place
  through a redraw.
