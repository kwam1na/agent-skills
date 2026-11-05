---
title: "Show a derived list in the live view without storing it as truth"
date: 2026-11-03
category: design-patterns
module: delivery-views
problem_type: design_pattern
severity: medium
tags: [live-view, supervisor, derived-state, staleness]
applies_when: "A view must show a list a process derives each pass, without the list becoming stored state"
---

# Show a derived list in the live view without storing it as truth

## Problem

Some of what a lead needs to see is derived, never stored, such as the supervisor's
"Needs you". The close condition reads the live function. The view can't call it, so
each pass writes the list it derived into a file beside the record, replacing the last
one. That file is only that pass's view of the list. Nothing reads it back as state.

## Solution

Derive the list where its inputs are current, and draw it from that same pass.
Publishers move the tracker ledger, so run them first, then derive and write the list,
then run the renderers. One render that runs both halves at once draws the previous
pass's list.

## Why This Matters

The live view pushes only when the record's own files change. A list written beside
the record gets no push, so an open page has to fetch the served page again on its own
timer. Otherwise it keeps an old list until something else writes to the record.

## Prevention

Never let a page show a list from a supervisor that is gone:
- Write `running: false` when the process stops, and draw it.
- Give a running list a `staleAfter`: the pass interval plus the longer of that
  interval and the pass's own time. That catches a process that was killed before it
  could say so.
- Refuse the whole file when any part of it can't be trusted, and say why.

With no file at all, draw nothing, so pages from before the list existed are unchanged.

## Examples

- The supervisor's "Needs you": each pass writes the list it derived beside the record,
  and the page draws that pass's list.
- A supervisor killed before it could write `running: false`: its list passes its
  `staleAfter`, and the page stops showing it as current.

## Related

- [Preserve reviewer reports without inventing a run](runless-review-observations-2026-10-23.md),
  another view that shows what was observed without storing it as truth.
