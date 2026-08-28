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

Receipts created before host exposure metadata existed remain readable and
represent an installed but unexposed generation. A later verified update or
rollback writes the fixed exposure metadata and projections; no separate
migration mechanism is required.
