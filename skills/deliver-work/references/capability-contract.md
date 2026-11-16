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
- `list-work`

`list-work` is read-only. Given a `parentReference`, it returns that parent and
its `workItems`: each with its `workReference`, `title`, `stateType` (one of
`backlog`, `started`, `completed` or `canceled`) and `blockedBy`, the references
of the items that block it. The blockers must form a dependency list: an item
is never the parent, never blocked by the parent, never its own blocker, names
each blocker once, and never waits on itself through other items. A blocker
outside the listed items is allowed. A result of any other shape normalizes to
`malformed`, naming the items involved: at most ten problems are named before
the rest are counted, and a long cycle is named by its first items and the one
it returns to. Its success can be written out as a `work-snapshot/1` tracker
snapshot.

Mutating operations require a stable idempotency key. Repeating an accepted
mutation with the same operation and key must return the same successful result
without applying the mutation again.

## Knowledge Library Operations

The knowledge-library capability exposes only these neutral operations, both
read-only:

- `search-knowledge`
- `read-knowledge`

`search-knowledge` takes a `query`, which is non-empty text, and an optional
`scope`, one name the adapter maps to its own filters. Its success returns
`hits`, best first, at most the adapter's declared `maxHits`, which is between 1
and 20. Each hit has exactly `itemRef`, `title` and `date`, and may have `kind`
and `scope`, each free text. Zero hits is a success with an empty `hits` list.

`read-knowledge` takes one `itemRef` from a hit. Its success returns that hit's
fields plus `body`.

Neither operation mutates anything, so neither needs an idempotency key. Both
use the outcomes below. A `retry` carries the adapter's declared positive delay.
A hit or item with a missing or extra field, more hits than `maxHits`, or a
`date` that is not a calendar date normalizes to `malformed`.

The capability is optional in every repository, and no repository authority
makes it required. When it is absent, available but not configured, or blocked,
its absence takes the complete core path, and the workflow reports that the
library was not consulted.

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
