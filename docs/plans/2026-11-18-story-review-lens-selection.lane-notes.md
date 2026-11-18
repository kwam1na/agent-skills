# Lane notes: story review lens selection

Notes the plan's review left for the lane. Both lenses aligned in round 3, and the record is
`2026-11-18-story-review-lens-selection.review.json`.

- **Criterion c3 and in-flight stories.** A later round of a story prepared before this change briefs every declared
  lens, plan-only lenses included (Decision 4). That is the legacy path, not a default: c3's "never defaulted" holds
  for every story whose round 1 meta carries a `lensesReason`. Say so in your hand-off evidence for c3.
- **Restart discards.** Discard the results of the lenses the previous meta named, as Decision 4 says. Decision 4's
  closing list says "the story's set"; before a restart that is the previous meta's set.
- **A missing round 1 meta.** When `lead-accept` finds round 1's meta in neither the lane checkout nor the kept
  evidence, it falls back to every declared lens, which refuses a chosen-subset story. That fails closed and is
  accepted as is.
- **A legacy later round with `--lenses`.** The plan doesn't say what it does. Treat it as Decision 4 does for any later
  round: the same set as round 1's (every declared lens) is accepted, any other is refused.
- **Supervisor fixture.** Its extra lenses are story lenses. If you can do it in the same fixture cheaply, declare a
  plan-only lens too, so the legacy fallback in `lead-accept` is shown to use every declared lens, not every story lens.
- **Your own review.** This delivery runs the installed generation, which has no `--lenses`, so your own rounds run
  every declared lens. Its briefs now list the findings still open for each lens; list only those in
  `resolvedFindings`.
- **Another planned delivery.** V26-3589 is planned on this base and owns `run_delivery.py` and `SKILL.md` too. Keep
  your edits to the functions the plan names; the lead claims this delivery's files with `lead-own --claim`.
