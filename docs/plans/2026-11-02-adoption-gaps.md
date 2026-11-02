---
title: "feat: The workflow does the lead's and lane's mechanical duties itself"
type: feat
status: in-review
date: 2026-11-02
execution_posture: test-first
context: Three leads delivered with run-delivery and orchestrate-work in eshyft's repositories, which use Bitbucket, Jira, protected long-lived dev-alt branches and an AI PR reviewer. Their friction logs hold about 100 entries. About half are fixed upstream or belong to work already in flight. Most of the rest are one failure repeated: the workflow names a duty in a guide or brief, nothing does it, and it waits for someone to remember. The other causes are a binding that serves one base, refusals that don't say how to recover, a plan review that only runs inside a delivery, and a guide that assumes GitHub and an unprotected `main`.
---

# feat: The workflow does the lead's and lane's mechanical duties itself

## Outcome

A lead does three things in a delivery: assign stories, accept hand-offs, and answer what only it or the operator can
decide. The workflow does everything else that today waits for the lead or a lane to remember it:

- running the reconcile sweep, the merge queue and the live view;
- marking accepted pull requests ready and merging them;
- cleaning up after each merge;
- every tracker update and transition the guide names;
- recording each finish line that is met;
- keeping a current copy of the record outside the checkout;
- the close-out.

A lane runs commands, and the commands make its tracker moves and posts, or, where the tracker is reachable only
through the host, print the exact move for the lane to make. Plan review runs through a command for any plan, not only
a delivery's.

A duty the workflow can't do in a given repository, because a grant, credential, adapter or setting is missing, is
reported by `doctor` and refused at `lead-init`. It never surfaces mid-delivery as something forgotten. A failure of
one of the workflow's own duties, such as a tracker post rejected by its credentials, is raised in the record as
"Needs you" and holds the close. The workflow also adopts from the guide alone into a repository with a protected default branch, several long-lived
bases, a tracker whose workflow reserves some moves for people, and a pipeline that reviews pull requests. It delivers
to any of those bases without editing shared settings, and every refusal says how to recover. None of this is specific
to the adopters the evidence comes from: every behaviour that differs between adopters is a setting, with a default
that matches today's behaviour.

## Evidence behind this plan

Three friction logs are the sources. Each entry gives a timestamp and a quote. They stay uncommitted, in the owner's
checkout (decision A6).

- `docs/delivery/eshyft-pilot-friction.md`: the SHFT-7049 lead in eshyftnew, 2026-10-30 to 11-02.
- `docs/delivery/eshyft-pilot-friction.workflow-pilot.md`: the workflow pilot lead's log, mined from its transcript.
  It covers SHFT-6934 in eshyft-admin-dashboard, SHFT-6931/7109 in eshyftnew, and V26-3037 here, 2026-10-24 to 11-02.
- `docs/delivery/eshyft-pilot-friction.shft-7056.md`: the SHFT-7056 lead's log, mined from its transcript. It covers
  nine lanes in eshyft-admin-dashboard, plus run-delivery's own delivery, 2026-10-28 to 10-30.

Duties that were forgotten across the three logs:
- **The reconcile sweep:** never started in any of the three until the operator noticed stale stops.
- **"Start work":** missed or late in all three.
- **Tracker updates on hand-off, merge, finish and close:** all skipped in SHFT-7049.
- **The epic's moves:** forgotten in SHFT-7056.
- **Follow-up tickets:** never filed for deferred findings in SHFT-7056.
- **Marking PRs ready:** done by hand on every PR in SHFT-7109 and SHFT-7049.
- **Housekeeping at close:** handed back to the operator in SHFT-7109.
- **The friction log:** started only when the operator asked.
- **The live view's address:** asked for three times in SHFT-7056.

Causes confirmed in this repository at `main` ebb3aec, and by plan review round 1:

- **One base per binding.**
  - `run_delivery.py` refuses a delivery file whose `baseBranch` differs from the binding's `base.ref`.
  - `merge_queue.py`'s `read_binding` requires the binding to name `--base`.
  - `shelf_for()` reads the binding, `run-delivery.json` and the delivery file from `<remote>/<base.ref>` whenever the
    current branch isn't `base.ref`.
  - The harness cross-check compares against `base.ref`.
- **Closed schemas.** `skills/orchestrate-work/references/schemas.json` declares the binding, its base and the state's
  `meta` with `additionalProperties: false`.
- **The plan log outlives the state.** It lives in `<state>/../flow-log`, not in the state directory. Setting aside a
  stale state and running `lead-init` again kept the earlier approval snapshot, so a later edit was recorded as drift
  "by unrecorded".
- **Publishers run only when the render wrapper runs.** That is after the merge queue runner's own writes, and when
  the lead runs `render_state.py`. The state tool and every run-delivery command render nothing, and `live` strips
  publishers. Publication is acknowledged by `ack`: one watermark in `published.json`, shared by every publisher and
  capped by any host outbox.
- **`serve` takes a candidate as soon as it is accepted.** It runs any queued item that isn't pending. An adopter's
  merge step that refuses a draft (one adopter's `merge.py` does) stops the candidate, and a stopped candidate waits
  for its lane to hand it off again.
- **Forge adapters can't mark ready.** The Bitbucket adapter answers `find`, `open`, `get` and `reviews`. The GitHub
  adapter has no `reviews`. Nothing can mark a pull request ready.
- **Two places build lens briefs.** run-delivery builds every lens brief from `skills/run-delivery/templates/round-brief.md`,
  mapping `lens.adversarial-testing` to `personas/testing-policy.md`. obtain-review's template isn't used by
  run-delivery lanes.
- **A story before hand-off has no head.** Round evidence is bound to the queue item's head, both in
  `delivery_state.py`'s `review_observations` and in `skills/delivery-views/assets/line-map.js`. A planned story has
  no head, so its rounds read as stale until hand-off.
- **A false backlog warning.** `delivery_state.py reconcile` reports a planned story at building or review whose
  ticket is in backlog, and nothing in the record says whether its ticket was ever started.
- **Plan review needs a delivery.** `plan-review prepare|reduce|end` reads its lenses from a delivery file's
  `planReview`, and needs the binding and run-delivery's config.
- **Signal dispositions.** `adopting.md` warns that a runner started with `nohup` or `&` passes ignored signals to its
  checks, which then fail as a broken base.

## Disposition of every open entry

Each open entry is in exactly one place: a unit below, an in-flight item, or the out-of-scope table. An entry whose
status line names a residual ("still open: …") is placed for that residual too. Entries the sources mark as fixed
aren't repeated. The fixes they name are run-delivery-v1, review-evidence-v1, jira-tracker-v1, plan-review-v1 and
draft-building-v1. That includes "lens ids don't map to charters", which run-delivery's brief resolves by naming each
lens's persona.

"The release vehicle was discovered after the delivery finished" is placed in C7. Once a binding names its `bases`,
`lead-setup` requires `--base`, so where the work lands is decided at setup, before any lane starts.

In-flight items that already own an entry:

- **V26-3051** (tracker profiles build on run-delivery): "run-delivery and jira can't be combined", the Jira
  `tracker-properties.json` shape, and "`harness.config.ts` written by hand for the core and jira profiles". The
  tracker profiles gain run-delivery's configuration in its place.
- **V26-3041** (tag-driven releases): every entry about release cadence, requalification, qualification records,
  release names, "adopters fall behind fast", "an upstream release invalidated the requalification", and "hot-fixing
  an installed release means editing its receipts". It also owns standing docs for the release flow, and the lane
  ruling (V26-3047): "whether lanes are subagents or sessions".
- **V26-3050** (agent-skills runs its own deliveries): a delivery with a plan but no `plan.json`, a live view started
  without its home, and this repository running its own plan reviews through an install.
- **GitHub issue #69** (round brief carries the lens's scoped-check rule): the adversarial lens mutation-testing a
  whole module. The unit "Scoped lenses" closes it.
- **V26-2951**: the run-delivery tests leaving `http.server` processes behind.

## Contract decisions

- **C1. The lead supervisor.**
  - **Lifetime.** `lead-init` starts `run-delivery lead-run`, one process per record. It detaches by starting a new
    session (`start_new_session`), never by ignoring signals. Before starting any child it resets SIGINT, SIGHUP and
    SIGQUIT to their defaults.
  - **Liveness.** Liveness is the lock `lead-run` holds on the flow log's `supervisor` lock, which also records its pid,
    start time and children. Nothing about the supervisor goes in the state's `meta`.
  - **Children.** It runs and keeps alive the merge queue's `serve`, the reconcile sweep and the live view. Its first
    line of output is the live view's address, and every lead command repeats it.
  - **When it's down.** `doctor --delivery` and every lead command report it. Only `lead-run --resume` restarts it.
    `--resume` stops or adopts children an earlier supervisor left, and picks up from the journal and the publisher's
    ledger (C2).
  - **Each pass,** in order:
    1. reconcile the forge and tracker snapshots;
    2. after each merge, do what `lead-cleanup` does today, skipping a story a lead already cleaned up by hand;
    3. journal `finish.met` once for each finish line whose criteria and proofs are all done and that isn't journalled
       yet;
    4. render with the binding's full `state` member, publishers included, so the tracker publisher runs. This also
       runs after each of the supervisor's own writes;
    5. copy the record and the flow log, under the state lock, into one fixed `latest` directory at the archive
       location `lead-close` uses;
    6. when the close condition holds, run the close-out (C6).
  - **"Needs you" is derived, never stored.** Each pass computes it from live conditions:
    - the publisher's ledger lagging the journal, and its last error;
    - an undrained host outbox;
    - a failed `fullHealth` for the current base head, from its result kept in the flow log's `full-health` file by base
      head. It clears when `fullHealth` passes at a later head;
    - `base.broken` with no later `base.repaired`;
    - a lane whose state is `blocked`, or that is waiting on the lead. It clears when the lane's state changes;
    - a `blocker.recorded` the lead hasn't resolved. `lead-resolve <journal id> --why TEXT`, a new command, resolves
      it: it records the resolution in the flow log's `resolutions` file and journals a `decision.recorded` naming it;
    - ledger `failed` and `handled` lines: tracker updates that couldn't be posted, and moves that weren't offered;
    - a supervisor child that won't stay up.

    Each item is either **holding** or **shown**. Only ledger `failed` and `handled` lines are shown; everything else
    holds. The close condition reads the holding items only, and the friction note lists the shown ones.

    Some resolutions are automatic:
    - the runner's own blocker is resolved by the hold clearing, or by `base.repaired`. It is recognised as a
      `blocker.recorded` with no lane and no subject whose summary starts with the runner's own text ("Serving is
      held:"). The journal records no writer;
    - `lead-resolve full-health --why TEXT` asks the supervisor's next pass to rerun `fullHealth` at the current
      head, for a failure the lead judges flaky. It doesn't run the check itself, so only the supervisor writes the
      `full-health` file.

    The line map shows the same list, and the close condition reads the same function, so an item clears when its
    condition clears. Nothing is added to the record's schema for it.
  - **Close condition.** Every planned and queued story is merged, or removed through `lead-plan change --kind
    removed`. Every finish line is met. The queue is empty. Every lane is idle or done. No "Needs you" item is
    holding.
  - **Lead commands** keep working when the supervisor is down, so a lead can always act by hand. The supervisor
    skips anything already done when it resumes.
  - **Cleanup waits for the lane.** It cleans up a merged story only once its lane has moved off it, idle or on its
    next story, so it never removes a worktree a lane session still stands in.
  - **Ready marks.** The supervisor never marks a pull request ready (C4). It reports a candidate that was accepted as
    a draft while `forge.mergeNeedsReady` was set.
- **C2. Tracker updates are a publisher with its own ledger.** run-delivery defines a tracker adapter contract and
  ships a publisher that the binding names in `state.publishers`, so it runs whenever the render wrapper runs (C1 step
  4, and the runner's writes). The publisher holds everything below: the ledger, the ordering, the mapping from journal
  kinds to updates and moves, and the failure handling. It calls a tracker adapter for only five operations: read a
  ticket's status and markers, comment, transition, and create an item, each carrying a marker. run-delivery ships a
  Jira adapter; an adopter on another tracker with an API supplies its own adapter under the same contract, the way
  forge adapters work today.
  - **The ledger is the one record of tracker actions.** The publisher doesn't use `ack`. It keeps
    `tracker-published.jsonl` in the state directory, with one line per journal id and outcome. The outcomes are:
    - `comment`, `transition` or `create`, for an action taken;
    - `skip`, for an entry with nothing to post;
    - `handled`, for a move not offered (C3);
    - `failed`, for a permanent rejection.

    Every journal entry gets at least one line, so "the ledger covers entry N" is well defined.
  - **Locking.** The publisher takes the ledger's lock with a non-blocking try. When another run holds it, the
    publisher exits 0 and leaves the entries to that run or the next pass, so a runner write never waits on a
    backlog. Lane commands take the same lock for the actions they make (C5).
  - **Ordering.** It processes journal entries in id order and adds the ledger line after each one. A transient
    failure stops the run, and the next run retries from there. Transient means a network error, a timeout, a 5xx, a
    409, 423 or 429, a 401, or a 403 that the adapter's status read of the epic also returns, meaning the whole site
    refuses the credentials. A delivery with no epic, or an epic that is itself unreadable, makes that read against the
    configured project instead.
  - **Permanent failures.** Any other 4xx is permanent, such as a deleted ticket or a body the tracker refuses. The
    publisher writes a `failed` line and goes on to later entries. It journals nothing, so a failure can't produce an
    entry that fails again. The failure shows in "Needs you" from the ledger (C1). So one rejected entry never holds
    every later update or the close.
  - **Bodies.** Every comment is truncated to the adapter's declared limit (32,767 characters for Jira), with a
    pointer to the record for the rest.
  - **Idempotency.** Each comment and move carries a marker the adapter stores on the known ticket (the Jira adapter
    uses an entity property). The marker is the delivery key, the record's init id, the journal id and the action together. The
    action is `comment`, `transition` with the transition's name, or `create`. The init id is the time of the
    record's first journal entry, the "Record starts at base" `decision.recorded` that every `lead-init` writes,
    criteria or not. It never changes during the record and needs no record field. It is
    combined this way because journal ids restart at 1 after a `--replace` and two deliveries can post to one
    ticket. A created item carries a marker the tracker can search for without an
    installed app. The Jira adapter uses the label `agent-followup-<story>-<finding id>`, lowercased to Jira's label
    rules, and searches for it within the configured project. Before
    writing, the publisher asks the adapter for the marker. A crash between the write and the ledger line therefore
    doesn't produce a second post or a second item.
  - **What it posts.** It handles every journal kind, using the orchestration guide's table. Two rules decide what it
    posts:
    - an entry whose ledger already holds an action line is skipped, whoever wrote that line, the publisher or a lane
      command;
    - `decision.recorded` is posted only when the run-delivery command that wrote it marks it. That covers a
      `lead-plan change`, a deferral, a `lead-resolve` and a blocker. The mark is the entry's journal id in
      the flow log's `tracker-marks` file, because the journal records no writer. The marking command holds the
      publisher's ledger lock, waiting for it, across the journal write and the mark, so no publisher run sees the
      entry unmarked. Because the lock spans both writes, a live command's entry is never seen unmarked. A command
      that dies between the two leaves the entry unmarked, and it gets a `skip` line. That is the only way a
      tracker-worthy decision goes unposted, and the friction note can't tell it from an ordinary skip. Every other entry gets a `skip` line.
      Nothing is added to the journal schema for this.
  - **Failure.** The supervisor's "Needs you" (C1) includes a publisher whose ledger lags the journal for more than one
    pass, naming its last error and the waiting entries. It clears when the lag does.
  - **Trackers reachable only through the host.** Some trackers are reachable only through the host's connector,
    which a process can't call. Linear is one, by this corpus's own rule. Such a repository uses the existing host
    publisher, and the supervisor raises an undrained outbox as "Needs you", naming the updates waiting.
  - **Where the ledger is.** The publisher finds the state directory through a `{stateDirectory}` substitution in
    publisher argv, which `render_state.py` gains. Today it substitutes only in renderer argv.
  - **Refusals.** Either of these fails `doctor`: a tracker with an adapter but no tracker publisher, or a tracker
    reachable only through the host but with no host publisher. `lead-init` refuses either unless
    `run-delivery.json` sets `tracker.updates: "manual"`.
- **C3. Every tracker move is configuration.** `run-delivery.json` `tracker.transitions` has three parts:
  - **`start`.** The publisher applies it for a story's `lane.assigned`, whatever `lead` says, once that story has
    started. A story starts when `lane-start <key>` records it in the flow log's `lane-starts` file
    (named with the delivery key, as every lead-side log is). Lane commands already
    write the lead-side flow log, and a lane may journal only `review.round`. Until then the publisher writes nothing
    for that entry and moves on. An adopter's existing `start` therefore keeps moving each ticket to started when its
    lane begins it, as the brief has the lane do today, with no new configuration. The move applies only on a story's first
    recorded start, so reassigning a story that is already past started never moves its ticket back. A story removed
    or reassigned before it starts gets a `skip` line and never moves.
  - **`review`.** It stays with the lane commands, timed by `reviewWhen` (C5).
  - **`lead`.** A new object from any later journal kind to the transitions the publisher makes, such as
    `candidate.merged` or `delivery.closed`. If `lead` is absent, or a kind maps to an empty list, no move is made for
    that kind and the update says the move belongs to people. That is the default, because some teams reserve every
    move after review for people.

  `lead-assign` journals one `lane.assigned` per story key it assigns, so each ticket has an entry for its own start,
  made when its lane begins it. The wait ends at the story's first `lane-start` record. If the journal reaches the
  story's `candidate.handed`, its `candidate.merged` or its removal first, the wait ends there instead. The publisher
  writes a `skip` line and makes no move, because a late start could move a ticket that is already in review back to
  started, and the adapter contract carries no status order. The friction note lists such a story as "start never
  recorded". `start` is applied only on a `lane-start` record, never once the ledger holds the story's review move, and never
  when the adapter finds a `transition` marker for the `start` transition from the same delivery key, under any init
  id, on the ticket. In that last case
  the story was started under an earlier record, before a `--replace`, and the entry gets a `skip` line.
  Either way the entry gets a line.
  While an entry waits, it isn't ledger lag in "Needs you". But during C6 step 3 it counts as uncovered and is shown,
  naming the story. Each move reads the ticket's status first:
  - one already made by a person or an integration is skipped, and reported as made by someone else;
  - one not offered from the current status is recorded in the ledger as `handled`, shown in "Needs you" (C1), and
    neither retried nor journalled.
- **C4. Marking ready happens at acceptance, under a grant.** The delivery file's `authority` gains
  `readyPullRequests`, and forge adapters gain `ready --url`. `run-delivery.json` gains `forge.mergeNeedsReady`, which
  says the repository's merge step refuses a draft.
  - **At acceptance.** With the grant, `lead-accept` marks the candidate's draft pull request ready before
    `handoff --accept` clears `pending`. A failed ready leaves the hand-off pending, and names the adapter.
  - **Refusals.** With `forge.mergeNeedsReady` set and no grant, `doctor` reports it and `lead-init` refuses, naming the
    grant to add.
  - **Lanes** never get this grant.
- **C5. Lane commands make the lane's tracker moves.**
  - **`lane-start <key> --base <base>`,** a new command:
    1. checks `--base` against the binding's bases;
    2. creates `.orchestration/lanes/<key>` from that base and runs the prepare step;
    3. sets the lane's focus.

    The start transition and start update belong to `lane.assigned`, which the publisher handles under the guide's
    table ("it moves to started") once `lane-start` records the story's start (C3). Neither the lead nor the lane makes the move.
    For a tracker reachable only through the host, `lane-start` prints the start move and the start update for the
    lane to make, and records them. The orchestration guide's host-publisher line tells the lead's drain
    to skip `lane.assigned`'s start move and update when `lane-start` recorded them, so the start is posted once.
    The Guide unit owns that line. `lane-start` replaces four steps the brief lists today. `lead-assign` prints it with the base filled
    in.
  - **The review transition's timing is a setting, and the automatic move is opt-in.**
    `tracker.transitions.reviewWhen` is one of:
    - `gates-passed`: `open-pr` applies it once the PR is open and the gates pass at its head;
    - `pr-open`: `open-pr` applies it as soon as the PR opens;
    - `pipeline-green`: `pr-review` applies it once the pipeline's review is current at the PR's head and its checks
      are green. It needs `review.pr`, and `doctor` fails it without one.

    With `reviewWhen` absent, nothing moves automatically. The lane commands print the move for the lane, as the brief
    does today. `doctor` names the setting. `lead-setup` asks for it when drafting the delivery's settings and records
    the answer. It's never inferred from `review.pr`, so an adopter whose existing configuration has `review.pr` and
    no `reviewWhen` sees no change in when its tickets move.
  - **`handoff`** posts the hand-off update.
  - **Lane posts go through the ledger.** Every post and move a lane command makes takes the publisher's lock and
    writes its ledger line under the journal id of the entry it journals. When the tracker call fails, the command
    writes no line, says the publisher will retry, and doesn't fail. A Jira outage never blocks a hand-off or a lane
    start.
  - **Trackers reachable only through the host.** The commands print the exact move and post for the lane to make
    with its own connector, and record that they printed them.
  - **The turn rule.** The brief tells lanes to wait inside their turn: a lane ends a turn only to hand off, to report
    itself blocked, or to ask the lead, never just to wait for a reviewer or the pipeline.
- **C6. Close-out runs itself, step by step.** When the close condition holds, the supervisor runs these steps. Each
  must succeed before the next.
  1. **Health check.** Run the binding's `fullHealth` in a fresh detached checkout of the final base, at most once per
     base head. Its result and output are kept in the flow log's `full-health` file. A failure shows in "Needs you" (C1)
     and holds the close until `fullHealth` passes, at a later head or on a `lead-resolve full-health` rerun.
  2. **Follow-ups.** File one follow-up item for each deferred finding that has none, keyed by story and finding id
     (`<story>-<finding id>`, since finding ids repeat across stories), which is also the `followups[]` item's `id`.
     Filing goes through the tracker publisher. A transient failure holds the close and is retried on the next pass.
     A permanent one writes `failed`, is shown, and isn't retried. A tracker reachable only through the host raises the
     filings as "Needs you" instead.
  3. **Close.** Journal `delivery.closed` and render, then wait until the tracker has every entry through
     `delivery.closed`. What that means depends on the tracker:
     - with a tracker publisher, its ledger covers that entry, skip lines included;
     - with a host publisher, `ack`'s `journalThrough` reaches it, and "Needs you" says so until then;
     - under `tracker.updates: "manual"`, no wait.
  4. **Archive.** Release every lease the lead holds, and archive the record. If the operator's checkout is clean and
     on the final base, fast-forward it. Otherwise name it in step 5, along with removing the lead's checkout once
     the operator no longer needs it.
  5. **Friction note.** Write a friction note stub beside the archive for the lead's `compound-learning` pass. It
     lists every shown item, naming each permanently refused follow-up as an unfiled follow-up. Then
     raise it as the one remaining "Needs you" item, together with the host steps the workflow can't do, such as
     archiving lane sessions.
  6. **Stop.**

  `lead-close` stays as the explicit command for closing early or when the supervisor is down. It asks a running
  supervisor to stop after its current pass, waits for it to release its lock, and then closes. It refuses while the
  lead's checkout has uncommitted changes to `.agents/` or the binding.
- **C7. A binding names the bases it serves.** The binding gains an optional `bases`, a list of exact names or `*`
  patterns, such as `["main", "integration-*"]`. `base.ref` stays the default.
  - **Matching.** A delivery file's `baseBranch` must match `base.ref` or one of `bases`. So must the merge queue's
    `--base`, `lane-start`'s `--base`, and `lead-setup`'s `--base`, which `lead-setup` requires when the binding
    declares `bases`.
  - **Where settings are read.**
    - A lead on a branch matching `bases` reads its working tree.
    - A lane reads from `<remote>/<base>`, with the base from `lane-start --base`, recorded in its worktree's
      run-delivery state, or from `RUN_DELIVERY_BASE`, always checked against `bases`.
    - The harness cross-check uses the delivery's base.
  - **No new field.** The base comes from the delivery file, never from the state's `meta`.
  - **Compatibility.** A binding without `bases` behaves exactly as today. A base cut before its binding gained `bases`
    still needs that setting on it, and `doctor` says so.
- **C8. A fresh record starts a fresh plan log.** `lead-init` refuses when the state directory holds a record, or the
  flow log holds a snapshot or a supervisor lock, and names the delivery, its base and when it started. Pre-init
  `lead-plan change` entries and `plan.lock` don't count. `lead-init --replace` stops any supervisor, confirms its pid is gone, archives both, carrying nothing over (every
  flow-log file names entries of the archived journal, whose ids the fresh journal reuses from 1),
  and starts fresh. The plan's approval snapshot is taken from the delivery file at that `lead-init`.
- **C9. Plan review for any plan.** `plan-review prepare|reduce|end --plan <path> --lenses <ids>` works without a
  delivery file, binding or record. It keeps its rounds in a store under
  `.orchestration/plan-review/<slug>/`, keyed by a `reviewSlug` in the plan's front matter, so renaming the plan file
  doesn't orphan its review. `prepare` refuses a plan without one and prints the line to add, so the author commits it
  before round 1's candidate. When the plan later becomes a delivery, `lead-setup` and `lead-init` adopt that
  store, so the review shows in the plan chapter. `plan-work`'s review section names the command.

## In scope, by unit

### Unit: Lead supervisor

- **Outcome.** C1, C4 and C6.
  - `lead-init` starts `lead-run`, and `lead-close` stops it.
  - `lead-reconcile` stays for one-off use, and its `--every` is replaced by `lead-run`.
  - `lead-accept` marks ready under C4.
  - **Forge adapters.** Both gain `ready` and `merge --strategy merge|squash`, which merges a pull request through the
    forge's API. The GitHub adapter gains `reviews`. The guide shows a binding whose `merge` argv calls the adapter, so
    an adopter no longer writes its own `merge.py`.
  - `lead-resolve <journal id> --why TEXT` resolves a blocker, or reruns a failed `fullHealth` (C1). Its function
    belongs to this unit, and its argument parsing to Setup.
  - `lead-accept --requeue <key>` re-queues a stopped candidate the lead has resolved, without a new hand-off from the
    lane.
  - `doctor --delivery` reports:
    - the supervisor's state;
    - a running `serve` whose signal dispositions aren't the defaults;
    - each duty the supervisor can't do here.
- **Owns.**
  - In `run_delivery.py`: the new `lead-run`, and changes to `lead-init`, `lead-accept`, `lead-close`, `lead-cleanup`
    and `lead-reconcile`.
  - `skills/run-delivery/adapters/forge_bitbucket.py` and `forge_github.py`.
  - The supervisor, `authority` and `forge` entries in `config.md`, and run-delivery's `SKILL.md` command table.
  - `tests/test_run_delivery.py`.
- **Depends on.** Setup, for `lead-init`'s refusals.
- **Required scenarios.** Supervisor, Signals, Ready, Requeue, Finish and close, Supervisor down.
- **Sensors.** `tests/test_run_delivery.py`, `scripts/validate-registry.py`.

### Unit: Tracker publisher

- **Outcome.** C2 and C3.
  - The tracker adapter contract (`tracker-adapter/1`: status, markers, comment, transition, create), the
    tracker-neutral publisher, and a Jira adapter under the same command-line shape and credential variables as the
    delivery-views Jira snapshot.
  - `lead-setup` adds the publisher to the binding when the configuration names a tracker with an adapter.
  - The supervisor's "Needs you" checks for a failing publisher and an undrained host outbox.
- **Owns.** The `{stateDirectory}` substitution in publisher argv in `skills/orchestrate-work/scripts/render_state.py`,
  with its case in `tests/test_orchestration_render.py`. Also a new `skills/run-delivery/scripts/tracker_publisher.py`
  and `skills/run-delivery/adapters/tracker_jira.py`,
  the contract in `config.md` beside the forge adapter contract, the publisher and `tracker.transitions` entries in
  `config.md`, `lead-setup`'s binding draft, the publisher checks in `run_delivery.py`, and a new
  `tests/test_run_delivery_tracker.py`.
- **Required scenarios.** Publisher, Adapter contract, Moves, Host outbox.
- **Sensors.** `tests/test_run_delivery_tracker.py`, `tests/test_run_delivery.py`.

### Unit: Lane commands

- **Outcome.** C5.
  - `lane-start` is added.
  - `open-pr`, `pr-review` and `handoff` make the lane's moves and posts through the tracker adapter, or print them
    for a tracker reachable only through the host.
  - The generated brief shrinks to match, and carries the turn rule.
- **Owns.** `lane-start`, `open-pr`, `pr-review` and `handoff` in `run_delivery.py`, the brief templates in
  `skills/run-delivery/templates/` other than `round-brief.md`, and the brief and command pins in
  `tests/test_run_delivery.py`.
- **Depends on.** Tracker publisher, for the adapter it calls. Bases, for `--base`.
- **Required scenarios.** Lane start, Review move, Brief.
- **Sensors.** `tests/test_run_delivery.py`.

### Unit: Setup refusals and doctor checks

- **Outcome.**
  - C8.
  - `--delivery` is accepted before or after the subcommand.
  - `doctor --delivery` reports, and `lead-init` refuses on, each of these:
    - a story with no plan section;
    - a `context.sections` heading the plan lacks;
    - a forge or tracker configured with no matching snapshot or publisher in the binding;
    - Node below the runtime's `nodeMinimum`;
    - a remote base missing the configuration, naming `RUN_DELIVERY_LOCAL=1` for local setup.
  - `lead-assign` refuses a story with no plan section.
  - `lead-setup` asks for the lanes' authority grant, and won't write a delivery file without one unless
    `--no-authority` says that's intended.
  - A forge or tracker adapter that prints nothing is a failure that names the adapter path it ran.
- **Owns.** In `run_delivery.py`: `lead-setup`, `lead-assign`, the argument parsing, `doctor`, the adapter-call
  wrapper, and the `lead-init` refusals. Also `tests/test_run_delivery.py`.
- **Required scenarios.** Stale record, Doctor gaps.
- **Sensors.** `tests/test_run_delivery.py`, `tests/test_run_delivery_plan_review.py`.

### Unit: Bases a binding serves

- **Outcome.** C7. Every command and the merge queue accept a delivery whose base matches `bases`. The guide's anchor
  for a base mismatch exists and explains `bases`.
- **Owns.**
  - The `bases` member of `orchestration-binding/1` in `skills/orchestrate-work/references/schemas.json`, and its
    schema pins.
  - `merge_queue.py`'s `read_binding` base check.
  - In `run_delivery.py`: config loading, `shelf_for` and the harness cross-check.
  - The base entries in `config.md` and `adopting.md`.
  - New tests in `tests/test_run_delivery.py` and `tests/test_merge_queue.py`, and the schema pins in
    `tests/test_orchestration_adopter.py`.
- **Required scenarios.** Bases.
- **Sensors.** `tests/test_run_delivery.py`, `tests/test_merge_queue.py`, `tests/test_orchestration_adopter.py`,
  `tests/test_registry.py`.

### Unit: Rounds before hand-off

- **Outcome.**
  - `review-reduce` writes each reduced round into the record as the lane's own round.
  - The view binds a planned story's evidence to its latest `review.round` candidate, rather than to a queue item's
    head, so the line map shows round cards and notes while review is open.
  - The placeholder says when rounds will appear.
  - `run-delivery view [--archive PATH]` serves the live record or the supervisor's `latest` copy without a checkout of
    the delivery, and prints its address.
- **Owns.**
  - In `run_delivery.py`: `review-reduce` and `view`.
  - `review_observations` in `skills/orchestrate-work/scripts/delivery_state.py`.
  - The review panel and placeholder in `skills/delivery-views/assets/line-map.js`.
  - Their tests: `tests/test_run_delivery.py`, `tests/test_orchestration_state.py`'s review-observation cases,
    `tests/test_delivery_views_page.py` and `tests/test_delivery_views_review_projection.py`.
- **Required scenarios.** Rounds before hand-off, Lost checkout.
- **Sensors.** Those tests.

### Unit: Never-started tickets

- **Outcome.** Reconcile sets a new `planned[].trackerStarted` when it first sees the story's ticket started, and
  reports a backlog ticket only for a story with `trackerStarted`.
- **Owns.**
  - The `planned[]` entry of `delivery-state/1` in `schemas.json`, and its pins.
  - The tracker branch of `reconcile` in `delivery_state.py`.
  - Its cases in `tests/test_orchestration_state.py`.
- **Depends on.** Bases, which edits `schemas.json` first, so the two never edit it at once.
- **Required scenarios.** Never started.
- **Sensors.** `tests/test_orchestration_state.py`, `tests/test_registry.py`.

### Unit: Scoped lenses

- **Outcome.** This closes issue #69. Both places that build lens briefs carry the lens's scoped-check rule:
  run-delivery's `templates/round-brief.md` and obtain-review's `round-brief-template.md`. The adversarial lens's
  persona, `personas/testing-policy.md`, limits mutation and fault injection to the candidate's changed lines and the
  code that calls them, and says so in its evidence.
- **Owns.** `skills/run-delivery/templates/round-brief.md`, `skills/obtain-review/references/round-brief-template.md`,
  the scope text in `skills/obtain-review/SKILL.md`, `personas/testing-policy.md`, and their pins in
  `tests/test_review_bound_corpus.py` and `tests/test_run_delivery.py`.
- **Required scenarios.** Scoped lens.
- **Sensors.** `tests/test_review_bound_corpus.py`, `tests/test_run_delivery.py`,
  `scripts/update-provenance.py --check`.

### Unit: Plan review for any plan

- **Outcome.** C9.
- **Owns.**
  - In `run_delivery.py`: the `plan-review` commands and the store adoption in `lead-setup` and `lead-init`.
  - The review section of `skills/plan-work/SKILL.md`.
  - `tests/test_run_delivery_plan_review.py`.
- **Depends on.** Setup, which edits `lead-init`.
- **Required scenarios.** Plan review without a delivery.
- **Sensors.** `tests/test_run_delivery_plan_review.py`, `scripts/update-provenance.py --check`.

### Unit: Guide and orchestration text

- **Outcome.**
  - **`adopting.md`.** Its walk-through becomes the short one C1, C5, C6 and C9 allow: plan and review, set up,
    assign, accept, answer.
    - **A worked adopter**, naming no real organization: Bitbucket, Jira, a pipeline that reviews pull requests, a
      protected default branch, and long-lived bases under C7, with the binding's `merge` argv calling the forge
      adapter and `reviewWhen: pipeline-green`. The existing GitHub walk-through stays beside it, and every setting the
      worked adopter changes is shown against its default.
    - **Repository setup:**
      - ignore entries for hooks and formatters, including the generated lane brief;
      - the gitignored `.claude/`;
      - the whitespace gate's exclusion;
      - a lint-only-changed-lines recipe;
      - a red baseline.
    - **Running:**
      - settings landing through reviewed PRs, and install updates in their own PR, which is the one home of
        "parallel deliveries each carry their own tooling update";
      - `lead-run --resume` after an install update or a token change;
      - merge strategy;
      - single-session test accounts;
      - why lanes create their own worktrees;
      - the lead working from a checkout of the delivery's base, which C7 reads.
    - **Host notes:** they name what the workflow can't do. That covers starting lane sessions, archiving them,
      queued lead messages, Bitbucket PRs the host can't watch, and session limits.
    - Every example that pushes to `main` either opens a PR instead or is labelled for an unprotected base.
  - **The orchestration guide's lead duties.** Its close-out list and tracker-update table say which duties
    run-delivery does. They also say the supervisor judges `finish.met` by the record's covers and proofs, while a
    finish line the record can't prove stays "Needs you". So a lead reading the guide doesn't do them twice, and
    hand-journalled entries are skipped.
- **Owns.** `skills/run-delivery/references/adopting.md`, the lead-duty passages of
  `skills/orchestrate-work/references/orchestration-guide.md` and `SKILL.md`, and their pins.
- **Depends on.** Every other unit.
- **Required scenarios.** Guide.
- **Sensors.** `tests/test_run_delivery.py`, `scripts/validate-registry.py`.

## Out of scope

| Gap | Why it's out |
| --- | --- |
| Accepting a hand-off automatically | Acceptance is the lead's check of a claim. `lead-accept` already does its mechanical checks. |
| Starting and archiving lane sessions; lead messages queued until a lane's turn ends; Bitbucket PRs the host can't watch; sessions not startable from a turn another session triggered; session limits and context exhaustion | Host behaviour. The guide's host notes record these, and the supervisor's record and C5's turn rule reduce what a compaction loses. |
| The merge queue not checking the PR review itself | `lead-accept` checks it at the reviewed head. A lane pushing after hand-off is refused by `lead-accept`'s head check. Follow-up. |
| The lead's app server holding the in-app port | In-app checks. Follow-up. |
| Lead-only evidence notes lost at compaction | In-app evidence. Follow-up. |
| `ship`'s path-based stripping | Follow-up. |
| Adopters writing their own `candidate.py` | It resolves a story's candidate from repository conventions that differ by adopter. The guide's worked adopter shows one. Follow-up for a ready-made one. |
| No early check of lane ownership against the plan's file lists | By design, ownership is the plan's. Follow-up for a `doctor` hint. |
| Sharing the live view outside the machine | Needs a publisher design. Follow-up. |
| A record that continues past merge (deploy, QA) | Deploying is outside the delivery at the adopters in evidence, and differs by adopter. Follow-up. |
| One record for an epic that spans repositories | Follow-up. |
| Installed product size, and a user-level `delivery-flow` skill duplicating run-delivery | The first is lifecycle, which V26-3041 leaves untouched. The second is on the operator's machine, not in this corpus. |
| `__pycache__` blocking the install, and `status` not checking Node | Lifecycle. The Node check is added to run-delivery's `doctor` instead. |
| Scope changes during review rounds, and plan review's overall cost | Process, plus the scoped-check rule above. |
| `build` needing a story key, and the tracker-properties validator needing `AGENTS.md` | Minor. Follow-ups. |
| eshyftnew's `CLAUDE.md` naming a test helper that doesn't exist | The adopter's own fix. |

## Finish line

- Every unit is merged to the default branch. On the merged tree, `scripts/run-tests.py` and
  `scripts/validate-registry.py` pass, and CI is green on all three operating systems.
- The first version-tagged release after the merge carries these changes.
- eshyftnew installs that release through its own PR. Then a one-story delivery runs there:
  - **The lead** runs only `plan-review`, `lead-setup`, `lead-init`, `lead-assign` and `lead-accept`.
  - **The lane** runs only its commands.
  - **What must happen:**
    - the stop moves when the lane starts;
    - round cards show before hand-off;
    - every Jira update and move the configuration names happens once;
    - the accepted PR is marked ready and merged;
    - the delivery closes by itself, leaving only the friction note and its host steps.

  The lead of that delivery records any remaining friction in the friction log.
- Every out-of-scope follow-up has a Linear item, and the friction logs' entries name their disposition.

## Dependencies

1. **V26-3041 integrated to the default branch.** All units start after it. It changes how releases are cut, the
   current-set test pins, the catalog and provenance, which every unit here touches. Its lane ruling also edits the
   orchestration guide that the last unit edits.
2. **Order of units.**
   - **First, in parallel:** Setup, Tracker publisher, Bases, Rounds before hand-off and Scoped lenses.
   - **After Setup:** Lead supervisor, and Plan review for any plan.
   - **After Bases:** Never-started tickets.
   - **After Tracker publisher and Bases:** Lane commands.
   - **Last:** Guide and orchestration text.
3. **The finish line's eshyftnew delivery** needs the first version tag. Cutting it is the owner's decision under
   V26-3041.

## Test scenarios

- **Supervisor.**
  - `lead-init` starts one `lead-run`. A second `lead-init` refuses. `--replace` stops the first supervisor before
    starting another, and only one lock is held.
  - Its first line names the live view's address.
  - Each pass reconciles, renders, copies `latest`, and keeps `serve` running.
  - With an idle queue, a journal entry written only by a lane or lead command reaches the fake Jira within one pass.
- **Signals.** Started from a parent that ignores SIGINT and SIGHUP, a check spawned under the supervisor's `serve`
  sees the default dispositions.
- **Ready.**
  - With the grant, `lead-accept` marks the draft ready before releasing it. `serve`, running throughout, merges it
    with no `merge_refused`, including while the supervisor is stopped.
  - A failed ready leaves the hand-off pending and names the adapter.
  - With `forge.mergeNeedsReady` and no grant, `lead-init` refuses and names the grant.
- **Requeue.** A candidate stopped with `merge_refused` merges after `lead-accept --requeue`, without a new hand-off.
- **Finish and close.**
  - When the last criterion's proofs are done, `finish.met` is journalled once, including when the lead also
    journalled it by hand.
  - Each of these holds the close:
    - a planned, unassigned story not covered by any finish line;
    - an open escalation;
    - a failing publisher.
  - A failing `fullHealth` holds the close and isn't rerun until the base head changes or `lead-resolve full-health`
    asks for a rerun.
  - A failed follow-up filing holds the close, and is retried and filed once.
  - With a host publisher, the close waits until the outbox is acked through `delivery.closed`. Under
    `tracker.updates: "manual"`, it doesn't wait.
  - A story removed through `lead-plan change --kind removed` doesn't hold the close.
  - After a red `fullHealth` and a green one at a later base head, the delivery closes. A `lead-resolve` of a red
    `fullHealth` at the same head reruns it.
  - A broken base, once repaired, doesn't hold the close: the runner's blocker clears with `base.repaired`.
  - A permanently refused follow-up filing is shown, not retried, and doesn't hold the close.
  - A lane's `blocker.recorded` holds the close until `lead-resolve` resolves it, and then the delivery closes.
  - A host-only tracker with a host publisher passes `doctor` and `lead-init`.
  - Cleanup of a merged story waits until its lane has moved off it.
  - On success, the fake Jira holds the `delivery.closed` update. Then the record is archived, the friction stub is
    written, and the supervisor stops.
- **Supervisor down.** With `lead-run` killed:
  - `doctor --delivery` and each lead command report it;
  - `lead-run --resume` adopts or stops the orphaned children, and posts nothing twice;
  - a `lead-cleanup` run by hand meanwhile isn't repeated.
- **Publisher.** Against a fake Jira that, like real Jira without an app, refuses JQL on issue properties:
  - each journal kind gets the guide's update once, except an entry whose ledger already holds an action line, and
    `decision.recorded` entries outside the subjects and writers C2 names, which get `skip` lines;
  - when entry 11 fails and entry 12 succeeds, the next run posts only 11;
  - two renders running at once post each entry once;
  - a process killed after the POST and before the ledger line posts nothing again;
  - a 401 raises "Needs you", and once the token is fixed the lag clears, "Needs you" empties, and the close isn't
    held;
  - a `blocker.recorded` entry with 40,000 characters of output posts one comment truncated to the limit;
  - an entry rejected with 400 gets one `failed` line over three runs, later entries are still posted, the journal
    doesn't grow, and the close isn't held by it;
  - a follow-up created before a crash is found by its label and not filed again;
  - two stories deferring the same finding id each get their own follow-up, once;
  - a 409 is retried, and a 403 that the epic read also returns is retried; a 403 on one ticket alone is permanent;
  - a `handoff` whose own post got a 401 still hands off, and the next publisher run posts its update once;
  - a runner write during a publisher backlog doesn't wait: the second run exits at once.
- **Moves.** With `tracker.transitions.lead` configured:
  - a move whose status was already reached is skipped, and reported as made by someone else;
  - a move not offered gets one `handled` line, is shown in "Needs you", and journals nothing;
  - with `lead` absent, no later move is made and the update says so;
  - with `start` set and `lead` absent, a story's start move and update happen once, when its `lane-start` runs;
  - with neither set, no move is made and the update says so;
  - a two-story `lead-assign` moves neither ticket until each one's `lane-start`, then moves each once;
  - a story removed before its `lane-start` never moves, and its entry gets a `skip` line;
  - a story reassigned after it reached review gets no second start move;
  - a story moved to review by hand, then handed off and merged with no `lane-start`, keeps its review status, gets
    one `skip` line for its `lane.assigned`, and the delivery closes;
  - a publisher run started between a `lead-plan change`'s journal write and its mark still posts the update once;
  - an unmarked `decision.recorded`, such as an acceptance, gets a `skip` line, while a `lead-plan change`'s is
    posted.
- **Adapter contract.** The publisher, run against a second fake adapter that implements only `tracker-adapter/1`
  (no Jira code), posts, moves and files exactly as it does against the Jira adapter.
- **Host outbox.** With a tracker reachable only through the host, an undrained host outbox raises "Needs you", naming
  the waiting updates.
- **Lane start.**
  - `lead-assign` journals `lane.assigned`. After `lane-start` records the story's start, the publisher applies the
    start transition and posts the start update once.
  - `lane-start` creates the worktree from `--base` and sets focus, and makes no tracker call of its own.
  - Run twice, it changes nothing the second time.
  - A base outside `bases` is refused.
  - With a tracker reachable only through the host, it prints the move and the post and records them.
- **Review move.**
  - With no `reviewWhen`, nothing moves and `open-pr` prints the move, whether or not `review.pr` is set, and `doctor`
    names the setting.
  - With `gates-passed`, `open-pr` makes the move once the gates pass.
  - With `pr-open`, `open-pr` makes it as the PR opens.
  - With `pipeline-green`, `open-pr` makes no move and `pr-review` makes it once the fake pipeline is green.
  - `pipeline-green` without `review.pr` fails `doctor`.
- **Brief.** The brief lists the lane commands in place of the manual tracker steps, and carries the turn rule.
- **Stale record.**
  - `lead-init` over an existing record or supervisor lock refuses and names it.
  - A pre-init `lead-plan change` doesn't count.
  - `--replace` archives the record and the flow log, and an edit made between the old init and the new one isn't
    reported as drift.
  - After `--replace`, a new entry whose journal id equals one the old delivery posted to the same ticket is posted,
    for a delivery with criteria and for one without.
  - After `--replace`, a story whose old delivery only commented on its ticket still gets its first start.
  - A story in review before `--replace`, re-assigned after it and given a new `lane-start`, keeps its review status.
    Its new `lane.assigned` gets a `skip` line, because the ticket holds the same delivery's start marker under the
    earlier init id.
- **Doctor gaps.** `doctor --delivery` reports each of these, and `lead-init` refuses on each:
  - a story without a plan section;
  - a missing `context.sections` heading;
  - a Jira tracker with no publisher;
  - a forge with no snapshot;
  - Node below the minimum;
  - a remote base without the configuration.
- **Bases.** With a binding that has `bases: ["main", "integration-*"]`, and a delivery file committed only on
  `integration-2`:
  - the lead's `doctor`, `lead-init` and `lead-brief` pass, as do a lane's `lane-start`, `gate` and `open-pr`, and a
    queue merge;
  - `release/1` is refused, with the guide anchor;
  - a binding without `bases` refuses `integration-2` exactly as today.
- **Rounds before hand-off.** After `review-reduce` round 1, before hand-off, the line map shows the round card and
  its notes for the planned story.
- **Lost checkout.** After the lead's checkout is deleted, `run-delivery view --archive` serves `latest`, which holds
  the latest round. Its state and journal come from the same write.
- **Never started.**
  - A draft PR moves a story to building while its ticket is still in backlog, and no report follows.
  - A story whose ticket was started and then went back to backlog is reported.
- **Scoped lens.**
  - A brief generated by `review-prepare` carries the scoped-check rule, and so does obtain-review's template.
  - Each corpus pin proves red when the rule is removed, with the digest refreshed as `AGENTS.md` directs.
- **Plan review without a delivery.**
  - `plan-review prepare --plan docs/plans/x.md --lenses …` runs in a repository with run-delivery's config but no
    delivery file, binding or record, and `reduce` and `end` follow.
  - A later `lead-setup` for that plan adopts the store, and the plan chapter shows the rounds.
- **Guide.**
  - The guide pins name the supervisor, `lane-start`, `bases`, the adapter merge, the formatter ignore list, the
    settings-PR rule and the base-mismatch anchor.
  - No unlabelled push to `main` remains.
  - The orchestration guide says which duties run-delivery does.

## Test dispositions

- `tests/test_run_delivery.py`:
  - the subcommand inventory gains `lead-run`, `lead-resolve`, `lane-start` and `view`, plus the `--requeue`, `--plan` and `--base`
    options;
  - the brief text and round-brief pins change;
  - the guide pins change;
  - the `lead-init` refusal text pin changes;
  - tests that call `lead-reconcile --every` move to `lead-run`.
- `tests/test_run_delivery_plan_review.py`: the store keyed by plan path, and the adoption into a delivery.
- `tests/test_merge_queue.py`: the `--base` refusal text, and the new `bases` acceptance.
- `tests/test_orchestration_adopter.py` and `tests/test_registry.py`: the schema's new `bases` member and
  `planned[].trackerStarted`.
- `tests/test_orchestration_state.py`: the never-started report and the planned-story review observations.
- `tests/test_delivery_views_page.py` and `tests/test_delivery_views_review_projection.py`: the panel binding and the
  placeholder wording.
- `tests/test_review_bound_corpus.py`: the new round-brief pins.
- **Orchestration text:** pins on the lead-duty passages of the orchestration guide, wherever V26-3041 leaves them.
- **Catalog and provenance:** each unit that changes a releasable skill updates its catalog entry and refreshes digests
  with `scripts/update-provenance.py`, as `AGENTS.md` directs.

## Shared resources

- **Repositories and servers.** Tests use temporary repositories, remotes, and fake forge and tracker servers created
  inside each test, bound to port 0. Fixtures name no real organization, repository or ticket, as `AGENTS.md`
  requires.
- **Background processes.** Supervisor tests start real background processes. Their classes are marked
  `run_serially = True`, and they stop every process they start, so they don't add to V26-2951's leaks.
- **Flow-log files.** Every new lead-side file is named with the delivery key, as `flow_log()` names every lead-side
  log today.
- **Shared files.**
  - `run_delivery.py` is edited by several units. Setup lands before Lead supervisor and Plan review. Lane commands
    and Rounds touch only their own commands.
  - `schemas.json` is edited by Bases, then by Never-started.
  - The tracker adapter is landed by Tracker publisher before Lane commands uses it.

## Sensors

Each unit's tests are listed with it. On the merged tree:
- `scripts/validate-registry.py`;
- `scripts/update-provenance.py --check`;
- `scripts/run-tests.py`;
- CI on Linux, macOS and Windows. run-delivery is POSIX-only, so its tests skip on Windows as they do today.

The finish line's eshyftnew delivery is the end-to-end sensor.

## Decisions recorded

The owner approved A1 to A5 as written on 2026-11-02, and decided A6: the friction logs stay uncommitted.

- **A1. The supervisor.** C1 makes one background process per delivery responsible for every mechanical lead duty,
  including merging accepted work and closing the delivery. The alternative was commands that do their own duties and
  print the rest. That is less machinery, but it leaves the sweep, merges and close-out to memory, which the logs show
  fails.
- **A2. Automatic tracker writes.** C2 and C3 have the workflow post to and move tickets through a tracker adapter
  (Jira's API for the shipped one),
  without the lead acting on each one, and file follow-up items at close.
- **A3. Turn rule.** C5 makes lanes wait inside their turn.
- **A4. Bases.** C7 lets one binding serve several bases.
- **A5. Sequencing.** Every unit waits for V26-3041 to integrate.
- **A6. Friction logs.** They stay uncommitted, in the owner's checkout at `docs/delivery/`. This plan cites them by
  path as evidence outside the tree.

C9 was added after the owner noted that this plan's own review had to be composed by hand. After round 1, the owner
had the plan made adopter-neutral: every behaviour that differs between adopters is a setting, and the review
transition's timing became `reviewWhen` rather than being inferred from `review.pr`.

## Approval handoffs

- **Tracking.** After plan review aligns, one Linear epic in the agent delivery harness project, with one child item
  per unit and one per out-of-scope follow-up. No tracker change has been made yet.
