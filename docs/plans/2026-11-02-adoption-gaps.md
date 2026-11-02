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
"Needs you" and holds the close. The workflow also adopts from the guide alone into a repository like eshyft's,
delivers to any long-lived base without editing shared settings, and every refusal says how to recover.

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
- **`serve` takes a candidate as soon as it is accepted.** It runs any queued item that isn't pending. eshyft's
  `merge.py` refuses a draft, and a stopped candidate waits for its lane to hand it off again.
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
  - **Liveness.** Liveness is the lock `lead-run` holds on `flow-log/supervisor.lock`, which also records its pid,
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
  - **Close condition.** Every planned and queued story is merged, or recorded as dropped by a lead decision. Every
    finish line is met. The queue is empty. Every lane is idle or done. No escalation or "Needs you" item is open.
  - **Lead commands** keep working when the supervisor is down, so a lead can always act by hand. The supervisor
    skips anything already done when it resumes.
  - **Ready marks.** The supervisor never marks a pull request ready (C4). It reports a candidate that was accepted as
    a draft while `forge.mergeNeedsReady` was set.
- **C2. Tracker updates are a publisher with its own ledger.** run-delivery ships a Jira tracker publisher that the
  binding names in `state.publishers`, so it runs whenever the render wrapper runs (C1 step 4, and the runner's
  writes).
  - **Ledger and lock.** It doesn't use `ack`. It keeps its own ledger, `tracker-published.jsonl` in the state
    directory, with one line per journal id and action: comment, transition or create. It holds its own file lock for
    the whole run.
  - **Ordering.** It processes journal entries in id order and stops at the first failure. It adds the ledger line
    after each successful action, and the next run retries from there.
  - **Idempotency.** Each comment, move and created follow-up carries its journal id as a Jira entity property. Before
    writing, the publisher looks for that marker on the ticket, so a crash between the write and the ledger line
    doesn't produce a second post or a second issue.
  - **What it posts.** It posts one update per journal kind, using the orchestration guide's table, except:
    - kinds a lane command already posted (C5);
    - `decision.recorded` entries the writing command didn't mark for the tracker. Acceptances, cleanups and
      registrations aren't marked; plan changes, deferrals and blockers are.
  - **Failure.** A failure lasting more than one pass raises "Needs you" in the record, naming the publisher, its last
    error and the waiting entries. It holds the close.
  - **Linear.** The host's connector is the only way to reach Linear, and a process can't call it. A Linear repository
    uses the existing host publisher, and the supervisor raises an undrained outbox as "Needs you", naming the updates
    waiting.
  - **Refusals.** A binding with a Jira tracker but no tracker publisher fails `doctor`. `lead-init` refuses it unless
    `run-delivery.json` sets `tracker.updates: "manual"`.
- **C3. Every tracker move is configuration.** `run-delivery.json` `tracker.transitions` keeps `start` and `review`
  for lanes. It gains `lead`, an object from journal kind to the transitions the publisher makes. If `lead` is absent,
  or a kind maps to an empty list, no move is made and the update says the move belongs to people, which matches
  eshyft's workflow. Each move reads the ticket's status first:
  - one already made by a person or an integration is skipped, and reported as made by someone else;
  - one not offered from the current status raises one blocker in the record, and is recorded in the ledger as
    handled, so it isn't retried or raised again.
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
    3. applies the start transition, posts the start update, and sets the lane's focus.

    It replaces four steps the brief lists today. `lead-assign` prints it with the base filled in.
  - **The review transition** is applied by `pr-review` once the pipeline's review is current at the PR's head and its
    checks are green, when `review.pr` is configured. Without `review.pr`, `open-pr` applies it once the gates pass at
    the PR's head.
  - **`handoff`** posts the hand-off update.
  - **Linear.** The commands print the exact move and post for the lane to make with its own connector, and record
    that they printed them.
  - **The turn rule.** The brief tells lanes to wait inside their turn: a lane ends a turn only to hand off, to report
    itself blocked, or to ask the lead, never just to wait for a reviewer or the pipeline.
- **C6. Close-out runs itself, step by step.** When the close condition holds, the supervisor runs these steps. Each
  must succeed before the next.
  1. **Health check.** Run the binding's `fullHealth` in a fresh detached checkout of the final base, at most once per
     base head. If it fails, journal `blocker.recorded` with its output, raise "Needs you", and hold the close until
     the base head changes.
  2. **Follow-ups.** File one follow-up item for each deferred finding that has none, keyed by finding id against
     `followups[]`, through the tracker publisher. A failed filing holds the close and is retried on the next pass. A
     Linear tracker raises the filings as "Needs you" instead.
  3. **Close.** Journal `delivery.closed` and render, then wait for the publisher's ledger to cover every entry through
     `delivery.closed`.
  4. **Archive.** Archive the record. If the operator's checkout is clean and on the final base, fast-forward it.
     Otherwise name it in step 5.
  5. **Friction note.** Write a friction note stub beside the archive for the lead's `compound-learning` pass, and
     raise it as the one remaining "Needs you" item, together with the host steps the workflow can't do, such as
     archiving lane sessions.
  6. **Stop.**

  `lead-close` stays as the explicit command for closing early or when the supervisor is down. It takes the
  supervisor's lock, waiting for any pass in progress, and refuses while the lead's checkout has uncommitted changes to
  `.agents/` or the binding.
- **C7. A binding names the bases it serves.** The binding gains an optional `bases`, a list of exact names or `*`
  patterns, such as `["main", "dev-alt-*"]`. `base.ref` stays the default.
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
  `lead-plan change` entries and `plan.lock` don't count. `lead-init --replace` stops any supervisor, archives both,
  and starts fresh. The plan's approval snapshot is taken from the delivery file at that `lead-init`.
- **C9. Plan review for any plan.** `plan-review prepare|reduce|end --plan <path> --lenses <ids>` works without a
  delivery file, binding or record. It keeps its rounds in a store keyed by the plan's path, under
  `.orchestration/plan-review/<slug>/`. When the plan later becomes a delivery, `lead-setup` and `lead-init` adopt that
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
  - A Jira tracker publisher ships with run-delivery, under the same command-line shape and credential variables as
    the delivery-views Jira snapshot.
  - `lead-setup` adds it to the binding when the configuration names Jira.
  - The supervisor's "Needs you" checks for a failing publisher and an undrained Linear outbox.
- **Owns.** A new `skills/run-delivery/adapters/tracker_jira.py`, the publisher and `tracker.transitions` entries in
  `config.md`, `lead-setup`'s binding draft, the publisher checks in `run_delivery.py`, and a new
  `tests/test_run_delivery_tracker.py`.
- **Required scenarios.** Publisher, Moves, Linear outbox.
- **Sensors.** `tests/test_run_delivery_tracker.py`, `tests/test_run_delivery.py`.

### Unit: Lane commands

- **Outcome.** C5.
  - `lane-start` is added.
  - `open-pr`, `pr-review` and `handoff` make the lane's moves and posts through the tracker adapter, or print them
    for Linear.
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
    - **A worked adopter:** Bitbucket, Jira, an AI PR reviewer, and long-lived dev-alt bases under C7, with the
      binding's `merge` argv calling the forge adapter.
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
      - why lanes create their own worktrees.
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
| No early check of lane ownership against the plan's file lists | By design, ownership is the plan's. Follow-up for a `doctor` hint. |
| Sharing the live view outside the machine | Needs a publisher design. Follow-up. |
| A record that continues past merge (deploy, QA) | Deploying belongs to people at eshyft. Follow-up. |
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
  - A failing `fullHealth` holds the close and isn't rerun until the base head changes.
  - A failed follow-up filing holds the close, and is retried and filed once.
  - On success, the fake Jira holds the `delivery.closed` update. Then the record is archived, the friction stub is
    written, and the supervisor stops.
- **Supervisor down.** With `lead-run` killed:
  - `doctor --delivery` and each lead command report it;
  - `lead-run --resume` adopts or stops the orphaned children, and posts nothing twice;
  - a `lead-cleanup` run by hand meanwhile isn't repeated.
- **Publisher.** Against a fake Jira:
  - each journal kind posts the guide's update once, except kinds a lane command posted, and unmarked
    `decision.recorded` entries;
  - when entry 11 fails and entry 12 succeeds, the next run posts only 11;
  - two renders running at once post each entry once;
  - a process killed after the POST and before the ledger line posts nothing again;
  - a 401 raises "Needs you".
- **Moves.** With `tracker.transitions.lead` configured:
  - a move whose status was already reached is skipped, and reported as made by someone else;
  - a move not offered raises one blocker, once;
  - with `lead` absent, no move is made and the update says so.
- **Linear outbox.** An undrained host outbox raises "Needs you", naming the waiting updates.
- **Lane start.**
  - `lane-start` creates the worktree from `--base`, applies the start transition, posts the start update and sets
    focus.
  - Run twice, it changes nothing the second time.
  - A base outside `bases` is refused.
  - With Linear, it prints the move and the post and records them.
- **Review move.**
  - With `review.pr` configured, `open-pr` makes no move, and `pr-review` makes it once the fake pipeline is green.
  - Without `review.pr`, `open-pr` makes it.
- **Brief.** The brief lists the lane commands in place of the manual tracker steps, and carries the turn rule.
- **Stale record.**
  - `lead-init` over an existing record or supervisor lock refuses and names it.
  - A pre-init `lead-plan change` doesn't count.
  - `--replace` archives the record and the flow log, and an edit made between the old init and the new one isn't
    reported as drift.
- **Doctor gaps.** `doctor --delivery` reports each of these, and `lead-init` refuses on each:
  - a story without a plan section;
  - a missing `context.sections` heading;
  - a Jira tracker with no publisher;
  - a forge with no snapshot;
  - Node below the minimum;
  - a remote base without the configuration.
- **Bases.** With a binding that has `bases: ["main", "dev-alt-*"]`, and a delivery file committed only on
  `dev-alt-10`:
  - the lead's `doctor`, `lead-init` and `lead-brief` pass, as do a lane's `lane-start`, `gate` and `open-pr`, and a
    queue merge;
  - `release/1` is refused, with the guide anchor;
  - a binding without `bases` refuses `dev-alt-10` exactly as today.
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
  - the subcommand inventory gains `lead-run`, `lane-start` and `view`, plus the `--requeue`, `--plan` and `--base`
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

- **Repositories and servers.** Tests use temporary repositories, remotes, and fake forge and Jira servers created
  inside each test, bound to port 0.
- **Background processes.** Supervisor tests start real background processes. Their classes are marked
  `run_serially = True`, and they stop every process they start, so they don't add to V26-2951's leaks.
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
- **A2. Automatic tracker writes.** C2 and C3 have the workflow post to and move tickets in Jira through its API,
  without the lead acting on each one, and file follow-up items at close.
- **A3. Turn rule.** C5 makes lanes wait inside their turn.
- **A4. Bases.** C7 lets one binding serve several bases.
- **A5. Sequencing.** Every unit waits for V26-3041 to integrate.
- **A6. Friction logs.** They stay uncommitted, in the owner's checkout at `docs/delivery/`. This plan cites them by
  path as evidence outside the tree.

C9 was added after the owner noted that this plan's own review had to be composed by hand.

## Approval handoffs

- **Tracking.** After plan review aligns, one Linear epic in the agent delivery harness project, with one child item
  per unit and one per out-of-scope follow-up. No tracker change has been made yet.
