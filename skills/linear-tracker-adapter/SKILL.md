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
