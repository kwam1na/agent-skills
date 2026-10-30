---
name: jira-tracker-adapter
description: Use when optional tracking should map the neutral tracker contract to host-native Jira operations through the host's Atlassian MCP connector.
---

# Jira Tracker Adapter

## Purpose

Connect the neutral tracker capability to Jira without changing the complete
core delivery path. Apply `$deliver-work` for workflow routing and follow the
exact mapping, output, failure, and mutation rules in the
[adapter contract](references/adapter-contract.json).

Use only the host's native Atlassian MCP capability for Jira. The adapter
contract's `transportBoundary` is exhaustive; no other provider access path is
allowed.

## Resolve Tracker Properties

The adopting repository declares its tracker properties in one fixed
repository-relative document at `.agents/tracker-properties.json`. That
document is the seam a different tracking skill would replace, so read it as
declared data and never write to it. Apart from `schemaVersion`, which
identifies the document format, it declares only properties an operation
consumes:

- `project`: the Jira project key every item belongs to.
- `team`: the owning team, which `resolve-context` validates when the project
  assigns items to teams and otherwise reports as unused.
- `statuses`: the status names `update-status` and `close-or-handoff` map onto.
- `jira.site`: the Atlassian site every operation targets, by its cloud
  identifier or its site address.
- `jira.childIssueType`: the issue type for an item recorded under an existing
  item that is not an epic.
- `jira.standaloneIssueType`: the issue type for an item recorded under an epic
  or with no parent.
- `jira.dependencyLinkType`: the issue link type that records a true blocker.
- `jira.summaryPrefix`, `jira.components` and `jira.labels`, when present: the
  prefix every item summary carries, and the components and labels every item
  is created with.
- `jira.canceledResolutions`, when present: the resolutions that mean an item
  was abandoned rather than finished.

Anything else an item carries, further labels among them, is decided per item
rather than declared once for the repository.

Resolve context in this order and stop at the first source that resolves:

1. The properties document, when it is present and names the needed property.
2. An explicit work reference in the prompt, resolved with `resolve-context`.
3. Explicit project or team names in the prompt, validated with
   `resolve-context` before use.
4. The invoking working directory: its basename, the repository root basename
   when it differs, and obvious workspace metadata names. Validate every
   candidate with `resolve-context` before use, and report that the context was
   directory-derived.

Stop and ask the caller for context when zero plausible candidates remain, when
more than one remains, when a resolved item carries no project, when the site
is not declared and the connector reaches more than one, or when explicit names
conflict with the resolved item. When a named or derived property does not
exist in the tracker, such as an issue type the project does not offer or a
link type the site does not define, surface the mismatch instead of
substituting another.

## Operation Mapping

Map the neutral operations directly:

- `resolve-context` uses search, then read when a reference needs resolution.
  Search uses a JQL query scoped to the declared project.
- `create-work` uses create. The item gets `jira.childIssueType` under a parent
  that is not an epic and `jira.standaloneIssueType` otherwise, with the
  declared summary prefix, components and labels. A new item keeps the status
  Jira gives it.
- `link-dependencies` uses relations: one `jira.dependencyLinkType` link per
  true blocker. The direction of a link is easy to invert, so read every link
  back and confirm the blocked item shows the blocker on the link type's
  inward side before reporting success.
- `update-status` uses update: the transition whose target is the declared
  status name. When no available transition reaches that status, surface the
  mismatch instead of stepping through other statuses.
- `attach-evidence` uses create: a comment on the item.
- `close-or-handoff` uses update, with the same transition rule.
- `list-work` uses search for the parent's child items, then read for each
  item's status category, resolution and blocking links. It is read-only.
  Each item's status category folds into the normalized four: `new` becomes
  `backlog`, `indeterminate` becomes `started`, and `done` becomes `completed`,
  or `canceled` when the item's resolution is one `jira.canceledResolutions`
  declares. A category outside these three makes the result `malformed`,
  never a guess. Each item names each of its blockers once, never itself and
  never the parent, even when the tracker lists a link from both of its ends.
  The parent is never one of the items, and a dependency cycle among them makes
  the result `malformed`.
- Reconciliation after an uncertain mutation uses reconciliation only: a
  search for the exact item, comment or link the mutation intended.

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
make a repeated mutation safe. Jira offers none, and this adapter never invents
one from a local cache, search result, comment marker, issue property, or
idempotency key.

## Keep the Boundary Optional

When the host capability is absent, available but unconfigured, or blocked,
return the neutral actionable outcome. If tracking is optional, continue the
declared complete core path and report that no tracking mutation was performed.
