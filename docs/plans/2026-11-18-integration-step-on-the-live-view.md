---
reviewSlug: integration-step-on-the-live-view
---

# Show a delivery's integration step on the live view

A delivery on an integration base lands its stories on `integrate/<name>`. Then the lead takes it into the target:
merges the target in, runs the full suite, has its own changes reviewed, opens the change request and merges it. The
live view shows none of this. Once the last story lands, it says "Every piece has landed." and its line ends at one
finish post, while the work has not reached the target. In V26-3585 the view said that while main had moved 20
commits, two conflicts were being resolved, a suite result had gone stale and the integration review was on round 3.

The approved design, from the operator's review of three mockups on 2026-11-18: the two-beat summary, the simple line
map carried on past the integration base, and a ledger of proofs pinned to their heads.

Checked against `main` at `e935fe4` on 2026-11-18:

- The view's payload is `line_map_data` (`skills/delivery-views/scripts/render.py`), the record document with its
  story, waits, runs, reviews, plan and needs. The only base it carries is `state.meta.baseHead`.
  `integrationTarget` and `baseBranch` live only in `.agents/deliveries/<key>.json` and in `run_delivery.py`.
- The headline is `drawNow` in `skills/delivery-views/assets/line-map.js`, and the progress line is `journey()`. The
  line chapter's map is `drawLine` and `mergeDrawing`, which stop at one finish post. The finish lines are `drawLeft`,
  read as `state.finish[].met`.
- `lead-sync --merge` journals `base.moved` `{from, to, merged, why}` through `complete_base_move_locked`, with no
  conflicts. `lead-suite` journals nothing, and writes the flow log's `<delivery>.suite.jsonl`. A `review.round` under
  the delivery's own key validates, but the view reads rounds only per piece, so it is never drawn. No kind records the
  change request into the target or its merge.
- The guide's "Reviewing an integration base for the default branch" merges the target into a separate
  integration-review branch, not into the integration base. Its head moves with each fix and each catch-up.

## Finish line

- Both plan lenses align.
- On a delivery with an integration target, the live view shows the summary, the line past the integration base, and
  the ledger, each read from the record. On one without, it renders as before, and every existing golden is unchanged.
- The story has landed on `main` through the merge queue. The full local suite has passed on macOS with Python 3.11
  at the base head after it landed, run with `--workers 4 --budget tests/suite-budget.json`, inside the budget.
- Tagging a release is not this delivery's. The change is additive, a minor version.

## Decisions

1. **The record carries the integration.** `lead-init` writes `meta.integration = {base, target}` from the delivery
   file's `baseBranch` and `integrationTarget`, and only when there is a target. `lead-setup --integration` already
   drafts both. A record without it is a delivery without an integration step, and every reader treats it so.
   `line_map_data` passes it through unchanged.
2. **Four new kinds, all story-altitude, with the delivery's own key as subject.** In `journal-kinds.json`, each with
   words and vectors:
   - `integration.merged` `{target, targetHead, head, conflicts}`: the target merged into the integration candidate.
     `head` is the merge commit, `conflicts` the paths resolved by hand (possibly empty).
   - `integration.suite` `{head, result, seconds, budgetSeconds?}`: one `lead-suite` run. `result` is `passed`,
     `failed`, `over-budget` or `unreadable`, as the flow log line says.
   - `integration.requested` `{changeRequest, head}`: the change request into the target, opened at `head`.
   - `integration.landed` `{target, head}`: that change request merged, `head` the target's new head.
   `review.round` gains an optional `head`, the candidate the round read. A round under the delivery's key is an
   integration review round.
3. **Who writes them.** `lead-suite` journals `integration.suite` when the record has `meta.integration`, besides its
   flow log line, which is unchanged. A new lead command, `lead-integrate`, journals the rest:
   - `lead-integrate merged --head H [--conflict PATH]...` reads the target and its head from the record and the
     merge's second parent.
   - `lead-integrate round --round N --head H --verdict LENS=approve|changes... --findings P0,P1,P2,P3` writes
     `review.round` with the delivery's key.
   - `lead-integrate requested --change-request URL --head H` and `lead-integrate landed --head H`.
   Each refuses on a record without `meta.integration`, and refuses a head that isn't a commit in the lead checkout.
   The orchestration guide's integration recipe names each command at its step.
4. **The current head is the newest head an integration fact names.** A proof is superseded when its head is not that
   head. The order is the journal's, not commit ancestry, so a catch-up after a fix supersedes both. A fix the lead has
   not yet recorded supersedes nothing until the next fact names its head.
5. **The headline.** While `meta.integration` is set, every piece has landed and no `integration.landed` exists,
   `drawNow`'s title reads in two beats: "N pieces landed beside <target>." and, muted, "Now it rejoins." The sentence
   under it says how far the target moved and how often the lead caught up: "<target> moved C commits while this
   worked. The lead has caught up K times, and the gap is G." C and G come from the payload (Decision 6), K counts
   `integration.merged`. When the payload has no drift, the sentence leaves out C and G. After `integration.landed`,
   the title reads "Delivered." and the closed views take over as today.
6. **The drift is computed at render time.** `render.py` adds `integration.drift = {moved, behind}` to the payload:
   `moved` is `git rev-list --count <baseHead at record.started>..<target>` and `behind` is
   `git rev-list --count <current head>..<target>`, from the repository the renderer runs in, with a 5-second timeout.
   When git is unavailable, a ref is missing or the call fails, `drift` is absent, and the view says nothing of it. The
   hosted snapshot does not carry it.
7. **The line.** `mergeDrawing` keeps the lanes and the join. With `meta.integration`, the join is labelled with the
   base branch, and the trunk runs on past it through four stops: "<target> in", "suite", "review" and "change
   request", then bends into the target. The target is drawn beneath, from the join to the end. A stop is filled when
   its fact exists at the current head, ringed in the accent when it is the next to happen, and hollow otherwise. A
   superseded fact leaves its stop hollow. The finish post moves to the target's end.
8. **The ledger.** A new block, "Before it rejoins", under the line, with one row each for Caught up, Suite, Review
   and Rejoin. Each row says what the latest fact of its kind shows and names its head in the mono face, struck
   through when the head is superseded. Review shows each lens's latest verdict as a dot. Rejoin names the change
   request, or says it waits on the review and the suite at one head, and that the merge is the operator's. A row with
   no fact yet says what will make it. Without `meta.integration`, the block isn't drawn.
9. **What a finish line has to do with it.** Nothing changes in how finish lines are met. The journey's share counts
   the integration's four stops as pieces once `meta.integration` is set, so the bar does not read full before the
   work is on the target.

## Out of scope

- Recording the integration facts automatically from git or the forge. The lead runs `lead-integrate`, as the guide
  says.
- The hosted page. Its snapshot publishes each entry's id and kind, and registers the four new kinds; it draws nothing
  new.
- A delivery whose integration review is run by `review-prepare` on the delivery's key.
- Changing `lead-sync --merge`, which still journals `base.moved`. An integration base that moves through it before
  the integration step is outside the ledger.

## Approval Handoffs

- The operator approves this plan before `lead-setup`.
- The operator grants the lane `push` and `draftPullRequests`.

## Execution posture and sensors

Test-first. Sensors: `tests/test_run_delivery.py` for `lead-init`'s meta, `lead-suite`'s entry and `lead-integrate`;
`tests/test_journal_kinds.py`, `tests/test_journal_shape.py` and `tests/fixtures/journal-kinds/vectors.json` for the
kinds; `tests/test_delivery_views_render.py` for the payload and the drift; `tests/delivery_views_page/` for the page,
with a new golden case, `integration`, under `tests/fixtures/delivery-views/line-map/`;
`tests/test_delivery_views_hosted_projection.py` for the snapshot's kinds; `scripts/validate-registry.py` and
`scripts/update-provenance.py --check`.

## Scenarios

- `lead-init` on a delivery file with `integrationTarget` writes `meta.integration`; one without writes none.
- `lead-suite --at H` on such a record journals `integration.suite` with H and the result, and appends its flow log
  line as before. On a record without `meta.integration`, it journals nothing, as today.
- `lead-integrate merged --head M --conflict a.py --conflict b.py` journals `integration.merged` with the target's head
  read from M's second parent. A head that isn't a two-parent commit is refused. On a record without
  `meta.integration`, every `lead-integrate` subcommand is refused and journals nothing.
- `lead-integrate round` writes `review.round` with the delivery's key, its head, verdicts and findings, refused for a
  verdict outside `approve` and `changes`.
- The payload carries `integration` with drift from a fixture repository; with git failing, it carries no `drift`, and
  the page's sentence leaves the counts out.
- Page rows, in a new `line-map-integration.test.mjs`:
  - With every piece landed and no `integration.landed`, the title is the two beats with the target's name; with one
    piece still open, it is today's "N of M pieces have landed."
  - With `integration.landed`, the title is "Delivered.".
  - The line draws four stops past the join and the target beneath; a stop with a fact at the current head is filled,
    the next ringed, the rest hollow.
  - A suite at H1, then `integration.merged` at H2: the suite row's head is struck through and its stop hollow. A
    later `integration.suite` at H2 fills it again.
  - The ledger's Review row shows the latest round's lens dots and its head.
  - The journey's share with `meta.integration` counts the four stops.
- Every existing golden case renders unchanged. The new `integration` golden case pins the page whole.
- The hosted projection registers the four kinds and publishes their ids and kinds only.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3589 | `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`, `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/journal-kinds.json`, `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/delivery-views/assets/`, `skills/delivery-views/scripts/render.py`, `skills/delivery-views/references/hosted-snapshot.schema.json`, `tests/test_run_delivery.py`, `tests/test_journal_kinds.py`, `tests/test_journal_shape.py`, `tests/fixtures/journal-kinds/`, `tests/test_delivery_views_render.py`, `tests/test_delivery_views_hosted_projection.py`, `tests/delivery_views_page/`, `tests/fixtures/delivery-views/` |

One lane, one story. `catalog.json`, `profiles/*` and `provenance.lock.json` follow the repository's rules for a
releasable skill change: the lane updates the catalog entry, every selected profile and the digests in the same change.

## Stories

### V26-3589. Show a delivery's integration step on the live view

Lane 1, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/orchestrate-work/references/journal-kinds.json`, `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/references/orchestration-guide.md`, `skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/line-map.css`, `skills/delivery-views/scripts/render.py`,
`skills/delivery-views/references/hosted-snapshot.schema.json`, `tests/test_run_delivery.py`,
`tests/test_journal_kinds.py`, `tests/test_journal_shape.py`, `tests/fixtures/journal-kinds/vectors.json`,
`tests/test_delivery_views_render.py`, `tests/test_delivery_views_hosted_projection.py`,
`tests/delivery_views_page/` and `tests/fixtures/delivery-views/line-map/`.

Implement Decisions 1 to 9. Name `lead-integrate` and `lead-suite`'s entry in run-delivery's `SKILL.md` and at each
step of the guide's integration recipe.
