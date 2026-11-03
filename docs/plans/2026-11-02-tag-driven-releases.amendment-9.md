# Amendment 9: plan-work and compound-learning say where their files go

The operator ruled to close a gap found during this delivery: `plan-work` never says where a plan is saved, and `compound-learning` declines to say where a solution note goes. This adds a unit and overrides
nothing.

- **The unit.** `plan-work`'s hand-off says to save the plan in the planning folder the repository's instructions declare, otherwise at
  `docs/plans/<key>.md`, and to report the path, with a corpus pin.
- **Solution notes.** `compound-learning`'s hand-off says that a required learning with no existing home is saved by the caller as a
  solution note in the folder the repository's instructions declare, otherwise at `docs/solutions/<slug>.md`, with the path reported. The
  workflow itself still writes nothing. This replaces its line that it does not choose a storage path, and leaves schema, tracker and
  transport to the repository.
- **Order.** It touches only these two skills and their pins, so it runs beside the gate unit and lands before the integration change merges.
