---
title: A delivery that adds binding keys sets them in this repository's own binding only after its release is applied
date: 2026-11-20
category: best-practices
module: orchestrate-work
problem_type: best_practice
severity: high
tags: [binding, generations, schema, merge-queue, own-deliveries]
applies_when: "A delivery in this repository adds a key to orchestration-binding/1 or run-delivery's settings, and its plan also wants this repository to use that key"
---

# A delivery that adds binding keys sets them in this repository's own binding only after its release is applied

## Problem

This repository delivers with the generation installed at `.agent-skills/current`, not with the source in `skills/`.
That generation's queue, `doctor` and state tool validate `.agents/orchestration.json` against the binding schema
they carry. A delivery that adds a key to the schema, such as a check environment's `inherit` list or `reuse.tools`,
changes only the source. Its own stories land on a base whose binding the older, installed generation still reads.

A story that also sets the new key in this repository's binding therefore writes a binding the installed generation
refuses. `doctor` from a fresh clone reports the binding invalid, the tests that run it fail, and once the story
merges, every later queue run reads the base binding and stops with `binding_invalid` until a release holding the
key is applied.

## Solution

Split the two changes. The delivery adds the key to the schema and the code that reads it, with tests that use a
fixture binding. Setting the key in this repository's own `.agents/orchestration.json` goes in the change that
applies the release carrying it, after that release is installed, and the plan says so as a follow-up rather than as
part of a story.

Before granting a story `.agents/orchestration.json`, check each key it adds against the schema in
`.agent-skills/current`, not the one in `skills/`.

## Why This Matters

The refusal doesn't show in the story's own review or module runs, which use the candidate's source. It shows in
`doctor` and in the queue, which run the installed generation, so a story can pass review and still stop the queue for
every story after it.

## Prevention

- In a plan, list "this repository sets the new key" under the release's apply step, not under a story.
- A lens reviewing a binding change runs `doctor --offline` from the story's checkout, which uses the installed
  generation.
- A value the binding's `environment` already sets needs no `inherit` entry; check before adding one.

## Examples

A delivery that made a check's environment declared rather than inherited added `inherit` and `reuse.tools` to the
binding schema. A later story was asked to list `PYTHONDONTWRITEBYTECODE` in `inherit` and `git`, `node` and `npm` in
`reuse.tools` here. Review found the installed generation refused both keys, failing three fresh-clone tests, and
`PYTHONDONTWRITEBYTECODE` was already set in the binding's `environment`. The lead withdrew the instruction, and
`reuse.tools` moved to the change that applies the release.

## Related

- `docs/solutions/integration-issues/upgrade-the-lead-before-writing-a-shared-record-2026-11-13.md`: the same
  generation gap for the shared record's shape rather than the binding.
