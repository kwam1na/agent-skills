---
title: "feat: Show a delivery's plan review in the live view, produced by run-delivery"
type: feat
status: draft
date: 2026-10-30
execution_posture: test-first
context: A second adopter's pilot ran a plan through plan-work's unbounded review by hand. The rounds converged, but the live view showed none of it, and said nothing needed the engineer while three approvals blocked the lane.
---

# feat: Show a delivery's plan review in the live view, produced by run-delivery

## Outcome

The plan chapter shows how the plan came to be approved, and what still has to be approved, without anyone writing
view data by hand.

- **One line, open by default,** beside the approval: "Approved <date> after N review rounds · every lens aligned",
  or the honest alternative when the review didn't converge or wasn't recorded.
- **A fold, "Plan review (N rounds)",** holding the same round track and round card the line map already draws for
  a story's review: each lens's verdict per round, the findings raised, and each one's closure with the plan
  revision that closed it. The rounds read as unbounded, never "round k of 4".
- **Approvals ahead and out of scope** come from the delivery itself, so "Nothing in the plan needs you right now" is
  never shown while an approval the plan names is still waiting.
- **`run-delivery` runs the plan review** the way it runs a story's review: it prepares each lens's brief and private
  checkout, carries findings forward, reduces each round, and says when the review stops converging.

**The view stays agnostic.** It reads only what `delivery-plan/1` declares. A producer other than `run-delivery`
gets the same fold by writing the same keys, and `check-plan` checks them.

## Current-State Evidence

**The view.**
- `delivery-plan/1` carries `approved: {at, review?, snapshot}`. `review` is free text of at most 600 characters,
  shown only inside the "All units" fold after "Approved <time> · ".
- The line map draws a story's review rounds through `runSummaryNode` (`run-summary.js`) from a run's `roundDetail`,
  or, for runless lanes, from `render.py`'s `native_review_summaries`, which projects retained review outcomes into
  the same `roundDetail`: verdicts per lens, `raised` findings, `settled` closures with `fixCommit`.
- Both paths are keyed by a queue item. `capture-review` refuses a subject that isn't queued or planned work, so a
  plan has no path into either.

**The producer.**
- `run_delivery.py` has `review-prepare` and `review-reduce` for stories only. They are bounded by `review.bound`,
  write per-lens round briefs from `templates/round-brief.md`, and journal `review.round`.
- `lead-init` writes `plan.json` with `approved.at` and the snapshot. It sets `approved.review` only to mark a late
  snapshot, and writes no `approvals` or `outOfScope`, so those folds never appear.

**The pilot** (a one-story delivery, five plan-review lenses, three rounds to alignment):
- Every brief was composed by hand from the round-brief template: binding tuple, carried entries, resolution pairs.
  Each lens's clean checkout and its identity checks were set up by hand.
- Rounds 1 to 3 raised 4, 1 and 0 blocking findings; every one was closed by the next plan revision. None of it
  reached the record or the view.
- After `lead-init`, the plan chapter read "Nothing in the plan needs you right now" while three Product Owner
  approvals blocked the lane, because the plan's approvals and out-of-scope list never reached `plan.json`.
- The review cost 15 lens runs. Without a recorded track, that cost and what it bought were invisible.

**Repository precedent.** `2026-10-30-plan-chapter.md` kept the plan out of `orchestrate-work`'s schemas: a plan write
moves the record through an ordinary "Plan updated:" journal entry (its C9). This plan keeps that boundary.

## Execution Posture

- **`test-first`:** the contract and validator (P1), the page (P3), the producer commands (P4, P5).
- **`characterization-first`:** the shared projection (P2). Every existing native-review golden and page test passes
  unchanged before and after the projection moves.
- **`sensor-only`:** skill text, the adoption guide, catalog and profile descriptions, provenance (P6).

## Plan Review Status

Not reviewed yet. Plan review carries no round bound.

## Scope

### In scope

1. **The input.** An optional `planReview` key in `delivery-plan/1`, and optional `id` on `approvals` entries, with
   their caps; the validator and `check-plan` learn them.
2. **One projection** from review outcomes to `roundDetail`, shared by `render.py`'s native summaries and the producer.
3. **The page.** The approval line, the "Plan review" fold with the existing track and card in an unbounded mode, and
   the "needs you" flag for waiting approvals.
4. **The producer.** `run-delivery` plan-review commands, and `plan.json` carrying `planReview`, `approved.review`,
   `approvals` and `outOfScope` from the delivery.
5. **Tests:** contract and validator cases; the projection's characterization; page tests and goldens for a synthetic
   delivery with a three-round plan review and for one without; producer command tests.

### Out of scope

- Changes to `orchestrate-work`'s state, journal kinds or `capture-review`.
- Recording plan review through the harness runtime's `runs`; the producer embeds the projection instead.
- A per-delivery base branch, and the other onboarding friction from the pilot.
- Changing this repository's own `docs/plans` review evidence (`plan-review-evidence/1`).
- Cutting the release; it follows the batch rule in AGENTS.md.

## Contract Decisions

**C1. Plan review lives in the plan input.** `delivery-plan/1` gains an optional `planReview`. No record or journal
schema changes. A plan write that changes `planReview` moves the record through the existing "Plan updated:" entry.

**C2. `planReview`.**

| Field | Contents |
| --- | --- |
| `lenses` | Ordered lens ids, fixed for the review; 1 to 12. |
| `state` | `aligned` (the last round approved by every lens), `open`, or `ended` (the operator ended it without alignment). |
| `endedReason` | Required exactly when `state` is `ended`; at most 600 characters. |
| `rounds` | Ordered `{round, candidateRef, at, outcome, verdicts, raised, settled}`, the same shape `roundDetail` already has, round numbers from 1 with no gaps; at most 64 rounds. |

- `verdicts` maps each of `lenses` to `approve`, `changes` or `unavailable`.
- `raised` and `settled` keep `roundDetail`'s fields and caps: at most 50 findings a round, summaries of at most 280
  characters, `fixCommit` 40 lowercase hex or null.
- `candidateRef` is opaque text of at most 128 characters; the page shows it and never parses it.
- Unknown keys are refused, as elsewhere in the input.

**C3. `approvals` entries gain an optional `id`** (`[A-Za-z0-9._-]{1,64}`, unique), so a producer can update one
approval's state. Entries without one stay valid.

**C4. One projection.** The function that turns review outcomes into `roundDetail` moves out of
`native_review_summaries` into a module both `render.py` and `run-delivery` import. Words and states can't drift.

**C5. The page.**
- **Open by default:** one line beside the approval. With `planReview`: "Approved <date> after N review rounds",
  then "· every lens aligned", "· ended without alignment: <reason>", or "· still open". Without it, today's
  `approved.review` text, as today.
- **Folded:** "Plan review (N rounds)", drawn by `runSummaryNode` from `planReview.rounds`, with lens names and icons
  from the config's `lenses`. In this mode the track labels read "Round k" and the summary says "unbounded"; no bound
  or grace wording appears.
- **What needs you:** a new flag, ordered after "The base is broken": **"Approvals are waiting"**, naming each
  `approvals` entry whose state is `waiting`. "Nothing in the plan needs you right now" never shows while one is.
- No new chapter, rail entry or line in Now.

**C6. Plan review commands.** `run_delivery.py lead-plan review-prepare --round N` and `lead-plan review-reduce
--round N`.
- The lens set comes from the delivery file's `planReview.lenses`, is fixed at round 1, and a later round with a
  different set is refused.
- The candidate is the commit that carries the plan file; its `candidateRef` and resolution pairs are composed by the
  tool, per `obtain-review`.
- Each lens gets a clean checkout of that commit under `.orchestration/review/<delivery>/plan/<lens>`, with the start
  identity checks done before the brief is printed and the end checks done by `review-reduce`.
- Briefs come from `templates/round-brief.md` in its `unbounded` form, with each lens's carried entries.
- `review-reduce` validates each result, writes the round's outcome, and prints the open findings. When a round after
  the first raises a new finding and leaves no fewer open findings than the round before, it says the review isn't
  converging and that the operator may end it. It never ends it on its own.
- `lead-plan review-end --reason TEXT` records `ended`. No round count ends a plan review.

**C7. Freezing.** `lead-init` embeds `planReview` when plan-review rounds exist.
- It refuses to freeze while the last round isn't aligned, unless the review was ended.
- `approved.review` gets the C5 line's text, so a page without the fold still says it.
- A delivery with no plan-review rounds freezes as today.

**C8. Approvals and out of scope come from the delivery file.** Optional `approvals: [{id, text, state, who?}]` and
`outOfScope: [text]` in `delivery-flow-delivery/1`, copied into `plan.json` on every plan write.
`lead-plan approval <id> --state done|waiting [--who NAME]` changes one and rewrites the plan. A hand edit to these
keys is picked up on the next plan write.

## Implementation Units

### P1. The contract and validator

`references/delivery-plan.schema.json`, `views_common.py` (validator and `check-plan`) in `delivery-views`. Test-first:
valid and invalid `planReview` and `approvals` cases, caps at their limits, and today's fixtures still valid.

### P2. The shared projection

A new module in `delivery-views/scripts`, used by `render.py`. Characterization-first: the existing native-review
goldens and page tests are unchanged.

### P3. The page

`line-map.js` and `line-map.css` (and `run-summary.js` for the unbounded mode only). Test-first: page tests for the
line's four forms, the fold's track and card, the "Approvals are waiting" flag, and the absence of every new element
with no `planReview` and no waiting approval. Goldens for a synthetic three-round plan review and for today's plan
fixtures, unchanged.

### P4. The plan review commands

`run_delivery.py` (`lead-plan review-prepare`, `review-reduce`, `review-end`) and the round-brief template's unbounded
form. Test-first, in a fixture repository: a three-round review to alignment, a refused lens-set change, a
non-converging round's message, an ended review, and a lens checkout that moved.

### P5. The plan write

`run_delivery.py` (`lead-init`, the plan writer, `lead-plan approval`) and the delivery file's schema. Test-first: a
frozen plan with `planReview` passes `check-plan`; freezing refuses an unaligned review; approvals and out-of-scope
reach `plan.json` and change through `lead-plan approval`.

### P6. Text and registry

`delivery-views` and `run-delivery` SKILL.md, `references/adopting.md` (plan review before `lead-init`),
`references/config.md`, catalog and profile descriptions, and output digests through `update-provenance.py`.
Sensor-only.

## Dependency Graph

- P1 → P2 → P3; P1 → P5; P4 → P5; P6 after P3 and P5.
- P3 and P4 touch disjoint files and can run in parallel.
- **Shared:** `provenance.lock.json`, through the writer only, per AGENTS.md; the page-test lane's `node_modules`; the
  serial test modules.

## Representative End-to-End Scenarios

1. **No plan review, no approvals:** every existing golden is unchanged.
2. **Three rounds to alignment:** five lenses; rounds raise 4, 1 and 0 blocking findings, each closed by the next
   revision. The line reads "after 3 review rounds · every lens aligned"; the fold shows the track and each round's
   card with closures and their revisions.
3. **Waiting approvals:** three approvals waiting. The flag names them; "Nothing in the plan needs you" is absent until
   `lead-plan approval` marks each done.
4. **A review that isn't converging:** round 3 raises a new finding with open findings not lower; `review-reduce` says
   so; `review-end` records the reason, and the line and fold show "ended without alignment".
5. **A refused freeze:** `lead-init` refuses while the last round has a lens asking for changes.
6. **A live page:** `lead-plan approval` rewrites the plan; the open page shows the reload notice.

## Repository Sensors

- `validate-registry.py` and the registry tests;
- the full suite through `scripts/run-tests.py`;
- the page-test lane (jsdom, Node 20 or newer);
- `update-provenance.py --check`;
- `views_common.py check-plan` on every plan fixture.

The page goldens compare whole pages, so a base that moves under P3 is re-checked before hand-off.

## Risks and Mitigations

- **Clutter:** C5 keeps one line open and everything else folded; a page test enforces it.
- **The view and producer drift:** C4's one projection, and P5 validates every plan it writes with P1's validator.
- **Unbounded rounds on the page:** C2's 64-round cap, and the plan's fixed page share clips oldest round text first.
- **Hostile text in findings:** text-only rendering, as for story rounds.
- **Lens checkouts left behind:** `review-reduce` removes a round's checkouts after its end checks; a failed check
  keeps them for inspection and fails the round.

## Approval Handoffs

1. This plan's approval by the operator after its lenses align.
2. C7's strictness: freezing refuses an unaligned plan review unless ended. The operator may prefer a warning.
3. Which release batch carries it, per AGENTS.md.

## Observable Finish Line

1. A fixture delivery driven through `lead-plan review-prepare/review-reduce` and `lead-init` yields a `plan.json`
   that passes `check-plan`, with `planReview`, approvals and out of scope.
2. Its line map shows the approval line and the "Plan review (3 rounds)" fold, with the track and cards per C5, and
   the "Approvals are waiting" flag until each approval is marked done.
3. Every existing golden and page test passes unchanged.
4. Every sensor above is green.
