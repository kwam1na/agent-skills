# Lane notes: keep lane ownership in step with the work

Notes the plan and its review left for the lanes. Both plan lenses aligned in round 4, and the record is
`2026-11-18-ownership-follows-work.review.json`.

- **The base is `integrate/ownership-follows-work`.** Start, hand off and open pull requests against it, never
  `main`. The lead merges it into `main` at close-out.
- **Your own delivery runs the installed generation.** It has no `claims-check`, no carve, no anchors and no
  release on landing, so you still learn about a missing claim at review preparation. Before your first round, run
  `git diff --name-only $(git merge-base HEAD origin/integrate/ownership-follows-work) HEAD` and ask the lead in one
  message for every file and span you need, by function name and the first line of text, not by bare numbers.
- **The test modules exist already.** `tests/test_region_claims.py`, `tests/test_region_grants.py`,
  `tests/test_ownership_lock_race.py` and `tests/test_lane_changes.py` are stubs with an empty test class, entered in
  the catalogue by the lead (Decision 19). Replace the class with your own. Never edit `tests/test_own_deliveries.py`
  or `.agents/orchestration.json`. If you need another new test module, ask the lead to enter it.
- **The lead's files.** `catalog.json` and `profiles/*` are the lead's (Decision 18). Run
  `scripts/update-provenance.py` for `provenance.lock.json`, never edit it by hand.
- **`run_delivery.py` is split by lines before V26-3564 starts.** Lane 1 holds it whole until then. The lead revokes
  that claim and grants spans in one run while neither lane has a candidate under review, so tell the lead before you
  start a review round, and expect a message naming your spans. Lane 1 keeps a span inside `needs_you` for Decision
  2's regrant items, and the `steps = (...)` statement in `one_pass` until V26-3591 lands. Lane 2 adds its branch read
  there only after the lead moves that span to it, and hands off V26-3564 only then.
- **Test cost.** Most of the region logic is testable on small repositories built with `tests/quiet_git.py`, with no
  installed release. Build at most one command-level fixture per class, as `RegionOwnershipTests` does, and reuse it.
  A class that waits on real processes within a bound, such as the race fixture, is `run_quietly`, never
  `run_serially`. The suite stays inside `tests/suite-budget.json`.
- **Left open by the plan review, for you to close cheaply if your fixture allows.** No row yet proves the landing
  settle takes `ownership_lock`. No row covers a claim anchored exactly at the merge commit, or a supervisor stopped
  between the settle's patch and its journal entries. Add them if they cost little, and say so in your hand-off either
  way.
- **V26-3476's pause point.** Inside `ownership_lock`, `lead-accept` calls the forge adapter's `get` before it
  journals, so a fixture forge adapter that records its pid and blocks until signalled is the pause (Decision 14).
  Prove the fixture red with the lock made a no-op, and keep that run in your evidence.
- **Another delivery on `main`.** V26-3606 changes `run_delivery.py`'s review preparation, hand-off and acceptance on
  `main`. Keep your edits there to what Decision 6 needs, so the merge into `main` stays small.
