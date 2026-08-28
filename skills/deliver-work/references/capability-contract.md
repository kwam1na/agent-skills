# Neutral Capability Contract

## States

Every declared capability has one normalized state:

- `absent`: the capability is not present.
- `available`: the capability exists but is not configured for use.
- `configured`: the capability may execute neutral operations.
- `blocked`: the capability cannot proceed until a named blocker is resolved.

Optional absent, available, or blocked capabilities use a declared complete core
path. Required capabilities in those states return an actionable handoff.

## Tracker Operations

The tracker capability exposes only these neutral operations:

- `resolve-context`
- `create-work`
- `link-dependencies`
- `update-status`
- `attach-evidence`
- `close-or-handoff`

Mutating operations require a stable idempotency key. Repeating an accepted
mutation with the same operation and key must return the same successful result
without applying the mutation again.

## Outcomes

Each invocation returns its requested operation and one typed outcome:

- `success`: includes normalized result data.
- `unavailable`: says how to continue, configure, or resolve a blocker.
- `unsupported`: identifies that the operation cannot be performed.
- `malformed`: identifies the contract field that must be corrected.
- `auth-required`: says how to restore access before retrying.
- `retry`: includes a positive retry delay.
- `reconciliation-required`: includes a stable key for checking the prior
  attempt before another mutation.

Every non-success outcome includes an actionable next step. An adapter result
with an unknown shape, mismatched operation, missing action, invalid retry delay,
or missing reconciliation key normalizes to `malformed`.
