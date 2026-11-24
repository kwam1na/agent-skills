---
title: Make the Now page say where a delivery stands, and stop four lead-command frictions - Plan
type: feat
date: 2026-11-24
topic: now-page-and-lead-fixes
execution: code
status: approved
---

# Make the Now page say where a delivery stands, and stop four lead-command frictions - Plan

## Goal

Deliver five tracker items that were already fully specified, as one delivery
under the epic V26-3845. V26-3819 replaces the top of the live view's Now page
with one sentence, four marks and at most one fact, at every stage of a delivery.
V26-3844, V26-3841, V26-3436 and V26-3843 fix frictions the lead met while
delivering V26-3775 and V26-3306.

This plan adds nothing to the items: each story's scope, acceptance criteria,
test scenarios, posture and sensors are its tracker item's. The plan only bundles
them, orders them and gives each lane its files.

## Scope

- In: V26-3819, V26-3844, V26-3841, V26-3436, V26-3843, as their items say.
- Out: V26-3805, whose fix is already in draft change request
  kwam1na/agent-skills#491; every other chapter of the live view; a "since you
  last looked" digest; the cleanliness checks of `lead-plan` and `lead-close`,
  unless they share the one function V26-3841 changes.

## Decisions recorded here

1. **No plan review.** The operator ruled on 2026-11-24 that this plan bundles
   specified items and runs no plan-review rounds. Each story still runs the two
   mandated lenses to alignment.
2. **Integration base.** The stories land on `integrate/now-page-and-lead-fixes`,
   which reaches `main` as one change request after its integration review.
3. **One lane per surface.** Lane 1 holds the delivery views; lane 2 holds
   run-delivery. Every lane-2 item changes `run_delivery.py`, so lane 2 takes them
   one at a time, in the order below.

## Finish line

All of the following hold at the integration base's head when it merges into `main`:

1. Every story has merged into the integration base, with both mandated lenses
   aligned on its last round.
2. Each story's acceptance criteria, as its tracker item states them, hold.
3. Catalog, profiles and provenance are refreshed for every skill the stories change.
4. The full local suite passes on macOS with Python 3.11 inside its budget.
5. The integration change request into `main` has passed its review and merged.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3819 | `skills/delivery-views/**`, `hosted/delivery-viewer/**`, `tests/delivery_views_page/**`, `tests/test_delivery_views*.py`, `skills/orchestrate-work/references/wait-words.json`, `tests/test_waits.py` |
| 1 | 2 | V26-3844, V26-3841, V26-3436, V26-3843 | `skills/run-delivery/**`, `tests/test_run_delivery*.py`, `skills/orchestrate-work/scripts/merge_queue.py`, `skills/orchestrate-work/scripts/delivery_state.py`, `tests/test_merge_queue.py` |

Every story that changes a releasable skill also refreshes `catalog.json`, the
selected `profiles/` and `provenance.lock.json` in its own change, through
`scripts/update-provenance.py`. These files are shared: see Shared resources.

The stories are children of the epic V26-3845.

## Stories

### V26-3819. Lead the live view's Now page with one sentence, four marks and one fact, at every stage of a delivery

Lane 1, tier 1, not blocked. Test-first. Files: `skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/line-map.css`, `skills/delivery-views/scripts/render.py`,
`hosted/delivery-viewer/**`, `tests/delivery_views_page/**`,
`tests/test_delivery_views_render.py`, `tests/test_delivery_views_hosted_projection.py`
and `skills/orchestrate-work/references/wait-words.json`.

The design is settled: direction D on the Now During the Rejoin canvas. Draw all 12
stages and 3 overrides from fixture records, and check the rendered view in light
and dark mode at 375px.

### V26-3844. Stop the supervisor's lane-changes step failing on non-UTF-8 output

Lane 2, tier 1, not blocked. Characterization-first. Files:
`skills/run-delivery/scripts/run_delivery.py`, `tests/test_run_delivery_supervisor.py`.

### V26-3841. Let lead-setup --integration run beside untracked files it never reads

Lane 2, tier 1, after V26-3844. Test-first. Files:
`skills/run-delivery/scripts/run_delivery.py`, `tests/test_run_delivery.py`.

### V26-3436. Drop a resolved queue stop from the supervisor's status line once the candidate is requeued

Lane 2, tier 1, after V26-3841. Test-first. Files:
`skills/run-delivery/scripts/run_delivery.py`, `tests/test_run_delivery_supervisor.py`.

If the status line already drops the stop at the base, hand it off with
`handoff --no-change` and the evidence.

### V26-3843. Say, in every lead command, that a broken base holds the queue until a repair is named

Lane 2, tier 1, after V26-3436. Test-first. Files:
`skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`,
`skills/orchestrate-work/scripts/delivery_state.py`, `tests/test_run_delivery.py`,
`tests/test_merge_queue.py`.

## Shared resources

- **Provenance, catalog and profiles.** On a merge conflict confined to the lock,
  take either side and run `scripts/update-provenance.py`, as AGENTS.md says. The
  queue lands one story at a time.
- **`run_delivery.py`.** Only lane 2 edits it.
- **Wait words.** `wait-words.json` is lane 1's. If a lane-2 story needs a new
  sentence there, it asks the lead.
- **Processes and ports.** Servers in tests bind port 0 and are stopped by their
  recorded pid, never by pattern.

## Sensors

- **Lane 1:** `tests.test_delivery_views_page` (Node 24, jsdom),
  `tests.test_delivery_views_render`, `tests.test_delivery_views_hosted_projection`,
  `tests.test_waits`, `hosted.build`, `hosted.browser`.
- **Lane 2:** `tests.test_run_delivery`, `tests.test_run_delivery_supervisor`,
  `tests.test_merge_queue`.
- **Both:** `scripts/validate-registry.py`, `tests.test_registry`.
- **Before the integration change request:** the full local suite on macOS with
  Python 3.11, with `--budget tests/suite-budget.json`.

## Approval handoffs

1. Approved by the operator on 2026-11-24: lanes may push and open draft change
   requests; integration base; no plan review.
2. Tracker items exist under V26-3845; done.
3. The integration change request into `main` is merged by the operator.

## Risks

- **The Now page's text is pinned.** The line map's tests and the supervisor's
  tests pin Needs-you and wait sentences. V26-3819 re-derives each moved pin in its
  own change.
- **`run_delivery.py` is large and shared with V26-3802.** The co-lead runtime may
  change the same file on `main` while lane 2 works. The integration review merges
  `main` in once, at close-out.
