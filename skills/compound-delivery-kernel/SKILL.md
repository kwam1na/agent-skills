---
name: compound-delivery-kernel
description: Use when agent work needs one portable delivery posture across planning, implementation, review, and skill updates.
---

# Compound Delivery Kernel

## Purpose

Apply one small delivery loop across repositories while leaving repository
authority in the repository. The loop is:

Plan -> Work -> Review -> Compound.

## Plan

- Confirm the outcome, constraints, acceptance criteria, and non-goals.
- Read repository instructions and discover the smallest honest sensors.
- Choose `test-first`, `characterization-first`, or `sensor-only` posture.
- Capture the named scenarios before implementation.

## Work

- Isolate non-trivial work and preserve unrelated changes.
- Prove the selected posture before changing behavior.
- Implement the smallest slice that satisfies the named scenario.
- Run the narrowest relevant sensor after each meaningful slice.
- Classify failures as repairable drift, semantic blockers, or unrelated
  environmental blockers.

## Review

- Review against acceptance criteria, repository authority, and sensor evidence.
- Use specialized review only when the change's risk warrants it.
- Treat concrete findings as work, then rerun the proving sensor.
- Do not expand scope for speculative hardening or unrelated cleanup.

## Compound

Choose the smallest durable response to a reusable learning: update existing
guidance, add a focused sensor, record a follow-up, or explicitly record that no
durable learning emerged. Do not create documentation for obvious one-off work.

## Handoff

Report the changed outcome, sensors run, review result, residual risk,
compounding decision, and any blocked validation with its exact cause.
