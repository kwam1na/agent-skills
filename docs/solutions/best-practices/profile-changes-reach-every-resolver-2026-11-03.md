---
title: "Search every test that resolves a profile when its layering changes"
date: 2026-11-03
category: best-practices
module: release-profiles
problem_type: best_practice
severity: medium
tags: [profiles, sensors, qualification]
applies_when: "A change alters what a release profile includes"
---

# Search every test that resolves a profile when its layering changes

## Problem

A unit's sensor list is usually chosen by name, so a change to a profile's
includes runs the tests named after that profile. Moving the tracker profiles onto
`run-delivery` passed every listed sensor while `tests/test_release_integration.py`,
which also pinned the old layering, would have turned the gating CI job red and
blocked the next release. A reviewer found it, not a sensor.

## Solution

Before choosing sensors for a layering change, search for every test that resolves
a profile or names its contents, and include them. Treat a check scoped to
"everything above core" as a constraint on the layering too, because it stops
being true when the layering moves.

## Why This Matters

The cost lands on the release rather than on the change: `main` can take the merge,
and the failure shows when someone tries to tag.

## Prevention

`resolve_profile` drops an include it wasn't given, so a test whose profile set
omits one gets a short closure rather than an error. Load the whole profile set in
tests that resolve a profile.

## Examples

`scripts/qualify-linear.py` takes the `run-delivery` pair as its base now. Its
earlier base was core, with a carve-out for `delivery-views`.

## Related

[The tracker layering is described in `docs/host-adapters.md`](../../host-adapters.md).
