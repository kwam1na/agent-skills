---
name: compound-learning
description: Decide whether delivery evidence supports a reusable learning and return a repository-owned handoff.
---

# Compound Learning

## Purpose

Make the delivery's compounding decision explicit without choosing a
repository's knowledge format or writing durable state.

## Decide

A reusable learning is `required` only when the caller provides:

- a concrete reusable reason, such as a repeatable failure, missing guardrail,
  non-obvious delivery path, or reusable workflow decision;
- the learning to preserve; and
- evidence from the delivery.

When no reusable reason exists, return `not-required` with the caller's reason
that the learning is local, obvious, or unlikely to recur. Missing or
conflicting inputs return `blocked` rather than inventing knowledge.

## Handoff

Emit `compounding.recorded` with the decision's outcome, and with the reference
the handoff names where a required learning has one, through the run-event
command the repository's root instruction file declares, when it declares one.
Where the repository declares none, proceed silently, with no handoff and no
blocker. The emission records the decision; it is not the durable state, and
this workflow still writes none.

For a required learning, hand the decision to the adopting repository's
existing guidance, sensor, skill, or follow-up workflow. For a not-required
decision, record that outcome in the caller-owned delivery handoff. This
workflow does not choose a document schema, storage path, tracker, or transport.
