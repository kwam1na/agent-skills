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
- The epic's own finish line in Linear matches what this delivery delivers. Its clause "the lead pushes no ownership commit to the base,
  and holds the queue by hand only for an integration step" belongs to V26-3251 and V26-3253, which are V26-3307's. The lead moves that
  clause to V26-3307's description and removes it from V26-3250's, with a comment on both naming the move, before the epic closes.
- No finding is deferred (operator ruling, 2026-11-08): every finding any review in this delivery raises, at any severity, is fixed in
  the story that found it, and no follow-up item is filed for it. The epic and its items are closed with this evidence.

## Out of scope

- The other backlog epics and the items they hold: release safety (V26-3266), posture readers (V26-3267), the harness runtime (V26-3268),
  and the related epics V26-3305, V26-3306 and V26-3307, with the items the epic's relations name that are theirs, V26-3251 and V26-3253
  among them.
- The items the epic lists as already tracked elsewhere: V26-3170, V26-3201, V26-3069 and V26-3070.
- A final (non-candidate) release. V26-3309's candidate is promoted at a later batch boundary, as `docs/product-generation.md` says.

## Decisions

1. **V26-3276 takes the delivery-file path; V26-3256 owns the one-command add.** A story in the delivery file may carry a `why`.
   Registration, from `lead-brief`, `lead-reconcile` or `lead-assign`, copies it into the `added` change, and refuses a story added after the
   plan froze that has none, naming its key and the fix. The plan freezes at `lead-init`, so a story present then is not an added change.
   V26-3256's `lead-plan change --kind added` adds the whole story in one command and requires `--why`, which it writes into the delivery
   file's story, so both of V26-3276's scope paths exist and neither leaves a change with no reason. V26-3276 goes first, so V26-3256's
   command is built on the refusal and tested against it.
2. **The plan format `lead-setup` reads is this plan's, and this plan keeps to it.** The grammar:
   - A story is a `### KEY. Title` heading under `## Stories`. Its section's first sentence is `Lane N, tier T, not blocked.` or
     `Lane N, tier T, after KEY.`, with several keys written `after KEY and KEY.` or `after KEY, KEY and KEY.`. `Lane N` gives lane
     `lane-N`. A section with no such sentence leaves the story `unassigned`, and `lead-setup` says so.
   - A sentence starting `Files:` lists the story's files as backticked repository paths, comma-separated, with `and` before the last.
     A path ending `/` names a folder and becomes `folder/*`.
   - The `## Units and order` table has the columns `Tier`, `Lane`, `Items, in order` and `Owns`. `Owns` is backticked repository paths
     in the same form. Setup reads each lane's lowest-tier row as its `owns`. A later row lists only the files the lane gains when an
     earlier item merges, which the lead applies at that hand-over.
   - Criteria come from each tracker item's Acceptance Criteria section, carried by the tracker snapshot as a new optional `criteria`
     member of `work-snapshot/1` items, when the binding declares a tracker snapshot. Without one, `lead-setup` leaves the story's criteria
     empty and says so.
   V26-3256's acceptance row sets up a copy of this plan, with a tracker snapshot carrying each item's criteria. It compares each story's
   lane, `after` keys and criteria with the committed delivery file, and each lane's `owns` with this table's lowest-tier rows, written
   out in the row as explicit path lists. It reads nothing the lead's ownership moves rewrite, so a later move can't turn it red. `plan-work`'s template
   documents the grammar with an example, and a row runs `lead-setup` on that example.
3. **`next` reads a story's dependencies without the tracker.** `lead-setup` writes each story's `after` keys into the delivery file.
   `work-hints/1` items gain an optional `blockers` member, run-delivery passes the delivery file's `after` keys through it, and `next`
   reads it beside the tracker's `blockedBy`. A dependency stops blocking once the record holds it merged or settled, or the tracker
   snapshot holds it completed. V26-3256 makes all of this, in tier 2, once lane 2's files pass to lane 1, and V26-3263 pins the settled
   case once settling exists.
4. **A no-change hand-off never enters the merge queue (V26-3263).** `handoff --no-change --evidence` needs no branch, change request or
   review round. It records the base head the evidence ran at. `lead-accept` checks that head is an ancestor of the current base, which
   may have moved since, and that the evidence names every one of the story's criteria by id, refusing with the ids missing. A story with
   no criteria is refused, naming it and the fix: record its criteria first. The record
   holds the story as settled with no change, which is not removed: the close and the `stories` finish line count it, and the views show
   "closed, no change needed" with the evidence. No base commit is made, by the lane or the lead.
5. **`readyPullRequests` is the lead's grant, written by `lead-setup` (V26-3257).** `--authority` accepts it beside `push` and
   `draftPullRequests` and writes it into `authority`. The brief's list of what lanes may do leaves it out and says lanes never mark a
   pull request ready. `push` and `open-pr` are unchanged, so `open-pr` still opens a draft under the grant: only `lead-accept` reads it.
6. **The default delivery (V26-3257).** A lead command with no `--delivery` takes, in this order: the delivery whose record is open in
   this checkout's state directory; else the only delivery file whose delivery isn't known to be closed; else it refuses, naming every
   delivery not known to be closed. A delivery is known to be closed when this checkout's flow log holds its `closed.json`, or, where the
   binding declares a tracker snapshot, when that snapshot, run with `{parent}` filled by the delivery's own key, shows the parent
   completed or canceled. `work-snapshot/1` gains an optional parent-state member, which `snapshot.py` emits from the parent issue its
   Linear and Jira queries already fetch. When the command fails, or there is no tracker, the delivery stays not known to be closed. Any
   checkout can read the second source, so a delivery closed from another checkout counts as closed here.
7. **The check catalogue is additive (V26-3259).** `select --checks` takes comma-separated catalogue ids as well as today's JSON list, so
   existing selections and bindings stay valid. Always-selected guards are added to every lead selection. A schema's readers are named in
   the binding, never inferred from what a test mentions. The base-head warning is per check: the merge queue records the base head
   beside each base re-run's result on the record, and `select` warns, naming the check and the head, when the record holds a failing base
   re-run of a selected check id at the current base head. A passing re-run there gives no warning. A head this delivery's queue never re-ran a check on, such as one another
   delivery's merge made, gives no warning, and `select` says that nothing is known there. This is a minor, additive change to the
   binding schema.
8. **V26-3309 is the release, by the lead, and the adoption, by lane 2.** The binding validates against the installed generation's
   schema, which refuses unknown members, so the catalogue can be declared here only once a release carrying V26-3259 is applied. After
   every other item merges, the lead runs the full suite on macOS with Python 3.11, tags the next candidate on a commit CI passed as the
   tip of a push to `main`, and applies it. Lane 2 then lands the binding, its pin in `tests/test_own_deliveries.py` and
   `docs/own-deliveries.md` through the queue. The tag and the apply are agreed with the leads of the deliveries running then, whose
   supervisors restart on the new generation with `lead-run --resume`.

## Units and order

Two lanes. Four of the five items change `run_delivery.py`, and no two lanes hold one file at a time, so lane 1 holds the run-delivery chain
throughout. Lane 2 holds the state tool, the queue and the views for V26-3259, then hands them to lane 1 for the three items that span
both, and takes the binding for V26-3309 at the end.

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3276 | `skills/run-delivery/`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery_plan_review.py`, `tests/test_run_delivery_tracker.py`, `skills/plan-work/`, `docs/solutions/`, `catalog.json`, `profiles/` |
| 1 | 2 | V26-3259 | `skills/orchestrate-work/`, `tests/test_orchestration_state.py`, `tests/test_orchestration_render.py`, `tests/test_merge_queue.py`, `tests/test_lead_wait.py`, `tests/test_registry.py`, `skills/delivery-views/`, `tests/test_delivery_views_page.py`, `tests/test_delivery_views_snapshots.py`, `tests/delivery_views_page/`, `tests/fixtures/delivery-views/` |
| 2 | 1 | V26-3256, V26-3263, V26-3257, V26-3331, V26-3310 | `skills/orchestrate-work/`, `tests/test_orchestration_state.py`, `tests/test_orchestration_render.py`, `tests/test_merge_queue.py`, `tests/test_lead_wait.py`, `tests/test_registry.py`, `skills/delivery-views/`, `tests/test_delivery_views_page.py`, `tests/test_delivery_views_snapshots.py`, `tests/delivery_views_page/`, `tests/fixtures/delivery-views/` |
| 3 | 2 | V26-3309 | `.agents/orchestration.json`, `tests/test_own_deliveries.py`, `docs/own-deliveries.md` |

**Moving ownership.** A file passes when the item holding it merges. The lead rewrites `lanes.*.owns` at the hand-over and pushes only
while no candidate is synced or checking. `provenance.lock.json` moves to the lane being accepted immediately before each `lead-accept`.
Each item's test-pin updates belong to it, including the `schemas.json` shape pins and the tracker updates table if a journal kind is added.
Lane 1 finishes V26-3276 before V26-3259 merges and then waits, recorded as resting on V26-3259. Between V26-3259 and V26-3309, lane 2
holds nothing and rests, recorded the same way. The release and its apply (`.agent-skills/`, `.agents/skills/`, `.claude/skills/`) are the
lead's, between V26-3257's merge and V26-3309's start.

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
`lead-assign`. With the story's `why` in the delivery file, the `added` change carries it. Without one, after `lead-init`, each refuses,
naming the story's key and the fix. A story added to the delivery file between `lead-setup` and `lead-init` is frozen with the plan and
needs no `why`, pinned by a row. `doctor --delivery` names a reason-less `added` change already in a record, and is clean once `lead-plan why`
records one. `SKILL.md` step 3 and `references/adopting.md` describe the path. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `skills/run-delivery/references/adopting.md`, `tests/test_run_delivery.py` and
`tests/test_run_delivery_plan_review.py`. Sensors: `tests/test_run_delivery.py`, `tests/test_run_delivery_plan_review.py`,
`scripts/validate-registry.py`.

### V26-3259. Make the lead's check selections cheaper to write and harder to get wrong

Lane 2, tier 1, not blocked. Test-first, per the item and Decision 7. The binding schema gains a catalogue of named checks,
always-selected guards, and each schema's readers. `select --checks run-delivery,corpus-guards` resolves ids to the catalogue's entries, and
an unknown id is refused, naming it. A guard is in every lead selection, including one that names none. A selection for a story changing
`schemas.json` includes the readers the binding maps to it, captured plan fixtures included, without the lead naming them, and a test that
mentions `schemas.json` but isn't mapped to it is not selected. The warning rows: a selected check whose base re-run failed at the current
base head is warned about, by id and head; the same check passing its base re-run at this head gives no warning and no "nothing is
known"; the same check failed at an older base head gives no warning; a check the story didn't select that failed at this head gives no
warning; and a head with no re-run says nothing is known. A row in `tests/test_merge_queue.py` drives a candidate whose selected check
fails, so the queue re-runs it on the base, and asserts the record's re-run entry carries the check's id, its verdict and the base head
the re-run checked out, which differs from the candidate's synced head. It then runs `select` against that record and asserts the
warning names that head. A JSON list as today still selects exactly
what it lists, plus the guards. The queue runs a selection by ids the same as one by full entries. `orchestration-guide.md` documents the
catalogue. Files: `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/orchestration-guide.md`,
`tests/test_orchestration_state.py`, `tests/test_merge_queue.py` and `tests/test_registry.py`. Sensors: `tests/test_orchestration_state.py`,
`tests/test_merge_queue.py`, `tests/test_registry.py`, `scripts/validate-registry.py`. Compounding: the solution note the item names,
select by what reads a change, in `docs/solutions/`, which the lead moves to lane 2 for it.

### V26-3256. Draft lanes, ownership, criteria and order from the plan at setup

Lane 1, tier 2, after V26-3276 and V26-3259. Test-first, per the item and Decisions 1 to 3. The acceptance row sets up a copy of this plan,
with a tracker snapshot carrying each item's criteria, and compares every story's lane, criteria and `after` keys with this delivery's
committed delivery file, and each lane's `owns` with the plan table's lowest-tier rows written out in the row (Decision 2). A row in
`tests/test_delivery_views_snapshots.py` gives the snapshot producer a tracker item whose description has an Acceptance Criteria section,
and asserts the new member holds exactly that section's criteria; an item without the section yields none, and both snapshots validate
against `work-snapshot/1`. A row with no tracker snapshot leaves criteria empty and says so. A plan section with no lane
sentence leaves that story `unassigned` and says so. A row runs `lead-setup` on `plan-work`'s template example. `next` ranks a story after
its open dependencies when the tracker snapshot has no `blockedBy`, proved red without the hints. Allow-side rows: the same fixture with
the dependency merged in the record, then with it completed in the tracker snapshot, ranks the dependent story unblocked, ahead of a story
whose dependency is still open. `lead-plan change --kind added --subjects KEY --title T --text C --why W` adds the plan heading, the
delivery-file story with its `why`, and the planned entry in one command, and `lead-assign` then accepts the story; with no `--why` it
refuses. Files: `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/references/adopting.md`,
`skills/plan-work/SKILL.md`, `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/delivery-views/scripts/snapshot.py`, `tests/test_run_delivery.py`, `tests/test_orchestration_state.py` and
`tests/test_delivery_views_snapshots.py`. Sensors: `tests/test_run_delivery.py`, `tests/test_orchestration_state.py`,
`tests/test_delivery_views_snapshots.py`, `scripts/validate-registry.py`. Compounding: `plan-work`'s template documents the plan format.

### V26-3263. Let a lane close a story that needs no change, with its evidence

Lane 1, tier 2, after V26-3256. Test-first, per the item and Decision 4. A row hands off a story whose criteria already hold with
`handoff --no-change --evidence`, with no branch or change request, then `lead-accept` settles it, and the close condition and the
`stories` finish line count it. A row proves no base commit is made, by comparing the base ref before and after. A row advances the base
after the hand-off, and `lead-accept` still settles the story. A head that is not an ancestor of the base is refused. A story with two
criteria and evidence naming one is refused, naming the missing criterion, and evidence naming both is accepted. A story with no
criteria is refused, naming it. In `tests/test_orchestration_state.py`, a dependency the record holds as settled with no change unblocks
its dependent story in `next`, ahead of a story whose dependency is still open. The views show "closed,
no change needed" with the evidence, pinned in `render_state.py`'s output and in the page tests. Files:
`skills/run-delivery/scripts/run_delivery.py`, `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/orchestrate-work/scripts/render_state.py`, `skills/orchestrate-work/references/schemas.json`, `skills/delivery-views/`,
`tests/test_run_delivery.py`, `tests/test_orchestration_state.py`, `tests/test_orchestration_render.py` and
`tests/test_delivery_views_page.py`. Sensors: `tests/test_run_delivery.py`, `tests/test_orchestration_state.py`,
`tests/test_orchestration_render.py`, `tests/test_delivery_views_page.py`.

### V26-3257. Fix the lead commands' defaults, refusals and grants that cost round trips

Lane 1, tier 2, after V26-3263. Test-first: each of the item's six points has a row that fails before its fix (Decisions 5 and 6). The
default-delivery rows: one closed and one not-closed delivery file selects the not-closed one; two not-closed files and no record open
here, each with its parent started in its tracker snapshot, refuses, naming both as not known to be closed; two not-closed files with
one record open here selects that one; a delivery closed from another checkout, with its file on the base, no local `closed.json` and its
parent completed, counts as closed, so the one other delivery is selected; a parent canceled counts the same; and a snapshot command
that fails leaves its delivery not known to be closed. These rows drive the tracker snapshot through the snapshot producer's output,
not a hand-built snapshot. A row in `tests/test_delivery_views_snapshots.py` asserts the producer emits the parent's state, and the
snapshot validates against `work-snapshot/1`. A missing delivery file's refusal names the
open delivery and says when the argument looks like a story key. `lead-setup --authority push,draftPullRequests,readyPullRequests` writes
all three, replacing the row that pins today's refusal. The brief's list of lane grants leaves `readyPullRequests` out, and `open-pr`
leaves the pull request a draft under the grant, each pinned by a row. `lead-plan change --kind finish --text` updates the line's text.
`ack` with no `--version` acks the outbox's own version and range, and a refusal names the outbox's version. For `wait`, the lane finds why
its output file held more than one document and makes one run write one document. If more than one is the design, it documents the stream
and ships a reader that a row uses. Files: `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/templates/`,
`skills/run-delivery/references/adopting.md`, `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/references/schemas.json`,
`skills/delivery-views/scripts/snapshot.py`, `tests/test_run_delivery.py`, `tests/test_orchestration_state.py`, `tests/test_lead_wait.py`
and `tests/test_delivery_views_snapshots.py`. Sensors: `tests/test_run_delivery.py`, `tests/test_orchestration_state.py`,
`tests/test_lead_wait.py`, `tests/test_delivery_views_snapshots.py`, `scripts/validate-registry.py`.

### V26-3331. Fix main: the outside-adopter line map tests fail since the tracker snapshot reads the parent's state

Lane 1, tier 2, after V26-3257. Added on 2026-11-09 (plan change): V26-3257's merge (415fe33) turned `main` red. Three
`tests/test_delivery_views_adopter.py` rows fail because the tracker snapshot step leaves no file, most likely the Jira parent fetch
V26-3257 added and the fixture's replay lacks. Test-first, per the item: recorded red at 415fe33; the cause confirmed before the fix; a
row pins the producer's contract when the parent fetch fails (the children's snapshot with the parent state unknown, per Decision 6, or a
loud failure). Goes ahead of V26-3310, as its own candidate. Files: `skills/delivery-views/scripts/snapshot.py`,
`skills/orchestrate-work/scripts/delivery_state.py`, `tests/test_delivery_views_adopter.py` and `tests/test_delivery_views_snapshots.py`.
Sensors: `tests/test_delivery_views_adopter.py`, `tests/test_delivery_views_snapshots.py`, `scripts/validate-registry.py`.

### V26-3310. Let lead-plan run from a lead's own worktree, not only a checkout on the base branch

Lane 1, tier 2, after V26-3257. Added on 2026-11-09 (plan change): with three deliveries on one machine, each lead works from its own
worktree on `lead/<key>`, and `lead-plan change` and `lead-plan approval` refuse there by branch name. Test-first, per the item. Rows: from
a worktree on `lead/<key>` at the base tip, `lead-plan approval` and `lead-plan change` each commit the delivery file and the commit
reaches the base; a worktree behind the base, one ahead with another lead's unpushed commit, and a dirty one are each refused, naming the
reason; a checkout on the base branch behaves as today. `references/adopting.md` describes one lead worktree per delivery. Files:
`skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/references/adopting.md`, `tests/test_run_delivery.py` and
`docs/solutions/`. Sensors: `tests/test_run_delivery.py`, `tests/test_run_delivery_plan_review.py`, `scripts/validate-registry.py`.
Compounding: the solution note the item names.

### V26-3309. Adopt the check catalogue and always-selected guards in this repository's binding

Lane 2, tier 3, after V26-3257 and V26-3259. Per Decision 8, it starts once the lead has tagged, published, verified and applied the
release candidate. Test-first: a `StandingBinding` row in `tests/test_own_deliveries.py` pins the catalogue's ids and the guard list, and
runs `delivery_state.py select --checks` with catalogue ids against the committed binding, asserting the ids resolve to the catalogue's
entries with the guards added. Then `.agents/orchestration.json` declares them and `docs/own-deliveries.md`'s "Checks" section says the
lead selects by catalogue id and names the guards. Files: `.agents/orchestration.json`, `tests/test_own_deliveries.py` and
`docs/own-deliveries.md`. Sensors: `tests/test_own_deliveries.py`, `scripts/validate-registry.py`, `run_delivery.py doctor`.

## Execution posture

`test-first` for every item. Lanes work to the focused lane profile: no full suite in a lane. The queue runs the selected checks on the
synced head, `fullHealth` runs after each merge, and the lead runs the full suite before tagging.

## Approval handoffs

- The plan itself, before `lead-setup`.
- The release candidate's tag and its apply here (V26-3309), under the operator's grant for this delivery, agreed with the other running
  deliveries' leads.
- The edit to the epic's finish line in Linear, under the same grant.
- Nothing else is outward-facing beyond the merge queue's own merges, which the binding already authorizes.

## Tracking

The epic and its eight items are in Linear. The lead posts at each milestone with the host's Linear tools and moves items to their final
state.
