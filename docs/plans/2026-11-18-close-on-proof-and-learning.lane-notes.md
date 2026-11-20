# Lane notes: close a delivery only on what it proved and what it taught

Notes for the lanes from the plan's review. Both lenses aligned in round 3; the record is
`2026-11-18-close-on-proof-and-learning.review.json`.

- **Your base is `integrate/close-on-proof`, not `main`.** Open each story's draft PR into it. The lead takes the
  integration base into `main` at close-out.
- **Spans in shared files.** At tier 1 both lanes change `run_delivery.py`, run-delivery's `SKILL.md`,
  `orchestration-guide.md`, `journal-kinds.json`, `tests/fixtures/journal-kinds/vectors.json` and `delivery_state.py`.
  The lead grants each lane line spans there, anchored as the plan's "Units and order" says, and the assignment names
  them. Edit another lane's file only inside a span you were given. Add a new function or command inside one of your
  spans. If you need more room, ask the lead for a wider span before you hand off. Don't hand off and wait for a
  refusal.
- **Existing tests.** No tier-1 lane owns `tests/test_run_delivery_supervisor.py` or `tests/test_run_delivery.py`. If
  your change breaks an assertion there, ask the lead for a span over it and fix it in the same change. Never leave an
  assertion red or delete it.
- **V26-3477 has no code change** (Decision 4). Lane 1 runs it right after V26-3614 hands off. Add any criterion's
  missing test to `tests/test_run_delivery_finish.py` as part of V26-3614. Then hand V26-3477 off with
  `handoff V26-3477 --no-change --evidence @FILE`, naming each criterion `v26-3477-1` to `v26-3477-4` and the test that
  shows it, at a base head that holds V26-3614. If a criterion fails on the base, hand off blocked on it.
- **Releasable skill changes.** Refresh `catalog.json`, `profiles/*` and `provenance.lock.json` with
  `scripts/update-provenance.py` in the same change. Lane 2 adds `skills/run-delivery/scripts/gaps.py` to the lock's
  listing. Lane 1 adds no output.
- **The suite budget.** The new test modules reuse the supervisor fixtures through one `SharedCorpus` per module, and
  add no class that runs alone. No number in `tests/suite-budget.json` moves.
- **Node.** Put Node 22 first on `PATH` for the suite:
  `export PATH="$HOME/.nvm/versions/node/v22.23.3/bin:$PATH"`.
