---
title: Shared delivery collaboration - Plan
type: feat
date: 2026-11-24
topic: shared-delivery-collaboration
reviewSlug: shared-delivery-collaboration
execution: code
---

# Shared delivery collaboration - Plan

## Goal

Let two or more engineers, each driving their own agent session on their own
machine, run one delivery together:

- co-author its plan;
- raise and settle feedback on it;
- approve it jointly;
- after approval, either stay co-leads with split lanes or hand back to one lead.

Each session sees the others' work within seconds, without a manual push and
pull. A delivery with one engineer behaves exactly as it does today.

Operator decisions, 2026-11-24:

- Both engineers drive deliveries and both edit the plan.
- After approval, both shapes are supported: co-leads with split lanes, and hand
  back to one lead. Either can turn into the other mid-delivery.
- **Synced state:** per-author partitions, pushed as one git ref per author and
  composed when read. Git is the only transport. Local state stays authoritative.
- **Plan review:** run plan review with every lens raising every finding it can
  state in the current round. The aim is the fewest rounds.

## Current state (evidence)

**Record files and writers**

- The record is `state.json` plus `journal.jsonl` in one local state directory
  (`skills/orchestrate-work/scripts/delivery_state.py:68-90`). Both are written
  under `fcntl` locks with atomic replace.
- State changes are JSON merge patches pinned to a version (`patch()`,
  `delivery_state.py:2741-2814`).
- `WRITERS = ("lead","runner","lane")` (`delivery_state.py:99`). Field ownership is
  held in module tuples:
  - `LANE_FIELDS`, `RUNNER_*`, `HANDOFF_QUEUE`, `SELECT_QUEUE`, `REPAIR_QUEUE`, `TOOL_OWNED` (`:101-151`);
  - enforced by `ownership_violations` and `lane_violations` (`:1182-1272`).
- No person identity exists anywhere. "lead" is a role label any caller can pass.
  `lead-plan approval --who`, `--granted-by` and `change --by` are free text.
  `sessions.json` maps host session ids to roles (`run_delivery.py:11857-11906`).

**Journal ids**

- Journal ids must "run in sequence from 1" (`delivery_state.py:692-693`).
- `because` and `answers` must name earlier ids (`:760`).
- Integer, dense ids are read by:
  - `wait --after` (`:6478-6483`) and the recovery intent (`:4559`, `:4690`);
  - `meta.closing.*.entry` and `regionsSettled`;
  - the narrator (`narrator.py:107,169,177,250-286`);
  - `line-map.js` (`first.id === 1` as the delivery start, id sorts, review boundaries);
  - `render.py:537-543`;
  - `hosted.py:383-390`;
  - the tracker publisher's markers and `journalThrough` (`tracker_publisher.py:126-214,747-784`);
  - the peer cursor (`run_delivery.py:14432-14476`).

**Writer pinning**

- Writers are pinned to the applied archive digest (`require_writer`,
  `delivery_state.py:810-824`).
- They change only through the parked transition `lead-upgrade-writer`
  (`run_delivery.py:8774-8810`).
- `docs/solutions/integration-issues/upgrade-the-lead-before-writing-a-shared-record-2026-11-13.md`:
  a reader from an older generation refuses newer fields, so writers of one record
  must share a generation.

**Peers**

- Peer deliveries are read from absolute local paths through
  `<git-common-dir>/agent-skills/deliveries/<key>.json`, with local pid liveness
  (`run_delivery.py:8039-8091,14422-14476`).
- Nothing reads a record over a network. The only remote read is the sibling
  hosted snapshot.

**Planning phase**

- Before `lead-setup` there is only the per-machine plan-review store
  `.orchestration/plan-review/<slug>/`.
- `lead-setup` and `lead-init` adopt that store by moving it
  (`run_delivery.py:2593-2627`).
- No record exists until `lead-init`, which seeds `state.json`, journals
  `record.started`, writes `plan.json` and starts the supervisor (`:7971-8037`).

**Plan editing commands**

- `lead-plan change|approval` commit in a lead checkout. That checkout is on the
  base or `lead/<key>`, with only this delivery's delivery-file and plan commits
  ahead (`run_delivery.py:6436-6479`;
  `docs/solutions/best-practices/one-lead-worktree-per-delivery-2026-11-09.md`).
- Neither command pushes.

**Region claims**

- Region claims live in `/laneRegions`.
- Markdown sections resolve by heading (`region_ownership.py:625-667`).
- A plan file can be claimed. Nothing checks the lead's own plan commits against
  claims.

**Supervisor**

- The supervisor (`lead-run`) runs one pass every `supervisor.passSeconds`
  (default 60).
- Its children are the live view and `merge_queue.py serve`.
- It publishes hosted and tracker updates (`run_delivery.py:14350-14420,14515-14616`).

**Views and hosted**

- Views read `{state, journal, stats}`. The record must exist for every view but
  `runs`.
- `delivery-plan/1` requires `approved` (`views_common.py:724-725`).
- The hosted snapshot schema keeps `plan` and `document` as generic records
  (`hosted-snapshot.schema.json`, `properties.plan` is `null | record`).
- The hosted publisher needs `state.json` and `journal.jsonl`
  (`hosted.py:1033-1047`). Its identity binds the delivery key permanently.

**Knowledge library**

- Not searched: no knowledge tool is configured in this session.

## Scope

In scope:

1. **Seats.** One writer of a shared record per checkout, with a generated id,
   a display label, and admission by the `delivery` holder.
2. **Partition sync.** One git ref per author, carrying that author's journal
   segment and state slice. Peers' partitions are ingested into the local record.
3. **Plan sync.** Co-authored plan text on a shared branch, composed section by
   section, with a watch stream any host session can follow.
4. **Authority scopes.** `delivery`, `queue`, `lane:<id>` and `plan:<section>`.
   Each has one holder and can be transferred or requested.
5. **Planning record.** A record that exists from the first draft, adopted by
   `lead-setup` and `lead-init`, so the views and the hosted page work while
   planning.
6. **Feedback and joining.** Feedback facts anchored to the plan, a `join`
   catch-up brief, and a watch stream over the shared record.
7. **Joint approval.** Every approver seat endorses the same plan commit.
8. **Co-lead runtime.** Per-lane assignment and acceptance, one queue holder, and
   a queue handover.
9. **Views.** Draft plan, scopes, feedback and peer freshness on the line map and
   the hosted page.
10. **Docs.** An adopter guide, skill pages and orchestration guide.

Out of scope:

- A hosted "ref moved" doorbell, feedback written from the browser, or any write
  route on the hosted service.
- Binding a seat to a person: signed partition commits checked against admitted
  keys (D19).
- Merge assistance for overlapping edits to one plan section, beyond a refusal
  that names the section.
- Peer reads across repositories, more hosts, Windows validation.
- Any change to `hosted-delivery-api/1`, `hosted-delivery-snapshot/3` or
  `delivery-line-map/3`.

## Decisions recorded here

- **D1. Opt-in, and an unshared record is unchanged.** A record without
  `meta.collab` behaves exactly as today. Every existing test, golden and
  fixture passes unchanged, except the additive pins each story lists. Collaboration
  starts only when `plan-open` creates a shared record with its first seat. A record
  `lead-init` creates without `plan-open` is never shared, and an existing record
  never becomes shared.
- **D2. Seats, not people.**
  - **What a seat is:** one writer of a shared record, in practice one clone on
    one machine. The clone's worktrees (the lead worktree and its lanes' worktrees)
    share its git config, so they write as the same seat, and lane writes keep
    today's `lane` writer under it. The plan defines no person entity. One person
    on two machines holds two seats.
  - **Id:** generated when the seat is created, as `s-` plus 12 lowercase hex
    characters from `secrets`. It is kept in that checkout's local git config as
    `agent-skills.seat.<key>`. Nobody configures it.
  - **Label:** taken from `git config user.name` when the seat is created, falling
    back to the id, and editable with `collab label`. It is display only: no
    check, refusal or ownership rule reads it.
  - **Membership:**
    - `plan-open` creates the first seat and admits it;
    - `collab join` creates a seat and journals `seat.requested`;
    - the `delivery` holder journals `seat.admitted`, `{seat, label, approver}`,
      with `approver` true unless `--no-approver` is given.
    - `meta.collab.seats` is derived from those entries, never written directly.
  - **Refusals:**
    - on a shared record, any write from a checkout with no seat is refused with
      `collab.seat_missing`;
    - any write from a seat not yet admitted, other than its own `seat.requested`,
      is refused with `collab.seat_not_admitted`.
  - **Environment:** no environment variable sets or overrides the seat.
- **D3. Transport.**
  - **Where:** each author's partition is a commit chain at
    `refs/agent-skills/<key>/<seat>` on the binding's base remote.
  - **What a commit holds:**
    - `partition.json`, spec `delivery-partition/1`:
      - `seat`;
      - `generation`, `{releaseId, archiveSha256}`;
      - `through`, the last own seq;
      - `slice`, a map from JSON pointer to value;
    - `journal.jsonl`: the author's own-origin entries in seq order.
  - **Push:** fast-forward only, never forced. A rejected push re-reads the remote
    ref.
    - If the remote ref is not an ancestor of the local chain, the same id is
      writing from a second checkout, for example a copied one. The push is refused
      with `collab.seat_split`, and nothing is ingested from that ref.
  - **Admitted seats only:** a peer ref whose seat has no `seat.admitted` entry is
    fetched but ingested only for its `seat.requested` entry.
  - **Fetch:** peers' refs come down by explicit refspec into
    `refs/agent-skills-peers/<key>/<seat>`. No branch or tag namespace is used,
    so forges show nothing new and `git fetch` defaults are untouched.
- **D4. Journal ids stay local integers.**
  - **Origin field:** every entry of a shared record carries `origin`,
    `{seat, seq, seen}`. `seq` is dense from 1 per seat. `seen` maps each other
    seat to the last seq this seat had ingested.
  - **Local ids:** the local `id` keeps today's rule, dense from 1 in this
    machine's append order. Peer entries take the next local id when they are
    ingested.
  - **Causal ingest:** an entry is ingested only once every entry its `seen` names
    is present.
  - **Links:** `because` and `answers` are written by origin and translated to
    local ids on ingest, so "names an earlier id" still holds.
  - **Effect:** every integer-id reader listed above keeps working unchanged. Two
    machines' journals hold the same set of origins in an order consistent with
    causality. Local ids may differ between machines, and nothing compares them
    across machines.
  - **Schema:** `delivery-journal/2` gains `origin` as an optional member, so its
    version does not change. D5 keeps every writer on one generation.
- **D5. One generation per shared record.** A peer partition whose generation
  digest differs from the local writer pin is held, not ingested.
  - Needs you names it as `collab.generation_differs`.
  - It is ingested after the lagging machine runs its own `lead-upgrade-writer`.
  - Each machine's upgrade is its own parked transition. No machine upgrades
    another.
- **D6. Scopes have exactly one holder.**
  - **Rule:** only the holder writes a scope's fields and kinds, and only the
    holder transfers it. Anyone else journals `scope.requested`.
  - **Initial holder:** `plan-open` gives every scope to the seat it creates.
  - **Unclaimed sections:** a plan section nobody holds belongs to `delivery`.
  - **Auto-grant:** where `collab.autoGrant` is true in `.agents/run-delivery.json`,
    the `delivery` holder's supervisor grants a requested unclaimed section on its
    next pass.
  - **Why no voiding is needed:** each scope has one writer, so two machines can
    never act on a scope concurrently. The only case is a non-holder acting on a
    stale view, and that is refused locally before anything is written.
  - **Field map:** each scope's state fields are fixed by a table in
    `delivery_state.py`, written by COLLAB-3 and COLLAB-6. A field with no scope is
    tool-owned, as today.
- **D7. Ingesting a slice is a tool write.**
  - A new writer, `sync`, applies a peer's slice to the local `state.json`. It may
    set only the fields of the scopes that peer held at the slice's `through`.
  - The `patch` CLI refuses `--writer sync`.
  - On a shared record, `lead` writes are checked against the local seat's
    scopes. `runner` writes are allowed only on the `queue` holder's machine.
- **D8. Plan text sync.**
  - **Branch:** plan text lives on `plan/<slug>` until `lead-setup`, then on
    `lead/<key>`. Both branches are pushed to the base remote.
  - **Composing:** `plan-sync` composes section by section, not by git's
    three-way merge:
    - it resolves sections in base, ours and theirs with `region_ownership`'s
      Markdown rules;
    - if no section was changed on both sides, it writes theirs with our changed
      sections replaced;
    - otherwise it refuses with `plan.sections_overlap`, naming each section and
      both diffs.
  - **Scope check:** after COLLAB-3, a local change outside the sections the
    seat holds is refused before any push, with `plan.section_not_held` and the
    `scope.requested` command that asks for it.
  - **Retries:** a push rejected for being behind recomposes and retries, up to 5
    times, the bound `RECONCILE_ATTEMPTS` already uses.
  - **History:** commits carry each committer's own git identity, as today. Nothing is
    force-pushed.
- **D9. One watch stream, built for a host monitor.**
  - **Output:** `plan-watch` (COLLAB-1) and `collab watch` (COLLAB-5) print one
    line per new event and nothing else, and flush every line. Events include peer
    plan pushes and ingested peer journal entries.
  - **Exit:** they exit 0 at `--timeout` (default 1800, as `wait`) and 3 when
    `--until` matches nothing.
  - **Poll:** default 5 seconds, set by `collab.pollSeconds`, at least 2. A tick
    costs one `git fetch` process.
  - **Host-neutral:** Claude Code's monitor and a plain terminal read the stream the
    same way, and no host interface is called.
- **D10. The planning record.**
  - **Command:** `plan-open --plan P --key KEY`. The plan must be committed and
    carry `reviewSlug`. It creates and admits the first seat (D2).
  - **What it creates:** a record in the delivery's usual state directory for
    `KEY`, with `meta.phase: "planning"`, `meta.collab`, no planned work, and
    `record.started` as entry 1.
  - **Adoption:** `lead-setup` and `lead-init` adopt it in place. The journal,
    ids, partitions and hosted identity carry on, and `lead-init` sets
    `meta.phase: "delivery"`. The key is fixed at `plan-open`, so the hosted link
    survives approval.
  - **Single engineer:** a shared record with one admitted seat works, as a
    record others can join later. A plan nobody will share follows today's path,
    without `plan-open`.
- **D11. Joint approval.**
  - Each seat journals `plan.endorsed`, `{commit}`, against the plan commit it
    approves.
  - On a shared record, `lead-init` requires:
    - one endorsement from every admitted approver seat, naming the same commit;
    - that commit equal to the commit plan review aligned on;
    - no open blocking feedback.

    It refuses naming the first of these that is missing.
  - A new plan commit makes earlier endorsements stale. The views say so.
- **D12. Which machine runs what.**
  - Every seat runs a supervisor on its machine. It handles sync, the live
    view, peers and Needs you.
  - Only the `queue` holder's supervisor starts `merge_queue.py serve` and runs
    `fullHealth`.
  - Only the `delivery` holder's supervisor runs the hosted and tracker publishers.
  - On a shared record the tracker marker uses `<seat>.<seq>` in place of the
    local id. Its ledger keys by origin, so moving `delivery` never posts twice.
  - The machine ledger and the merge slot stay per machine.
- **D13. Queue handover is a parked transition.**
  - `collab give queue --to B` waits for an idle queue, meaning no check running
    and no merge in progress, up to `--wait` seconds.
  - It stops this machine's queue child, then journals `scope.transferred`.
  - B's supervisor starts its queue on the pass that ingests that entry.
  - Between the two, no machine serves the queue. The line map says
    "queue moving to B".
- **D14. Hosted pages without a version change.**
  - `delivery-plan/1` gains an optional `draft` member, `{commit, at}`. It
    replaces `approved` until the plan is approved, and exactly one of the two is
    present.
  - Snapshot v3 already carries `plan` and `document` as generic records, so
    `hosted-delivery-snapshot/3` and `delivery-line-map/3` stay.
  - A planning cut exists only with a publisher and service from this release. The
    docs already require the service bundle from the same release.
- **D15. Lanes are unchanged sessions.**
  - A lane belongs to the seat holding its `lane:` scope.
  - The assignment message names that seat's label. The lane reports to that
    seat's session, which accepts its hand-offs.
- **D16. Plan review is unbounded.** It runs until its lenses align, unless the
  operator ends it. This is the recorded decision from `$plan-work`.
- **D17. Release shape.**
  - The record shape and runtime commands change, so this ships first as a
    candidate, is applied in one adopter, and is promoted under AGENTS.md.
  - Each story refreshes its catalog entries, profiles and provenance digests in
    its own change.
- **D18. Suite budget.**
  - New tests reuse one fixture per class.
  - The two-machine fixture builds one bare remote and two clones once per class.
  - It is marked `run_quietly`, because it waits on real `git` and supervisor
    processes.
  - `tests/suite-budget.json` is not raised.
- **D19. Identity is trust-based.**
  - Anyone who can push to the base remote can write any `refs/agent-skills/...`
    ref. A seat id proves nothing about who wrote a partition, exactly as `--who`
    and `--granted-by` prove nothing today.
  - This delivery adds no authentication.
  - Binding seats to people means signed partition commits checked against
    admitted keys. That is a separate unit, out of scope here.

## Finish line

All of the following hold at one main-push commit:

1. **Two machines, end to end.** One test class (COLLAB-9) drives two clones of
   one bare remote, with separate state directories, through:
   - `plan-open` on the first clone, creating seat `a`;
   - `collab join` on the second, creating seat `b`, admitted by `a`;
   - concurrent disjoint plan edits that compose;
   - an overlapping edit refused with its section named;
   - a section requested and auto-granted;
   - feedback raised by `b` and settled by `a`;
   - both endorsements, and plan review aligned on that commit;
   - `lead-init` adopting the planning record with its hosted identity unchanged;
   - lanes split between `a` and `b`, each accepting its own lane's hand-off;
   - `a`'s queue merging `b`'s accepted candidate;
   - the queue handed to `b`, and `b` merging;
   - every scope handed back to `a`, and the close.

   At each step both machines' records compose to the same state and the same set
   of journal origins in a causal order. "The same state" ignores only the fields
   COLLAB-2 names as local.
2. **Unshared records unchanged.** Every existing test, line-map, history and runs
   golden, narrator story and hosted fixture passes without change, other than the
   pins the stories list.
3. **Refusals named.** Each refusal code in D2-D13 has a test that triggers it and
   checks that nothing was written.
4. **Views.** The line map and the hosted viewer render a new planning-record
   fixture and a co-lead fixture, with goldens captured by the existing recapture
   mechanism in their own commit.
5. **Docs and the full suite.**
   - Docs, skill pages and the orchestration guide have landed.
   - Catalog, profiles and provenance are refreshed.
   - The full local suite passes on macOS with Python 3.11 within
     `tests/suite-budget.json`.

Two real people on two real machines, the release candidate applied in one
adopter, and its promotion are operator acceptance. This delivery does not claim
them.

## Approvals

- Seats with generated ids, admitted by the `delivery` holder, with no person
  entity (D2): waiting.
- Identity stays trust-based, with no authentication (D19): waiting.
- Pushing `refs/agent-skills/<key>/<seat>` to the base remote (D3): waiting.
- No hosted snapshot or renderer version change (D14): waiting.
- Ships as a release candidate before the final (D17): waiting.

## Out of scope for this delivery

- A hosted doorbell or any hosted write route.
- Browser-written feedback.
- Binding seats to people with signed partitions (D19).
- Merge assistance for overlapping section edits.
- Windows validation.

## Sensors

From `AGENTS.md`, after `python3 scripts/bootstrap-test-environment.py`:

- `.venv/bin/python scripts/validate-registry.py`
- `.venv/bin/python tests/test_registry.py`
- `.venv/bin/python scripts/update-provenance.py --check`
- each story's named test modules, run alone first;
- the page lane through `tests/test_delivery_views_page.py` for COLLAB-7, on Node 24;
- the full suite once at the end:
  `.venv/bin/python scripts/run-tests.py --workers 4 --budget tests/suite-budget.json`,
  run through the lead's suite command.

Clear `skills/**/__pycache__` before `scripts/cut-release.py`.

## Shared resources

- **Git remotes.** Every multi-machine test builds its own bare remote and clones
  under its own temporary folder through `tests/quiet_git.py`. No fixed path or
  port is used.
- **Live view ports.** The supervisor's live view in COLLAB-8 and COLLAB-9 binds a
  derived port, as `tests.test_run_delivery_supervisor.LiveViewAddressTests`
  already does. These tests reuse that class's fixture, or are listed in the
  budget's `serial` map with their reason in the same change.
- **Merge slot.** The merge slot lives under the clone's git common directory, so
  the two simulated machines never share one.
- **Sensors compared to the merge base.** `claims-check`, `lead-claims` and the
  line-map goldens are compared to the merge base. Each story re-runs them on a
  fresh base before hand-off.

## Units and order

Lane 1 is the record layer, lane 2 the command layer and lane 3 the views.
`skills/run-delivery/scripts/run_delivery.py` and
`skills/orchestrate-work/scripts/delivery_state.py` are shared by region:

- Lane 1 holds `delivery_state.py` whole.
- Lane 2 holds `run_delivery.py` whole.
- Lane 1's two call sites in `run_delivery.py` (COLLAB-3's `patch_from` seat
  argument and COLLAB-6's supervisor role check) are carved to lane 1 with
  `lead-own --carve` when those stories are assigned.

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | COLLAB-2 | `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/collab.py`, `skills/orchestrate-work/references/`, `skills/orchestrate-work/SKILL.md`, `tests/test_collab_partitions.py`, `tests/test_orchestration_state.py`, `tests/test_journal_kinds.py`, `tests/test_journal_shape.py`, `tests/fixtures/journal-kinds/` |
| 1 | 2 | COLLAB-1 | `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/scripts/plan_sync.py`, `skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`, `tests/test_plan_sync.py`, `tests/test_run_delivery.py` |
| 2 | 1 | COLLAB-3 | `skills/orchestrate-work/scripts/region_ownership.py`, `tests/test_collab_scopes.py` |
| 2 | 2 | COLLAB-4 | `tests/test_planning_record.py`, `tests/test_run_delivery_plan_review.py` |
| 3 | 2 | COLLAB-5 | `skills/orchestrate-work/scripts/narrator.py`, `tests/test_collab_join.py`, `tests/test_lead_wait.py` |
| 3 | 1 | COLLAB-6 | `skills/orchestrate-work/scripts/merge_queue.py`, `tests/test_collab_colead.py` |
| 4 | 2 | COLLAB-8 | `skills/run-delivery/scripts/tracker_publisher.py`, `tests/test_run_delivery_tracker.py`, `tests/test_run_delivery_supervisor.py` |
| 4 | 3 | COLLAB-7 | `skills/delivery-views/`, `tests/fixtures/delivery-views/`, `tests/delivery_views_page/`, `tests/test_delivery_views_plan.py`, `tests/test_delivery_views_render.py`, `hosted/delivery-viewer/test/` |
| 5 | 2 | COLLAB-9 | `docs/collaboration.md`, `docs/host-adapters.md`, `README.md`, `tests/test_collab_two_machines.py`, `tests/suite-budget.json` |

Lane 1 registers every new journal kind, and every new wait kind, so lane 2 never
edits the kind registry, the wait words or the orchestration guide.
`tests/test_journal_shape.py` counts journal writes in both layers' scripts, so
its `INVENTORY` is shared by region: each story's entries are granted to its lane
with `lead-own --region --around` when it is assigned.

The stories are children of one epic, created through `$create-linear-ticket`
after this plan is approved. The `COLLAB-n` keys are placeholders. The tracker
keys replace them in one commit before `lead-setup`.

## Stories

### COLLAB-1. Sync and watch a co-authored plan

Lane 2, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/plan_sync.py`, `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`, `tests/test_plan_sync.py` and `tests/test_run_delivery.py`.

This story relieves the pain on its own and needs no record.

- **Commands:**
  - `plan-sync --plan P` composes and pushes `plan/<slug>`, or `lead/<key>` once a
    delivery file names the plan, by D8 without the scope check.
  - `plan-watch --plan P [--timeout S] [--until RE]` streams by D9.
- **Section rules:** reused from `region_ownership.markdown_block`; it is not
  reimplemented.
- **Settings:** `collab.pollSeconds` is added to the `run-delivery.json` schema and
  `references/config.md`.
- **Skill page:** a new `## Collaboration` section in `skills/run-delivery/SKILL.md`
  carries the rows. They come after every existing table, so the
  `lead-add-lane`-after-`lead-plan` row order pinned by `tests/test_lead_add_lane.py`
  is untouched.
- **Pins:**
  - `tests/test_run_delivery.py::RunDeliveryAdoptionGuideTests.test_every_command_the_guide_names_exists`:
    both commands appear in `--help` choices.
  - `test_every_shipped_file_is_listed_and_clean`: `plan_sync.py` is listed.
- **Scenarios:**
  - disjoint edits by two clones compose, including a section added by one side;
  - one section changed on both sides gives `plan.sections_overlap`, naming it,
    with both diffs and the local branch unchanged;
  - an edit at the end of one section and the heading of the next compose, which is
    the case git's three-way merge would conflict on;
  - a push behind the remote recomposes and succeeds; six straight rejections stop
    with a named refusal;
  - nothing is force-pushed, which the test checks from the remote's reflog;
  - `plan-watch` prints one line per peer push within two ticks, flushes each line,
    and exits 0 at its timeout.

### COLLAB-2. Seats, partitions and causal journal ingest

Lane 1, tier 1, not blocked. Characterization-first. Files: `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/collab.py`, `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/journal-kinds.json`, `skills/orchestrate-work/SKILL.md`, `tests/test_collab_partitions.py`, `tests/test_orchestration_state.py`, `tests/test_journal_kinds.py`, `tests/test_journal_shape.py` and `tests/fixtures/journal-kinds/`.

- **Characterize first:** journal validation, `because`/`answers` linking, `wait`
  and the writer pin on an unshared record. These tests stay unchanged
  through the story.
- **Record and journal:** `meta.collab` (`seats`, derived from the journal,
  `peers` with each peer's
  `through`, `fetchedAt` and `heldReason`) and the `origin` member (D4), each
  optional.
- **Partition and transport:** `collab.py` holds seat creation and lookup (D2),
  partition build
  and push, peer fetch, causal ingest with link translation, and the generation
  hold (D5). It journals only through `delivery_state`'s append functions.
- **The `sync` writer** (D7), and the state-tool subcommands `collab-push`,
  `collab-fetch` and `collab-ingest`.
- **Local fields:** which state fields are machine-local (`meta.updatedAt`,
  `version`, `meta.collab.peers`). The finish line's comparison ignores exactly
  these.
- **New kinds:**
  - `seat.requested`, with data `{seat, label}`;
  - `seat.admitted`, with data `{seat, label, approver}`, linked by `answers` to
    its request except for the first seat;
  - `partition.held`, with data `{seat, reason}`;
  - `partition.resumed`.
- **Pins:**
  - `tests/test_orchestration_state.py::test_schemas_are_keyed_by_name_use_urn_ids_and_declare_no_dialect`
    gains `delivery-partition/1`, making 14 names;
  - `ADOPTER_FILES` gains `scripts/collab.py`;
  - `tests/test_journal_kinds.py` `TABLE` and `ALTITUDES` gain the new kinds;
  - `tests/test_journal_shape.py` `SCRIPTS` gains `collab.py` and its `INVENTORY`
    counts are re-derived.
- **Scenarios:**
  - two records exchange partitions and reach the same origin set and state;
  - a peer entry whose `because` names the other side's entry links to the right
    local id;
  - with three seats, an entry waits until the entry its `seen` names is
    ingested;
  - a different generation digest is held, named, and ingested after the local
    writer upgrade;
  - a copied checkout writing as the same seat gets `collab.seat_split`, and its
    ref is never ingested;
  - a write with no seat, or from a seat not yet admitted, is refused with nothing
    written;
  - a requested but unadmitted seat's partition contributes only its
    `seat.requested` entry;
  - a seat's label changes nothing but display: the same record with labels
    swapped composes to the same scopes and ownership;
  - `patch --writer sync` is refused;
  - `wait --after` wakes on an ingested peer entry of a wait kind.

### COLLAB-3. Authority scopes and plan-section enforcement

Lane 1, tier 2, after COLLAB-2. Test-first. Files: `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/collab.py`, `skills/orchestrate-work/scripts/region_ownership.py`, `skills/orchestrate-work/references/`, `skills/orchestrate-work/SKILL.md`, `tests/test_collab_scopes.py`, `tests/test_journal_kinds.py` and `tests/test_journal_shape.py`.

- **Scope register:** the scopes of D6, derived from the journal. Who holds what
  is never stored as a second copy.
- **Kinds:**
  - `scope.transferred` (`scope`, `from`, `to`);
  - `scope.requested` (`scope`, `reason`);
  - `scope.declined`, linked by `answers` to its request.
- **Kinds registered here for lane 2's commands:**
  - `plan.endorsed` (`commit`);
  - `plan.seen` (`commit`);
  - `feedback.raised` (`anchor`, `text`, `blocking`);
  - `feedback.resolved` (`outcome` is `accepted`, `declined` or `answered`, plus
    `reason`), linked by `answers`.
- **Wait kinds:** `feedback.raised` and `scope.requested` join `WAIT_KINDS`, with
  their wait words. The orchestration guide's list is updated in this change,
  which keeps `test_the_guide_names_every_kind_that_ends_a_wait` green.
- **Field map:** `delivery` covers `finish`, `meta` fields that are neither tool
  nor runner fields, `extensions` and approvals. `plan:<section>` has no record
  fields. Lane and queue rows come in COLLAB-6.
- **Checks:**
  - `ownership_violations` checks `lead` writes against the local seat's
    scopes on a shared record (D7);
  - `delivery_state` exposes `holder(scope)` and `holds(seat, scope)` for
    run-delivery;
  - `plan_sync` gains the scope check of D8 through `region_ownership`'s section
    spans.
- **Admission:** only the `delivery` holder may journal `seat.admitted`, and
  `approver` changes only by a new `seat.admitted` from that holder.
- **State-tool subcommands:** `scope give|request|decline` and
  `seat admit [--no-approver]`. `--auto-grant` is read
  from the binding.
- **The carved call site** in `run_delivery.py` passes the seat through
  `patch_from`. Nothing else in `run_delivery.py` changes.
- **Scenarios:**
  - a non-holder's write to a `delivery` field is refused with
    `scope.not_held`, naming the holder;
  - only the holder can transfer; any other transfer is refused;
  - a seat admitted by a non-holder is refused with `scope.not_held`;
  - a request is auto-granted for an unclaimed section, and never for a held
    section or for `queue`;
  - a plan edit outside held sections is refused before any push, naming the
    request command;
  - an unshared record ignores scopes entirely, so its field-ownership tests
    are unchanged.

### COLLAB-4. The planning record and joint approval

Lane 2, tier 2, after COLLAB-1 and COLLAB-3. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`, `tests/test_planning_record.py` and `tests/test_run_delivery_plan_review.py`.

- **`plan-open`** (D10). It starts the supervisor in planning mode: sync, live view
  and hosted publishing, with no queue and no tracker.
- **Adoption:** `lead-setup` and `lead-init` adopt the planning record in place,
  beside the existing review-store adoption (`run_delivery.py:2593-2627`), which is
  unchanged.
- **`lead-plan endorse`** journals `plan.endorsed`, registered by COLLAB-3.
  `lead-init`'s joint-approval check follows D11.
- **Plan file:** `build_plan` writes a planning `plan.json` with `draft` (D14).
  Stories come from the plan headings through `lead-setup`'s existing parser.
- **Pins:** `PlanReviewWithoutDeliveryTests` stays unchanged. New adoption cases
  sit beside it.
- **Scenarios:**
  - `plan-open` creates exactly one admitted approver seat holding every scope;
  - `lead-init` refuses naming the missing endorsement, a stale endorsement, a
    commit plan review did not align on, or an open blocking feedback;
  - adoption keeps the journal ids, partitions and `hosted-identity.json`
    unchanged;
  - `meta.phase` moves to `delivery` exactly once.

### COLLAB-5. Feedback, join and the shared watch stream

Lane 2, tier 3, after COLLAB-4. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`, `skills/orchestrate-work/scripts/narrator.py`, `tests/test_collab_join.py` and `tests/test_lead_wait.py`.

- **Kinds:** `feedback.raised` and `feedback.resolved`, registered by COLLAB-3.
  Only the anchor's scope holder may resolve.
- **Commands:**
  - `collab feedback add|resolve`;
  - `collab give|request`, wrapping the state tool;
  - `collab join` creates a seat and requests admission on first use (D2). Every
    use prints, in this order: the narrator's story, the plan diff since this
    seat's last `plan.seen`, open feedback, and scopes held;
  - `collab label` changes this seat's label;
  - `collab watch` (D9), over the shared record.
- **Narrator:** words for the new kinds, and peer entries attributed to their
  seat's label.
- **Pins:** narrator story goldens are unchanged, because unshared records' journals
  have no new kinds.
- **Scenarios:**
  - `b`'s join wakes `a`'s `lead-wait` with the admission request;
  - feedback from `b` wakes `a`'s `lead-wait`;
  - a resolution by a non-holder is refused;
  - `join` after a fresh clone gives the full catch-up, and run again gives only
    what is new;
  - blocking feedback shows in Needs you, and releases once it is resolved.

### COLLAB-6. Lane and queue scopes in the record and the queue

Lane 1, tier 3, after COLLAB-3. Characterization-first. Files: `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/merge_queue.py`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/SKILL.md` and `tests/test_collab_colead.py`.

- **Characterize first:** the queue's serve loop and acceptance writes on an
  unshared record.
- **Field map rows:**
  - `lane:<id>` covers `lanes[id=…]` lead fields, the lead fields of queue elements
    whose lane is `<id>`, that lane's planned items, `laneOwnership[<id>]`, and
    `laneRegions` with that lane;
  - `queue` covers the runner's fields, and authorizes the `runner` writer on its
    holder's machine only.
- **Queue:** `merge_queue.py serve` refuses to start on a shared record unless the
  local seat holds `queue`, with `queue.not_held`.
- **Supervisor role check:** the carved call site in `run_delivery.py`.
- **Queue handover:** the state-tool half of D13. An idle check runs, then
  `scope.transferred`.
- **Scenarios:**
  - each lead writes its own lanes' rows and is refused on the other's;
  - a runner write on a non-holder machine is refused;
  - a handover with a check running waits, then times out naming the check;
  - after a handover the old machine's serve exits and the new one's starts.
  - Every existing merge-queue test passes unchanged.

### COLLAB-8. The co-lead runtime in run-delivery

Lane 2, tier 4, after COLLAB-5 and COLLAB-6. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/scripts/tracker_publisher.py`, `skills/run-delivery/SKILL.md`, `tests/test_run_delivery_tracker.py`, `tests/test_run_delivery_supervisor.py` and `tests/test_collab_colead.py`.

- **Lane commands:** `lead-assign`, `lead-accept`, `lead-own` and `lead-add-lane`
  check the lane's scope. The assignment message names the seat's label (D15).
- **Supervisor roles** follow D12.
- **Queue handover:** `collab give queue --to` runs the full handover of D13.
- **Tracker:** markers use `<seat>.<seq>` on shared records. Its ledger
  migration is none, because no shared record existed before.
- **Pins:** tracker publisher tests on unshared records are unchanged. New
  cases sit beside them.
- **Scenarios:**
  - `b` assigning a lane `a` holds is refused, naming `a`;
  - `delivery` moving from `a` to `b` posts nothing twice;
  - only the `delivery` holder's supervisor publishes hosted and tracker updates;
  - a supervisor restart keeps its roles.

### COLLAB-7. Planning, scopes and feedback in the views

Lane 3, tier 4, after COLLAB-4 and COLLAB-5. Characterization-first. Files: `skills/delivery-views/`, `tests/fixtures/delivery-views/`, `tests/delivery_views_page/`, `tests/test_delivery_views_plan.py`, `tests/test_delivery_views_render.py` and `hosted/delivery-viewer/test/`.

- **Plan schema:** `validate_plan` accepts `draft` as exclusive with `approved`
  (D14).
- **Line map:**
  - the plan chapter shows a draft with its endorsements and whether each is still
    current;
  - a Scopes section lists who holds what;
  - feedback shows as threads on its anchors;
  - each peer shows its freshness ("b as of 4s", "b held: generation differs").
- **Rendering placement:** additions go only behind new blocks, so existing
  goldens recapture to identical bytes. A test asserts that.
- **New corpus cases:** `plan-draft-shared` and `colead`. Goldens are captured
  with `node tests/delivery_views_page/recapture-goldens.mjs` in their own commit.
- **Hosted:** a hosted viewer test renders a planning cut built by the publisher at
  this commit. The conformance fixtures stay unchanged.
- **Scenarios:**
  - a draft with one stale endorsement;
  - a section held by `b`;
  - open, blocking and resolved feedback;
  - a held peer;
  - a queue moving between seats;
  - hostile text in feedback.

### COLLAB-9. Two machines end to end, and the adopter guide

Lane 2, tier 5, after COLLAB-7 and COLLAB-8. Sensor-only for docs, test-first for the end-to-end class. Files: `docs/collaboration.md`, `docs/host-adapters.md`, `README.md`, `tests/test_collab_two_machines.py` and `tests/suite-budget.json`.

- **End-to-end class:** the finish line's two-machine scenario, as one
  `run_quietly` class.
- **Budget entry:** only if the class runs alone, added to `serial` with its
  reason. No ceiling is raised.
- **`docs/collaboration.md`:**
  - joining with `collab join`, admission, labels, and what a seat is and isn't
    (D2, D19);
  - starting with `plan-open`;
  - following with `plan-watch` and `collab watch` under a host monitor;
  - feedback, endorsement, co-leading, handover and hand-back;
  - generation holds and how to clear them.
- **`docs/host-adapters.md`:** one paragraph saying shared records add no host
  projection.
- **Pins:** the docs' corpus pins in `tests/test_declared_mechanics_corpus.py`,
  where they read these files.

## Scenarios across stories

- A seat offline for ten minutes keeps writing its own scopes. They catch
  up on reconnect, and their peer sees "a as of 10m" meanwhile.
- One machine on an older generation is held, not broken. It recovers by its own
  upgrade.
- Hand-back mid-delivery: one seat takes every scope through transfers, and
  the other's lanes are re-assigned or kept as lanes.

## Plan review

Lenses: the repository's declared plan lenses. Each lens raises every finding it
can state in round 1, ends its evidence with approval conditions, and in later
rounds raises a new finding on unchanged text only at P0 or P1.

The decisions D1-D19 and each story's pin inventory are where a lens checks
completeness. A lens that finds an unlisted pin, integer-id reader or field
without a scope raises it as a finding against the relevant decision or story.
