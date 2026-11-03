---
name: compound-learning
description: Decide whether delivery evidence supports a reusable learning, and preserve one where the repository keeps its solution notes.
---

# Compound Learning

## Purpose

Make the delivery's compounding decision explicit, and preserve a reusable
learning where the next delivery can find it. A repository's own declared
location and format win over the ones here.

## Decide

A reusable learning is `required` only when the caller provides:

- a concrete reusable reason, such as a repeatable failure, missing guardrail,
  non-obvious delivery path, or reusable workflow decision;
- the learning to preserve; and
- evidence from the delivery.

Record no durable learning only when the change is local, obvious and unlikely to
recur, and return `not-required` with that reason. A change that is not all three
has a reusable reason to name. Missing or conflicting inputs return `blocked`
rather than inventing knowledge.

Decide before the final review-and-merge loop, while the context is fresh, so a
note is part of what gets reviewed. Where the caller's sequence names compounding
after review, that step confirms and records the decision. A learning first seen
there goes through review with its note while a round and an open change remain to
carry it. Where none does, such as at an exhausted review bound or at close-out, it
goes to a follow-up item, which carries the note through a review of its own. No
note lands unreviewed.

## Preserve

Put a required learning in the one place that fits:

- A learning that changes how agents deliver across repositories goes to a skill.
- A concrete missing sensor or tooling gap goes to a follow-up item.
- Guidance or a sensor the repository already has, when the learning belongs in
  it, is updated there.
- Anything else becomes a solution note.

A caller limited to listed files, such as a lane, hands the learning through its
handoff to whoever owns the store rather than writing it.

## Solution Notes

Where the repository declares another store, read it for `docs/solutions/`
throughout.

Search `docs/solutions/` first. If an existing note covers the same problem, root
cause and solution, update it in place and add `last_updated`, because two notes
on one problem drift. A related note from another angle means a new note.

Write the note at `docs/solutions/<category>/<slug>-<YYYY-MM-DD>.md`, unless the
repository's instructions declare another location or format, and create the
category folder if it is missing. One file per learning.

Every note's frontmatter has `title`, `date`, `category`, `module`,
`problem_type`, `severity` and `tags`. `category` is the folder, and
`problem_type` is its singular form, such as `build_error` for `build-errors`.
Quote YAML array items that start with a reserved character.

**Bug track**, something diagnosed and fixed. Categories: `build-errors`,
`test-failures`, `runtime-errors`, `performance-issues`, `database-issues`,
`security-issues`, `ui-bugs`, `integration-issues`, `logic-errors`. Frontmatter
also has `symptoms`, `root_cause` and `resolution_type`. Sections, in order:
Problem, Symptoms, What Didn't Work, Solution, Why This Works, Prevention, Related
Issues.

**Knowledge track**, guidance and practice. Categories: `best-practices`,
`documentation-gaps`, `workflow-issues`, `developer-experience`,
`architecture-patterns`, `design-patterns`, `tooling-decisions`, `conventions`.
Frontmatter also has `applies_when`. Sections, in order: Problem, Solution, Why
This Matters, Prevention, Examples, Related.

Make the store discoverable: the repository's instruction file says, in one line,
that `docs/solutions/` exists, how it is organised, and when it is relevant.

## Handoff

Emit `compounding.recorded` with the decision's outcome, and with the reference
the handoff names where a required learning has one, through the run-event
command the repository's root instruction file declares, when it declares one.
Where the repository declares none, proceed silently, with no handoff and no
blocker. The agent following this workflow writes the note, and
`compounding.recorded` records the decision only; the emission is not the durable
state.

For a required learning, report where it went in the caller-owned delivery
handoff: the note's path, the guidance or sensor changed, or the follow-up item.
For a not-required decision, record that outcome in the same handoff. This
workflow does not choose a tracker or transport.
