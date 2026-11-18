# Gaps and friction: V26-3553

What the lead or a lane hit while running V26-3553 that made the work harder than it should have been. Each entry gives
what happened, why, and where it stands. Entries are classed by where the gap lives, because that decides where it goes:

- **Workflow** gaps are in the delivery tooling (run-delivery, orchestrate-work, delivery-views, their briefs and views).
  They go to the workflow's maintainers with the release they were seen on, never to an adopter's tracker. Here the
  workflow's source and its adopter are the same repository, which hides the difference but doesn't remove it.
- **Repo** gaps come from this repository's own content: its tickets, plans and tests. They are this repository's
  follow-ups.
- **Host** gaps come from the environment the agents ran in. They change agent practice and are filed nowhere.

Seen on release `linear-v3.0.0` (generation `7ff4b0e5`), the installed workflow throughout this delivery.

## Workflow

### The plan record

- **Plan review refused lens results that re-listed closed findings.** Rounds 3 and 6 needed fresh reviewers, because the
  brief didn't name which findings were still open. Fix: PR #379 (open).
- **`lead-plan change --kind removed` ignored `--criterion`.** It removed V26-3565 whole (c13), which then had to be
  restored (c14 to c16). Fix: V26-3566 (lane 3, this delivery).
- **No command adds a lane.** Lane 3 needed a hand edit to the delivery file and a state-tool patch to `laneOwnership`
  before `lead-own` or `lead-assign` would accept it. Open.

### Ownership and region claims

- **`lead-assign` moved the assigned lane's row to the end, and the page coloured lanes by position.** Lanes showed in
  each other's colours. The record was reordered by hand. Fix: V26-3426 c3, narrowed by c42 to keep declared order
  (this delivery).
- **Region spans by bare line numbers.** A lane asked for a span two lines off, the lead granted it unchecked, and
  another span needed widening by hand for an edge insertion. Fix: V26-3570 (backlog).
- **A story's own landing invalidates its lane's spans.** After V26-3552 landed, most of lane 1's spans needed
  re-granting at the new base, done by script with `region_ownership.mapped`. Open.
- **A grant at the current base can be outside a lane's history.** After a peer delivery (V26-3431) moved `main`, spans
  granted there were unusable for lanes branched earlier, and were re-granted at a common ancestor (42fbc233). Open:
  a grant could map to each lane's own merge base.
- **One unmappable claim blocks every grant on its file, after the revoke has gone through.** `lead-own --region` maps
  every claim on the path to the current base, so one landed story's stale span, or a span whose mapping repeated lines
  make ambiguous, refuses all grants there. Carving a span is a revoke and then grants, so when the grants were refused,
  lane 1 was left with unclaimed lines and its review-reduce stopped. Later, one regrant in a carve gave up with
  `version_stale` while the queue was writing the record, leaving another gap. Open: carve in one command, and name
  stale claims when a story lands.
- **`lead-assign`'s co-change warning missed files stories needed.** `hosted-snapshot.schema.json` and the pins in
  `tests/test_registry.py` and `tests/test_run_delivery.py` surfaced mid-story, one grant at a time. Open.

### The merge queue and checks

- **The queue's sync merge blocks the lane's next push.** When the queue checks a candidate, it pushes its own "Merge
  main into claude/KEY" to the lane's branch. After a stop, the lane's fix sits on the older head, so its push isn't a
  fast-forward. And `lead-authorize-base-merge` refuses the sync commit, because it isn't on `main`. This happened to
  V26-3552 (cherry-picked onto the sync) and V26-3555 (authorized base merge plus one force-push). Open.
- **A base merge that regenerates digests can't carry its review.** Resolving `provenance.lock.json` in V26-3426's
  authorized base merge reran the writer, which changed nine digests the branch's own files feed. The hand-off then
  refused to carry round 3's review, because a lane-owned file had changed, and the story spent a round on generated
  output. Open: treat a regenerable file that matches its regenerator's output as unchanged for carrying a review.
- **A provenance listing change is caught only in the queue, after acceptance.** V26-3552 was stopped for adding
  `wait-words.json`. Fix: V26-3565 (lane 3, this delivery).
- **Tests outside a lane's files go unrun until the queue runs them.** `tests.test_registry` (V26-3552) and
  `tests.test_run_delivery` (V26-3555) failed in the queue on pins the lanes never ran. Open: nothing proposes the test
  modules that pin a changed file.
- **A story's new test module can't be among its acceptance checks.** The base's catalogue learns the module only when
  the story lands (`tests.test_waits_review`). Open.
- **A batch runs every check after one has already failed.** V26-3565 and V26-3426 were checked together. The new
  catalogue guard failed on V26-3565's uncatalogued module in the first minutes, and the queue still ran the batch's
  remaining modules and a base rerun before stopping either. The lead has no command to cut a batch short once the
  cause is known, and V26-3426 waited behind it. Open: stop a batch on the lead's word, or split it when a candidate
  fails.
- **Lead pushes starve behind a long batch.** The queue held the merge slot for over an hour checking V26-3565 and
  V26-3568, so the lead's plan commits for V26-3588 could not publish, and lane 3's gates refused the story as not in
  the delivery. One `lead-push` gave up with `slot_held`. Open: let plan-only commits publish between checks, or let a
  lane read new stories from the lead checkout.
- **A re-queued candidate leaves its lane's row "waiting on the lead".** The row was patched by hand. Fix: V26-3426
  (this delivery).

### The lead's loop

- **`lead-push` refuses any change in the checkout,** including the operator-approved patch to the installed copy.
  Setting the patch aside during a long queue wait left the live view without it. Worked around with
  `git update-index --skip-worktree`. Open: a declared local overlay could be exempt.
- **A waiting `lead-push` gives up silently when its wait ends or the base moves.** 19 commits sat unpushed until the
  lead noticed. Open: say so loudly, or retry.
- **`lead-wait` exits on every event and timeout.** Restarting it is constant manual work, and the stop hook catches
  any miss. Open.
- **Lead and lane messages cross.** A lane often asks again for what the lead has already granted, because the grant
  message is still queued behind the lane's turn. Open: a lane could read its grants from the record (a `lane-grants`
  command) rather than wait for a message.
- **Gaps had no home.** Friction was noticed and mentioned, but only some of it was filed, until this log. Fix: V26-3571 (backlog),
  a gap command with routing by class.

- **A failing tracker read never reached the lead.** The tracker snapshot failed on every supervisor pass for about
  1h 40m, because a ticket marked Duplicate has a state type (`duplicate`) the snapshot doesn't know. Only the live
  page showed it. `lead-wait` didn't wake, and no command's status line named it. Fixed for now by moving V26-3569 to
  Canceled. Open: the snapshot could read `duplicate` as canceled, and a failing reader could wake the lead.

### The live page

- **The line chapter was mis-laid out by a class-name collision** (`.chapter-line`), and its track had a fixed width
  floor. Fixed on `main` (f5f08a41, be9f8a30). The installed copy is patched under the operator's exception.
- **The Checking card's finishing animation is cut off by redraws.** Fix: V26-3568 c6 (this delivery).
- **"Needs you" and "Needs attention" repeat each other, and "you" names no one.** Fix: V26-3567 (this delivery).

## Repo

- **Tickets written without `## Summary`.** V26-3552, V26-3554 and V26-3555 were written by hand without the template's
  Summary, so their cards showed none. Fixed on the tickets. A workflow-side setup check is PR #381 (open).
- **The plan missed files and pins its stories needed.** `hosted-snapshot.schema.json`, the core profile pin in
  `tests/test_registry.py`, and the check catalogue's pinned list in `tests/test_own_deliveries.py` were not in any
  story's files. Fixed as they surfaced.
- **The plan misstated a current behaviour.** It said a `blocked` lane is counted as quiet. It isn't, and V26-3426
  pinned the real behaviour.
- **The lead's catalogue edit broke a pin on `main`.** Two catalogue entries went in without `tests/test_own_deliveries.py`'s
  list, so `main` failed until 049a0ac7.
- **A lead fix on `main` inside a lane's span forced that lane to rebase.** This came from the two line-chart fixes.
  Practice: no lead edits to held spans mid-story.
- **Exact-set pins nearly serialized two stories.** `tests/test_waits.py`'s pinned `waits` template set had to gain
  "review" (V26-3555) and "lane" (V26-3426). Lane 1 avoided it by putting its sentence in the `lanes` group. Open: pins
  that every new template must extend could read the table instead.
- **The lead-wait tests read the real machine's load.** `LeadListenerTests` (three cases) and one `LeadMayStopTests`
  case expect `lead-wait` to time out. Over twice the cores, it wakes on load pressure instead. With three lanes, their
  reviewers and the queue running, the 5-minute load reached 30 on 10 cores. That stopped V26-3565 in the queue on a
  change it didn't make. Open: the tests could pin the load threshold, or pass `--seen-pressure=load`.
- **Hand-editing the delivery file shows as drift.** V26-3564's criteria went in by hand, which made changes c2 to c6.
  Reasons were recorded with `lead-plan why`. Practice: always use `lead-plan`.

## Host

- **zsh parsing broke several compound shell commands** (`$B:t` read as a modifier, `for … do` with heredocs).
  Practice: write a script file and run it with bash.
- **Load ran past 2× cores with three lanes, their reviewers and the queue at once.** Timing-sensitive tests may flake
  under it. Practice: treat a timing failure under load as a likely flake first.
