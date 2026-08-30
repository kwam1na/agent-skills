---
name: deliver-work
description: Use when an agent should clarify, plan, diagnose, implement, review, or compound software work through one portable entry point.
---

# Deliver Work

## Purpose

Route an ordinary software request into the smallest complete delivery path.
Always apply `$compound-delivery-kernel`; it owns the shared delivery posture.
This skill owns only request routing and capability decisions.

## Discover Authority

Before routing, read the repository's instructions and the smallest relevant
validation guidance. Repository authority overrides examples and host metadata.
Treat repository commands, branch policy, identifiers, deployment steps, and
domain rules as discovered inputs. Do not embed them here.

When repository instructions name a normalized workflow, that value overrides
any host workflow hint and request example. Without a repository workflow, a
declared host hint overrides request examples. An unknown repository workflow
or host hint returns `clarify` with an actionable handoff rather than guessing.

## Normalize the Request

Choose exactly one workflow:

- `clarify` when the outcome or boundary is unresolved.
- `implement` for bounded build, fix, modification, refactor, or shipping work.
- `diagnose` when the cause is unknown and implementation is not yet authorized.
- `plan` when the requested outcome is a plan rather than a code change.
- `review` when the request is read-only evaluation.
- `compound` when the request is to preserve a reusable delivery learning.

Route `plan` through `$plan-work`. Route an approved `implement` request through
`$execute-work`, `review` through `$review-work`, and `compound` through
`$compound-learning`. Route `diagnose` through `$diagnose-work` to retain causal
evidence without authorizing a fix. When caller-selected review lenses need
realization, `$obtain-review` acquires the exact round for unchanged `$review-work`.

Specific user intent wins over keyword examples. The same normalized request
must select the same workflow and the `deliver-work` entry point.

## Resolve Capabilities

Use [the capability contract](references/capability-contract.md) for optional or
required external operations.

- A configured capability may be invoked through its neutral operation.
- An absent, available-but-unconfigured, or blocked optional capability uses a
  declared complete core path and reports what was not performed.
- An unavailable required capability returns a named, actionable handoff.
- Never silently treat a required capability as successful.

Tracking is optional unless repository authority or the user requires it. When
optional tracking is unavailable, complete the core delivery and report that no
tracking mutation was performed.

## Continue Through the Kernel

After routing, follow the kernel's Plan -> Work -> Review -> Compound loop. Keep
the current slice bounded, run repository sensors, preserve unrelated work, and
hand off observable evidence.
