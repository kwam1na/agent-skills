# Gaps and friction: V26-3619

What the lead or a lane hit while running V26-3619 that made the work harder than it should have been. Each entry gives
what happened, why, and where it stands. Entries are classed by where the gap lives, because that decides where it goes:

- **Workflow** gaps are in the delivery tooling (run-delivery, orchestrate-work, delivery-views, their briefs and views).
  They go to the workflow's maintainers with the release they were seen on, never to an adopter's tracker. Here the
  workflow's source and its adopter are the same repository, which hides the difference but doesn't remove it.
- **Repo** gaps come from this repository's own content: its tickets, plans and tests. They are this repository's
  follow-ups.
- **Host** gaps come from the environment the agents ran in. They change agent practice and are filed nowhere.

Seen on release `linear-v3.2.0` (generation `e13cd6d1`), the installed workflow throughout this delivery.

## Workflow

### Adding work mid-delivery

- **No command adds a lane, seen a second time.** V26-3589 joined as lane 3 on 2026-11-18. `lead-plan change --kind
  added` adds a story whole, but refuses a `--lane` the delivery file's `lanes` doesn't hold, and `lead-own` and
  `lead-assign` refuse a lane the record's `laneOwnership` doesn't hold. The lead hand-edited the delivery file (the
  lane, the story and its six criteria), the plan's story section and its Units row, then wrote the record's lane with
  the state tool's raw `patch --set /laneOwnership/lane-3`. That patch skips the overlap checks `lead-own` makes. V26-3553
  logged the same gap (its lane 3) and left it open. Fix: V26-3628 (backlog), a command that adds a lane to the delivery file and the
  record in one write, with `lead-own`'s checks, and lets `lead-plan change --kind added` name it.
- **A story with its own reviewed plan joins with a stub section.** V26-3589's design and scenarios live in its own
  plan, `2026-11-18-integration-step-on-the-live-view.md`, reviewed to alignment. This delivery's plan gained a story
  section pointing at it, written by hand, and nothing links the two plans' review records. Open.

### Plan review and setup

- **Rerunning `lead-setup` lost the plan-review rounds.** The first `lead-setup`, run for a `main` base, adopted the
  plan-review store by moving its rounds into the delivery's review folder. The lead then switched to an integration
  base, removed that first draft and its folder, and the rerun found nothing to adopt. The committed review record
  (`2026-11-18-ownership-follows-work.review.json`) is intact, but the plan chapter shows no rounds. Open: adoption
  could copy rather than move, or setup could refuse to discard a draft that holds an adopted store.
- **The review record names commits the base never received.** The plan was reviewed on commits made in the operator's
  checkout. `origin/main` had moved, so the lead re-applied them onto it, and they got new ids. The record's
  `planCommit` and round candidates name the originals, which survive only on a local branch,
  `plan-review/V26-3619`. Open.

### Region claims

- **Every landing and every new lane meant revoke-then-regrant by hand.** The installed generation has no carve and
  doesn't release or carry claims when a story lands. After V26-3591 landed, the lead revoked and regranted all of
  `run_delivery.py`'s spans at the new base. Adding lane 3 meant revoking lane 1's spans and regranting nine. Splitting
  `tests/test_run_delivery_supervisor.py` and run-delivery's `SKILL.md` for lane 3 was the same. Fix: V26-3591 (landed
  in this delivery; reaches a lead once a release carries it).
- **No authorized base merge for a head past its last aligned round.** `lead-authorize-base-merge` refuses unless the
  lane's checkout is at the reviewed head. V26-3564 had a commit after its aligned round and needed the base for its
  regranted spans, so the lead recorded a plain merge as a decision (80) instead. Open: say so in the refusal, and name
  the plain merge as the path when no review can carry.

### The merge queue and checks

- **The setup check doesn't test a check's own Node range.** `doctor` and `lead-run` check the first `node` on `PATH`
  against the runtime's `nodeMinimum` (22.6.0) only. The supervisor started on Homebrew's Node 23.5.0, and V26-3564's
  `hosted.install` check, `npm ci --engine-strict` against a viewer that requires `^22.22.2 || >=24.15.0`, failed. The
  base re-run failed the same way, so the queue journalled `base.broken` for a host problem. The lead restarted the
  supervisor with nvm's Node 24.22.2 first on `PATH`, proved `hosted.install` passes at the base head, and journalled
  `base.repaired` by hand. Open: `doctor` could check each catalogued check's declared engine range, and a re-run that
  fails before the check starts, on the host, could stop as a host problem rather than a broken base.

## Repo

- **A catalogued test module needs a `TestCase`.** Decision 19 said the lead's stubs would hold only a docstring, but
  `test_the_lead_selects_by_catalogue_id_and_the_guards_join_every_selection` requires `TestCase` in every catalogued
  non-script module. Each stub got an empty test class. Fixed in this delivery's setup commit.

## Host

- **A lane's question can cross the lead's answer.** Cross-session messages wait until the receiving session's turn
  ends. Lane 1 asked twice for a grant that was already made, because the lead's answers were queued behind its turn.
  Practice: a lane checks the record (`laneOwnership`, `laneRegions`) before it reports itself blocked on a grant.
