---
name: linear-tracker-adapter
description: Use when optional tracking should map the neutral tracker contract to host-native Linear MCP operations.
---

# Linear Tracker Adapter

## Purpose

Connect the neutral tracker capability to Linear without changing the complete
core delivery path. Apply `$deliver-work` for workflow routing and follow the
exact mapping, output, failure, and mutation rules in the
[adapter contract](references/adapter-contract.json).

Use only the host's native Linear MCP capability. The adapter contract's
`transportBoundary` is exhaustive; no other provider access path is allowed.

## Resolve Tracker Properties

The adopting repository declares its tracker properties in one fixed
repository-relative document at `.agents/tracker-properties.json`: the team,
the project, the labels, the status names this adapter maps `update-status` and
`close-or-handoff` onto, and the identity of the item template to apply. That
document is the seam a different tracking skill would replace, so read it as
declared data and never write to it.

Resolve context in this order and stop at the first source that resolves:

1. The properties document, when it is present and names the needed property.
2. An explicit work reference in the prompt, resolved with `resolve-context`.
3. Explicit team or project names in the prompt, validated with
   `resolve-context` before use.
4. The invoking working directory: its basename, the repository root basename
   when it differs, and obvious workspace metadata names. Validate every
   candidate with `resolve-context` before use, and report that the context was
   directory-derived.

Stop and ask the caller for context when zero plausible candidates remain, when
more than one remains, when a resolved item carries no project, or when explicit
names conflict with the resolved item. When a named or derived property does not
exist in the tracker, surface the mismatch instead of substituting another.

## Operation Mapping

Map the neutral operations directly:

- `resolve-context` uses search, then read when a reference needs resolution.
- `create-work` uses create.
- `link-dependencies` uses relations.
- `update-status` uses update.
- `attach-evidence` uses create.
- `close-or-handoff` uses update.
- Reconciliation after an uncertain mutation uses reconciliation only.

Do not substitute another provider operation or access path. Retain the resolved
work reference for later neutral operations in the same workflow.

## Normalize Outcomes

Return only the operation-specific fields allowlisted by the adapter contract.
References are opaque, redacted audit values; they are not raw host responses.
Never forward a raw host payload, raw host action, secret, or raw idempotency
key into neutral result data, action text, evidence, comments, logs, or audit
references. Use adapter-owned actionable wording for every non-success outcome.

Normalize missing capability to `unavailable`, authorization failure to
`auth-required`, invalid request or response shape to `malformed`, and an
unsupported host operation to `unsupported`. A timed-out or ambiguous read may
return `retry` with a positive delay because no mutation occurred.

## Prevent Silent Duplicates

Apply an initial mutation at most once per invocation. A definitive result may
be normalized immediately. After a timeout or ambiguous mutation result, do
not repeat the mutation. Use reconciliation to look for one exact result that
matches the intended work, target, and operation.

Exact reconciliation may confirm success. A reconciliation miss, unavailable
reconciliation, malformed reconciliation, timeout, or ambiguous match returns
`reconciliation-required` with an opaque audit reference and stops. It does not
authorize another mutation.

Only an explicit authoritative idempotency fence supplied by the host could
make a repeated mutation safe. This adapter declares no such fence and never
invents one from a local cache, search result, comment marker, or idempotency
key.

## Keep the Boundary Optional

When the host capability is absent, available but unconfigured, or blocked,
return the neutral actionable outcome. If tracking is optional, continue the
declared complete core path and report that no tracking mutation was performed.
