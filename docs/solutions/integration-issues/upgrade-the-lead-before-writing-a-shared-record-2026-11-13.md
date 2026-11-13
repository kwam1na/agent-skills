---
title: Upgrade the lead before a lane writes a shared record with another generation
date: 2026-11-13
category: integration-issues
module: delivery-state
problem_type: integration_issue
severity: high
tags: [delivery-state, generations, schema, recovery]
applies_when: A lane applies a release during an active delivery
---

# Upgrade the lead before a lane writes a shared record with another generation

An active delivery failed with `state_invalid: ... no value is allowed here` immediately after a lane applied
a release and ran its lane commands. The older lead reader rejected newer `waitingBy` and `trackerStarted`
fields. Its wait failed and its merge queue exited.

The lane had changed the shared record's shape before the lead and queue moved to the same generation.
The recovery applied the same verified release in the lead checkout and resumed the supervisor from it.
Preserve the original record and journal when recovering this failure.

The writer guard now pins the applied archive SHA-256 in `meta.writerGeneration`, validates admission under
the state transaction lock, and refuses a different archive before writing record or journal bytes. Runtime
version labels cannot substitute for archive digests. Legacy records remain readable and require an explicit
lead transition to pin their first installed writer.

Apply a verified compatible release in the lead checkout and run its installed `lead-upgrade-writer` with the
previous archive digest, or `unpinned` for a legacy record, and a reason. The command parks the supervisor,
waits for the queue slot, checks the new reader can load the record and journal, pins its own archive, and
resumes from that generation. An integration-base move that applies a generation performs this transition
before reconciling the record. Lanes resume record writes after the lead transition.

Generations predating the guard must be parked before applying a release, because their writers cannot enforce
the pin. Fixture coverage installs two archives, proves the rejected write preserves record and journal bytes,
and repeats the same write successfully after the lead transition.
