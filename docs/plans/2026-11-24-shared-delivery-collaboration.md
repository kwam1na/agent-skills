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
    - `collab join` on a clone with no local record first fetches every admitted
      seat's partition and ingests it into a new local record, so the first
      seat's `record.started` is local entry 1. Only then does it create a seat and
      journal `seat.requested`, whose `seen` names what it ingested;
    - the `delivery` holder journals `seat.admitted`, `{seat, label, approver}`,
      with `approver` true unless `--no-approver` is given.
    - `collab label` journals `seat.labeled`, `{seat, label}`;
    - `meta.collab.seats` is derived from those entries, never written directly,
      and takes each seat's latest label.
  - **Refusals:**
    - on a shared record, any write from a checkout with no seat is refused with
      `collab.seat_missing`, except the `sync` writer's ingest;
    - any write from a seat not yet admitted, other than its own `seat.requested`
      and the `sync` writer's ingest, is refused with `collab.seat_not_admitted`.
  - **Environment:** no environment variable sets or overrides the seat.
- **D3. Transport.**
  - **Where:** each author's partition is a commit chain at
    `refs/agent-skills/<key>/<seat>` on the binding's base remote.
  - **What a commit holds:**
    - `partition.json`, spec `delivery-partition/1`:
      - `seat`;
      - `generation`, `{releaseId, archiveSha256}`;
      - `through`, the last own seq;
      - `slice`, a list of `{path, value, origin}`. `path` addresses array
        elements by the keyed selector the patch layer already uses
        (`ELEMENT_KEYS`, for example `queue[subject=K]/stage` or
        `lanes[id=L]/focus`), never by index. `origin` is the `{seat, seq}` of the
        write that set the value. An entry may carry `removed: true` in place of
        `value` for a keyed element its writer deleted. D7 filters it like any
        value, and ingest deletes the element;
      - `hosted`, only while the seat holds `delivery`: the hosted identity's
        `publicId`, `ownerId`, `epoch`, `sequence`, `receipt` and `pending`, never
        the token (D12);
      - `trackerThrough`, only while the seat holds `delivery`: `{seat: seq}`, the
        last origin of each seat the tracker publisher has posted (D12);
    - `journal.jsonl`: the author's own-origin entries in seq order.
  - **Push:** fast-forward only, never forced. A rejected push re-reads the remote
    ref.
    - If the remote ref is not an ancestor of the local chain, the same seat is
      writing from a second checkout, for example a copied one.
    - **Whichever chain reached the remote first is the seat's chain**, for every
      machine. A third seat ingests only what the remote ref holds.
    - **The checkout whose push is refused** gets `collab.seat_split`:
      - its unpushed chain is kept locally at `refs/agent-skills-split/<key>/<seat>`;
      - its record marks itself held, with `heldReason: "seat_split"`, and every
        later write from that checkout is refused with `collab.seat_split`;
      - Needs you names the split. `collab join --new-seat` gives that checkout a
        fresh seat, which needs admission.
  - **Remote settings.** The plan requires nothing of the remote. Test remotes
    allow forced updates, as forges do, and set `core.logAllRefUpdates=always`, so a
    forced update the product made would land and be recorded (D18).
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
  - **State fields that hold journal ids.** `delivery-state/1` has exactly three:
    - `regionsSettled` is carried in slices as the origin of the merge it names,
      and translated to the local id on ingest;
    - `queue[subject=K].leadSelection.reuse.decision`, the `decision.recorded`
      entry that journals a reuse grant, which `merge_queue.py` links as
      `because`, is carried and translated the same way;
    - `meta.closing.started.entry` and `meta.closing.finished.entry` are
      machine-local. Each machine derives them from its own local id of the
      `delivery.closed` entry, by the rule `_with_closing` applies on append.
  - **Every integer field is listed.** A COLLAB-2 test walks every integer field
    in `delivery-state/1` and checks it against an explicit list marking each as a
    journal id or not. The fields that are not journal ids:
    - `version`;
    - `queue[].order` and `queue[].reportedRounds`;
    - `laneRegions[].start` and `laneRegions[].end`;
    - `meta.checkHistory.runs[]`, `meta.checkHistory.checks[].seconds[]` and
      `meta.checkHistory.checks[].flakes`;
    - `meta.checking.now.steps[].expectedSeconds`;
    - `queue[].leadSelection.checks[].timeoutSeconds`.

    An integer field on neither list turns the test red.
  - **Schema:** `delivery-journal/2` gains `origin` as an optional member, so its
    version does not change. D5 keeps every writer on one generation.
- **D5. One generation per shared record.** A peer partition whose
  `archiveSha256` differs from the local writer pin is held, not ingested. Only
  the digest is compared, never `releaseId`.
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
  - **Why no voiding is needed:** each scope has one holder, so two machines can
    never act on a scope concurrently. The only case is a non-holder acting on a
    stale view, and that is refused locally before anything is written.
  - **Field map.** Every `delivery-state/1` property has one row: the scopes
    permitted to write it, or `local`, or `tool`. The table lives in
    `delivery_state.py`. COLLAB-3 writes its delivery rows, and COLLAB-6 its lane
    and queue rows. A test reads the schema's properties, and `meta`'s, and fails
    on any property without a row.

    | Property | Written by |
    | --- | --- |
    | `schemaVersion` | tool |
    | `version` | local |
    | `meta.updatedAt`, `meta.writerGeneration`, `meta.closing`, `meta.collab.peers` | local |
    | `meta.headline`, `planFile`, `trackerParent`, `hold`, `statusOverride`, `handoffsNeedAcceptance`, `integration`, `phase` | `delivery` |
    | `meta.collab.seats` | derived from the journal, never sliced |
    | `meta.checking`, `baseHead`, `releasedHead`, `checkHistory`, `baseReruns`, `sources`, `sourceReports`, `baseMove` | `queue` |
    | `siblings`, `tickets`, `outside` | `delivery`, whose holder alone runs reconcile and the tracker |
    | `finish[id=…]`, `extensions`, `leases[id=…]` | `delivery` |
    | `followups[id=…]` | `delivery`, and `lane:<L>` for the follow-ups its own acceptance writes |
    | `laneOwnership`, `laneRegions`, `regionsSettled` | `delivery`, which arbitrates files between lanes |
    | `liveFlakes` | `queue` |
    | `planned[subject=K]` | `delivery` (creation, `lane`, `after`); `lane:<its lane>` for the rest; a planned item with no lane is `delivery` only |
    | `lanes[id=L]` `id`, `title` | `delivery` |
    | `lanes[id=L]` `focus`, `state`, `waitingOn`, `since` and the tool's `focusAt`, `focusBy`, `waitingBy`, `restedFrom`, `waitingFrom`, `freed` | `lane:L` (the lead and the `lane` writer) and `queue` (the runner) |
    | `queue[subject=K]` `stage`, `checks`, `syncedHead`, `checkSelection`, `repair`, `order` | `queue` |
    | `queue[subject=K]` every other field | `lane:<its lane>` |

  - **Creating a queue element.** The writer that creates a `queue[subject=K]`
    element may set the starting values `QUEUE_START` names, plus `order`:
    - a `lane:<its lane>` acceptance;
    - or the `delivery` holder's reconcile adopting a missed hand-off.

    A re-hand-off may reset `stage`, `checks` and `syncedHead` to those values. The
    same acceptance removes `planned[subject=K]` (D3's `removed`). Every later write
    of those fields stays `queue`'s.
  - **Fields with two permitted scopes.** Only the rows that already have two
    writers today permit two scopes: lane rows, shared by a lane and the runner;
    planned items; and `followups`, which `delivery` and a lane's acceptance both
    add. Ingest applies such a value when its origin is causally
    after the local value's, ties broken by seat id. Each machine keeps the origin
    of every local value in `field-origins.json` beside the record, machine-local
    and outside `state.json`. Every other row has exactly one scope, so it never
    meets a concurrent write.
- **D7. Ingesting a slice is a tool write.**
  - A new writer, `sync`, applies a peer's slice to the local `state.json`.
  - **Filter:** for each value, it checks that the writing seat held a permitted
    scope at that value's own origin seq, by the journal as ingested so far. It
    ignores any other value, and journals nothing for it. The local value then
    stays.
  - The `patch` CLI refuses `--writer sync` with `collab.sync_writer_reserved`.
  - On a shared record, `lead` writes are checked against the local seat's
    scopes. `runner` writes are allowed only on the `queue` holder's machine.
- **D8. Plan text sync.**
  - **Branch:** plan text lives on `plan/<slug>` until `lead-setup`, then on
    `lead/<key>`. Both branches are pushed to the base remote.
  - **Composing:** `plan-sync` composes section by section, not by git's
    three-way merge:
    - it resolves sections in base, ours and theirs with `region_ownership`'s
      Markdown rules;
    - the text before the first heading, including the front matter, is one more
      section, `(preamble)`;
    - a section is changed on a side when it was edited, added or deleted there;
    - if no section was changed on both sides, the result applies both sides'
      changes:
      - every section ours deleted is removed;
      - every section ours edited replaces theirs;
      - every change of theirs stays;
      - every section ours added is inserted after its nearest preceding section in
        ours that still exists in theirs, or directly after `(preamble)` if none
        does. So an addition whose predecessor theirs deleted lands after the
        surviving section before it;
    - otherwise it refuses with `plan.sections_overlap`, naming each section and
      both diffs. A delete on one side and an edit on the other counts as changed
      on both.
  - **Scope check:** from COLLAB-4, on a planning or delivery record, a local
    change outside the sections the seat holds is refused before any commit or
    push, with `plan.section_not_held` and the
    `scope.requested` command that asks for it.
  - **Re-keying is exempt.** A `plan-rekey` commit is exempt from the scope
    check, because D11's map-equality rule already constrains its text.
  - **Retries:** a push rejected for being behind recomposes and retries, up to 5
    times, the bound `RECONCILE_ATTEMPTS` already uses. The sixth rejection stops
    with `plan.push_exhausted`, leaving the local branch at its last composed
    commit and the remote unchanged.
  - **History:** commits carry each committer's own git identity, as today. Nothing is
    force-pushed.
- **D9. One watch stream, built for a host monitor.**
  - **Output:** `plan-watch` (COLLAB-1) and `collab watch` (COLLAB-5) print one
    line per new event and nothing else, and flush every line. Events include peer
    plan pushes and ingested peer journal entries.
  - **Exit:** with no `--until`, they exit 0 at `--timeout` (default 1800, as
    `wait`). With `--until RE`, they exit 0 after printing the first line matching
    it, and 3 at `--timeout` if none did.
  - **Poll:** default 5 seconds, set by `collab.pollSeconds`, at least 2. The
    `run-delivery.json` schema refuses a lower value. A tick costs one
    `git fetch` process.
  - **Readers only:** neither command pushes or ingests. `plan-watch` fetches the
    plan branch. `collab watch` reads the local record, which the sync loop keeps
    current (D12).
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
  - **Re-keying.** `plan-rekey --map OLD=NEW[,OLD=NEW...]` replaces placeholder
    story keys with tracker keys in one commit, and journals `plan.rekeyed`,
    `{from, to, map}`. Only the `delivery` holder may run it.
  - On a shared record, `lead-init` requires:
    - one endorsement from every admitted approver seat, naming the current plan
      commit (`plan.endorsement_missing`);
    - no endorsement of an older commit standing in for one
      (`plan.endorsement_stale`);
    - the endorsed commit equal to the commit plan review aligned on, or reached
      from it only through `plan.rekeyed` commits whose text equals the aligned
      text with each map applied (`plan.not_aligned`);
    - no open blocking feedback (`plan.feedback_blocking`).

    It refuses with the code of the first of these that is missing.
  - A new plan commit makes earlier endorsements stale. The views say so.
- **D12. Which machine runs what.**
  - Every seat runs a supervisor on its machine. It handles sync, the live
    view, peers and Needs you.
  - **The sync loop** is a supervisor child, `collab.py serve`, as the merge queue
    is.
    - It pushes this seat's partition within one `collab.pollSeconds` of a local
      write, watched through `changes.jsonl`.
    - It fetches and ingests peers every `collab.pollSeconds`.
    - So a write reaches a running peer's record within two ticks plus one push.
    - `plan-open`, `collab join` and `lead-init` start it as part of the
      supervisor. Nothing else pushes or ingests a partition.
  - Only the `queue` holder's supervisor starts `merge_queue.py serve` and runs
    `fullHealth`.
  - Only the `delivery` holder's supervisor runs the hosted and tracker publishers.
    It alone also runs these pass steps, each of which writes `delivery` fields or
    journals once per delivery:
    - `reconcile`;
    - `regions` (`settle_landings`);
    - `finish`;
    - `lenses`;
    - `deferrals`.

    Every other seat's pass skips them.
  - On a shared record the tracker marker uses `<seat>.<seq>` in place of the
    local id. Its ledger keys by origin, so moving `delivery` never posts twice.
  - **Handing over `delivery`** is a parked transition, like D13:
    - it waits for no pending hosted publication and no tracker post in flight;
    - it stops both publishers on this machine;
    - it writes the hosted identity and `trackerThrough` into this seat's
      partition, then journals `scope.transferred`.
    - The incoming seat's supervisor writes them as its `hosted-identity.json` and
      tracker position before its first publish. It resumes the hosted session by
      `resume` with the carried `ownerId` and `epoch`, so the public link is
      unchanged.
    - Each machine that may hold `delivery` needs the repository's publishing
      token and tracker credentials in its own environment.
  - The machine ledger and the merge slot stay per machine.
- **D13. Queue handover is a parked transition.**
  - `collab give queue --to B` waits for an idle queue, meaning no check running
    and no merge in progress, up to `--wait` seconds. Past that it refuses with
    `scope.handover_busy`, naming the check or merge.
  - It stops this machine's queue child, then journals `scope.transferred`.
  - B's supervisor starts its queue on the pass that ingests that entry.
  - Between the two, no machine serves the queue. The line map says
    "queue moving to B".
- **D14. Hosted pages without a version change.**
  - `delivery-plan/1` gains an optional `draft` member, `{commit, at}`. It
    replaces `approved` until the plan is approved. Exactly one of the two is
    present, and a plan with both or neither is refused by `validate_plan`.
  - COLLAB-4 makes this change, because COLLAB-4 is the first story to write a
    planning `plan.json`.
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
  - **Bare remotes in tests:**
    - every bare remote a test builds sets `core.logAllRefUpdates=always` and
      leaves `receive.denyNonFastForwards` off, so a forced update lands and is
      recorded;
    - a shared helper asserts that the remote's reflog is non-empty, and that every
      recorded update to `plan/<slug>`, `lead/<key>` and each `refs/agent-skills`
      ref is a fast-forward;
    - one planted `git push --force` against such a remote shows the helper fails.
  - **Observing a peer.** Tests observe a peer by reading its `state.json` and
    `journal.jsonl` directly. They never run a command there that could ingest.
  - `tests/suite-budget.json` is not raised.
- **D19. Identity is trust-based.**
  - Anyone who can push to the base remote can write any `refs/agent-skills/...`
    ref. A seat id proves nothing about who wrote a partition, exactly as `--who`
    and `--granted-by` prove nothing today.
  - This delivery adds no authentication.
  - Binding seats to people means signed partition commits checked against
    admitted keys. That is a separate unit, out of scope here.

- **D20. Every refusal, and what its test checks.**
  - **Codes:**

    | Code | Raised when |
    | --- | --- |
    | `collab.seat_missing` | a shared-record write from a checkout with no seat |
    | `collab.seat_not_admitted` | a write from a seat not yet admitted |
    | `collab.seat_split` | a push finds the remote seat chain diverged, and every later write from that checkout |
    | `collab.record_exists` | `plan-open` for a key that already has a record |
    | `collab.sync_writer_reserved` | `patch --writer sync` |
    | `plan.sections_overlap` | `plan-sync` finds a section changed on both sides |
    | `plan.section_not_held` | a plan change outside the seat's held sections |
    | `plan.push_exhausted` | the sixth rejected `plan-sync` push |
    | `scope.not_held` | a write, transfer, admission, re-key or feedback resolution by a non-holder; a runner write on a machine not holding `queue` |
    | `queue.not_held` | `merge_queue.py serve` started where the seat does not hold `queue` |
    | `scope.handover_busy` | a `queue` or `delivery` handover past its `--wait` |
    | `plan.endorsement_missing`, `plan.endorsement_stale`, `plan.not_aligned`, `plan.feedback_blocking` | `lead-init`'s joint approval (D11), in that order |
    | `plan.uncommitted`, `plan.no_review_slug` | `plan-open` on a plan not committed at HEAD, or without `reviewSlug` |
    | `plan.draft_conflict` | `validate_plan` on a plan with both `draft` and `approved`, or neither |

    A held peer partition (`collab.generation_differs`) is a state shown in Needs
    you, not a refusal.
  - **Surfaces:** each code's test checks that these are unchanged:
    - `state.json` and `journal.jsonl`;
    - the local partition ref and the remote `refs/agent-skills` ref;
    - the local and remote plan branch.
  - **Allow case:** each code's test also runs the allowed case beside it.

## Finish line

All of the following hold at one main-push commit:

1. **Two machines, end to end.** One test class (COLLAB-9) drives two clones of
   one bare remote, with separate state directories and separate tracker ledgers
   sharing one fake tracker. It uses only user-facing commands and the running
   supervisors, never `collab-push`, `collab-fetch` or `collab-ingest`. With
   `collab.pollSeconds` set to 2, every journal write on one machine appears in the
   other's record within 10 seconds. The class runs through:
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
   - `delivery` handed to `b`, with the hosted public link unchanged and the
     tracker posting each entry exactly once;
   - every scope handed back to `a`, and the close.

   At each step both machines' records compose to the same state and the same set
   of journal origins in a causal order. "The same state" ignores only the fields
   D6 marks `local`. For `regionsSettled` and `meta.closing.*.entry`, the entry
   each names has the same origin on both machines.
2. **Unshared records unchanged.** Every existing test, line-map, history and runs
   golden, narrator story and hosted fixture passes without change, other than the
   pins the stories list.
3. **Refusals named.** Each code in D20 has a test that triggers it, checks
   every surface D20 lists, and runs its allow case.
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
Two scripts are shared by region:

- **`skills/orchestrate-work/scripts/delivery_state.py`:** lane 1 holds it whole.
- **`skills/run-delivery/scripts/run_delivery.py`:** lane 2 holds it whole. Lane
  1's two call sites in it are carved to lane 1 with `lead-own --carve` when those
  stories are assigned:
  - COLLAB-3's `patch_from` seat argument;
  - COLLAB-6's supervisor role check.

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | COLLAB-2 | `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/collab.py`, `skills/orchestrate-work/references/`, `skills/orchestrate-work/SKILL.md`, `tests/test_collab_partitions.py`, `tests/test_orchestration_state.py`, `tests/test_journal_kinds.py`, `tests/fixtures/journal-kinds/`, `tests/collab_remotes.py` |
| 1 | 2 | COLLAB-1 | `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/scripts/plan_sync.py`, `skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`, `tests/test_plan_sync.py`, `tests/test_run_delivery.py` |
| 2 | 1 | COLLAB-3 | `skills/orchestrate-work/scripts/region_ownership.py`, `tests/test_collab_scopes.py` |
| 2 | 2 | COLLAB-4 | `skills/delivery-views/scripts/views_common.py`, `skills/delivery-views/references/delivery-plan.schema.json`, `tests/test_planning_record.py`, `tests/test_run_delivery_plan_review.py`, `tests/test_delivery_views_plan.py` |
| 3 | 2 | COLLAB-5 | `skills/orchestrate-work/scripts/narrator.py`, `tests/test_collab_join.py`, `tests/test_lead_wait.py` |
| 3 | 1 | COLLAB-6 | `skills/orchestrate-work/scripts/merge_queue.py`, `tests/test_collab_colead.py` |
| 4 | 2 | COLLAB-8 | `skills/run-delivery/scripts/tracker_publisher.py`, `tests/test_run_delivery_tracker.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_collab_colead_runtime.py` |
| 4 | 3 | COLLAB-7 | `skills/delivery-views/`, `tests/fixtures/delivery-views/`, `tests/delivery_views_page/`, `tests/test_delivery_views_plan.py`, `tests/test_delivery_views_render.py`, `hosted/delivery-viewer/test/` |
| 5 | 2 | COLLAB-9 | `docs/collaboration.md`, `docs/host-adapters.md`, `README.md`, `tests/test_collab_two_machines.py`, `tests/suite-budget.json` |

**Ownership notes:**

- Lane 2 holds `views_common.py`, `delivery-plan.schema.json` and
  `tests/test_delivery_views_plan.py` for COLLAB-4 only. Lane 3's tier-4 row takes
  them whole once COLLAB-4 merges, and COLLAB-7 starts after it.
- Lane 1 registers every new journal kind and every new wait kind, so lane 2 never
  edits the kind registry, the wait words or the orchestration guide.
- `tests/test_journal_shape.py` counts journal writes in both layers' scripts, so
  its `INVENTORY` is shared by region. Each story's entries are granted to its lane
  with `lead-own --region --around` when it is assigned.
- `tests/collab_remotes.py` is the shared bare-remote helper of D18. COLLAB-2
  writes it. Later stories import it and never change it.

The stories are children of one epic, created through `$create-linear-ticket`
after this plan is approved. The `COLLAB-n` keys are placeholders. The
`delivery` holder replaces them with `plan-rekey` before `lead-setup`, as D11
allows.

## Stories

### COLLAB-1. Sync and watch a co-authored plan

Lane 2, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/plan_sync.py`, `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`, `tests/test_plan_sync.py` and `tests/test_run_delivery.py`.

This story relieves the pain on its own and needs no record.

- **Commands:**
  - `plan-sync --plan P` composes and pushes `plan/<slug>`, or `lead/<key>` once a
    delivery file names the plan, by D8. The scope check comes in COLLAB-4.
  - `plan-watch --plan P [--timeout S] [--until RE]` streams by D9.
- **Section rules:** reused from `region_ownership.markdown_block`; it is not
  reimplemented.
- **Settings:** `collab.pollSeconds` (minimum 2) and `collab.autoGrant` (default
  false) are added to the `run-delivery.json` schema and `references/config.md`.
- **Skill page:** a new `## Collaboration` section in `skills/run-delivery/SKILL.md`
  carries the rows. They come after every existing table, so the
  `lead-add-lane`-after-`lead-plan` row order pinned by `tests/test_lead_add_lane.py`
  is untouched.
- **Pins:**
  - `tests/test_run_delivery.py::RunDeliveryAdoptionGuideTests.test_every_command_the_guide_names_exists`:
    both commands appear in `--help` choices.
  - `test_every_shipped_file_is_listed_and_clean`: `plan_sync.py` is listed.
- **Scenarios, composing.** Each disjoint case asserts the exact composed text,
  with any added section placed mid-document, never last:
  - ours edits one section, theirs edits another;
  - ours adds a section, theirs edits another;
  - theirs adds a section, ours edits another;
  - ours deletes a section, theirs edits another;
  - theirs deletes a section ours left unchanged;
  - only one side edits the front matter;
  - ours adds a section after S while theirs deletes S; the addition lands after
    the section before S;
  - an edit at the end of one section and an edit to the next section's heading,
    the case git's three-way merge would conflict on.
- **Scenarios, refusals:**
  - one section edited on both sides gives `plan.sections_overlap`, naming it,
    with both diffs, and the local branch is unchanged;
  - a delete on one side against an edit on the other is refused the same way;
  - both sides editing the front matter is refused the same way;
  - five rejected pushes and then a success compose and land;
  - six rejections stop with `plan.push_exhausted` and leave the D20 surfaces
    unchanged;
  - the D18 helper finds only fast-forward updates on `plan/<slug>`.
- **Scenarios, watching:**
  - `plan-watch` prints one flushed line per peer push within two ticks, and
    nothing else;
  - with no `--until` it exits 0 at its timeout;
  - with `--until` it exits 0 on the first matching line;
  - with an `--until` that never matches, it exits 3 at the timeout;
  - a `collab.pollSeconds` of 1 is refused by the settings schema.
- **Scenarios, what stays untouched:**
  - `remote.origin.fetch` is unchanged;
  - `git ls-remote --heads --tags` shows only `plan/<slug>` as new.

### COLLAB-2. Seats, partitions and causal journal ingest

Lane 1, tier 1, not blocked. Characterization-first. Files: `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/collab.py`, `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/journal-kinds.json`, `skills/orchestrate-work/SKILL.md`, `tests/test_collab_partitions.py`, `tests/test_orchestration_state.py`, `tests/test_journal_kinds.py`, `tests/fixtures/journal-kinds/` and `tests/collab_remotes.py`.

- **Characterize first:** journal validation, `because`/`answers` linking, `wait`
  and the writer pin on an unshared record. These tests stay unchanged through
  the story.
- **Record and journal:** all optional:
  - `meta.collab`: `seats`, derived from the journal, and `peers`, with each peer's
    `through`, `fetchedAt` and `heldReason`;
  - `meta.phase`: `planning` or `delivery`, absent meaning `delivery`;
  - the `origin` member (D4).
- **Partition and transport:** `collab.py` holds:
  - seat creation and lookup (D2);
  - partition build, with keyed-selector paths (D3);
  - push;
  - peer fetch;
  - causal ingest with link translation, including `regionsSettled` (D4);
  - the generation hold (D5);
  - the split quarantine (D3);
  - the sync loop, `collab.py serve` (D12).

  It journals only through `delivery_state`'s append functions.
- **The `sync` writer** (D7), applying slice values without a scope filter until
  COLLAB-3 adds one.
- **State-tool subcommands:** `collab-push`, `collab-fetch` and `collab-ingest`,
  for tests and recovery. The sync loop is what runs them in use.
- **Local fields:** the D6 `local` rows. `meta.closing` is re-derived from the
  local `delivery.closed` entry on ingest (D4).
- **New kinds:**
  - `seat.requested`, with data `{seat, label}`;
  - `seat.admitted`, with data `{seat, label, approver}`, linked by `answers` to
    its request except for the first seat;
  - `seat.labeled`, with data `{seat, label}`;
  - `partition.held`, with data `{seat, reason}`;
  - `partition.resumed`.
- **Helper:** `tests/collab_remotes.py`, the D18 bare-remote helper with its
  planted forced-push case.
- **Pins:**
  - `tests/test_orchestration_state.py::test_schemas_are_keyed_by_name_use_urn_ids_and_declare_no_dialect`
    gains `delivery-partition/1`, making 14 names;
  - `ADOPTER_FILES` gains `scripts/collab.py`;
  - `tests/test_journal_kinds.py` `TABLE` and `ALTITUDES` gain the new kinds;
  - this story's region of `tests/test_journal_shape.py` (`SCRIPTS` gains
    `collab.py`, and its `INVENTORY` rows).
- **Scenarios, exchange:**
  - two records exchange partitions and reach the same origin set and state;
  - two seats append queue elements with different subjects concurrently, and
    both survive on both machines;
  - a peer entry whose `because` names the other side's entry links to the right
    local id;
  - with three seats, an entry waits until the entry its `seen` names is
    ingested;
  - a merge accepted on one machine and settled leaves `regionsSettled` on both
    naming the merge of the same origin;
  - a close journalled on one seat leaves both records with closing marks naming
    their own local ids of the same origin;
  - the integer walk finds every integer field on one of D4's two lists;
  - an element removed on one machine is absent on both after exchange, and a
    removal by a seat without a permitted scope is ignored;
  - a checkout with no seat ingests every admitted partition into a new record,
    while a `lead` write from that checkout gives `collab.seat_missing`;
  - after the sync loop's peer fetch, `remote.origin.fetch` is unchanged.
- **Scenarios, holds and splits:**
  - a partition whose `archiveSha256` differs, with the same `releaseId`, is held,
    named, and ingested after the local writer upgrade;
  - a copied checkout that pushes first wins. The original is refused with
    `collab.seat_split`, keeps its unpushed chain under
    `refs/agent-skills-split/`, and refuses later writes. A third seat's origin set
    holds the copy's chain only.
  - the reverse order gives the reverse result;
  - the D18 helper finds only fast-forward updates on each `refs/agent-skills`
    ref.
- **Scenarios, refusals:**
  - a write with no seat gives `collab.seat_missing`, and a write from an
    unadmitted seat gives `collab.seat_not_admitted`, each with the D20 surfaces
    unchanged;
  - a requested but unadmitted seat's partition contributes only its
    `seat.requested` entry;
  - setting `AGENT_SKILLS_SEAT` or any other variable changes nothing about which
    seat writes;
  - `patch --writer sync` gives `collab.sync_writer_reserved`;
  - `wait --after` wakes on an ingested peer entry of a wait kind.

### COLLAB-3. Authority scopes, admission and the scope filter

Lane 1, tier 2, after COLLAB-2. Test-first. Files: `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/collab.py`, `skills/orchestrate-work/scripts/region_ownership.py`, `skills/orchestrate-work/references/`, `skills/orchestrate-work/SKILL.md`, `tests/test_collab_scopes.py` and `tests/test_journal_kinds.py`.

- **Scope register:** the scopes of D6, derived from the journal. Who holds what
  is never stored as a second copy.
- **Kinds:**
  - `scope.transferred` (`scope`, `from`, `to`);
  - `scope.requested` (`scope`, `reason`);
  - `scope.declined`, linked by `answers` to its request.
- **Kinds registered here for lane 2's commands:**
  - `plan.endorsed` (`commit`);
  - `plan.seen` (`commit`);
  - `plan.rekeyed` (`from`, `to`, `map`);
  - `feedback.raised` (`anchor`, `text`, `blocking`);
  - `feedback.resolved` (`outcome` is `accepted`, `declined` or `answered`, plus
    `reason`), linked by `answers`.
- **Wait kinds:** `feedback.raised`, `scope.requested` and `seat.requested` join
  `WAIT_KINDS`, with their wait words. The orchestration guide's list is updated
  in this change, which keeps `test_the_guide_names_every_kind_that_ends_a_wait`
  green.
- **Field map:**
  - the D6 table's `delivery`, `local` and `tool` rows, with the lane and queue
    rows present but unenforced until COLLAB-6;
  - `field-origins.json`, and the two-scope ordering;
  - the completeness test of D6.
- **Checks:**
  - `ownership_violations` checks `lead` writes against the local seat's scopes on
    a shared record (D7);
  - the `sync` writer's filter at each value's origin (D7);
  - `delivery_state` exposes `holder(scope)`, `holds(seat, scope)` and
    `held_sections(seat, plan)`, the last through `region_ownership`'s section
    spans.
- **Admission:** only the `delivery` holder may journal `seat.admitted`.
  `approver` changes only by a new `seat.admitted` from that holder.
- **State-tool subcommands:** `scope give|request|decline` and
  `seat admit [--no-approver]`.
- **Auto-grant:** read from `collab.autoGrant` in the binding.
- **The carved call site** in `run_delivery.py` passes the seat through
  `patch_from`. Nothing else in `run_delivery.py` changes.
- **Scenarios, writes and transfers:**
  - a non-holder's write to a `delivery` field gives `scope.not_held`, naming the
    holder; the holder's write is applied;
  - only the holder can transfer; any other transfer gives `scope.not_held`;
  - a seat admitted by a non-holder gives `scope.not_held`.
- **Scenarios, auto-grant and approvers:**
  - with `collab.autoGrant` true, a request is granted for an unclaimed section,
    and never for a held section or for `queue`;
  - with it false, the same request stays open;
  - a seat admitted with `--no-approver` is not counted by `lead-init` (shown in
    COLLAB-4);
  - re-admission by the holder changes `approver`.
- **Scenarios, the sync filter:**
  - a peer slice carrying a `delivery` field the peer does not hold is ingested
    with that field ignored and the local value unchanged; the holder's slice
    with the same field is applied;
  - a slice value written while its seat held `lane:x`, ingested after a
    `scope.transferred` of `lane:x` that comes after that value's origin, is
    applied;
  - a value written after that transfer is ignored.
- **Scenarios, two-scope fields:**
  - two concurrent writes to one lane row's `focus` resolve the same way on both
    machines, by seat id;
  - the seat that loses that tie-break writes `focus` again after ingesting the
    other's write, and both machines end with its later value;
  - concurrent follow-ups with distinct ids from `delivery` and a lane both
    survive on both machines.
- **Scenarios, labels:** in a record where `a` and `b` hold distinct scopes
  including a lane, `holder`, `holds`, `scope.not_held` and lane ownership are
  unchanged:
  - with labels swapped;
  - with both seats carrying the same label.
- **Scenarios, completeness and compatibility:**
  - a schema property added without a field-map row turns the completeness test
    red, at the top level, in `meta`, and in `lanes`, `queue` and `planned`
    elements;
  - an unshared record ignores scopes entirely, so its field-ownership tests are
    unchanged.

### COLLAB-4. The planning record, the plan scope check and joint approval

Lane 2, tier 2, after COLLAB-1 and COLLAB-3. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/scripts/plan_sync.py`, `skills/run-delivery/SKILL.md`, `skills/delivery-views/scripts/views_common.py`, `skills/delivery-views/references/delivery-plan.schema.json`, `tests/test_planning_record.py`, `tests/test_run_delivery_plan_review.py` and `tests/test_delivery_views_plan.py`.

- **`plan-open`** (D10). It starts the supervisor in planning mode: the sync
  loop, the live view and hosted publishing, with no queue and no tracker.
- **Adoption:** `lead-setup` and `lead-init` adopt the planning record in place,
  beside the existing review-store adoption (`run_delivery.py:2593-2627`), which is
  unchanged.
- **Scope check:** `plan_sync` refuses changes outside `held_sections` (D8).
- **Approval commands:**
  - `lead-plan endorse` journals `plan.endorsed`.
  - `plan-rekey` follows D11.
  - `lead-init`'s joint-approval check follows D11.
- **Plan file:**
  - `validate_plan` accepts `draft` exclusive with `approved` (D14).
  - `build_plan` writes a planning `plan.json` with `draft`. Its stories come from
    the plan headings through `lead-setup`'s existing parser.
- **Pins:** `PlanReviewWithoutDeliveryTests` stays unchanged. New adoption cases
  sit beside it.
- **Scenarios, opening and adoption:**
  - `plan-open` creates exactly one admitted approver seat holding every scope;
  - `plan-open` for a key with an existing unshared record gives
    `collab.record_exists`, and that record is unchanged;
  - adoption keeps the journal, partitions and `hosted-identity.json`, and
    `lead-init`'s own entries continue the same journal's ids after the planning
    entries;
  - `meta.phase` moves to `delivery` exactly once.
- **Scenarios, plan edits:**
  - a plan edit outside held sections gives `plan.section_not_held` before any
    commit or push, naming the request command, with the D20 surfaces unchanged;
  - an edit inside a held section lands;
  - `b` holds a section naming a placeholder key, `a` runs `plan-rekey`, and the
    commit lands with `b`'s section re-keyed.
- **Scenarios, joint approval:**
  - with plan review aligned on X, `plan-rekey` at Y, and both seats endorsing Y,
    `lead-init` passes;
  - an unrecorded text edit at Y gives `plan.not_aligned`;
  - one approver missing gives `plan.endorsement_missing`;
  - an endorsement of X after Y gives `plan.endorsement_stale`;
  - open blocking feedback gives `plan.feedback_blocking`;
  - a `--no-approver` seat's missing endorsement does not hold `lead-init`;
  - `plan-rekey` by a non-holder gives `scope.not_held`;
  - with both an endorsement missing and blocking feedback open,
    `plan.endorsement_missing` is the code returned;
  - `plan-open` on an uncommitted plan gives `plan.uncommitted`, and one without
    `reviewSlug` gives `plan.no_review_slug`.
- **Scenarios, the plan schema:** a plan with both `draft` and `approved`, or
  neither, gives `plan.draft_conflict`.

### COLLAB-5. Feedback, join and the shared watch stream

Lane 2, tier 3, after COLLAB-4. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`, `skills/orchestrate-work/scripts/narrator.py`, `tests/test_collab_join.py` and `tests/test_lead_wait.py`.

- **Kinds:** `feedback.raised` and `feedback.resolved`, registered by COLLAB-3.
  Only the anchor's scope holder may resolve.
- **Commands:**
  - `collab feedback add|resolve`;
  - `collab give|request|admit`, wrapping the state tool;
  - `collab join`, by D2's order: build the local record from admitted
    partitions, start the supervisor, then create the seat and request admission.
    Every use prints, in this order: the narrator's story, the plan diff since
    this seat's last `plan.seen`, open feedback, and scopes held.
  - `collab label`, journalling `seat.labeled`;
  - `collab watch` (D9), over the shared record.
- **Narrator:** words for the new kinds, and peer entries attributed to their
  seat's label.
- **Pins:** narrator story goldens are unchanged, because unshared records'
  journals have no new kinds.
- **Scenarios, joining:**
  - on a fresh clone, `b`'s entry 1 is `record.started` with `a`'s `at`, and the
    tracker publisher's init id is equal on both machines;
  - `b`'s join wakes `a`'s `lead-wait` with the admission request;
  - `join` after a fresh clone gives the full catch-up, and run again gives only
    what is new;
  - a relabel reaches the peer's derived seats.
- **Scenarios, feedback:**
  - feedback from `b` wakes `a`'s `lead-wait`;
  - a resolution by a non-holder gives `scope.not_held`;
  - blocking feedback shows in Needs you, and releases once it is resolved.
- **Scenarios, watching:**
  - `collab watch` prints one flushed line per ingested peer entry within two
    ticks, and nothing else;
  - with no `--until` it exits 0 at `--timeout`;
  - with `--until` it exits 0 on the first matching line;
  - with an `--until` that never matches, it exits 3 at `--timeout`.

### COLLAB-6. Lane and queue scopes in the record and the queue

Lane 1, tier 3, after COLLAB-3. Characterization-first. Files: `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/merge_queue.py`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/SKILL.md` and `tests/test_collab_colead.py`.

- **Characterize first:** the queue's serve loop and acceptance writes on an
  unshared record.
- **Field map:** the D6 table's `lane:<L>` and `queue` rows are enforced. The
  `runner` writer is authorized only on the `queue` holder's machine.
- **Queue:** `merge_queue.py serve` refuses to start on a shared record unless the
  local seat holds `queue`, with `queue.not_held`.
- **Supervisor role check:** the carved call site in `run_delivery.py`.
- **Queue handover:** the state-tool half of D13. An idle check (no check
  running, no merge in progress), then the queue child stopped, then
  `scope.transferred`, in that order.
- **Scenarios, lanes and the runner:**
  - each lead writes its own lane's rows and queue elements, and gets
    `scope.not_held` on the other's;
  - a runner write on a non-holder machine gives `scope.not_held`, while the
    holder's runner write is applied;
  - `b` accepts its lane's hand-off while `a` holds `queue`; `a`'s record holds
    the element at `stage: queued`, without the planned item, and `a`'s queue
    merges it;
  - a reuse grant recorded on `a`, then the queue handed to `b`: `b`'s
    `check.reused` names, through `because`, the entry of the grant's origin;
  - the COLLAB-3 label scenarios, repeated with lanes and queue elements held by
    each seat.
- **Scenarios, handover:**
  - a handover with a check running waits, then gives `scope.handover_busy`,
    naming the check;
  - the same happens with a merge in progress;
  - the journal shows the queue child stopped before `scope.transferred`;
  - after a handover the old machine's serve exits and the new one's starts.
- **Compatibility:** every existing merge-queue test passes unchanged.

### COLLAB-8. The co-lead runtime in run-delivery

Lane 2, tier 4, after COLLAB-5 and COLLAB-6. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/scripts/tracker_publisher.py`, `skills/run-delivery/SKILL.md`, `tests/test_run_delivery_tracker.py`, `tests/test_run_delivery_supervisor.py` and `tests/test_collab_colead_runtime.py`.

- **Lane commands:**
  - `lead-assign`, `lead-own` and `lead-add-lane` need `delivery`, which
    arbitrates files and lanes (D6).
  - `lead-accept` needs the lane's scope.
  - The assignment message names the seat's label (D15).
- **Supervisor roles** follow D12. The sync loop runs as a supervisor child.
- **Handovers:**
  - `collab give queue --to` runs the full handover of D13;
  - `collab give delivery --to` runs the delivery handover of D12, carrying the
    hosted identity and `trackerThrough`.
- **Tracker:** markers use `<seat>.<seq>` on shared records. No ledger migration
  is needed, because no shared record existed before.
- **Pins:** tracker publisher tests on unshared records are unchanged. New
  cases sit beside them.
- **Scenarios, scope checks:**
  - `b` assigning a lane, when `a` holds `delivery`, gives `scope.not_held`,
    naming `a`;
  - `b` accepting its own lane's hand-off is applied.
- **Scenarios, publishers.** The two machines keep separate state directories and
  tracker ledgers, and share only a fake tracker and a fake hosted origin:
  - before `delivery` moves, `a` posts entry X and `b` posts nothing;
  - after it moves, `b` posts the next new entry Y with a `<seat>.<seq>` marker,
    and posts nothing for X;
  - the hosted publisher follows the same pair, with the public link unchanged
    after the move;
  - a delivery handover with a hosted publication pending, or a tracker post in
    flight, gives `scope.handover_busy`;
  - the non-holder's supervisor pass skips reconcile, regions, finish, lenses and
    deferrals and writes nothing, and a finish line met once yields one
    `finish.met` on both machines;
  - a supervisor restart keeps its roles;
  - a write on one machine reaches the peer's record within two
    `collab.pollSeconds` ticks plus one push, with no explicit sync command.

### COLLAB-7. Planning, scopes and feedback in the views

Lane 3, tier 4, after COLLAB-4 and COLLAB-5. Characterization-first. Files: `skills/delivery-views/`, `tests/fixtures/delivery-views/`, `tests/delivery_views_page/`, `tests/test_delivery_views_plan.py`, `tests/test_delivery_views_render.py` and `hosted/delivery-viewer/test/`.

- **Line map:**
  - the plan chapter shows a draft (accepted by COLLAB-4's `validate_plan`) with
    its endorsements and whether each is still current;
  - a Scopes section lists who holds what;
  - feedback shows as threads on its anchors;
  - each peer shows its freshness ("b as of 4s", "b held: generation differs",
    "b split");
  - handovers in flight ("queue moving to b").
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
  - a held peer and a split peer;
  - a queue moving between seats;
  - hostile text in feedback and labels.

### COLLAB-9. Two machines end to end, and the adopter guide

Lane 2, tier 5, after COLLAB-7 and COLLAB-8. Sensor-only for docs, test-first for the end-to-end class. Files: `docs/collaboration.md`, `docs/host-adapters.md`, `README.md`, `tests/test_collab_two_machines.py` and `tests/suite-budget.json`.

- **End-to-end class:** the finish line's two-machine scenario, as one
  `run_quietly` class. It is driven only through user-facing commands and running
  supervisors.
- **Budget entry:** only if the class runs alone, added to `serial` with its
  reason. No ceiling is raised.
- **`docs/collaboration.md`:**
  - joining with `collab join`, admission, labels, and what a seat is and isn't
    (D2, D19);
  - starting with `plan-open`;
  - following with `plan-watch` and `collab watch` under a host monitor;
  - feedback, endorsement, re-keying, co-leading, handovers and hand-back;
  - the credentials each machine that may hold `delivery` needs;
  - generation holds, seat splits, and how to clear each.
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

The decisions D1-D20 and each story's pin inventory are where a lens checks
completeness. A lens that finds an unlisted pin, integer-id reader or field
without a scope raises it as a finding against the relevant decision or story.
