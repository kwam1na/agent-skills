---
title: "Select checks by what reads a change, not only by what it touches"
date: 2026-11-08
last_updated: 2026-11-21
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

The same declarations tell a base move that matters from one that doesn't. A
plain readers entry only says which checks a change adds, so it can't say a
check is safe from a change elsewhere. An entry marked `"complete": true` says
its paths are everything its checks read, their test modules included, and
`selection.unread` names what nothing reads. When the base moves after a
candidate's checks passed, the queue lists the move's files without rename
detection. A check whose read set is known and untouched, or every check when
the whole move is unread, is carried to the new base with no run again, and
one `check.carried` names it and the base commits it was not run on. A file in
the candidate's own diff, or the binding file, carries nothing.

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
- Check the binding after writing the note. A journal kind named in the wrong skill
  reached a base a second time after this note named it, because the binding still
  had no reader for skill pages: a note that names a defect changes nothing until a
  `readers` or `guards` entry carries it. A change to any Markdown file under
  `skills/` now brings the corpus tests that read the shipped skill prose.
- Read `select`'s warnings before queueing: a selected check failing at the
  base head would stop the queue.
- Mark a readers entry complete only when its paths really are everything its
  checks read. A complete entry that misses a read file carries a check over a
  base change that breaks it, and only the post-merge `fullHealth` run would
  catch it.
- A reader the binding declares still has to run where the change is made. In V26-3689 a lane
  changed run-delivery's brief templates, its journal call sites and its skill page, and handed
  off green on its plan's sensors. The queue's full suite then failed three corpus tests the lane
  never ran: `tests.test_round_brief_template_corpus` and `tests.test_run_event_emission_corpus`,
  which the `skills/**/*.md` reader already names, and `tests.test_journal_shape`, which no reader
  names for the scripts it inventories (`run_delivery.py`, `merge_queue.py`, `delivery_state.py`).
  Before hand-off, run the readers of every path the story changes, and give a script whose
  journal writes a test inventories a `readers` entry naming that test.

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

Re-checking by what reads the base's change: a queued candidate's
`run-delivery` check passed, and then the base moved, in a merge that changed
only `tests/test_delivery_views_render.py`. With
`{"paths": ["skills/run-delivery/**", "tests/test_run_delivery.py"], "checks": ["run-delivery"], "complete": true}`,
that check is carried and the candidate merges without running it again.
`"unread": ["docs/plans/**"]` lets a base move that only adds a plan carry
every check.

## Related

- `skills/orchestrate-work/references/orchestration-guide.md`, "The check
  catalogue".
