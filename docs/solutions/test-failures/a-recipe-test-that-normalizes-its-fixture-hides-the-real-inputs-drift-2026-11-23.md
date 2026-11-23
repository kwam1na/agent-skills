---
title: A recipe test that normalizes its fixture hides the real input's drift
date: 2026-11-23
category: test-failures
module: own-deliveries
problem_type: test_failure
severity: medium
tags: [integration, binding, recipe, fixtures, own-deliveries]
symptoms: "The integration restore recipe in docs/own-deliveries.md failed with 'target Python checks are not in canonical order' on main's real binding, while the test that runs the recipe passed"
root_cause: "The test sorted the binding's catalogue before building its fixtures, so it never ran the recipe on the binding as main held it, whose Python checks had drifted out of order"
resolution_type: code_fix
---

# A recipe test that normalizes its fixture hides the real input's drift

## Problem

`docs/own-deliveries.md` documents a script that restores main's standing binding during an integration and keeps
only the delivery's reviewed hosted checks. The script inserts checks by canonical order, and it refuses a target
binding whose Python checks are out of order. `tests/test_own_deliveries.py` runs the script from the doc, but it
built its fixture from a sorted copy of the real binding. Earlier deliveries had added catalogue rows out of order,
so main's real binding had four Python checks out of order. The script refused it, and the test kept passing.

## Symptoms

- In the portable hosted service's integration, the documented restore stopped at `AssertionError: target Python checks are not in
  canonical order` against `origin/main`.
- `tests.test_own_deliveries`, which includes the recipe's own test and its `target-python-order` refusal case, was
  green on the same tree.

## What Didn't Work

- Hand-building the binding without the script worked once, but it skipped the script's guards. The next delivery
  would have hit the same refusal.

## Solution

- Sort the binding's `tests.*` catalogue rows canonically, with the hosted rows last in their reviewed order.
- Build `CATALOGUE_IDS` sorted, so the existing pin on the catalogue's order holds the binding to canonical order.
- Assert directly that the real binding's Python ids are sorted.

The script then exits 0 on the real binding and leaves it byte-identical. The adversarial integration lens and the
lead each ran that check.

## Why This Works

The recipe's precondition, canonical order, is now a property of the real input that a test holds, rather than a
property only of a fixture the test made.

## Prevention

When a test runs a documented command against a fixture derived from real state, check the command's
preconditions on the real state too, not on the normalized copy. Also run the command at least once against the
real input unchanged. A test may normalize its fixture to set up a case, but then the real input needs its own
assertion for the property the normalization assumed.

## Related Issues

- The same script also refuses, by design, when a reviewed id's binding row differs from the target's. It does not
  refuse when only the command that row runs changes. The portable hosted service changed one row,
  `hosted.browser-install`, whose argv gained `PLAYWRIGHT_SKIP_BROWSER_GC=1`, so its restore had to be built by hand.
  `hosted.browser`'s row was unchanged; only the npm script it runs changed. Once main holds the reviewed rows, later
  deliveries are unaffected.
