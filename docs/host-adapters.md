# Host projections

The lifecycle exposes one verified installed generation through two fixed
repository discovery roots:

| Host | Discovery root | Projection mode |
| --- | --- | --- |
| Codex | `.agents/skills` | Relative directory symlink |
| Claude Code | `.claude/skills` | Relative directory symlink |

Each selected skill name links directly to the matching directory under
`.agent-skills/generations/<generation>/skills`. The workflow body and its
resources therefore remain in one immutable generation. Host projection
metadata in the active receipt describes discovery only and cannot redefine
workflow behavior.

Install and update preflight every selected name in both roots. An unrelated
entry with the same name, a broken link, or a link that does not match the
active or target receipt produces the `exposure-conflict` blocker before
replacement. Status reports each host projection as `current` or `conflict`
and provides `review-host-projections` when maintainer review is required.

Rollback repoints both roots to the retained verified generation before the
active marker changes. Removal deletes only links proven by the current receipt
and leaves unrelated host entries untouched. Interrupted projection changes
are restored or completed through the existing repository lifecycle journal.
