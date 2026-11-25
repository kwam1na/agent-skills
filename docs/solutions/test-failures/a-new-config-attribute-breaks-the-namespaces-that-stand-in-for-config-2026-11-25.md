---
title: "A new Config attribute breaks the namespaces that stand in for Config"
date: 2026-11-25
category: test-failures
module: run-delivery
problem_type: test_failure
severity: high
tags: [run-delivery, fixtures, simplenamespace, check-selection, base-broken]
symptoms: "After V26-3741 merged, tests.test_run_delivery_supervisor's LaneChangesBytesTests failed on the integration base with AttributeError: 'types.SimpleNamespace' object has no attribute 'shelf', raised from shared_binding through write_lane_changes"
root_cause: "shared_binding began reading c.shelf.ref, which every real Config has, but tests/test_lane_changes.py builds its config as a SimpleNamespace with only the fields the lane-changes step used to read; the story's selected checks did not include the module that drives write_lane_changes through that fixture"
resolution_type: test_fix
---

# A new Config attribute breaks the namespaces that stand in for Config

## Problem

Several test modules stand a `types.SimpleNamespace` in for `run_delivery`'s `Config`. Each holds only the fields
the code under test read when the fixture was written. A change that makes a shared helper read a field every real
`Config` has passes against `Config`, but it breaks every namespace that lacks the field. The break shows only in the
modules that build those namespaces, which may be nowhere near the change.

## Symptoms

On delivery V26-3893, V26-3741 added `shared_binding`, which starts with `if c.shelf.ref:`. The story's checks passed,
and the queue merged it. The next candidate failed `tests.test_run_delivery_supervisor`, in two `LaneChangesBytesTests`
cases:

```text
AttributeError: 'types.SimpleNamespace' object has no attribute 'shelf'
```

The base re-run failed the same way at the merged head, so the queue recorded `base.broken` and stopped serving.

## What Didn't Work

The story's selection named the modules its plan section listed and the ones the lane touched. Nothing tied a
`run_delivery.py` helper to the modules whose fixtures call it through a namespace. No affected-checks provider is
declared here, so the gap rested on whoever chose the checks.

## Solution

Give the fixture the field a real lead `Config` has. In `tests/test_lane_changes.py` the config gained
`shelf=run_delivery.Shelf(self.lead)`. A lead's shelf reads its own working tree, with no ref, so `shared_binding`
takes the base-binding path, as it does for a real lead. V26-3616 carried the fix as the base's repair, and the queue
recorded `base.repaired` when it merged.

## Why This Works

The fixture's job is to look like a `Config` to the code under test. A field that every real `Config` sets belongs
on the fixture, with the value a real one of that kind (lead or lane) would have. Making the helper tolerate a missing
field would hide the next drift instead of fixing this one.

## Prevention

- Before a change makes a helper read a `Config` field it didn't read before, search the tests for
  `SimpleNamespace(` configs that reach the helper, and add the field to them in the same change.
- Select the modules that search finds as checks for the change, as well as the modules the story touches. On
  2026-11-25, 22 test modules used `SimpleNamespace(` (`git grep -l "SimpleNamespace(" tests/`), configs among them, so
  the search is the rule. `tests.test_run_delivery_supervisor` and `tests.test_lane_changes` are only the two this incident hit.

## Related Issues

- Delivery V26-3893's record: journal entries 293 (`check.failed`), 296 (`base.broken`), 317 (`base.repaired`), and
  repository gap 339.
- `docs/solutions/test-failures/a-recipe-test-that-normalizes-its-fixture-hides-the-real-inputs-drift-2026-11-23.md`
  approaches fixture drift from another side.
