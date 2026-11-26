---
title: Word an actor where it is known, and check it where the reader is
date: 2026-11-26
category: design-patterns
module: delivery-views
problem_type: design_pattern
severity: medium
tags: [shared-record, seats, needs-you, wording, line-map]
applies_when: "A view shows several readers words that name an actor (you, the lead), and the actor and the reader are known in different places"
---

# Word an actor where it is known, and check it where the reader is

## Problem

On a shared delivery record, every seat ran the supervisor's Needs you step and
wrote its own list, with each item's actor as a role: operator, lead or a lane.
Each seat's page then said "you" for the same call, and "the lead" named no seat.
The first idea was to let the page work out each item's owner from the journal
and swap a name in. That fails because the supervisor's headlines put the actor
mid-sentence ("Lane 2 waits on the lead"), so a page can't reword them.

## Solution

Split the work by who knows what:

- **The writer words.** The supervisor knows the actor: the scope an item belongs
  to and the seat holding it at the pass. It stamps each item with `scope` and
  `seat` and writes three wordings: `said` for its own seat's reader, `saidAll`
  for a reader with no seat (owners by label), and `saidRole` by role only.
- **The page checks.** The page knows the reader: the seat embedded in the local
  page, or none on the hosted page. An item is "yours" only when it is a person's
  call, its stamped seat is the viewer's, and that seat still holds its scope.
- **A stale stamp reads by role.** When the journal says the scope has moved since
  the pass, the page shows `saidRole`, takes nothing over and never says "you".
- **Unshared records stay byte-identical.** The page tells shared from unshared by
  `meta.collab`, not by whether a viewer exists, and nothing extra is written for
  an unshared record.

Words the page writes itself, such as the rejoin's "waits for your merge", are
worded on the page for the seat holding the owning scope (`delivery`).

## Why This Matters

Each piece of knowledge has one home, so the two can't disagree. Wording on the
page from the journal alone breaks on any sentence it didn't write, and wording
in the supervisor alone can't know which page reads it.

## Prevention

- Pin the unshared output byte for byte. A guard on `meta.collab` that a later
  change drops is otherwise invisible.
- Pin the owner table as one literal mapping, kind to scope. A completeness check
  alone says nothing about which scope each kind gets.
- Give every stamp a role-only fallback, so a reader is never told "you" for a
  call that has since moved.

## Examples

- Plan: `docs/plans/2026-11-26-first-class-seats.md`, decisions 8 to 10.
- Supervisor stamping and wording: V26-3994 (`NEED_SCOPES` in
  `skills/run-delivery/scripts/run_delivery.py`).
- The page's `yours(item)` and the seat chip: V26-3996
  (`skills/delivery-views/assets/line-map.js`).

## Related

- `docs/solutions/integration-issues/upgrade-the-lead-before-writing-a-shared-record-2026-11-13.md`
