---
title: Upgrade the lead before a lane writes a shared record with another generation
date: 2026-11-13
category: integration-issues
module: delivery-state
problem_type: integration_issue
severity: high
tags: [delivery-state, generations, schema, recovery]
symptoms: "After a lane applies a release, the older lead rejects the shared record with state_invalid: ... no value is allowed here, its wait fails, and its merge queue exits"
root_cause: "The lane wrote fields from a newer generation before the lead and queue moved to that generation"
resolution_type: code_fix
---

# Upgrade the lead before a lane writes a shared record with another generation

## Problem

A lane applies a release during an active delivery. The lane changes the shared record's shape before the lead
and queue move to the same generation.

## Symptoms

An active delivery failed with `state_invalid: ... no value is allowed here` immediately after a lane applied
a release and ran its lane commands. The older lead reader rejected newer `waitingBy` and `trackerStarted`
fields. Its wait failed and its merge queue exited.

## What Didn't Work

Running the newer lane commands while the lead still used the older generation introduced fields its reader
could not load. The lead's wait and queue could not continue with that older reader.

## Solution

The recovery applied the same verified release in the lead checkout and resumed the supervisor from it.
Preserve the original record and journal when recovering this failure.

Apply a verified compatible release in the lead checkout and run its installed `lead-upgrade-writer` with the
previous archive digest, or `unpinned` for a legacy record, and a reason. The command parks the supervisor,
waits for the queue slot, checks the new reader can load the record and journal, pins its own archive, and
resumes from that generation. An integration-base move that applies a generation performs this transition
before reconciling the record. Lanes resume record writes after the lead transition.

## Why This Works

The writer guard now pins the applied archive SHA-256 in `meta.writerGeneration`, validates admission under
the state transaction lock, and refuses a different archive before writing record or journal bytes. Runtime
version labels cannot substitute for archive digests. Legacy records remain readable and require an explicit
lead transition to pin their first installed writer.

Admitted operations hold the shared writer lock through their state and journal evidence. The lead transition
takes that lock exclusively, so it waits for an admitted hand-off to finish before changing the writer pin.

## Prevention

Generations predating the guard must be parked before applying a release, because their writers cannot enforce
the pin. Fixture coverage installs two archives, proves the rejected write preserves record and journal bytes,
and repeats the same write successfully after the lead transition.

## Related Issues

The [field-writer ownership guidance](../design-patterns/tool-written-fields-record-their-writer-2026-11-03.md)
covers ownership of individual fields when tools write on a person's behalf.
