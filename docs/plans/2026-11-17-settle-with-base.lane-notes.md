# Lane notes: settle a story with its base

Notes the plan's review left for the lanes. Both lenses aligned in round 3, and the record is
`2026-11-17-settle-with-base.review.json`.

- **Your own hand-offs run the installed generation.** It has no `base-merge` and still compares the base merge's
  delta the old way. If the base moves under your story, ask the lead for `lead-authorize-base-merge`, as the brief
  says, and use its printed commands.
- **Files across lanes.** Some stories change a file the other lane owns: V26-3576 and V26-3572 touch
  `orchestration-guide.md`, `delivery_state.py`, `journal-kinds.json` and `tests/test_orchestration_state.py`, and
  V26-3557 touches `run_delivery.py` and run-delivery's `SKILL.md`. The lead turns those files into line spans before
  assigning the story, and the assignment names your spans. Edit another lane's file only inside a span you were
  given.
- **Not yours: `catalog.json` and `profiles/*`.** The lead writes each story's sentence after it merges (Decision 10).
  Do run `scripts/update-provenance.py` for `provenance.lock.json`.
- **Decision 1's pairing.** When one `-U0` hunk holds several removed and added lines that differ only by a trailing
  `,`, pair them in order of appearance.
- **Two breaks for one setback.** Under `--self-repair`, the queue's own `stopped()` path may journal a second
  `base.broken`, with source `queue`, beside the lead's. Have the queue journal none when a lead's `base.broken` already
  names the checked head, and pin it in the self-repair row.
- **Node.** Put Node 22 first on `PATH` for the suite:
  `export PATH="$HOME/.nvm/versions/node/v22.23.3/bin:$PATH"`. Node 20 fails the launcher rows.
