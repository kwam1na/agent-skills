---
title: "Print a standing rule where the agent acts, and show its absence"
date: 2026-11-11
category: design-patterns
module: run-delivery
problem_type: design_pattern
severity: high
tags: [lead, wait, lease, supervisor, needs-you, live-view]
applies_when: "A workflow needs an agent to keep doing something, such as running a background listener, between commands"
---

# Print a standing rule where the agent acts, and show its absence

## Problem

The lead must keep a background `wait` running while a lane works or a
candidate is queued. That rule lived only in the orchestration guide's "The
lead's wake". Twice, a lead that followed run-delivery's commands never met it.
On 2026-11-07, and again on 2026-11-11 in an outside adopter's delivery, a
hand-off sat until the operator asked what the story was waiting on. Nothing
showed that nobody was listening.

## Solution

Two parts, neither of which depends on the agent having read the reference:

- **The command prints it.** Each lead command that can leave work in flight
  (`lead-init`, `lead-assign`, `lead-accept`, `lead-run --resume`) ends with the
  exact `lead-wait` to start, `--after` filled in.
- **The listener leaves a trace, and so does its absence.** `lead-wait` keeps a
  lease in the record: its pid and an `updatedAt` it refreshes every 60
  seconds. The supervisor's "Needs you", the live view and every lead command's
  status line read that lease through one function, `lead_listener`, so they
  never disagree. A lease counts as live only while its pid is alive and it was
  refreshed under 180 seconds ago.

Get the printed `--after` from the last wait's own `restart` (its default
output file), not from "the journal's last entry". Entries journalled between
that wait's wake and the lead's next command would otherwise be skipped, and
those are exactly the hand-offs that went unseen.

## Why This Matters

A rule kept only in a reference section is a rule the agent won't meet. The
supervisor already knew a lane waited on the lead, but only the live view said
so, and nobody looked. Without an exact command, a lead improvises a journal
watcher that misses most of what the real wait wakes on: stops, failed checks, a
broken base, blockers and machine pressure.

## Prevention

When a rule says "keep X running" or "don't stop until Y", put it in three
places:

- in the output of the command the agent just ran;
- in a trace the running thing keeps, with a liveness bound, so a killed process
  reads as absent;
- in a list a supervisor derives from that trace, shown where people already
  look.

Then enforce it in the host wherever the host offers a hook.

## Examples

`lead-assign` with a lane assigned and no listener ends with:

```text
The lead's wake: lane-1 (assigned), and no lead-wait is listening. Start this in the background before you end your turn, ...
  python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py --delivery FX-1 lead-wait --after 12 --output <flow-log>/FX-1.wait.json
```

The status line adds "the lead isn't listening: no lead-wait runs while lane-1
(assigned)", and the next supervisor pass lists "The lead isn't listening" in
"Needs you" and in the live view. Starting `lead-wait` clears all three.

## Related

- `skills/orchestrate-work/references/orchestration-guide.md`, "The lead's wake"
- `tests/test_lead_wait.py` (`LeaseTests`), `tests/test_run_delivery_supervisor.py` (`LeadListenerTests`)
- The Stop hook that holds a lead's stop while nobody listens builds on `lead_listener`.
