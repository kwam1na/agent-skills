# Lane notes: checks a story adds

Notes the plan's review left for the lane. Both lenses aligned in round 2, and the record is
`2026-11-17-checks-a-story-adds.review.json`.

- **Where the new reader goes.** Put Decision 6a's reader for `tests/test_*.py` before the hosted service reader in
  `selection.readers`, not after it. `test_integration_recipe_carries_only_named_checks_and_refuses_collisions` in
  `tests/test_own_deliveries.py` pops the last reader and expects it to be the hosted one.
- **Module names.** `CATALOGUE_MODULES` holds names without the `test_` prefix, and the glob gives `test_*` stems.
  Map one to the other in the guard.
- **Added entries with prerequisites.** `select` refuses an added entry whose prerequisites name an id the selection
  lacks. One row in the supervisor tests shows that refusal passes through `lead-accept` as a problem.
- **Your own hand-off.** This delivery runs on the installed generation, which still refuses an added id. Name only
  base catalogue ids in your own `handoff --checks`. `tests.test_suite_budget` is catalogued by your diff, so leave it
  out.
- **Another delivery on this base.** V26-3553 is live and edits `run_delivery.py`, `tests/test_run_delivery.py`,
  `.agents/orchestration.json` and `tests/test_own_deliveries.py` in regions of its own. Keep your edits to the
  functions the plan names. Expect the queue's sync to merge over its landings.
