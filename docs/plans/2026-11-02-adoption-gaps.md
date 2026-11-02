---
title: "feat: The workflow does the lead's and lane's mechanical duties itself"
type: feat
status: in-review
date: 2026-11-02
execution_posture: test-first
context: Three leads delivered with run-delivery and orchestrate-work in eshyft's repositories, which use Bitbucket, Jira, protected long-lived dev-alt branches and an AI PR reviewer. Their friction logs hold about 100 entries. About half are fixed upstream or belong to work already in flight. Most of the rest are one failure repeated. The workflow names a duty in a guide or brief, and nothing does it, so it waits for someone to remember. The other causes are a binding that serves one base, refusals that don't say how to recover, and a guide that assumes GitHub and an unprotected `main`.
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
- archiving the record as it changes;
- the close-out.

A lane runs commands, and the commands make its tracker moves and posts.

A duty the workflow can't do in a given repository, because a grant, credential, adapter or setting is missing, is
reported by `doctor` and refused at `lead-init`. It never surfaces mid-delivery as something forgotten. The workflow
also adopts from the guide alone into a repository like eshyft's, delivers to any long-lived base without editing
shared settings, and every refusal says how to recover.

## Evidence behind this plan

Three friction logs are the sources. Each entry gives a timestamp and a quote.

- `docs/delivery/eshyft-pilot-friction.md`: the SHFT-7049 lead in eshyftnew, 2026-10-30 to 11-02.
- `docs/delivery/eshyft-pilot-friction.workflow-pilot.md`: the workflow pilot lead's log, mined from its transcript.
  It covers SHFT-6934 in eshyft-admin-dashboard, SHFT-6931/7109 in eshyftnew, and V26-3037 here, 2026-10-24 to 11-02.
- `docs/delivery/eshyft-pilot-friction.shft-7056.md`: the SHFT-7056 lead's log, mined from its transcript. It covers
  nine lanes in eshyft-admin-dashboard, plus run-delivery's own delivery, 2026-10-28 to 10-30.

The forgotten duties across the three logs:

- **The reconcile sweep:** never started in any of the three deliveries until the operator noticed stale stops.
- **"Start work":** missed or late in all three.
- **Tracker updates on hand-off, merge, finish and close:** all skipped in SHFT-7049.
- **The epic's moves:** forgotten in SHFT-7056.
- **Follow-up tickets:** never filed for deferred findings in SHFT-7056.
- **Marking PRs ready:** done by hand on every PR in SHFT-7109 and SHFT-7049.
- **Housekeeping at close:** handed back to the operator in SHFT-7109.
- **The friction log:** started only when the operator asked.
- **The live view's address:** asked for three times in SHFT-7056.

Causes confirmed in this repository at `main` ebb3aec:

- **One base per binding.** `skills/run-delivery/scripts/run_delivery.py` refuses a delivery file whose `baseBranch`
  differs from the binding's `base.ref`. `merge_queue.py --base` requires the binding to name the same base.
- **The plan log outlives the state.** It lives in `<state>/../flow-log`, not in the state directory. Setting aside a
  stale state and running `lead-init` again kept the earlier approval snapshot, so a later edit was recorded as drift
  "by unrecorded".
- **Forge adapters can't mark ready.** The Bitbucket adapter answers `find`, `open` and `reviews`. The GitHub adapter
  has no `reviews`. Nothing can mark a pull request ready, and eshyft's `merge.py` refuses a draft.
- **A false backlog warning.** `delivery_state.py reconcile` reports a planned story whose ticket was never started as
  "back in the tracker's backlog: check it".
- **Machinery that already exists and goes unused:**
  - the binding's `state.publishers` runs a command after every state write;
  - `merge_queue.py serve` merges candidates as they come;
  - `lead-reconcile --every` sweeps;
  - `lead-close` archives.

  Nothing starts the queue and the sweep, and no publisher is shipped for a tracker.

## Disposition of every open entry

Each open entry is in exactly one place: a unit below, an in-flight item, or the out-of-scope list. Entries the
sources mark as fixed are not repeated here. The fixes they name are run-delivery-v1, review-evidence-v1,
jira-tracker-v1, plan-review-v1 and draft-building-v1.

In-flight items that already own an entry:

- **V26-3051** (tracker profiles build on run-delivery): "run-delivery and jira can't be combined", and the Jira
  `tracker-properties.json` shape.
- **V26-3041** (tag-driven releases): every entry about release cadence, requalification, qualification records,
  release names, and "an upstream release invalidated the requalification". It also owns standing docs for the
  release flow, and the lane ruling (V26-3047): "Whether lanes are subagents or sessions".
- **V26-3050** (agent-skills runs its own deliveries): a delivery with a plan but no `plan.json`, and a live view
  started without its home.
- **GitHub issue #69** (round brief carries the lens's scoped-check rule): the adversarial lens mutation-testing a
  whole module. Unit "Review rounds" below closes it.
- **V26-2951**: the run-delivery tests leaving `http.server` processes behind.

## Contract decisions

- **C1. The lead supervisor.**
  - **Lifetime.** `lead-init` starts `run-delivery lead-run`, one detached process per record. It holds a lock in the
    flow log, records its pid in the state's `meta`, and survives the lead's session ending. `lead-close` stops it.
    `doctor --delivery` and every lead command report when it isn't running, and restart it with `lead-run --resume`.
  - **What it runs.** It runs and keeps alive the merge queue's `serve`, the reconcile sweep, and the live view. Its
    first line of output is the live view's address. Every lead command repeats that address.
  - **Each pass**, in order:
    1. reconcile the forge and tracker snapshots;
    2. mark each accepted candidate's draft pull request ready (C4) and leave the merge to `serve`;
    3. after each merge, do what `lead-cleanup` does today;
    4. journal `finish.met` for each finish line whose criteria and proofs are all done;
    5. copy the record and the flow log to the archive location `lead-close` uses;
    6. when every finish line is met, the queue is empty, no lane is working and no escalation is open, run the
       close-out (C6).
  - **Lead commands.** They keep working when the supervisor is down, so a lead can always act by hand, and the
    supervisor takes over again when it resumes.
- **C2. Tracker updates are a publisher.** run-delivery ships a tracker publisher that the binding names in
  `state.publishers`. After each state write it posts one update for each new journal entry, using the orchestration
  guide's table of journal kind to update. It also makes the transitions C3 configures for that kind, records what it
  published with the state tool's `ack`, and retries what failed on the next write.
  - **Jira** is reached through its REST API, with the credentials the Jira snapshot already uses.
  - **Linear** is reached only through the host's connector, which a process can't call. A Linear repository uses the
    existing host publisher, and the supervisor raises an undrained outbox as a "Needs you" item on the line map,
    saying which updates are waiting.

  A binding with a tracker but no tracker publisher fails `doctor`. `lead-init` refuses it unless `run-delivery.json`
  sets `tracker.updates: "manual"`.
- **C3. Every tracker move is configuration, made by a command.** `run-delivery.json` `transitions` keeps `start` and
  `review` for lanes, and gains `lead`, an object from journal kind to the transitions the publisher makes. If the
  object is absent, or a kind has an empty list, no move is made and the update says the move belongs to people, which
  matches eshyft's workflow. Each move reads the ticket's status first:
  - one already made, by a person or an integration, is skipped and reported as made by someone else;
  - one not offered from the current status is a blocker, raised in the record.
- **C4. Marking ready is a granted, automatic action.** The delivery file's `authority` gains `readyPullRequests`, and
  forge adapters gain `ready --url`. The supervisor marks an accepted candidate's draft pull request ready when that
  grant is present. When it is absent, `lead-init` refuses a delivery whose `review.pr` needs a ready pull request to
  merge, and says which grant to add. Lanes never get this grant.
- **C5. Lane commands make the lane's tracker moves.**
  - `lane-start <key>`, a new command, creates the story's worktree from the delivery base, runs the prepare step,
    applies the start transition, posts the start update and sets the lane's focus. It replaces four steps the brief
    lists today.
  - `open-pr` applies the review transition once the gates have passed at the PR's head.
  - `handoff` posts the hand-off update.

  The lane's own tracker tools remain only for its decisions and questions. The brief tells lanes to wait inside their
  turn: a lane ends a turn only to hand off, to report itself blocked, or to ask the lead, never just to wait for a
  reviewer or the pipeline.
- **C6. Close-out runs itself.** When C1's close condition holds, the supervisor:
  1. runs the binding's `fullHealth` in a fresh detached checkout of the final base;
  2. files one follow-up item for each deferred finding that has none, through the tracker publisher;
  3. journals `delivery.closed`;
  4. archives the record, leaving the operator's own checkout as it is;
  5. writes a friction note stub beside the archive for the lead's `compound-learning` pass, and raises it as the one
     remaining "Needs you" item;
  6. stops.

  `lead-close` stays as the explicit command for closing early or when the supervisor is down. It refuses while the
  lead's checkout has uncommitted changes to `.agents/` or the binding.
- **C7. A binding names the bases it serves.** The binding gains an optional `bases`, a list of exact names or `*`
  patterns, such as `["main", "dev-alt-*"]`. `base.ref` stays the default. A delivery file's `baseBranch` must match
  `base.ref` or one of `bases`, and so must the merge queue's `--base`. Every place that reads the base reads the
  delivery's base, recorded in the state's `meta`. A binding without `bases` behaves exactly as today. The settings
  still live on each base, which inherits them when it is cut from the default branch.
- **C8. A fresh record starts a fresh plan log.** `lead-init` refuses when the state directory or the flow log holds a
  record, and names the delivery, its base and when it started. `lead-init --replace` archives both and starts fresh.
  The plan's approval snapshot is taken from the delivery file at that `lead-init`.

## In scope, by unit

### Unit: Lead supervisor

- **Outcome.** C1, C4 and C6.
  - `lead-init` starts `lead-run`, and `lead-close` stops it.
  - `lead-reconcile --every` becomes a pass of `lead-run`. The command stays for one-off use.
  - `lead-finish` isn't needed, because finish lines are recorded by the supervisor.
  - Both forge adapters gain `ready`, and the GitHub adapter gains `reviews`.
  - `doctor --delivery` reports the supervisor's state and each duty it can't do here.
- **Owns.** The new `lead-run` in `run_delivery.py`, changes to `lead-init`, `lead-close`, `lead-cleanup` and
  `lead-reconcile`, `skills/run-delivery/adapters/forge_bitbucket.py` and `forge_github.py`, the supervisor and
  `authority` entries in `config.md`, run-delivery's `SKILL.md` command table, and `tests/test_run_delivery.py`.
- **Depends on.** Setup, for `lead-init`'s refusals.
- **Required scenarios.** Supervisor, Ready, Finish and close, Supervisor down. **Sensors.** `tests/test_run_delivery.py`,
  `scripts/validate-registry.py`.

### Unit: Tracker publisher

- **Outcome.** C2 and C3. A Jira tracker publisher ships with run-delivery, under the same command-line shape and
  credential variables as the delivery-views Jira snapshot. It maps journal kinds to updates and moves, reads status
  before each move, acknowledges what it published, and files follow-up items.
  - `lead-setup` adds the publisher to the binding when the configuration names Jira.
  - For Linear, the supervisor raises an undrained host outbox as "Needs you".
- **Owns.** A new `skills/run-delivery/adapters/tracker_jira.py`, the publisher and `transitions` entries in
  `config.md`, `lead-setup`'s binding draft, a new `tests/test_run_delivery_tracker.py`, and the outbox check in
  `run_delivery.py`.
- **Required scenarios.** Publisher, Moves. **Sensors.** `tests/test_run_delivery_tracker.py`,
  `tests/test_run_delivery.py`.

### Unit: Lane commands

- **Outcome.** C5. `lane-start` is added. `open-pr` and `handoff` make the lane's moves and posts through the tracker
  adapter, or through the host outbox for Linear. The generated brief shrinks to match, and carries the turn rule.
- **Owns.** `lane-start`, `open-pr` and `handoff` in `run_delivery.py`, the brief templates in
  `skills/run-delivery/templates/`, and the brief and command pins in `tests/test_run_delivery.py`.
- **Depends on.** Tracker publisher, for the adapter it calls.
- **Required scenarios.** Lane start, Brief. **Sensors.** `tests/test_run_delivery.py`.

### Unit: Setup refusals and doctor checks

- **Outcome.**
  - C8.
  - `--delivery` is accepted before or after the subcommand.
  - `doctor --delivery` reports:
    - each story with no plan section and each `context.sections` heading the plan lacks;
    - a forge or tracker configured with no matching snapshot or publisher in the binding;
    - the Node version against the runtime's `nodeMinimum`;
    - a remote base missing the configuration, naming `RUN_DELIVERY_LOCAL=1` for local setup.
  - `lead-init` refuses on any of those.
  - `lead-assign` refuses a story with no plan section.
  - `lead-setup` asks for the lanes' authority grant, and won't write a delivery file without one unless
    `--no-authority` says that's intended.
  - A forge or tracker adapter that prints nothing is a failure that names the adapter path it ran.
- **Owns.** `run_delivery.py`'s `lead-setup`, `lead-assign`, argument parsing, `doctor` and the adapter-call wrapper,
  the `lead-init` refusals, and `tests/test_run_delivery.py`.
- **Required scenarios.** Stale record, Doctor gaps. **Sensors.** `tests/test_run_delivery.py`,
  `tests/test_run_delivery_plan_review.py`.

### Unit: Bases a binding serves

- **Outcome.** C7. `doctor`, `lead-setup`, `config`, every lead and lane command, the supervisor and the merge queue
  accept a delivery whose base matches `bases`. `lead-setup` requires `--base` when the binding declares `bases`. The
  guide's anchor for a base mismatch exists and explains `bases`.
- **Owns.** The binding validator and `merge_queue.py`'s base check in `skills/orchestrate-work/scripts/`,
  `run_delivery.py`'s config loading, the base entries in `config.md` and `adopting.md`, and new tests in
  `tests/test_run_delivery.py` and `tests/test_merge_queue.py`.
- **Required scenarios.** Bases. **Sensors.** `tests/test_run_delivery.py`, `tests/test_merge_queue.py`,
  `tests/test_orchestration_adopter.py`.

### Unit: Rounds and record in the view

- **Outcome.**
  - `review-reduce` writes each reduced round into the record as the lane's own round, so the line map shows round
    cards and notes while review is open.
  - The "Review evidence unavailable or stale" placeholder says when rounds will appear.
  - `run-delivery view [--archive PATH]` serves a live or archived record without a checkout of its delivery, and
    prints its address. Archiving itself is the supervisor's.
- **Owns.** `review-reduce` and `view` in `run_delivery.py`, the placeholder text in `skills/delivery-views/scripts/`
  and its page pins, and `tests/test_run_delivery.py` and `tests/test_delivery_views_page.py`.
- **Required scenarios.** Rounds before hand-off, Lost checkout. **Sensors.** `tests/test_run_delivery.py`,
  `tests/test_delivery_views_page.py`, `tests/test_delivery_views_review_projection.py`.

### Unit: Never-started tickets

- **Outcome.** Reconcile reports a planned story whose ticket the tracker shows in backlog only if the record shows it
  started earlier.
- **Owns.** The tracker branch of `reconcile` in `skills/orchestrate-work/scripts/delivery_state.py` and
  `tests/test_orchestration_state.py`.
- **Required scenarios.** Never started. **Sensors.** `tests/test_orchestration_state.py`.

### Unit: Review rounds

- **Outcome.** This closes issue #69. The round brief carries the lens's scoped-check rule. The adversarial-testing
  lens limits mutation and fault injection to the candidate's changed lines and the code that calls them, and says so
  in its evidence.
- **Owns.** `skills/obtain-review/references/round-brief-template.md`, the scope text in `skills/obtain-review/SKILL.md`,
  and `tests/test_review_bound_corpus.py`.
- **Required scenarios.** Scoped lens. **Sensors.** `tests/test_review_bound_corpus.py`,
  `scripts/update-provenance.py --check`.

### Unit: Guide and orchestration text

- **Outcome.**
  - **`adopting.md`.** Its walk-through becomes the short one C1, C5 and C6 allow: set up, assign, accept, answer.
    It gains a worked adopter: Bitbucket, Jira, an AI PR reviewer, and long-lived dev-alt bases under C7. It also
    covers:
    - ignore entries for hooks and formatters;
    - the gitignored `.claude/`;
    - the whitespace gate's exclusion;
    - a lint-only-changed-lines recipe;
    - a red baseline;
    - settings landing through reviewed PRs, with install updates in their own PR;
    - restarting after an install update or a token change;
    - merge strategy;
    - single-session test accounts;
    - why lanes create their own worktrees.

    Every example that pushes to `main` either opens a PR instead or is labelled for an unprotected base.
  - **The orchestration guide's lead duties.** Its close-out list and tracker-update table say which duties
    run-delivery does, so a lead reading the guide doesn't do them twice.
- **Owns.** `skills/run-delivery/references/adopting.md`, the lead-duty passages of
  `skills/orchestrate-work/references/orchestration-guide.md` and `SKILL.md`, and their pins.
- **Depends on.** Every other unit.
- **Required scenarios.** Guide. **Sensors.** `tests/test_run_delivery.py`, `scripts/validate-registry.py`.

## Out of scope

| Gap | Why it's out |
| --- | --- |
| Accepting a hand-off automatically | Acceptance is the lead's check of a claim. `lead-accept` already does its mechanical checks. |
| Starting lane sessions | A host action. `lead-assign` prints the exact prompt, and V26-3047 makes lanes always sessions. |
| Lead messages queue until a lane's turn ends; Bitbucket PRs can't be watched; sessions can't be started from a turn another session triggered | Host behaviour. The guide's host notes record these. |
| Sharing the live view outside the machine | Needs a publisher design. Proposed as a follow-up. |
| A record that continues past merge (deploy, QA) | Deploying belongs to people at eshyft. Proposed as a follow-up. |
| One record for an epic that spans repositories | Proposed as a follow-up. |
| Installed product size, and a user-level `delivery-flow` skill duplicating run-delivery | The first is lifecycle, which V26-3041 leaves untouched. The second is on the operator's machine, not in this corpus. |
| `__pycache__` blocking the install, and `status` not checking Node | Lifecycle. The Node check is added to run-delivery's `doctor` instead. |
| Scope changes during review rounds, and plan review's overall cost | Process, plus the scoped-check rule above. |
| `build` needing a story key, and the tracker-properties validator needing `AGENTS.md` | Minor. Recorded as follow-ups. |
| eshyftnew's `CLAUDE.md` naming a test helper that doesn't exist | The adopter's own fix. |
| Committing the friction logs | A separate decision (A4). |

## Finish line

- Every unit is merged to the default branch, with `scripts/run-tests.py` and `scripts/validate-registry.py` passing on
  the merged tree and CI green on all three operating systems.
- The first version-tagged release after the merge carries these changes.
- eshyftnew installs that release through its own PR. Then a one-story delivery runs there in which the lead runs only
  `lead-setup`, `lead-init`, `lead-assign` and `lead-accept`, and the lane runs only its commands. In that delivery:
  - the stop moves when the lane starts;
  - round cards show before hand-off;
  - every Jira update and move the configuration names happens;
  - the accepted PR is marked ready and merged;
  - the delivery closes by itself, leaving only the friction note.

  The lead of that delivery records any remaining friction in the friction log.
- Every out-of-scope follow-up has a Linear item, and the friction log's entries name their disposition.

## Dependencies

1. **V26-3041 integrated to the default branch.** All units start after it. It changes how releases are cut, the
   current-set test pins, the catalog and provenance, which every unit here touches. Its lane ruling also edits the
   orchestration guide that the last unit edits.
2. **Order of units.**
   - Setup and Tracker publisher run first, in parallel with Bases, Rounds, Never-started and Review rounds.
   - Lead supervisor follows Setup.
   - Lane commands follow Tracker publisher.
   - Guide and orchestration text is last.
3. **The finish line's eshyftnew delivery** needs the first version tag. Cutting it is the owner's decision under
   V26-3041.

## Test scenarios

- **Supervisor.** On a fixture delivery, `lead-init`:
  - starts one `lead-run`, and a second `lead-init --replace` stops it before starting another;
  - its first line names the live view's address;
  - each pass reconciles, archives, and runs `serve`.
- **Ready.** An accepted draft candidate:
  - is marked ready with the grant and a fake adapter, then merged by `serve`;
  - without the grant, makes `lead-init` refuse when `review.pr` requires ready, naming the grant;
  - is reported as a failure that names the adapter when the adapter prints nothing.
- **Finish and close.**
  - When the last criterion's proofs are done, `finish.met` is journalled once.
  - With every finish line met, an empty queue and no working lane, the close-out:
    - runs `fullHealth`;
    - files a follow-up for a deferred finding that has none;
    - journals `delivery.closed`;
    - archives the record;
    - writes the friction stub;
    - stops.
  - An open escalation holds the close.
- **Supervisor down.** With `lead-run` killed:
  - `doctor --delivery` and each lead command report it;
  - `lead-run --resume` picks up from the journal without posting anything twice;
  - a hand-run `lead-cleanup` while it's down isn't repeated after it resumes.
- **Publisher.** Against a fake Jira server, each journal kind posts the guide's update once. A failed post is retried
  on the next write, and an acknowledged one is never re-posted.
- **Moves.** With `transitions.lead` configured:
  - a move whose status was already reached is skipped and reported as made by someone else;
  - a move not offered from the current status raises a blocker;
  - with `transitions.lead` absent, no move is made and the update says so.
- **Linear outbox.** An undrained host outbox raises "Needs you", naming the waiting updates.
- **Lane start.** `lane-start` creates `.orchestration/lanes/<key>` from the delivery base, applies the start
  transition, posts the start update and sets focus. Run twice, it changes nothing the second time.
- **Brief.** The brief lists the lane commands in place of the manual tracker steps, and carries the turn rule.
- **Stale record.**
  - `lead-init` over an existing record refuses and names it.
  - `--replace` archives the record and the flow log. An edit made between the old init and the new one is not
    reported as drift.
- **Doctor gaps.** `doctor --delivery` reports each of these, and `lead-init` refuses on each:
  - a story without a plan section;
  - a missing `context.sections` heading;
  - a tracker with no publisher;
  - a forge with no snapshot;
  - Node below the minimum;
  - a remote base without the configuration.
- **Bases.** A binding with `bases: ["main", "dev-alt-*"]`:
  - a `dev-alt-10` delivery passes `doctor`, `lead-init`, a lane's `gate`, and a queue merge;
  - `release/1` is refused, with the guide anchor;
  - a binding without `bases` refuses `dev-alt-10` exactly as today.
- **Rounds before hand-off.** After `review-reduce` round 1, before hand-off, the line map shows the round card and
  its notes.
- **Lost checkout.** After the lead's checkout is deleted, `run-delivery view --archive` serves the last archived
  record, which holds the latest round.
- **Never started.** A planned story whose ticket stayed in backlog gives no report. One whose record shows it started
  and whose ticket went back does.
- **Scoped lens.** The round brief carries the scoped-check rule, and its corpus pin proves red when the rule is
  removed.
- **Guide.**
  - The guide pins name the supervisor, `lane-start`, `bases`, the formatter ignore list, the settings-PR rule and the
    base-mismatch anchor.
  - No unlabelled push to `main` remains.
  - The orchestration guide says which duties run-delivery does.

## Test dispositions

- `tests/test_run_delivery.py`:
  - the subcommand inventory gains `lead-run`, `lane-start` and `view`;
  - the brief text pins change to the lane commands and the turn rule;
  - the guide pins change;
  - the `lead-init` refusal text pin changes;
  - tests that call `lead-reconcile --every` move to `lead-run`.
- `tests/test_merge_queue.py`: the `--base` refusal text, and its new `bases` acceptance.
- `tests/test_delivery_views_page.py`: the placeholder wording.
- `tests/test_orchestration_state.py`: the never-started report.
- `tests/test_review_bound_corpus.py`: the new round-brief pin.
- **Orchestration text:** any pin on the lead-duty passages of the orchestration guide, wherever V26-3041 leaves them.
- **Catalog and provenance:** each unit that changes a releasable skill updates its catalog entry and refreshes digests
  with `scripts/update-provenance.py`, as `AGENTS.md` directs.

## Shared resources

- **Repositories and remotes.** Tests use temporary repositories, remotes and fake forge and Jira servers created
  inside each test, bound to port 0.
- **Background processes.** The supervisor's tests start real background processes. They mark their classes
  `run_serially = True` and stop every process they start, so they don't add to V26-2951's leaks.
- **Files edited by several units.**
  - Lead supervisor, Setup and Lane commands all edit `run_delivery.py`. Setup lands before the supervisor. Lane
    commands touch only lane commands.
  - Tracker publisher and Lane commands share the adapter, which the publisher lands first.

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

## Approval handoffs

- **Tracking.** After plan review aligns, one Linear epic in the agent delivery harness project, with one child item
  per unit and one per out-of-scope follow-up. No tracker change has been made yet.
