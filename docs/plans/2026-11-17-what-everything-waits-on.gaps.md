# Gaps and friction: V26-3553

What the lead or a lane hit while running V26-3553 that the workflow made harder than it should be. Each entry says what
happened, why, and where it now stands. The close-out turns open entries into follow-ups and compound-learning evidence.

## Planning and the plan record

- **Tickets written without `## Summary`.** V26-3552, V26-3554 and V26-3555 were written by hand without the template's
  Summary, so their cards showed no summary. Fixed on the tickets. A setup check is PR #381 (open).
- **Plan review refused lens results that re-listed closed findings.** Rounds 3 and 6 needed fresh reviewers because the
  brief didn't name which findings were still open. Fix is PR #379 (open).
- **Hand-editing the delivery file shows as drift.** V26-3564's criteria went in by hand, which made changes c2 to c6.
  Reasons were recorded with `lead-plan why`. Practice: always use `lead-plan`.
- **`lead-plan change --kind removed` ignored `--criterion`.** It removed V26-3565 whole (c13), which had to be restored
  (c14 to c16). Fix is V26-3566 (lane 3, in this delivery).

## Ownership and region claims

- **No way to add a lane.** Lane 3 needed a hand edit to the delivery file and a state-tool patch to `laneOwnership`
  before `lead-own` or `lead-assign` would accept it. Open.
- **`lead-assign` moves the assigned lane's row to the end, and the page colours lanes by position.** Lanes showed in
  each other's colours. The record was reordered by hand. Fix is V26-3426 c3 (in this delivery).
- **Region spans by bare line numbers.** A lane asked for a span two lines off, the lead granted it unchecked, and
  another span needed widening by hand for an edge insertion. Fix is V26-3570 (backlog).
- **A story's own landing invalidates its lane's spans.** After V26-3552 landed, most of lane 1's spans needed
  re-granting at the new base, done by script with `region_ownership.mapped`. Open.
- **A grant at the current base can be outside a lane's history.** After a peer delivery (V26-3431) moved `main`, spans
  granted there were unusable for lanes branched earlier. They were re-granted at a common ancestor (42fbc233). Open:
  grants could map to each lane's own merge base.
- **Files the plan didn't name keep surfacing mid-story.** `hosted-snapshot.schema.json` (V26-3426), the pins in
  `tests/test_registry.py` and `tests/test_run_delivery.py`, and the plan chapter's tests were each found by a lane or
  by the queue, then granted one by one. Open: lead-assign's co-change warning missed them.
- **Exact-set pins serialize stories.** `tests/test_waits.py`'s pinned set of `waits` templates must gain "review"
  (V26-3555) and "lane" (V26-3426). One line can't belong to two lanes, so V26-3426 waits for V26-3555 to land. Open:
  pins that every new template must extend could read the table instead.
- **A lead fix on `main` inside a lane's span forced that lane to rebase.** This came from the two line-chart fixes. Practice:
  no lead edits to held spans mid-story.

## The merge queue and checks

- **A provenance listing change is caught only in the queue, after acceptance.** V26-3552 was stopped for adding
  `wait-words.json`. Fix is V26-3565 (lane 3, in this delivery).
- **Tests outside a lane's files go unrun until the queue runs them.** `tests.test_registry` (V26-3552) and
  `tests.test_run_delivery` (V26-3555) failed in the queue on pins the lanes never ran. Open: nothing proposes the
  modules that pin a changed file.
- **A story's new test module can't be among its acceptance checks.** The base's catalogue learns it only when the story
  lands (`tests.test_waits_review`). Open.
- **The check catalogue and its pinned list in `tests/test_own_deliveries.py` drift apart.** The lead added entries
  without updating the pin, so `main` failed until it was fixed (049a0ac7). Open: nothing checks them together at
  commit.

## The lead's own loop

- **`lead-push` refuses a dirty checkout,** and the operator-approved installed line-chart fix made it dirty. Setting it
  aside during a long queue wait left the live view without the fix. Settled with `git update-index --skip-worktree`.
- **A waiting `lead-push` gives up silently when its own wait ends,** and when the base moves. 19 commits sat unpushed
  for a while. Open: say so loudly, or retry.
- **A re-queued candidate leaves its lane's row "waiting on the lead".** The row was patched by hand. Fix is V26-3426
  (in this delivery).
- **`lead-wait` exits on every event and timeout,** so restarting it is constant manual work, and the stop hook catches
  any miss. Open.
- **Lead and lane messages cross.** A lane often asks again for what the lead has already granted, because the grant
  message is still queued behind the lane's turn. Open: the lane could read its grants from the record (a `lane-grants`
  command) rather than wait for a message.
- **zsh parsing broke several compound shell commands** (`$B:t`, `for … do` with heredocs). Practice: write a script
  file and run it with bash.

## The live page

- **The line chapter was mis-laid out by a class-name collision** (`.chapter-line`), and its track had a fixed width
  floor. Fixed on `main` (f5f08a41, be9f8a30); the installed copy is patched under the operator's exception.
- **The Checking card's finishing animation is cut off by redraws.** Fix is V26-3568 c6 (in this delivery).
- **"Needs you" and "Needs attention" repeat each other, and "you" names no one.** Fix is V26-3567 (in this delivery).
