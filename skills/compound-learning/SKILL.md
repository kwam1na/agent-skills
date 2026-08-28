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

For a required learning, hand the decision to the adopting repository's
existing guidance, sensor, skill, or follow-up workflow. For a not-required
decision, record that outcome in the caller-owned delivery handoff. This
workflow does not choose a document schema, storage path, tracker, or transport.
