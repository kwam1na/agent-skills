---
title: "Grant line spans one line wider than the edit, and grant the registry-pinned tests with them"
date: 2026-11-19
last_updated: 2026-11-21
category: best-practices
module: run-delivery
problem_type: best_practice
severity: medium
tags: [lead-own, regions, ownership, lanes, catalogue, journal-kinds]
applies_when: "A lead splits a shared text file between lanes with lead-own --region, or a story adds a journal kind, a command, a script or a test module"
---

# Grant line spans one line wider than the edit, and grant the registry-pinned tests with them

## Problem

A delivery with two lanes split eight shared files by line span. The lanes then asked for nine more grants before
their stories could hand off, and each request cost a round-trip to the lead. The extra grants came from two causes.

- **Boundary insertions.** A span is `[start, end)`, and an insertion must fall strictly inside it. A lane that
  inserts after a span's last line, or before its first, is refused, even when it was told to "replace the closing
  `},` with `},` and the new entry". Git's diff reads repeated `},` and `{` lines as an insertion one line past the
  edit. This happened at the end of a file (`main()` before `if __name__`), in the catalogue in
  `.agents/orchestration.json`, and before the first line of a sorted list in three profiles.
- **Registry-pinned tests.** Adding a kind, a command, a script or a test module changes tests that enumerate every
  one of them. The plan named none of them, and each showed up as a red test in the lane.

## Solution

When granting a span:

- Pass `--end` two past the last line the lane will touch, so the span holds the line after it and an insertion after
  that line is inside. Pass `--start` one before the first, when the lane inserts before it.
- For an entry the lane adds next to an existing one, grant the whole neighbouring entry plus the line after it.
- Once only one lane is still working in a file, grant the whole file instead of a span.
- After a base edit crosses a span, `review-prepare` refuses it ("a base edit intersects the span"). Revoke it and
  grant against the current base, or grant the whole file.

When a story does one of the following, grant these with its files:

- **Adds a journal kind:**
  - its row in `tests/test_journal_kinds.py`'s table;
  - the writer's row in `tests/test_journal_shape.py`;
  - its row in the guide's tracker table, which must list every kind in registry order;
  - `REFERENCED_KINDS` in `tests/test_run_event_emission_corpus.py`, if a skill's text names it;
  - for a "Needs you" kind, its row in `wait-words.json` and in `tests/test_waits.py`'s `given` table.
- **Adds a command that journals:** its writer row in `tests/test_journal_shape.py`.
- **Adds a script under a skill:**
  - the provenance listing in `provenance.lock.json`, which needs a listing grant;
  - each profile's `expectedFiles`, which must stay sorted;
  - the release-integration set in `tests/test_release_integration.py`.
- **Adds a test module:**
  - `CATALOGUE_MODULES` in `tests/test_own_deliveries.py`;
  - an entry in `.agents/orchestration.json`'s `selection.catalogue`.

  The catalogue's ids must come in exactly `CATALOGUE_MODULES`' order, so both edits go at matching positions.

These registries can't be split between lanes by span. `CATALOGUE_MODULES`, the table in
`tests/test_journal_kinds.py` and the inventory in `tests/test_journal_shape.py` are each one multi-line Python literal.
In a Python file a span's edges must start a logical line, so a carve that gives one lane a line inside the literal is
refused ("the span cuts the statement"). Only one lane can hold such a file at a time, as a whole-file claim.

- In the plan, name one lane for each of these files, and the order in which the other lanes get it.
- Move it with `lead-own --move` as soon as the holder's story merges, and tell the next lane.
- Every other lane builds and gates its story meanwhile, keeping its one-line addition as it is. It reviews once it
  holds the file, because review preparation refuses a change to a file the lane doesn't hold.
- Assign the waiting lane its next story in the meantime, when that story doesn't touch the file.

## Why This Matters

In a later delivery with four lanes, five stories from lanes that didn't hold the file each added one word to
`CATALOGUE_MODULES` or one row to the journal tables. The file passed from lane to lane after each merge. Two finished
stories waited hours, with every other gate green, for a single line each.

A refused hand-off or a red registry test costs a lane a turn, and the lead another grant. With spans planned one line
wide and the pinned tests granted up front, the lanes in that delivery would have needed none of the nine extra
grants. A registry table that can't be edited in the lane also tempts a lane to delete the failing assertion, which
the repository forbids.

## Prevention

- In the plan, list the registry-pinned tests beside each story that adds a kind, a command, a script or a test
  module.
- `lead-assign` warns about files that often change with the story's files. Read its list before the lane starts, and
  grant from it.
- Count the stories that add a test module or a journal field. When more than one lane does, give those registry files
  to one lane in the plan, or sequence the stories so the moves are planned rather than discovered.

## Examples

- Two lanes both added a kind. One took the span after `blocker.recorded`, and the other the `finish.met` entry. Both
  then needed spans in the `types` section, because a field's type is named in `types` and can't go inline in the
  kind's entry.
- One lane put `run_delivery_gaps` before `run_delivery_pr_review` in `CATALOGUE_MODULES`. Its catalogue entry then
  had to move from before the `hooks` entry to just before `tests.test_run_delivery_pr_review`.

## Related

- `skills/run-delivery/SKILL.md`, "Lane ownership".
- `best-practices/a-listing-stop-is-cleared-by-ownership-not-a-base-merge-2026-11-16.md`.
