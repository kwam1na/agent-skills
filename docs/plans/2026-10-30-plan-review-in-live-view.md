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

The plan chapter shows how the plan came to be approved, and what has to be approved before work can go on, without
anyone transcribing view data by hand.

- **One line, open by default,** beside the approval: "Approved <date> after N review rounds · every lens aligned",
  or "· ended without alignment", or "· still open". A delivery without a recorded plan review reads as today.
- **A fold, "Plan review (N rounds)",** holding the round track and round card the line map already draws for a
  story's review: each lens's verdict per round, the findings raised, and each one's closure with the plan revision
  that closed it. The rounds read as unbounded.
- **Blocking approvals reach the engineer.** Approvals and out-of-scope items the plan names are drafted into the
  delivery from the plan, and an approval the lead marks blocking raises "Approvals are blocking work" until it is
  done. Approvals that are merely ahead stay in their fold, as today.
- **`run-delivery` runs the plan review** the way it runs a story's review: it prepares each lens's brief and private
  checkout, carries findings forward, reduces each round, records the revision that closed each finding, and says
  when the review stops converging.

**The view stays agnostic.** It reads only what `delivery-plan/1` declares. A producer other than `run-delivery`
gets the same fold by writing the same keys, and `check-plan` checks them.

## Current-State Evidence

**The view.**
- `delivery-plan/1` carries `approved: {at, review?, snapshot}`. `review` is free text of at most 600 characters,
  shown only inside the "All units" fold after "Approved <time> · ".
- `approvals` and `outOfScope` are optional keys the view already draws, as "Approvals ahead" and "Out of scope"
  folds. No flag reads them.
- The line map draws a story's review rounds through `runSummaryNode` (`run-summary.js`) from a run's `roundDetail`,
  or, for runless lanes, from `render.py`'s `native_review_summaries`, which projects retained review outcomes into
  the same `roundDetail`. Both are keyed by a queue item; `capture-review` refuses a subject that isn't queued or
  planned work.
- `views_common.read_plan` refuses a file over 1 MiB before validating it, and `render.py`'s `plan_block` clips plan
  text into a 512 KiB page share.

**The producer.**
- `run_delivery.py` has `review-prepare` and `review-reduce` for stories only, bounded by `review.bound`, with
  per-lens briefs from `templates/round-brief.md`. Closures in a lens result carry `{id, state, resolution}`; nothing
  records the revision that closed a finding.
- `build_plan` is a pure function of the delivery file, `plan-changes.jsonl`, the flow logs and the acceptance log,
  and `rebuild_plan` runs on every sweep and plan command. It already copies the delivery file's `approvals` and
  `outOfScope` into `plan.json`. `lead-setup` drafts only stories from the plan, so a plan's approvals and
  out-of-scope list reach the view only when someone types them into the delivery file.
- `plan.json` is written only for a delivery whose stories declare criteria (`plan_declared`), and `lead-plan`
  refuses without them.

**The pilot** (a one-story delivery, five plan-review lenses, three rounds to alignment):
- Every brief was composed by hand: binding tuple, carried entries, resolution pairs, and each lens's clean checkout
  and identity checks.
- Rounds 1 to 3 raised 4, 1 and 0 blocking findings; each was closed by the next plan revision. None of it reached
  the record or the view.
- After `lead-init`, the chapter read "Nothing in the plan needs you right now" while three Product Owner approvals
  blocked the lane: they were in the plan's prose, not the delivery file, and no flag reads approvals anyway.
- The review cost 15 lens runs, and nothing showed that cost or what it bought.

**Repository precedent.** `2026-10-30-plan-chapter.md` kept the plan out of `orchestrate-work`'s schemas (its C9).
This plan keeps that boundary.

## Execution Posture

- **`test-first`:** the contract and validator (P1), the page and its share (P3), the producer commands (P4, P5).
- **`characterization-first`:** the shared projection (P2). Every existing native-review golden and page test passes
  unchanged before and after the projection moves.
- **`sensor-only`:** skill text, the adoption guide, catalog and profile descriptions, provenance (P6).

## Plan Review Status

Round 1 (outcome-correctness, adversarial) asked for changes: eight P1 findings over five issues. This revision
answers them. Plan review carries no round bound.

## Scope

### In scope

1. **The input.** An optional `planReview` key in `delivery-plan/1`, and optional `id` and `blocking` on `approvals`
   entries, with their caps and cross-checks; the validator and `check-plan` learn them.
2. **One projection** from review outcomes to the native `roundDetail`, shared by `render.py` and the producer.
3. **The page.** The approval line, the "Plan review" fold, the blocking-approvals flag, and `planReview`'s place in
   the plan's page share.
4. **The producer.** Plan-review commands; a durable round store that `build_plan` reads; `approved.review` text;
   `lead-setup` drafting approvals and out-of-scope from the plan; `lead-plan approval`.
5. **Tests:** contract and validator cases; the projection's characterization; page tests and goldens for a
   synthetic delivery with a three-round plan review and for one without; producer command tests.

### Out of scope

- Changes to `orchestrate-work`'s state, journal kinds or `capture-review`.
- Recording plan review through the harness runtime's `runs`.
- A plan-review fold for a delivery whose stories declare no criteria: it has no plan chapter, today or after this.
  Its rounds are still kept and reduced.
- A per-delivery base branch, and the pilot's other onboarding friction.
- This repository's own `docs/plans` review evidence (`plan-review-evidence/1`).
- Cutting the release; it follows the batch rule in AGENTS.md.

## Contract Decisions

**C1. Plan review lives in the plan input.** `delivery-plan/1` gains an optional `planReview`. No record or journal
schema changes. A plan write that changes `planReview` moves the record through the existing "Plan updated:" entry.

**C2. `planReview`.**

| Field | Contents |
| --- | --- |
| `lenses` | Ordered lens ids, fixed for the review; 1 to 12. |
| `state` | `aligned`, `open`, or `ended`. The validator checks it against the last round: `aligned` needs every lens's verdict `approve` there; `open` needs at least one not `approve`; `ended` needs `endedReason`. |
| `endedReason` | Exactly when `state` is `ended`; at most 600 characters. |
| `rounds` | Ordered rounds, numbered from 1 with no gaps; at most 32. Each is `{round, candidateRef, at, outcome, verdicts, raised, settled}`, the native `roundDetail` shape `native_review_summaries` produces today. |

- `candidateRef` is opaque text of at most 2,048 characters, as the review outcome contract allows; the page shows
  it and never parses it.
- `verdicts` maps each of `lenses` to `approve`, `changes` or `unavailable`.
- `raised`: at most 30 a round, each `{id, lens, severity, summary, state}`, `summary` at most 280 characters,
  `state` `unresolved` or `resolved`. A plan has no deferral path, so no `deferred` state or `deferredIssue`.
- `settled`: at most 30 a round, each `{id, lens, state, fixCommit, resolution}`, `state` `fixed`, `dropped` or
  `open`, `resolution` at most 600 characters. `fixCommit` is 40 lowercase hex exactly when `state` is `fixed`, else
  null.
- **Aggregate:** the serialized `planReview` is at most 256 KiB, so a plan within every other cap stays under 1 MiB.
- Unknown keys are refused.

**C3. `approvals` entries gain optional `id`** (`[A-Za-z0-9._-]{1,64}`, unique) **and `blocking`** (boolean, false
when absent). Existing entries stay valid and unflagged.

**C4. One projection.** The function that turns review outcomes into the native `roundDetail` moves out of
`native_review_summaries` into a module both `render.py` and `run-delivery` import. For a plan review it takes, per
round, the candidate commit, and sets each `fixed` closure's `fixCommit` to the commit of the round that reports it
closed: the revision that carried the fix.

**C5. The page.**
- **Open by default:** one line beside the approval. With `planReview`: "Approved <date> after N review rounds",
  then "· every lens aligned", "· ended without alignment" or "· still open". Without it, today's `approved.review`
  text, as today. The reason for an ended review is shown in the fold, not the line.
- **Folded:** "Plan review (N rounds)", drawn by `runSummaryNode` from `planReview.rounds`. Lens names and icons come
  from the config's `lenses`; a lens it doesn't name shows its id and initial, as for story rounds. The summary says
  the review is unbounded; an ended review shows its reason.
- **What needs you:** a new flag after "The base is broken": **"Approvals are blocking work"**, naming each
  `approvals` entry that is `blocking` and `waiting`. Waiting entries that aren't blocking raise nothing and stay in
  "Approvals ahead".
- **Share:** `plan_block` gives `planReview` its place in the plan's 512 KiB share. Over it, the oldest rounds'
  `resolution` and then `summary` texts are clipped first; rounds, verdicts, counts and fix commits are always kept.
- No new chapter, rail entry or line in Now. Every existing golden and page test is unchanged.

**C6. Plan review commands.** `run_delivery.py plan-review prepare|reduce|end`, top-level, so they work before
`lead-init` and for any delivery.
- **Lenses:** from the delivery file's `planReview.lenses`; fixed at round 1, and a different set later is refused.
- **Candidate:** `clean_head` of the lead's checkout, with the plan file committed; a round whose candidate equals
  the previous round's is refused, as for stories. The tool composes `candidateRef`, the binding tuple and each
  lens's resolution pairs.
- **Checkouts:** each lens gets a clean checkout of the candidate under `.orchestration/review/<delivery>/plan/<lens>`,
  start identity checks before its brief is printed, end checks in `reduce`. `reduce` removes a round's checkouts
  after its end checks; a failed check keeps them and fails the round.
- **Briefs:** `templates/round-brief.md`'s `unbounded` form, with each lens's carried entries. In it, and in `reduce`'s
  result check, P2 and P3 findings belong in `evidence`; a plan-review result carries no deferral.
- **Convergence:** when a round after the first raises a new finding and leaves no fewer open findings than the round
  before, `reduce` says the review isn't converging and that the operator may end it. It never ends it.
- **End:** `plan-review end --reason TEXT` records `ended`; the reason is capped by C2. No round count ends a review.
- **Store:** each round's outcome, and the end record, are written under `.orchestration/review/<delivery>/plan/`.
  That folder is the durable store; `lead-close` archives it with the delivery.

**C7. The plan write.**
- `build_plan` reads the plan-review store on every build, and the store joins its list of pure-function sources. A
  sweep, `lead-plan change` and `lead-plan approval` therefore keep `planReview`.
- `approved.review` gets the line's fixed words without the date or reason, e.g. "after 3 review rounds · every lens
  aligned", always under 600 characters; the existing "Approved <time> · " prefix completes it.
- `lead-init` refuses to freeze while the review is `open`. With no recorded rounds it freezes as today, without
  `planReview`.
- A plan review after `lead-init` (an amendment) continues the round numbering in the same store; the line reads
  "still open" until it aligns or is ended.
- The producer checks the serialized `plan.json` against the 1 MiB limit as well as `validate_plan` before writing,
  and refuses a plan over it.

**C8. Approvals and out of scope from the plan.**
- `lead-setup` drafts `approvals` and `outOfScope` from the plan's sections named by `--approvals-section` and
  `--out-of-scope-section`, one entry per top-level list item, each approval `{id, text, state: "waiting"}`. The lead
  sets `blocking` while completing the delivery file, as it assigns lanes. `lead-setup --force` keeps `planReview`,
  `approvals` and `outOfScope` from the existing file.
- `lead-plan approval <id> --state done|waiting [--who NAME]` changes one approval with `lead-plan change`'s
  discipline: a clean checkout not behind the base, one commit, never a push. Approvals without `id` are changed by
  hand.
- `references/config.md` documents both delivery-file keys and `planReview.lenses`.

## Implementation Units

### P1. The contract and validator

`references/delivery-plan.schema.json` and `views_common.py` (validator, `check-plan`) in `delivery-views`. Test-first:
valid and invalid `planReview` cases, including each `state` cross-check, `fixCommit` only on `fixed`, every cap and
the 256 KiB aggregate at and over its limit; `approvals` with `id` and `blocking`; today's fixtures still valid.

### P2. The shared projection

A new module in `delivery-views/scripts`, used by `render.py`. Characterization-first: the existing native-review
goldens and page tests are unchanged. Test-first for the plan-review case: closures get the closing round's commit.

### P3. The page and its share

`line-map.js`, `line-map.css`, `run-summary.js` (unbounded wording, ended reason only) and `render.py`'s `plan_block`.
Test-first: the line's four forms; the fold's track and cards; the blocking flag named and the calm line absent while
one blocks, present when none does; no new element with no `planReview` and no blocking approval; clipping at the
share keeps every round's verdicts and fix commits. Goldens: a synthetic three-round plan review; today's plan
fixtures unchanged.

### P4. The plan review commands

`run_delivery.py` (`plan-review prepare|reduce|end`) and the round-brief template's unbounded form. Test-first, in a
fixture repository: three rounds to alignment with fix commits recorded; a refused lens-set change; a refused
unchanged candidate; a lens checkout that moved; the non-converging message; an ended review with a reason at its cap.

### P5. The plan write and the delivery file

`run_delivery.py` (`build_plan`, `lead-init`, `lead-setup`, `lead-plan approval`, `lead-close`'s archive) and
`references/config.md`. Test-first: a frozen plan with `planReview` passes `check-plan`; a sweep and `lead-plan
approval` after `lead-init` keep `planReview` and `approved.review`; freezing refuses an open review; an oversized
plan is refused; `lead-setup` drafts approvals and out-of-scope from a plan shaped like the pilot's, and `--force`
keeps them; `lead-plan approval` commits and refuses a dirty checkout.

### P6. Text and registry

`delivery-views` and `run-delivery` SKILL.md, `references/adopting.md` (plan review before `lead-init`, marking
blocking approvals), catalog and profile descriptions, and output digests through `update-provenance.py`. Sensor-only.

## Dependency Graph

- P1 → P2 → P3; P1 and P2 → P4 → P5; P6 after P3 and P5.
- P3 and P4 touch disjoint files, except that both read P2's module; they can run in parallel after P2.
- **Shared:** `provenance.lock.json`, through the writer only, per AGENTS.md; the page-test lane's `node_modules`; the
  serial test modules.

## Representative End-to-End Scenarios

1. **No plan review, no blocking approval:** every existing golden is unchanged.
2. **Three rounds to alignment,** driven through `plan-review prepare|reduce` from a plan shaped like the pilot's: five
   lenses; rounds raise 4, 1 and 0 blocking findings; each fixed closure carries the next round's commit. The line
   reads "after 3 review rounds · every lens aligned", and the fold shows the track and each round's card.
3. **Blocking approvals from the plan:** `lead-setup` drafts three approvals from the plan's approval section; the lead
   marks them blocking. The flag names them, and the calm line returns only after `lead-plan approval` marks each
   done. The plan survives every rebuild in between.
4. **A review that isn't converging:** round 3 raises a new finding with open findings not lower; `reduce` says so;
   `end` records the reason; the line reads "ended without alignment", and the fold shows the reason.
5. **A refused freeze:** `lead-init` refuses while the last round has a lens asking for changes.
6. **A review at its caps:** 32 rounds with full text stays readable; the page clips the oldest texts and keeps every
   verdict.

## Repository Sensors

- `validate-registry.py` and the registry tests;
- the full suite through `scripts/run-tests.py`;
- the page-test lane (jsdom, Node 20 or newer);
- `update-provenance.py --check`;
- `views_common.py check-plan` on every plan fixture.

The page goldens compare whole pages, so a base that moves under P3 is re-checked before hand-off.

## Risks and Mitigations

- **Clutter:** C5 keeps one line open and everything else folded; a page test enforces it. Only blocking approvals
  flag.
- **The view and the producer drift:** C4's one projection; C7's size and validator checks on every write.
- **A plan that the page can't read:** C2's aggregate cap and C7's size check; C5's clipping.
- **A dropped review on rebuild:** C7 makes the store a `build_plan` source, and P5 tests every rebuild path.
- **Hostile text in findings:** text-only rendering, as for story rounds.
- **Lens checkouts left behind:** C6's removal after the end checks.

## Approval Handoffs

1. This plan's approval by the operator after its lenses align.
2. C7's strictness: freezing refuses an open plan review. The operator may prefer a warning.
3. Which release batch carries it, per AGENTS.md.

## Observable Finish Line

1. A fixture delivery driven through `lead-setup`, `plan-review prepare|reduce` and `lead-init` yields a `plan.json`
   that passes `check-plan`, with `planReview` whose fixed closures carry their closing commits, and with approvals
   and out of scope drafted from the plan.
2. Its line map shows the approval line, the "Plan review (3 rounds)" fold per C5, and "Approvals are blocking work"
   until each blocking approval is marked done; a sweep in between keeps all of it.
3. Every existing golden and page test passes unchanged.
4. Every sensor above is green.
