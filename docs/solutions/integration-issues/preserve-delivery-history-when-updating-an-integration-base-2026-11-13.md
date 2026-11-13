---
title: Preserve delivery history when updating an integration base
date: 2026-11-13
category: integration-issues
module: run-delivery
problem_type: integration_issue
severity: high
tags: [integration-base, git, provenance, generations, recovery]
symptoms: "A long-lived delivery required repeated manual base moves, and rebasing rewrote commit identities retained by its record"
root_cause: "The base update rewrote history and lacked one coordinated publication and record-reconciliation operation"
resolution_type: code_fix
---

# Preserve delivery history when updating an integration base

## Problem

A long-lived delivery base outlived releases and default-branch changes. Moving it by hand required repeated
provenance repairs and conflict resolution. Its record already named merged commits and review candidates.

## Symptoms

A rebase rewrote those recorded SHAs, leaving the original history on a local backup branch. A later merge
needed substantive conflict resolution and integration review. A shared flake also received separate fixes
on the delivery and default branches.

## What Didn't Work

Rebasing the delivery history changed the identities its record retained. Repeating provenance repairs by
hand added work at each replayed commit. Separate flake fixes left the two branches carrying overlapping work.

## Solution

Use the installed `lead-sync --merge` command from the lead checkout with the verified archive and metadata
matching the default branch's applied receipt. It parks the supervisor and holds the queue lock through the
merge, lifecycle application, provenance refresh, normal publication and record reconciliation.

Resolve named substantive conflicts and stage them while leaving the merge open. Run `lead-sync --merge --resume`
to continue. Keep the pending intent through a publication interruption. Retry recognizes a previously published
head and reconciles its applied writer and base head once, preserving the existing merged commit identities.

## Why This Works

The merge keeps the original base as a parent, so recorded SHAs remain reachable. The verified archive digest
identifies the applied writer. A durable intent prevents supervisor processing while publication and the record
disagree. The record transition updates the writer pin and base head together and recovers missing journal evidence
without duplicating the move or creating another commit.

## Prevention

Start newly assigned stories from the moved remote base. Existing branches use the lead's base-merge authorizations
and review rules. Review and gate the updated integration candidate. Record a shared flake fix as pending upstream
on the delivery, land it on the default branch too, and record its landing. Keep temporary flake declarations
scoped to the exact base head and check.

## Related Issues

The [writer-generation recovery note](upgrade-the-lead-before-writing-a-shared-record-2026-11-13.md) explains
why the lead and queue move to an applied generation before lanes write with it.
