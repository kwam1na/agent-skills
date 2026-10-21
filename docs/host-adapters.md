# Host projections

The lifecycle exposes one verified installed generation through two fixed
repository discovery roots:

| Host | Discovery root | Projection mode |
| --- | --- | --- |
| Codex | `.agents/skills` | Relative directory symlink |
| Claude Code | `.claude/skills` | Relative directory symlink |

Each selected skill name has a stable, canonical POSIX link to
`.agent-skills/current/skills/<skill>`. The shared `current` link selects one
verified directory under `.agent-skills/generations/`. Updates and rollbacks
prepare both fixed host roots, finish final generation verification and
projection preflight, then atomically replace that one shared link through a
held repository directory. Host readers cannot resolve different generations,
and replacing the managed parent cannot redirect the switch outside the
repository. The workflow body and its resources remain in one immutable
generation. Host projection metadata in the active receipt describes discovery
only and cannot redefine behavior.

The core profile exposes `diagnose-work` and `obtain-review` through both roots
alongside the existing workflows; the Linear profile contains that same core
plus its tracker adapter and the two tracking workflows that reach the tracker
through it. `deliver-work` remains the single intake entry point. Its additive
`RouteDecision.workflow_skill` property names the released downstream workflow
only for direct or degraded routes; an unresolved handoff has no executable
target. Repository workflow selection still overrides a host hint, which
overrides request examples.

Hosts realize the canonical stages declared in `workflows/delivery-v1.json` and
return closed `workflow-stage-result/1` envelopes. Diagnosis supplies causal
evidence without fix authority. Review acquisition gathers caller-selected lenses
for unchanged `review-work`; separate labels do not prove independent execution.
The harness owns authenticated context, attempts, permission checks, durable
state, publication and feedback capability bindings, and finish-line authority.
Neither a graph edge nor a normalized result grants that authority.

Install and update preflight every selected name in both roots. An unrelated
entry with the same name, a broken link, or a link that does not match the
active or target receipt produces the `exposure-conflict` blocker before
replacement. Status reports each host projection as `current` or `conflict`
and provides `review-host-projections` when maintainer review is required.

Rollback switches the shared link to the retained verified generation only
after its final verification and before the active marker changes. After final
verification and preflight, removal unlinks the shared generation through the
held repository directory so both hosts become unavailable together. It then
deletes only the now-inert stable links proven by the current receipt and leaves
unrelated host entries untouched. Interrupted projection changes are restored
or completed through the existing repository lifecycle journal.

On Windows, directory links use native runtime separators and explicit
directory-link semantics. Because the standard library cannot atomically
replace an existing directory link there, the journaled switch removes the
shared `current` link before installing its prepared replacement. Both hosts
may be unavailable briefly, but they cannot expose different generations; an
interruption remains a typed, recoverable transition. Git continues to track
the canonical POSIX link target, while runtime comparisons normalize the value
returned by the operating system.

Receipts created before host exposure metadata existed remain readable and
represent an installed but unexposed generation. A later verified update or
rollback writes the fixed exposure metadata and projections; no separate
migration mechanism is required.

## Outside-adopter discovery

Follow the [workflow-only core install](../README.md#install-core-skills-without-a-harness)
for a skills-only pilot. An absent `.agents/` root is created during installation;
`AGENTS.md` is neither required nor generated. Agents still read existing
repository instructions, including `CLAUDE.md`; automatic loading of that file
is host-specific. Start a fresh session to discover installed skills.

Git ignore rules do not affect filesystem discovery. An ignored `.claude/`
therefore supports local Claude links through the ordinary lifecycle, while
those links remain excluded from normal Git staging. The README gives the
parent-directory negation needed to track only `.claude/skills/`; a child-only
negation cannot reopen an ignored parent. Removal preserves pre-existing host
directories and unrelated files, including local Claude settings.

## Single-workflow first adoption

The maintainer-only adoption command is available only while the managed
lifecycle is absent. It verifies the complete supplied release, then exposes
one named workflow already present in Codex. The expected prior SHA-256 must
match the regular `SKILL.md` in the existing Codex directory. A same-name
Claude Code directory is accepted only when it contains the same exact body;
otherwise Claude Code must be absent. Other release workflows are retained in
the immutable generation but receive no discovery links.

The adoption receipt records the one selected name, prior digest, and hosts
that owned the predecessor bytes. A separately marked predecessor store keeps
those bytes under lifecycle ownership. The predecessor is prepared in
operation-owned private staging and moved into place atomically; recovery can
safely clear a known partial stage without treating it as an owned predecessor.
Status verifies both the complete generation and predecessor before reporting
current. Rollback and removal restore prior host bodies and remove projections
created for previously absent hosts. If cleanup has already removed predecessor
storage, recovery completes only after verifying every host already matches the
receipt's recorded restored state. Verified predecessor storage is moved to
operation-owned private trash before deletion, so recovery can finish a partial
delete without reclassifying malformed managed storage as owned. The selected
catalog member must declare no required or routing dependencies. Any changed
projection, generation, receipt, predecessor, private stage, or private trash
blocks mutation for maintainer review.

## Consumer overlays and release installs

An adopting repository's overlay names what that repository adds: its gate, its
sensors, its worktree and branch rules, how it realizes a reviewer, and its
protected paths. For every rule the installed workflow already carries, the
overlay references the installed workflow rather than restating it; it does not
restate the review round bound, the grace round, the deferral rules, or the
rules for resolving a finding.

A release install in a consumer is delivered `sensor-only`, with `active.json`
as the declared review scope. Its sensors are the lifecycle status sensor —
`lifecycle: current`, empty `blockers`, and the new generation active — together
with any policy recompilation that consumer performs. Everything under the
installed generation's directory is generated release payload, outside review
scope by declaration.

## Observed delivery progress

Codex and Claude Code use the same
[run observation contract](../skills/execute-work/references/run-observation-contract.md).
A host supplies actual activity and attempt identities, native structured
results, known waits and available output at the boundaries it observes. A
missing permission signal remains unknown; a permission observation grants no
permission. Separate host names alone do not prove independent execution.

The workflow checks runtime capabilities and the selected run's writer version
before reporting. It captures selected reviewer, reducer, clarification and
available partial output before scratch cleanup, preserving actual round bounds,
history and reported cost. Unsupported reporting or capture failure stays
explicit without changing the work's underlying result. The shared contract
owns these details; discovery projections introduce no additional workflow.
