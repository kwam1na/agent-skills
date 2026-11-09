---
title: "Select checks by what reads a change, not only by what it touches"
date: 2026-11-08
category: best-practices
module: orchestrate-work
problem_type: best_practice
severity: high
tags: [merge-queue, check-selection, schemas, fixtures]
applies_when: "A lead or provider chooses a candidate's checks from the files its diff touches"
---

# Select checks by what reads a change, not only by what it touches

## Problem

A delivery's per-story check selections let five defects onto the base or up to
the queue: a ticket id in a test, a journal kind named in the wrong skill, an
unquietened fixture repository, plan fixtures that predated a schema change, and
a guide naming a file its corpus test didn't allow. Each selection was keyed to
the files the story touched, so it missed the corpus tests and the captured
fixtures that read those files' output. Each was also written out by hand as
full check entries, which made a wide selection expensive to write.

## Solution

The binding's `selection` declares a `catalogue` of named checks, `guards` that
join every lead selection, and `readers`: for each path, the checks that read
it, captured fixtures included. `select --checks run-delivery,corpus-guards`
resolves ids to entries, adds the guards, and adds every reader of a path the
candidate's diff changes. A test that only mentions a file is not its reader
unless the binding says so: readers are declared, never inferred from text.
`select` also warns when a selected check's latest base re-run at the current
base head failed, from the queue's `meta.baseReruns`, so a selection that would
stop as `base_broken` is seen before it queues.

## Why This Matters

A change's blast radius is what consumes it, not where it lives. Keying checks
to touched files is right for the files' own tests and wrong for every reader
downstream, and those readers are where a schema or wording change breaks.
Declaring readers once in the binding moves that knowledge out of each lead's
memory.

## Prevention

- When a file's output is read elsewhere (a schema, a generated listing, a
  guide a corpus test pins), add a `readers` entry naming its readers.
- Make the repository's corpus and qualification tests `guards`, so no
  selection can leave them out.
- Read `select`'s warnings before queueing: a selected check failing at the
  base head would stop the queue.

## Examples

A binding whose schema is read by the state tool's tests and by captured plan
fixtures:

```json
"selection": {
  "lead": true,
  "catalogue": [
    {"id": "schema-readers", "argv": ["python3", "-B", "tests/test_orchestration_state.py"], "cwd": ".", "reasons": ["reads schemas.json"], "prerequisites": []},
    {"id": "plan-fixtures", "argv": ["python3", "-B", "tests/test_plan_fixtures.py"], "cwd": ".", "reasons": ["fixtures captured against schemas.json"], "prerequisites": []},
    {"id": "corpus-guards", "argv": ["python3", "-B", "tests/test_registry.py"], "cwd": ".", "reasons": ["the corpus"], "prerequisites": []}
  ],
  "guards": ["corpus-guards"],
  "readers": [{"paths": ["skills/orchestrate-work/references/schemas.json"], "checks": ["schema-readers", "plan-fixtures"]}]
}
```

A story changing `schemas.json`, selected with `--checks schema-readers`, runs
`schema-readers`, `corpus-guards` and `plan-fixtures` without the lead naming
the last two.

## Related

- `skills/orchestrate-work/references/orchestration-guide.md`, "The check
  catalogue".
