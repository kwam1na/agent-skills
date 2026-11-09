---
reviewSlug: lead-hand-work
---

# Take the hand-work out of leading a delivery

Epic V26-3250: the lead-side hand-work, races and silent gaps the V26-3169 lead logged, as the five backlog items still under the epic plus
one split from them. Each item's tracker scope and acceptance criteria bind what it does. This plan decides only what the items leave open:
the choices two items share, order, file ownership while two other deliveries hold some of the same files, and how the work reaches `main`.

## Finish line

- Each item is merged to `main` through the merge queue, or closed with a recorded reason.
- `main`'s CI on the last merged commit passes on the Ubuntu job and `cut-release`, with the run linked.
- The full local suite passes on macOS with Python 3.11 at the commit the release candidate is tagged on.
- The release candidate V26-3309 names is tagged, published and applied here, and this repository's binding declares the check catalogue
  and its guards.
- No finding is deferred (operator ruling, 2026-11-08): every finding any review in this delivery raises, at any severity, is fixed in
  the story that found it, and no follow-up item is filed for it. The epic and its items are closed with this evidence.

## Out of scope

- The other backlog epics and the items they hold: release safety (V26-3266), posture readers (V26-3267), the harness runtime (V26-3268),
  and the related epics V26-3305, V26-3306 and V26-3307, with the items the epic's relations name that are theirs.
- The items the epic lists as already tracked elsewhere: V26-3170, V26-3201, V26-3069 and V26-3070.
- A final (non-candidate) release. V26-3309's candidate is promoted at a later batch boundary, as `docs/product-generation.md` says.

## Decisions

1. **V26-3276 takes the delivery-file path; V26-3256 owns the one-command add.** A story in the delivery file may carry a `why`.
   Registration, from `lead-brief`, `lead-reconcile` or `lead-assign`, copies it into the `added` change, and refuses a story added after the
   plan froze that has none, naming its key and the fix. V26-3256's `lead-plan change --kind added` adds the whole story in one command and
   requires `--why`, which it writes into the delivery file's story, so both of V26-3276's scope paths exist and neither leaves a change with
   no reason. V26-3276 goes first, so V26-3256's command is built on the refusal and tested against it.
2. **The plan format `lead-setup` reads is this plan's.** Each story section opens with `Lane N, tier T, ...`, with `after KEY[, KEY]` for its
   order, and names its files after `Files:`. The "Units and order" table's `Owns` column gives each lane's files. V26-3256's acceptance row
   sets up a copy of this plan and its delivery file is compared, lanes, `owns`, criteria and dependencies, with the one written by hand.
   Criteria come from each tracker item's Acceptance Criteria section through the tracker snapshot, when the binding declares one, and from
   the plan's story section otherwise. `plan-work`'s template documents the format (the item's compounding note).
3. **`next` reads a story's dependencies without the tracker.** `lead-setup` writes each story's `after` keys into the delivery file, and
   run-delivery hands them to `next` as `work-hints/1` blockers, so a story is ranked after its dependencies even when the tracker snapshot
   has no `blockedBy`.
4. **A no-change hand-off never enters the merge queue (V26-3263).** `handoff --no-change --evidence` needs no branch, change request or
   review round. It records the base head the evidence ran at, and `lead-accept` checks that head is on the base and the evidence names
   each criterion. The record holds the story as settled with no change, which is not removed: the close and the `stories` finish line
   count it, and the views show "closed, no change needed" with the evidence. No base commit is made, by the lane or the lead.
5. **`readyPullRequests` is the lead's grant, written by `lead-setup` (V26-3257).** `--authority` accepts it beside `push` and
   `draftPullRequests` and writes it into `authority`. The brief still says lanes never mark a pull request ready, and `push` and `open-pr`
   are unchanged: only `lead-accept` reads the grant.
6. **The default delivery (V26-3257).** A lead command with no `--delivery` takes the delivery whose record is open in this checkout, or
   else the only delivery file whose delivery isn't closed, and refuses otherwise, naming the open ones. With three deliveries' files on
   `main` now, this is checked against a fixture holding a closed, an open and an unrelated delivery.
7. **The check catalogue is additive (V26-3259).** `select --checks` takes comma-separated catalogue ids as well as today's JSON list, so
   existing selections and bindings stay valid. Always-selected guards are added to every lead selection, and a schema's readers are named
   in the binding, never inferred. The base-head warning reads a result the record already holds for that head, the post-merge
   `fullHealth` run's, and the lane names that source in its hand-off. This is a minor, additive change to the binding schema.
8. **V26-3309 is the release and its adoption, by the lead.** The binding validates against the installed generation's schema, which
   refuses unknown members, so the catalogue can be declared here only once a release carrying V26-3259 is applied. After every story
   merges, the lead runs the full suite on macOS with Python 3.11, tags the next candidate on a commit CI passed as the tip of a push to
   `main`, applies it, then lands the binding, its pin in `tests/test_own_deliveries.py` and `docs/own-deliveries.md` through the queue. The
   tag and the apply are agreed with the leads of the deliveries running then, whose supervisors restart on the new generation with
   `lead-run --resume`.

## Units and order

Two lanes, and one lead unit. Four of the five items change `run_delivery.py`, and no two lanes hold one file at a time, so lane 1 holds
the run-delivery chain throughout. Lane 2 holds the state tool, the queue and the views for V26-3259, then hands them to lane 1 for the two
items that span both.

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3276, V26-3256 | `skills/run-delivery/` (script, templates, `SKILL.md`, references), `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery_plan_review.py`, `tests/test_run_delivery_tracker.py`, `skills/plan-work/`, `docs/solutions/`, `catalog.json`, `profiles/*.json` |
| 1 | 2 | V26-3259 | `skills/orchestrate-work/` (scripts, `SKILL.md`, references), `tests/test_orchestration_state.py`, `tests/test_merge_queue.py`, `tests/test_lead_wait.py`, `skills/delivery-views/`, `tests/test_delivery_views_page.py`, `tests/delivery_views_page/`, `tests/fixtures/delivery-views/` |
| 2 | 1 | V26-3263, V26-3257 | lane 1's files, plus lane 2's once V26-3259 merges |
| 3 | lead | V26-3309 | `.agents/orchestration.json`, `tests/test_own_deliveries.py`, `docs/own-deliveries.md`, `.agent-skills/`, `.agents/skills/`, `.claude/skills/` |

**Moving ownership.** A file passes when the item holding it merges. The lead rewrites `lanes.*.owns` at the hand-over and pushes only
while no candidate is synced or checking. `provenance.lock.json` moves to the lane being accepted immediately before each `lead-accept`.
Each item's test-pin updates belong to it, including the `schemas.json` shape pins and the tracker updates table if a journal kind is added.
Once V26-3259 merges, lane 2 holds nothing, and the lead rests it and records why: no unblocked work in this epic needs files lane 1 doesn't own.

**Other deliveries on `main`.** V26-3304 and V26-3267 run at the same time, each with its own queue. V26-3304's lane 1 holds
`skills/run-delivery/` and `tests/test_run_delivery.py`, and its lane 2 holds `tests/test_own_deliveries.py`. V26-3267 changes
`run_delivery.py`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py` and orchestrate-work's `SKILL.md` and guide. The
leads agreed that V26-3304 merges its run-delivery items first. After that, whoever is ready goes next. No lead queues a candidate on a shared
file while another delivery's candidate touching it is queued. Each lead tells the others when a story merges. `provenance.lock.json` is
regenerated by the writer at sync and never merged by hand. A lane works and reviews freely meanwhile: only the hand-off to the queue waits.

**Shared resources.** No lane runs a `lead-*` command from the main checkout, and no test reaches `.orchestration/` or `.agents/deliveries/`
outside its own folder. A row that starts a supervisor records its pids and stops them in cleanup, and a module that can't share the
machine is marked `run_serially`.

## Stories

### V26-3276. Don't register a story added mid-delivery without a reason

Lane 1, tier 1, not blocked. Test-first, per the item and Decision 1. Rows try each entry point, `lead-brief`, `lead-reconcile` and
`lead-assign`. With the story's `why` in the delivery file, the `added` change carries it. Without one, after the plan froze, each refuses,
naming the story's key and the fix. A story present at `lead-init` is not an added change and needs no `why`. `doctor --delivery` names a
reason-less `added` change already in a record, and is clean once `lead-plan why` records one. `SKILL.md` step 3 and
`references/adopting.md` describe the path. Files: `run_delivery.py`, its `SKILL.md` and `references/adopting.md`, `tests/test_run_delivery.py`,
`tests/test_run_delivery_plan_review.py` if the plan chapter's rows move. Sensors: `tests/test_run_delivery.py`,
`tests/test_run_delivery_plan_review.py`, `scripts/validate-registry.py`.

### V26-3256. Draft lanes, ownership, criteria and order from the plan at setup

Lane 1, tier 1, after V26-3276. Test-first, per the item and Decisions 1 to 3. The acceptance row sets up a copy of this plan, with a
tracker snapshot carrying each item's Acceptance Criteria, and compares every story's lane, criteria and `after` keys, and each lane's
`owns`, with the expected values, with no hand edit. A row with no tracker snapshot drafts criteria from the plan's story sections. A
plan section with no lane line leaves that story `unassigned` and says so. `next` ranks a story after its dependencies when the tracker
snapshot has no `blockedBy`, proved red without the hints. `lead-plan change --kind added --subjects KEY --title T --text C --why W` adds the
plan heading, the delivery-file story with its `why`, and the planned entry in one command, and `lead-assign` then accepts the story; with
no `--why` it refuses. `plan-work`'s template documents the plan format. Files: `run_delivery.py`, its references, `skills/plan-work/`,
`tests/test_run_delivery.py`, and, with the lead's say, any corpus test that pins `plan-work`'s template wording. Sensors: `tests/test_run_delivery.py`,
`tests/test_orchestration_state.py` (read only, for `next`), `scripts/validate-registry.py`.

### V26-3259. Make the lead's check selections cheaper to write and harder to get wrong

Lane 2, tier 1, not blocked. Test-first, per the item and Decision 7. The binding schema gains a catalogue of named checks,
always-selected guards, and each schema's readers. `select --checks run-delivery,corpus-guards` resolves ids to the catalogue's entries, and
an unknown id is refused, naming it. A guard is in every lead selection, including one that names none. A selection for a story changing
`schemas.json` includes the readers the binding maps to it, captured plan fixtures included, without the lead naming them. `select`
warns, without refusing, when a selected check failed at the current base head, and is silent when it passed or never ran there. A JSON
list as today still selects exactly what it lists, plus the guards. The queue runs a selection by ids the same as one by full entries.
`orchestration-guide.md` documents the catalogue. Files: `delivery_state.py`, `merge_queue.py`, `references/schemas.json`, the guide,
`tests/test_orchestration_state.py`, `tests/test_merge_queue.py`, and `tests/test_registry.py` pins if the schema's shape pins move (which
then moves to lane 2 with the lead's say). Sensors: `tests/test_orchestration_state.py`, `tests/test_merge_queue.py`,
`scripts/validate-registry.py`. Compounding: the solution note the item names, select by what reads a change.

### V26-3263. Let a lane close a story that needs no change, with its evidence

Lane 1, tier 2, after V26-3256 and V26-3259. Test-first, per the item and Decision 4. A row hands off a story whose criteria already hold
with `handoff --no-change --evidence`, with no branch or change request, then `lead-accept` settles it, and the close condition and the
`stories` finish line count it. A row proves no base commit is made, by comparing the base ref before and after. A no-change hand-off at a
head that is not on the base, or with evidence that names no criterion, is refused. The views show "closed, no change needed" with the
evidence, pinned in the page tests. Files: `run_delivery.py`, `delivery_state.py`, `render_state.py`, `skills/delivery-views/`, their
tests. Sensors: `tests/test_run_delivery.py`, `tests/test_orchestration_state.py`, `tests/test_delivery_views_page.py`.

### V26-3257. Fix the lead commands' defaults, refusals and grants that cost round trips

Lane 1, tier 2, after V26-3263. Test-first: each of the item's six points has a row that fails before its fix (Decisions 5 and 6). The
default-delivery row runs from a fixture holding a closed, an open and an unrelated delivery. A missing delivery file's refusal names the
open delivery and says when the argument looks like a story key. `lead-setup --authority push,draftPullRequests,readyPullRequests` writes
all three, and the brief still says lanes never mark ready. `lead-plan change --kind finish --text` updates the line's text. `ack` with no
`--version` acks the outbox's own version and range, and a refusal names the outbox's version. For `wait`, the lane finds why its output
file held more than one document and makes one run write one document. If more than one is the design, it documents the stream and ships
a reader that a row uses. Files: `run_delivery.py`, `delivery_state.py`, `tests/test_run_delivery.py`,
`tests/test_orchestration_state.py`, `tests/test_lead_wait.py`, and the references that name these commands. Sensors: those three test
modules, `scripts/validate-registry.py`.

### V26-3309. Adopt the check catalogue and always-selected guards in this repository's binding

Lead, tier 3, after V26-3257 and V26-3259. Per Decision 8: the full local suite on macOS with Python 3.11, the next release candidate tagged
on a `main` commit CI passed, published, verified and applied here. Then a candidate through the queue declares the catalogue and guards in
`.agents/orchestration.json`, pins them in `tests/test_own_deliveries.py` test-first, and updates `docs/own-deliveries.md`'s "Checks".
Files: those three, and the generation the apply writes. Sensors: `tests/test_own_deliveries.py`, `scripts/validate-registry.py`,
`run_delivery.py doctor`.

## Execution posture

`test-first` for every item. Lanes work to the focused lane profile: no full suite in a lane. The queue runs the selected checks on the
synced head, `fullHealth` runs after each merge, and the lead runs the full suite before tagging.

## Approval handoffs

- The plan itself, before `lead-setup`.
- The release candidate's tag and its apply here (V26-3309), under the operator's grant for this delivery, agreed with the other running
  deliveries' leads.
- Nothing else is outward-facing beyond the merge queue's own merges, which the binding already authorizes.

## Tracking

The epic and its six items are in Linear. The lead posts at each milestone with the host's Linear tools and moves items to their final
state.
