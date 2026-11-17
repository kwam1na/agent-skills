# Lane notes: what everything waits on

Notes for the lanes that the plan's review left open. The plan review ended after round 7 by the lead's decision,
and its record is `2026-11-17-what-everything-waits-on.review.json`.

## V26-3426

Plan review finding `adversarial-testing-r7-1` is carried here, and V26-3426 covers it as part of its scenarios.
Decision 7 says that any write taking a row out of `waiting` removes `waitingFrom` and clears a `waitingOn` that
named the waited-on subject. Test that clause on every path that takes a row out of `waiting`, not only the
no-change hand-off:

- A lane `waiting` on lane 2's story says `focus --state working`. The row has no `waitingFrom`, and its `waitingOn`
  no longer names lane 2's story.
- A lane `waiting` on lane 2's story hands its own story with a change, through `handoff`. The row is `handed`, with
  no `waitingFrom`, and its `waitingOn` no longer names lane 2's story.

Two smaller notes from the review, worth a row each:

- A lane that leaves `waiting` by its own `focus` keeps no stale `waitingFrom`, so setting `waiting` again starts
  afresh.
- `merge_slot`'s `doing` values `merge` and `release` are exercised at least once each in V26-3554's slot rows,
  besides `check`.
