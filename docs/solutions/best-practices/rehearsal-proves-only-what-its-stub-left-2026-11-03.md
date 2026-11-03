---
title: "A rehearsal proves only what its stub left in"
date: 2026-11-03
category: best-practices
module: run-delivery
problem_type: best_practice
severity: medium
tags: [rehearsal, stubs, adoption]
applies_when: "A change is proved by running a real flow with some of its steps stubbed or removed"
---

# A rehearsal proves only what its stub left in

## Problem

Adopting `run-delivery` here was rehearsed end to end in a scratch clone, reaching a
merged story. The forge snapshot in that real configuration matched nothing `open-pr`
can title, and the rehearsal could not show it, because its stub had removed both
snapshot commands. The first write-up of the stub named the steps it replaced and
left the removal out, so the claim read wider than the proof.

## Solution

Treat the stub's whole diff as part of the claim. Say what was replaced and what was
removed, and list what has never run for real. Then run the removed parts on their
own, even read-only.

## Why This Matters

A rehearsal that reaches the end feels like proof of everything on the way, and a
reviewer reads the summary rather than the stub.

## Prevention

Give reviewers the stub itself. A reviewer running the real configuration in its own
clone found the title mismatch here.

## Examples

Here the forge adapter, the `candidate`, `ready` and `merge` steps, `prepare`, and the
regenerators were stubbed or emptied. Run read-only afterwards, `.agents/delivery/candidate.py`
and the forge snapshot resolved real change requests. `ready`, `merge` and the tracker
snapshot have still never run for real in this repository.

## Related

`docs/own-deliveries.md` describes how this repository runs its own deliveries.
