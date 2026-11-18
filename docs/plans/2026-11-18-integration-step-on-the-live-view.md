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

1. **The record carries the integration.** `lead-init` writes `meta.integration = {base, target, remote}` from the
   delivery file's `baseBranch` and `integrationTarget` and the binding's remote, and only when there is a target. `lead-setup --integration` already
   drafts both. A record without it is a delivery without an integration step, and every reader treats it so.
   `line_map_data` passes it through unchanged.
2. **Four new kinds, all story-altitude, with the delivery's own key as subject.** In `journal-kinds.json`, each with
   words and vectors:
   - `integration.merged` `{target, targetHead, head, conflicts}`: the target merged into the integration candidate.
     `head` is the merge commit, `conflicts` the paths resolved by hand (possibly empty).
   - `integration.suite` `{head, result, seconds, budgetSeconds?}`: one `lead-suite` run. `result` maps the run's exit:
     0 is `passed`; 3 is `over-budget` only when the run's argv carries `--budget FILE` and that file reads as
     `suite-budget/1`: every test passed and the run went over, which doesn't hold a release; every other exit,
     including a 3 without such a budget, is `failed`, since exit 3 means something else to another repository's
     suite; and `unreadable` is a run whose checkout or prepare step
     failed before the suite started. `budgetSeconds` is that file's `wallSeconds`, and absent otherwise. The flow log line carries the same `result`, so `over-budget` and `unreadable` are new
     values there too.
   - `integration.requested` `{changeRequest, head}`: the change request into the target, opened at `head`.
   - `integration.landed` `{target, head}`: that change request merged, `head` the target's new head.
   `review.round` gains an optional `head`, the candidate the round read. A round under the delivery's key is an
   integration review round.
3. **Who writes them.** `lead-suite` journals `integration.suite` when the record has `meta.integration`, besides its
   flow log line, which keeps its fields and carries the result Decision 2 names. A new lead command, `lead-integrate`, journals the rest:
   - `lead-integrate merged --head H [--conflict PATH]...` reads the target and its head from the record and the
     merge's second parent.
   - `lead-integrate round --round N --head H --verdict LENS=approve|changes... --findings P0,P1,P2,P3
     [--started T] [--finished T]` writes `review.round` with the delivery's key. `startedAt` and `finishedAt` default
     to the command's own time.
   - `lead-integrate requested --change-request URL --head H` and `lead-integrate landed --head H`.
   Each fetches `<remote>/<target>` first, refuses on a record without `meta.integration`, and refuses a head that
   isn't a commit in the lead checkout, journalling nothing. The orchestration guide's integration recipe names each
   command at its step, and says to record `requested` again when a fix moves the change request's head.
4. **The current head is the newest head an integration fact names.** The integration facts are `integration.merged`,
   `integration.suite`, `integration.requested`, and `review.round` under the delivery's key. The order is the
   journal's, not commit ancestry. A suite, a round or a request is superseded when its head is not the current head,
   so a fix recorded after a round strikes the round through, and a catch-up after a fix strikes both. A merge is
   superseded only by a later merge, since every head after it still holds it: the target in stays filled while the
   latest merge stands, and the drift (Decision 6) says when a new one is due. A fix the lead has not yet recorded
   supersedes nothing until the next fact names its head. `integration.landed` ends the integration and supersedes
   nothing.
5. **The headline.** While `meta.integration` is set, every piece has landed and no `integration.landed` exists,
   `drawNow`'s title reads in two beats: "N pieces landed beside <target>." and, muted, "Now it rejoins." The sentence
   under it says how far the target moved and how often the lead caught up: "<target> moved C commits while this
   worked. The lead has caught up K times, and the gap is G." C and G come from the payload (Decision 6), K counts
   `integration.merged`. When the payload has no drift, the sentence leaves out C and G. After `integration.landed`,
   the title reads "Delivered." and the closed views take over as today.
6. **The drift is computed at render time.** `render.py` adds `integration.drift = {moved, behind}` to the payload:
   `moved` is `git rev-list --count <base head at record.started>..<remote>/<target>` and `behind` is
   `git rev-list --count <current head>..<remote>/<target>`, from the repository the renderer runs in, with a 5-second
   timeout. Before any integration fact exists, the current head is `meta.baseHead`. The counts are as fresh as the
   last fetch of the target, which each `lead-integrate` makes. When git is unavailable, times out, a ref is missing or
   the call fails, `drift` is absent, and the view says nothing of it. The hosted projection drops `drift`, and the
   page reads only `meta.integration` and the journal besides it.
7. **The line.** `mergeDrawing` keeps the lanes and the join. With `meta.integration`, the join is labelled with the
   base branch, and the trunk runs on past it through four stops: "<target> in", "suite", "review" and "change
   request", then bends into the target. The target is drawn beneath, from the join to the end. A stop is filled when
   its latest fact stands (Decision 4) and passed: a merge; a suite `passed` or `over-budget`; a round where every
   lens approves; a request. A stop whose standing fact did not pass is ringed in the danger colour (a suite `failed`
   or `unreadable`) or the warning colour (a round with any `changes` verdict). The first stop that is neither filled
   nor ringed is ringed in the accent as the next to happen, and the rest are hollow. A superseded fact leaves its stop
   hollow. The finish post moves to the target's end.
8. **The ledger.** A new block, "Before it rejoins", under the line, with one row each for Caught up, Suite, Review
   and Rejoin. Each row says what the latest fact of its kind shows and names its head in the mono face, struck
   through when the head is superseded. Suite says `failed`, `unreadable` or `over-budget` in those words, with the
   seconds against the budget when it has one. Review shows each lens's latest verdict as a dot and says which lenses
   ask for changes. A suite counts as passing for Rejoin when it is `passed` or `over-budget`, and never when it is
   `failed` or `unreadable`. Rejoin is ready only when a passing suite and an all-approve round stand at the same, current head:
   it then names the change request, or says one can open, and that the merge is the operator's. Otherwise it names
   what is missing: a passing suite at the current head, an approving round at it, or both at one head. A row with no
   fact yet says what will make it. Without `meta.integration`, or after `integration.landed`, the block isn't drawn.
9. **What a finish line has to do with it.** Nothing changes in how finish lines are met. With `meta.integration`, the
   journey's share is (landed pieces + met finish lines + filled stops) / (pieces + finish lines + 4), counting a stop
   as Decision 7 fills it. So the bar does not read full before the work is on the target, and a superseded stop takes
   its share back. After `integration.landed` all four stops count.

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

In `tests/test_run_delivery.py`, for the record and the commands:

- `lead-init` on a delivery file with `integrationTarget` writes `meta.integration` with base, target and remote; one
  without writes none.
- `lead-suite --at H` on such a record journals `integration.suite` with H and its result, for each exit: 0 `passed`,
  3 under a `suite-budget/1` `--budget` file `over-budget` with its `wallSeconds`, 3 with no `--budget` `failed`, 1 `failed` (journalled before the command exits
  non-zero), and a prepare step that fails `unreadable`. The flow log line carries the same result each time. On a
  record without `meta.integration`, it journals nothing, and its flow log line is as before apart from the result
  values.
- `lead-integrate merged --head M --conflict a.py --conflict b.py` journals `integration.merged` with the target's head
  read from M's second parent. A head that isn't a two-parent commit is refused.
- `lead-integrate round`, `requested` and `landed` each journal their exact entry: kind, subject the delivery's key,
  and data `{round, head, verdicts, findings, startedAt, finishedAt}`, `{changeRequest, head}` and `{target, head}`
  with the target read from `meta.integration`. `round` is refused for a verdict outside `approve` and `changes`.
- Each `lead-integrate` subcommand refuses a head that isn't a commit, and every one refuses on a record without
  `meta.integration`, each journalling nothing.
- Each entry the commands write validates against `journal-kinds.json`.
- Round trip: the record those commands produce, read through `line_map_data`, renders the stop states the page rows
  below expect.

In `tests/test_delivery_views_render.py`, for the payload:

- In a fixture repository where the integration base moved after `record.started`, the target gained commits after
  the last catch-up, and `<remote>/<target>` is ahead of a stale local branch, `drift` is `{moved, behind}` with exact,
  pairwise different values, counted against the remote-tracking ref.
- With no integration fact yet, `behind` counts from `meta.baseHead`.
- A missing target ref, and a git call that times out, each leave `drift` absent.
- The hosted projection carries no `drift` and no `review.round` `head`, and registers the four kinds, publishing their
  ids and kinds only.

Page rows, in a new `tests/delivery_views_page/line-map-integration.test.mjs`:

- Headline: with every piece landed and no `integration.landed`, the two beats with the target's name; with one piece
  still open, today's "N of M pieces have landed."; with `integration.landed`, "Delivered." and no ledger.
- The sentence with drift and two `integration.merged` entries reads exactly "main moved C commits while this worked.
  The lead has caught up 2 times, and the gap is G."; without drift it leaves the counts out.
- The line draws four stops past the join and the target beneath.
- Supersession, each asserting stop states and struck heads:
  - merged at M, then a suite at a later non-merge H2: the target in stays filled and Caught up is not struck.
  - merged, suite and a round at H1, then a suite at H3 (a recorded fix): the round's head is struck, the review stop
    hollow, the target in filled.
  - A round as the newest fact strikes an earlier suite.
  - A suite at H2 (a fix), then a merge at H3: both the suite and any earlier round are struck, and so is the earlier
    merge's Caught up head.
  - A request at H1, then a suite at H2: the request is struck and its stop hollow.
- Outcomes at the current head: a `failed` suite rings the suite stop in danger and Rejoin names a passing suite as
  missing; a round with one `changes` verdict rings the review stop in warning and Review names the lens; an
  `over-budget` suite fills the stop and says so, and with an all-approve round at the same head makes Rejoin ready; an
  `unreadable` suite rings the stop in danger, says `unreadable`, and Rejoin names a passing suite as missing; a passing suite and an all-approve round at one head make Rejoin
  ready, and at different heads it names both at one head as missing.
- The journey's share: exact numerator and denominator before any stop, with two stops filled, after a later fact
  supersedes a filled stop, and after `integration.landed`.
- A `review.round` under the delivery's key on a record without `meta.integration` draws nothing.
- Every existing golden case renders unchanged. A new `integration` golden case pins the page whole.

In `tests/fixtures/journal-kinds/vectors.json`: each new kind, and `review.round` with and without `head`, and one with
a `head` that isn't a commit, rejected.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3589 | `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`, `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/journal-kinds.json`, `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/delivery-views/assets/`, `skills/delivery-views/scripts/render.py`, `skills/delivery-views/references/hosted-snapshot.schema.json`, `skills/delivery-views/scripts/public_projection.py`, `tests/test_run_delivery.py`, `tests/test_journal_kinds.py`, `tests/test_journal_shape.py`, `tests/fixtures/journal-kinds/`, `tests/test_delivery_views_render.py`, `tests/test_delivery_views_hosted_projection.py`, `tests/delivery_views_page/`, `tests/fixtures/delivery-views/` |

One lane, one story. `catalog.json`, `profiles/*` and `provenance.lock.json` follow the repository's rules for a
releasable skill change: the lane updates the catalog entry, every selected profile and the digests in the same change.

## Stories

### V26-3589. Show a delivery's integration step on the live view

Lane 1, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/orchestrate-work/references/journal-kinds.json`, `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/references/orchestration-guide.md`, `skills/delivery-views/assets/line-map.js`,
`skills/delivery-views/assets/line-map.css`, `skills/delivery-views/scripts/render.py`,
`skills/delivery-views/references/hosted-snapshot.schema.json`,
`skills/delivery-views/scripts/public_projection.py`, `tests/test_run_delivery.py`, `tests/test_journal_kinds.py`, `tests/test_journal_shape.py`, `tests/fixtures/journal-kinds/vectors.json`,
`tests/test_delivery_views_render.py`, `tests/test_delivery_views_hosted_projection.py`,
`tests/delivery_views_page/` and `tests/fixtures/delivery-views/line-map/`.

Implement Decisions 1 to 9. Name `lead-integrate` and `lead-suite`'s entry in run-delivery's `SKILL.md` and at each
step of the guide's integration recipe.
