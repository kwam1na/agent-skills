# Amendment 9: plan-work and compound-learning say where their files go

The operator ruled to close a gap found during this delivery: `plan-work` never says where a plan is saved, and `compound-learning` declines to say where a solution note goes. This adds a unit and overrides
nothing.

- **The unit.** `plan-work`'s hand-off says to save the plan in the planning folder the repository's instructions declare, otherwise at
  `docs/plans/<key>.md`, and to report the path, with a corpus pin.
- **Solution notes.** `compound-learning` carries the core of the delivery it was drawn from: its folder structure and instructions, not its
  machinery. Notes go to `docs/solutions/<category>/<slug>-<YYYY-MM-DD>.md` unless the repository's instructions say otherwise, on a bug or a
  knowledge track with its categories, frontmatter and sections. The agent searches first, updates a note on the same problem rather than
  duplicating it, sends cross-repository learnings to a skill and missing sensors to a follow-up, and makes the store discoverable from the
  instruction file. Its gate, fingerprint, validator, subagents and refresh skill stay out. This replaces the skill's line that it chooses no
  schema or storage path, and the agent following it now writes the note. The operator's ruling is the permission to bring this in.
- **Order.** It touches only these two skills and their pins, so it runs beside the gate unit and lands before the integration change merges.
