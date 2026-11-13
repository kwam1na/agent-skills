---
name: orchestrate-work
description: Deliver a decomposed epic with one lead session coordinating lane sessions, a lead-owned merge queue, and a delivery state document that any view can render.
---

# Orchestrate Work

The merge queue holds the same lock as lead publication. A temporary flake declared in the record applies to its
base commit and check id. The runner reads it at the retry decision, with precedence given to the binding.

A record initialized by run-delivery can hold lead-owned lane claims. The planned row holds the current story
lane. A handed candidate keeps the lane that supplied its evidence, and its optional assigned lane tells the
lead where to return the work. The queue continues to check the handed candidate.
Lanes can hold disjoint line claims in committed text files. The queue maps each handing lane's spans to the
base of its merged tree and refuses changes outside those spans before publishing.


Deliver a decomposed epic: one lead session plans and assigns tickets to lane
sessions, owns every merge, shared resource and release, and keeps the
delivery's state current. Each lane delivers one ticket at a time through the
existing delivery workflow and never merges.

Use this workflow when the requested outcome is an epic: several tickets that
share a plan, a base branch and a finish line, delivered in parallel. For one
bounded change, use `$execute-work` instead.

## Dependencies

- `$plan-work` produces the plan and its contract decisions before any lane
  starts.
- `$execute-work` runs each lane's ticket through the existing delivery graph.
- `$review-work` runs each lane's review rounds within its bound.
- `$compound-learning` preserves what the epic taught, at close-out.
- The tracker capability, when configured, holds the epic, its tickets and
  their states, and receives an update at every milestone so it carries the
  decisions made. Agents post with their host's own tracker tools. Without
  it the delivery state and journal are the record, and the lead reports
  which tracker operations were not performed.

This workflow never calls the entry point that routed to it, and names no
tracker, host or forge.

## Delivery posture

A repository declares its delivery posture in one file, `.agents/delivery-posture.json`,
and the tip of the delivery's own base is the only place it counts. With `focused`
declared in `.agents/delivery-posture.json` at the tip of the delivery's base, the
close-out runs no `fullHealth`, and a failed run at that head holds nothing. Nothing
runs in its place, and no `fullHealth` runs after a merge either. The journal, the close-out summary and the plan chapter say the run was skipped under the posture,
naming its source and reason, so a skip never reads as a pass. `gate`, no file, or a
file that is malformed or can't be read runs `fullHealth` and holds the close on it.
`orchestration-binding/1` doesn't change: `fullHealth` stays required, so restoring
`gate` needs no binding change.

## Roles and write ownership

- **Lead.** One session. Only the lead assigns tickets, starts the runner,
  holds or grants leases, approves releases with the operator, and moves
  tracker items to their final state.
- **Lane.** A session of its own, always: never a subagent, and never the lead
  working in its own session. A lane delivers one assigned ticket at a time,
  and never merges, releases, or holds a lease it was not granted.
- **State writers.** The lead writes every state field except the
  runner-owned and lane-owned ones. The runner writes only its result
  documents, journal entries, and its own fields: `queue[i].stage`,
  `queue[i].checks`, `queue[i].syncedHead`, `meta.checking`, `meta.baseHead`,
  `meta.releasedHead` and `meta.checkHistory`, and one lane row when it merges
  or stops that lane's candidate, in the same write: `focus`, `state`,
  `waitingOn` and `since`. A lane with nothing else open rests. A row that doesn't
  name the candidate stays if its lane has another assignment open, or is
  active and has spoken since its hand-off. So does a blocked lane's row while it
  has other work open. Any later `focus` wins: the lane's next write of its
  own focus, by `focus` or by `patch` with `--patch` or `--set`, ends a
  `waitingOn` the queue set unless it sets its own. A `waitingOn` the lane or
  the lead set stays until they change it, even under the queue's words, and
  a lead rewriting only the focus leaves the queue's `waitingOn` for the lane
  to end. A new hand-off ends the queue's words and a `waitingOn` the queue
  set. The queue putting the candidate back ends its words saying the
  candidate stopped, and a `waitingOn` the queue set, while those words
  stand; once anyone else rewrote them, the wait stays for the lane to end.
  A lead who drops a stopped candidate from the queue also clears a row still
  saying it stopped, since no later queue write will. The tool alone keeps a
  row's `focusAt`, `focusBy`, which says the queue wrote its words,
  `waitingBy`, which says who set its `waitingOn`, and `restedFrom`, the
  lane's own last word, kept while the queue's words stand over it. A lane writes only its own
  lane's `focus`, `state` and `waitingOn`, its own review rounds, the run
  building its own planned work, the one stage move it makes, a story planned in its own lane from `planned` to
  `building` when it starts it, and its own hand-off, which the tool turns
  into a queue element, with any suite evidence
  it gives in `queue[i].evidence`, which only a hand-off writes. Every write goes
  through the state tool, which checks the declared writer against this
  ownership and refuses anything else with `writer_not_owned` for a lane.
  A story whose criteria already hold needs no change: the lane's
  `handoff --no-change` moves its planned work to `review` with its evidence,
  for the lead to settle, and it never enters the queue.

## Lead authority

The lead owns the judgement calls within the approved plan and the operator's
standing direction. It decides, records the decision in the journal and the
tracker, and informs; it does not ask. That covers finding triage and
deferrals, review-churn scope calls, fixing forward on a broken base, lane
assignment and rebalancing, lease arbitration, merge order, and plan-text
corrections that keep the approved finish line.

The operator is brought in only for:

- a release or production approval the binding requires;
- a change to the approved scope or finish line;
- an irreversible or outward-facing action not already authorized;
- a permission or access grant the lead does not hold;
- a host that cannot start or message sessions, so no lane can start;
- owner intent that is genuinely ambiguous, where neither the plan nor
  standing direction settles it.

Each escalation is one consolidated question with a recommendation; while it
waits, the lead keeps every unblocked lane working, giving a lane blocked on
it other unblocked work. Lanes escalate to the lead, never to the operator; a
question only the operator can answer reaches the operator through the lead.
The lead keeps its wake while a lane works or a candidate is queued: with
run-delivery, `run_delivery.py lead-wait` runs in the background, which wraps
the `wait` below and keeps a lease that shows when the lead isn't listening
([Keeping the lead's wake](#keeping-the-leads-wake)).

- **Never hold approved work.** An approved, handed-off candidate enters the
  queue at once, not batched behind unrelated work or held for a convenient
  moment.
- **Never leave a lane idle.** When a lane hands off, the lead assigns it the
  next unblocked ticket or follow-up in the same step. An idle lane while
  unblocked work exists is a lead defect, and the lead records it.
- **Reconcile at every event.** At every event the lead reconciles the
  delivery record with the forge, the tracker and the lanes, by
  [the sync loop](#keeping-the-live-view-current), so the operator
  never has to prompt a status check. For the tracker, the lead lists the
  epic's child items, with the binding's snapshot command or the tracker
  capability's `list-work`, and compares them with the record. An item the record lacks, such as a follow-up filed but never
  recorded, is added before the views are published: to the follow-ups, or to
  planned work only when the approved plan covers it. When a state disagrees
  with the record, the record wins for states the lead or runner owns, except
  a change the operator made, which is recorded as a decision and never
  reverted. Like the
  updates, this is one attempt; a failed listing is noted and never delays
  acting or publishing.

## The focused lane profile

Every lane works to one named profile, not a posture:

1. Run the unit's named tests first.
2. Run the unit's declared sensors only. There is no repository-wide gate and
   no full suite in the lane; the lead's queue runs the selected checks on the
   synced head.
3. Run the two mandated review lenses until both approve, within
   `$review-work`'s bound; at the bound, stop and report the blocker.
4. Run the repository's compounding check.
5. Open a draft change request.

A lane whose work a product run builds binds that run at start, with
`delivery_state.py bind-run --lane <lane> --subject <subject> --run <id>`, so
the views show the run's rounds while the work is still planned. It binds only
its own lane's planned element, never replaces a bound run (binding the same
one again changes nothing), and is refused with `run_bound` for a run another
subject holds; another lane's or a queued element is refused with
`writer_not_owned`. The hand-off keeps the bound run unless it names another. A
hand-off naming a run another subject holds is refused with `run_bound` too.

The lane hands off the candidate reference, the reviewed head, the review
rounds, the validation, the run id or `none`, every declared lead input the
candidate needs, and any expensive non-blocking findings for the lead. The lead records the hand-off in `queue[]`, including
`head` and `leadInputs`. Brief each lane with
[the lane brief template](references/orchestration-guide.md#lane-brief-template).

## Coordinating lanes

Follow
[the coordination protocol](references/orchestration-guide.md#coordination-protocol):
one consolidated message per lane per milestone, a lane retitled in the same step
as its assignment, undelivered messages kept in the lead's notes, and every
lane report treated as a claim to check. Without a session capability, no lane
starts: the lead records the blocker and brings it to the operator, who may
choose to deliver the tickets one at a time through `$execute-work`. Subagents
remain how a lane realizes its review lenses. A host that loses the ability to
start or message sessions during a delivery is the same blocker. Leases,
including those held
outside the epic, follow the same protocol.

When a lane hands off, the state tool can suggest its next work. It ranks the
planned work no other lane holds and the follow-ups, by their tracker
references, leaving out anything queued, in review, completed or canceled.
It lists five unless `--limit` says otherwise, and never assigns any of it;
the lead still decides and sends:

```bash
python3 -B scripts/delivery_state.py --dir <state directory> next --lane <lane> --snapshot <tracker snapshot> --binding .agents/orchestration.json
```

Unblocked work comes first, by the tracker snapshot's `blockedBy` and the
hints' `blockers`; then work
whose files do not overlap what another open lane holds; then priority; then
files the lane holds or has worked on. Each item comes with a one-line reason,
which says whether the files compared were declared or what a change actually
touched; work the snapshot does not list comes after work it shows
unblocked. Paths are relative to the repository, and a glob's `*` crosses
folders. The files and priorities come from a `work-hints/1` document (in
`references/schemas.json`): each item's declared `files`, as paths, folders
ending in `/` or globs; the `changedFiles` its change actually touches, once
it has one, used in their place; its `priority`, 1 the most urgent to 4, or
null; and its `blockers`, the subjects it comes after in the plan's order, so
the order holds when the tracker has none. A blocker stops blocking once the
record holds it merged or settled with no change needed, or the snapshot holds it completed or canceled. The repository's own command produces it when the binding declares
`state.workHints`, `{argv, timeoutSeconds?}`, run like `trackerSnapshot` with
`{parent}`; how it learns files and priorities is the repository's choice. A
`--hints` file is the fallback when the binding declares no command or the
command fails, and the result says which was used and why.

## Keeping the lead's wake

A lead does not end its turn while a lane is working or a candidate is queued
unless a `wait` is running in the background. It restarts the wait after each
wake, with the arguments the last one printed. The host's notes are a
convenience and never the lead's only wake. Where nothing can run in the
background, the lead tells the operator that it won't wake on its own.

```bash
python3 -B scripts/delivery_state.py --dir <state directory> wait [--after <journal id>] [--timeout <seconds>]
```

With run-delivery the wait is `run_delivery.py lead-wait`, which wraps it,
keeps its arguments, output and exit, and holds a lease in the record while it
runs, so the supervisor's "Needs you" and the live view say when the lead isn't
listening, and the lead commands print the exact `lead-wait` to start.

The wait reads the record and the machine only, and returns on a hand-off, a
failed check, a merge, a stop, a broken base, a blocker, a blocked lane, or
machine pressure. A failed check is news, not the outcome: the turn is still
telling it from the base's own failure, and its stop follows.
A lane whose row has stood still past `--quiet-seconds`, an hour by default, is
information, not a reason to return: `quietLanes` names it when the wait next ends,
once a quiet stretch, and it may be waiting on a long review for good reason.
Whenever a wait names pressure, whatever else woke it, the lead finds what is
loading the machine before taking more work, and reduces this delivery's own
load first, such as by capping test workers. It reports what it can't reduce to
the operator, such as other work or a system service. It stops nothing it didn't
start without the operator's word.

## Findings and a broken base

Triage every review finding with
[the triage and base-broken rules](references/orchestration-guide.md#triage-and-base-broken-rules):

- Blocking findings are fixed in the lane.
- Cheap non-blocking findings, about an hour or less, are fixed in the lane
  with no ticket.
- Expensive non-blocking findings go to the lead, who defers them with a
  tracker follow-up or drops them, and records a decision.
- A finding first raised in a later round, against code an earlier round
  approved, is judged on scope before it blocks.

When the runner reports `base_broken`, the queue stops. The lead fixes forward
first, declares the missing provider membership or adds the check to
`alwaysRun`, and records a decision.

## The merge queue

The lead owns every merge through the runner, `merge_queue.py`. Run it with
`python3 -B` from the top level of a checkout of the repository it serves, or
name that top level with `--repo`; a subdirectory is refused. Name the state
directory, the remote and the base branch every time:

```text
python3 -B <skill>/scripts/merge_queue.py [--repo <checkout top level>] --state <state directory> --remote <remote> --base <base branch> [--lead <holder>] [--wait <seconds>] <subcommand>
```

- The runner reads the repository's binding, `.agents/orchestration.json`,
  from the tip of `<remote>/<base>`, never from a candidate or the operator's
  checkout. A binding that doesn't serve `--base`, as its `base.ref` or one of
  its `bases`, or that names a different remote, is refused with
  `binding_invalid`. Write and check the binding with
  [the binding guide](references/orchestration-guide.md#binding-guide).
- `--lead` is the holder name the lead uses in `leases[]`; it defaults to
  `lead`. A lease counts as the lead's only when its holder equals it.
- One runner works the queue at a time. `--wait` sets how long to wait for the
  queue lock before stopping with `queue_locked`.

- `sync <candidate>` merges the base into the reviewed head, runs the named
  lead inputs, the regenerators and the post-sync steps, proves the result
  differs from the reviewed head only in declared paths, and pushes it.
- `check <candidate>` runs the checks the repository's affected-checks
  provider selects, plus `alwaysRun`, plus `fallback` when impact is unknown,
  on a fresh checkout of the synced head. Each check's elapsed time shows
  against its usual duration; a failed check's failing tests and its log reach
  the record, the journal and the views, journaled as `check.failed` as soon as
  the check fails, before the base re-run. While the failing checks re-run on
  the base, the checking panel's `baseRerun` says the candidate failed and
  waits on that re-run; its start and its verdict are journaled as
  `base.rerun.started` and `base.rerun.finished`, so the journal reads failure,
  re-run start, verdict, stop. A failure made only of the binding's
  declared flakes is retried once and journaled as `check.retried`; any other
  failure is never retried. A check the binding lists in `reusableChecks` is
  not run again when the synced tree is exactly the tree the lane's hand-off
  evidence shows it passing, under the same definition: it is recorded as
  reused, with the head of the lane's run, and journaled as `check.reused`.
  Every other check still runs, and the merge stays pinned to the synced head.
  When the binding sets `selection.lead` and the lead recorded a selection for
  the candidate's reviewed head with the state tool's `select` command, the
  runner checks it with the lead's checks plus `alwaysRun` instead of the
  provider's answer and `fallback`. The result, the record, the views and the
  stats say the checks were selected by the lead, with the lead's reason. A
  hand-off at a new head voids the selection. A batch uses the union of its
  members' selections only when every member has one, and the provider
  otherwise. A selection is the lead's judgment; the post-merge `fullHealth` run
  is its backstop, unless the base declares the `focused` posture. Under
  `selection.refusalStops`, a provider that exits non-zero stops the candidate
  with `provider_refused` and runs no check, until the lead's selection is checked
  in its place.
- `merge <candidate>` merges the checked head through the binding's `ready`
  and `merge` commands and confirms the new base.
- `run <candidate>...` does sync, check and merge in order for each candidate,
  stopping at the first failure. When the base moves after a sync, `run`
  re-syncs and re-checks once; a second move stops with `base_moved`. A
  candidate is merged only onto the exact base it was checked against.
- `batch <candidate> <candidate>...` checks two or more candidates once, as
  one combined tree, then lands each in order with exactly the tree the batch
  recorded for it. A member that conflicts drops out and the rest continue.
  When the checks fail, nothing merges until the runner finds the member whose
  arrival made them fail; it stops that member, lands those before it, and
  batches those after it again. Use it when the checks are slow and several
  approved candidates wait; a lone candidate goes through `run`.
- `serve [--poll <seconds>] [--until-idle]` takes whatever is queued, in
  order, whenever the merge slot is free: a batch when two or more wait, one
  alone otherwise. The lead starts it once. `meta.hold` pauses it from its
  next turn, and so does a queue-stopping stop until the lead journals
  `base.repaired`; a candidate waiting for the lead to accept its hand-off is
  left alone, a refused one stops, and a killed `serve` is cleared by the next
  one. It reads the binding and checks the checkout once before taking
  anything, and does not start when either is wrong. It checks them again
  before each turn that takes work and after any stop or refusal other than
  `queue_locked`: when they break while it runs, it sets `meta.hold` itself,
  only where none is set, journals one blocker entry, and puts that
  turn's candidates back to `queued`, except one whose merge had begun. On
  the binding's `state.refreshSeconds` it also reads the declared forge and
  tracker snapshot commands and reconciles them, so the record follows both
  with no lead step. It prints each result document on a line of its own as
  it writes it,
  and `state/results` keeps them all; `--until-idle` prints them together.
- `release <ref> [--approved-by <text>]` runs the binding's release targets on
  the base tip or an ancestor of it.

The runner works only in detached checkouts under the binding's `workRoot`,
never touches the operator's checkout or the base branch, and writes a result
document and journal entries for every subcommand. Every stop names a code,
such as `candidate_moved`, `source_conflict`, `sync_step_failed`,
`check_failed`, `base_broken` or `merged_unverified`; the lead reads the code
and acts. Under `serve` the lane hands a stopped candidate off again; without
it, the lead names the candidate again to re-queue it. A candidate that already
merged is refused with `already_merged`. The lead checklist lists every code
and what to do about it.

`--approved-by` records an approval the operator already gave in the
conversation. It is not a gate the runner can verify: pass it only after the
operator approves that release.

## Views and publishing

The runner renders the views itself after each of its state writes, passing
the binding's `state` member to the render wrapper; a failed render is noted
in its result and never stops it. When the member declares no renderer and no
publisher, the wrapper would write nothing, so the runner starts no wrapper: it
reads the record itself, and notes a failure as the wrapper would. A member
the wrapper would refuse still goes to the wrapper. The change log is the state tool's own, either
way. After each of the lead's own state writes,
render them with the wrapper, `scripts/render_state.py`, from an up-to-date
checkout of the base, so the lead's views use the same renderers and
publishers as the runner's:

```bash
python3 -B scripts/render_state.py --dir <state directory> --binding .agents/orchestration.json
python3 -B scripts/render_state.py --dir <state directory> --state-config '<state configuration>'
python3 -B scripts/render_state.py --dir <state directory>
```

- The wrapper validates the state and the journal first; when either is
  invalid, nothing runs and it exits 2.
- It reads only the binding's `state` member: `directory`, `renderers` and
  `publishers`. A renderer is any argv, or a built-in (`markdown` or
  `static-page`), that reads the validated `{state, journal, stats}` on stdin and
  writes one view on stdout to its `output` inside the state directory. A
  publisher is any argv that puts the same document somewhere a view reads,
  such as run-delivery's tracker publisher. A publisher with a `kind` and no
  `argv` runs nothing, and the wrapper refuses the configuration, naming the
  fix: name the tracker publisher instead.
- With no configuration it renders both built-ins, `delivery.md` and
  `delivery.html`.
- It exits 0 when every renderer succeeded and 1 when one failed. A failing
  publisher is recorded in the result and never changes the exit code, so a
  view is never a reason to stop a delivery.

When a host stores the state with its own tool rather than an argv, follow
[the publishing step](references/orchestration-guide.md#publishing-step)
after each runner result.

## Keeping the live view current

Keeping the view current is the lead's job, never the operator's: the
operator should never have to ask whether it is up to date.

**The live view.** At the start of a delivery, start the live view and give
the operator the address it prints:

```bash
python3 -B scripts/render_state.py live --dir <state directory> --binding .agents/orchestration.json
```

Started without the binding that names its `liveHome`, or from a binding that
declares a page view and names no home, the live view shows the built-in page and
says so: in the line it prints and at the top of that page.

- Every successful write through the state tool, by the lead, the runner or a
  lane, appends one line to `changes.jsonl` in the state directory; a write
  never fails because of it. The log keeps only its latest lines once it
  passes 64 KiB. `live` waits on that log, pushes the record to
  every open page, then re-renders the configured views, so a page shows each
  change within seconds, with no reload and no publish step. It runs no
  publisher: those run only when the lead renders.
- It serves the built-in static page in its live mode, with a quiet live or
  disconnected indicator, and any HTML view whose head carries
  `<meta name="delivery-state-live">`, at `/view/<output>`, the output's
  path segments percent-encoded as a browser sends them. The binding's
  `state.liveHome`, a renderer's id, makes that renderer's view the front
  page instead of the built-in page. A record caught half-written is never
  served: the last valid one stands until the next one reads cleanly.
- It listens on this machine's loopback address only, unless `--host` names
  another; a wildcard such as `0.0.0.0` is refused, since the live view serves
  this machine only. It answers only requests for its own host and port. `--port 0`,
  the default, picks a free port. Ctrl-C stops it, non-zero, with nothing left
  running.
- Views a host stores with its own tool become mirrors for sharing; the live
  view is the one to watch.

At every event, such as a hand-off, a merge, a decision or a tracker change,
run the sync loop in this order:

1. **Snapshot.** List the epic's work and write it as `work-snapshot/1` files
   (in `references/schemas.json`): `{takenAt, source, items}`, where `source`
   is `forge` or `tracker`. Each item is `{subject, title?, summary?, state,
   changeRequest?, head?, mergedAt?, blockedBy?}`. A forge merge's `mergedAt`
   is its verified UTC source occurrence time, no later than `takenAt`; omit
   it when unknown. A tracker item's `summary` is the
   ticket's own summary as plain text, at most 1000 characters; whatever
   makes the snapshot shortens a longer one. A tracker state is `backlog`,
   `started`, `completed` or `canceled`; a forge state is `open`, `draft`,
   `merged` or `closed`. The state tool never calls a forge or a tracker
   itself. Blockers are the tracker's: `blockedBy` on a forge item is
   ignored, never merged with the tracker's. A tracker snapshot's blockers
   must form a dependency list under the record's `meta.trackerParent`, by
   the same rules as `list-work`, or the snapshot is refused. `reconcile`
   also checks all its tracker snapshots together, so a cycle that runs
   across two of them is refused too.
   - **Tracker, with a bound command.** When the binding declares
     `state.trackerSnapshot`, `{argv, timeoutSeconds?}`, run it through the
     state tool. The repository's command prints the snapshot for the record's
     `meta.trackerParent`, which `{parent}` in its argv names; `{stateDirectory}`
     names the resolved state directory, in a renderer's, a publisher's or `workHints` argv too. A command that
     fails, or prints anything but a fresh tracker snapshot, keeps the last
     snapshot and journals why once, and reconcile then refuses that snapshot
     once it is too old. The command runs in its own process group with a
     marker for that run in its environment; once it exits, whatever it left
     in the group or carrying the marker is stopped, never the caller or its
     parents, pid 1, another user's process or one older than the run, and the
     result says what was stopped or refused. A child that clears its
     environment, or whose environment the system hides, such as some system
     binaries on macOS, cannot be found and survives:

     ```bash
     python3 -B scripts/delivery_state.py --dir <state directory> tracker-snapshot --binding .agents/orchestration.json --output <tracker snapshot>
     ```

   - **Tracker, otherwise.** Take it through the tracker capability's
     `list-work` for the epic's parent item, and write its normalized
     outcome as the snapshot: each item's `workReference`, `title`,
     `stateType` and `blockedBy` become its `subject`, `title`, `state` and
     `blockedBy`. It carries no summary, so the views show titles alone.
   - **Forge, with a bound command.** When the binding declares
     `state.forgeSnapshot`, `{argv, timeoutSeconds?}`, the same way, the state
     tool runs it with the same checks and the same single note on a failure:

     ```bash
     python3 -B scripts/delivery_state.py --dir <state directory> forge-snapshot --binding .agents/orchestration.json --output <forge snapshot>
     ```

   - **Forge, otherwise.** List the epic's change requests with the host's own
     forge tools.
   - **With `serve` running,** steps 1 and 2 need no lead: every
     `state.refreshSeconds` (180 when absent, at most 86400; 0 turns it off),
     between turns and whether or not it is held, `serve` runs each declared
     command into `snapshots/` in the state directory and reconciles what it
     took. A snapshot is kept there only once reconcile has applied it, and a
     failed source's last fresh one still counts. An idle refresh writes
     nothing. The record's `meta.sources` says, per source, `observed`,
     `stale` (with `lastReadAt`, the last read the record took in, and the
     reason) or `not_observed` when the binding declares no command, and
     changes only when that does. What reconcile could not settle, such as a
     change request closed without merging, goes to `meta.sourceReports` for
     the lead. The status line names a stale source, when it was last read,
     and the first report. A run in progress delays the next read, and
     `serve` does not start on a snapshot block or interval it cannot use.
2. **Reconcile.**

   ```bash
   python3 -B scripts/delivery_state.py --dir <state directory> reconcile --snapshot <forge snapshot> --snapshot <tracker snapshot> --binding .agents/orchestration.json
   ```

   - A tracker child the record lacks is added: `backlog` as a follow-up,
     `started` as planned work at `building` (the state's `planned` list).
     Planned work is only what the approved plan covers, so write a started
     child outside it as `backlog` in the snapshot: it then becomes a
     follow-up.
   - Planned work at `planned` whose ticket the tracker shows `started` moves
     to `building`, so a lane starting its ticket moves its stop with no lead
     step. The first time reconcile sees a planned item's ticket `started`, it
     sets the item's `trackerStarted`, which a hand-off carries onto the
     queue element and a rejected hand-off hands back. Work at `building` or
     in review whose ticket was started and that the tracker shows back in
     `backlog` is reported for the lead, never moved back. Work a draft
     change request moved while its ticket was never started is not
     reported.
   - A change request is linked to its queue or planned item. A draft one
     moves planned work at `planned` to `building`, so a delivery with no
     tracker snapshot sees the stop move once the lane's draft opens; an open
     one moves planned or building work to review.
   - Each ticket's tracker title and summary, for work the record holds, are
     kept in the state's `tickets`, keyed by subject, and written only when
     they change; the views show them in place of the lead's own text. A
     title the record already has, with no summary, is not repeated.
   - A merge the runner never recorded is written as the runner's, with a
     `candidate.merged` journal entry saying so. The runner records its own
     merges, so this is a recovery path, not the normal one. A recovery a
     crash interrupts leaves `recovery.json` in the state directory, and the
     next reconcile finishes it: the entry is never lost or written twice.
     Recovery also fills a missing merge entry when the queue already says
     `merged`. The entry's `at` remains the time it was appended; `occurredAt`
     holds the snapshot's `mergedAt`, or null when the source time is unknown.
     Existing entries and review provenance are never rewritten. A normal
     runner entry and an old journal without `occurredAt` retain their `at`
     semantics. Never backdate the clock or edit the journal to recover a merge.
     Recovery retains the source head and change request as `recovery`, so a
     delayed hand-off append cannot duplicate that same merge. A conflicting
     already-merged head is reported for the lead to settle; absent source heads
     use the recorded synced head, then the candidate head. A future source
     occurrence is refused before writes, even within snapshot clock skew.
     `show` reports `recoveryPending` while one waits. One that cannot be
     read is set aside as `recovery.json.invalid` (numbered after the first)
     and reported; check the queue item it names against the journal, journal
     the merge if it is missing, then delete the file.
   - Anything it cannot settle, such as a completed child still planned, is
     reported and left for the lead.
   - A snapshot older than the binding's `state.snapshotMaxAgeSeconds` (900
     when absent; `--max-age` overrides it) is refused
     with `snapshot_stale`, and one dated ahead of this machine's clock with
     `snapshot_invalid`; nothing is written: list the work again. A listing
     that fails is noted, and the loop carries on to render and publish.
   - Tracker snapshots apply before forge snapshots, whatever their order, so
     one pass settles both.
   - A merge is never recovered while the runner holds the queue: it is
     reported, and the next pass after the runner stops records it if the
     runner did not.
   - It is idempotent and prints its changes as JSON, or `no drift`.
3. **Render**, with the wrapper as above. Publishers run as part of it.
4. **Publish** by hand only for a view whose store the host alone can write:
   follow [the publishing step](references/orchestration-guide.md#publishing-step).

A record an earlier generation kept with an `outbox/` folder and a
`published.json` beside it reopens as it is: nothing reads the folder, and
run-delivery's tracker publisher reads the file once, so nothing it
acknowledged is posted again.

## Running the epic

Work through [the lead checklist](references/orchestration-guide.md#lead-checklist): plan,
decompose, assign, run the queue, publish, check the finish lines live,
release with the operator's approval, and close out.

With run-delivery's supervisor running, the lead assigns, accepts and answers:
the supervisor runs the queue, publishes, meets each finish line the record
proves, and closes out, as each checklist step says. A lead reading the
checklist does none of those twice.

## Telemetry and tracker updates

Telemetry is best-effort. Each existing run emission is attempted at most
once. A failure is skipped and never delays, gates or reorders the delivery.

Tracker updates follow the same rule: one attempt, never a gate. The lead
posts one at each journal milestone, as
[the tracker updates table](references/orchestration-guide.md#tracker-updates)
maps them, and each lane posts on its own ticket at the points its brief
names. With no tracker tools, delivery proceeds and the gap is noted. Where
run-delivery's tracker publisher is in the binding, it posts them instead.

## Delivery state

The state document and the journal record where the epic stands. The state is
the contract; views are rendered from it and can be replaced without touching
it.

`references/schemas.json` holds the skill's schemas, keyed by name:

- `delivery-state/1` describes the state document.
- `delivery-journal/1` describes one journal entry. The journal is
  append-only.

Write both only through the state tool, `scripts/delivery_state.py`. Run it
with `python3 -B` so no bytecode is written beside the skill:

```bash
python3 -B scripts/delivery_state.py --dir <state directory> init --headline "<headline>"
python3 -B scripts/delivery_state.py --dir <state directory> show
python3 -B scripts/delivery_state.py --dir <state directory> patch --writer lead --version <n> --patch '<json merge patch>'
python3 -B scripts/delivery_state.py --dir <state directory> patch --writer lead --version <n> --element 'queue[subject=<subject>]' --patch '<json merge patch>'
python3 -B scripts/delivery_state.py --dir <state directory> patch --writer lead --version <n> --element 'lanes[id=<lane>]' --set '{"/waitingOn": null}'
python3 -B scripts/delivery_state.py --dir <state directory> journal --kind <kind> --summary "<what happened>"
python3 -B scripts/delivery_state.py --dir <state directory> journal --kind review.round --subject <subject> --lane <lane> --review '<review json>'
```

- A merge patch removes any field it sets to null. To set a nullable field to
  null, use `--set`, which assigns exact values by JSON Pointer (relative to
  the selected element when `--element` is given). `--patch` and `--set` may
  be combined; the merge patch applies first.
- A patch is pinned to the state version it was written against. A stale
  version is refused and nothing is written; read the state again and rewrite
  the patch.
- The declared writer must own every field the patch changes; any other
  write is refused with `field_not_owned`. The runner owns `meta.checking`,
  `meta.baseHead`, `meta.releasedHead`, and each queue element's `stage`,
  `checks` and `syncedHead`. The lead owns everything else. The tool
  maintains `version` and `meta.updatedAt`. Before a repository has the
  runner, whatever performs its merges writes the runner's fields as
  `--writer runner`.
- An element update names one element with `--element`, such as
  `queue[subject=<subject>]`. Every write needs the current `--version`, so
  read the version again between writes.
- A queue element the lead adds starts with `stage` set to `queued`, empty
  `checks` and a null `syncedHead`.
- Journal appends are never refused for concurrency; the tool allocates the
  next id.
- A `review.round` entry records one review round of a candidate: its
  `review` is `{round, startedAt, finishedAt, verdicts, findings}`, where
  `verdicts` maps each lens to `approve` or `changes` and `findings` counts
  `P0` to `P3`. The summary is written for it when `--summary` is left out. A
  round that finishes before it starts, names no candidate or has no verdict
  is refused. The lead or the lane writes it; the runner never does.
- `show` prints the validated `{state, journal, stats}` document that
  renderers read, with a `publication` block holding `recoveryPending`. `stats` is derived, never stored: per queued or planned
  candidate, the build, review, wait, check-to-merge and total seconds, the
  rounds and their findings, and `checksSelectedBy`: `lead` or `provider` once
  a binding that enables lead selection has checked it, absent otherwise. A stat whose events are missing is null, never
  zero; with no recorded rounds, the count is the lane's `reportedRounds`. A
  candidate handed again follows its latest attempt: its total runs to that
  attempt's merge and is null unless it merged. Times keep six fractional
  digits, dropping the rest, and durations are whole seconds.
  Recovered merges use their source occurrence time. An unknown source time
  stays unknown; a hand-off recorded after the merge supplies no wait or
  check-to-merge duration. The last landing follows occurrence order, even
  when an older merge is observed later.
- Output is ASCII JSON on every platform and console; a patch read from
  stdin is UTF-8, with or without a byte-order mark. Text that has no UTF-8
  form, such as a lone surrogate, is refused. The state is UTF-8 and the
  journal ASCII, both with `\n` line ends, so their bytes are the same
  everywhere.
- `extensions` holds a repository's own fields. It is validated as an object
  only.


## Review observations without a product run

When a lane has no product run, keep its actual native reducer outcomes through
`capture-review` after hand-off. Supply the complete sequence, one file per
round in order; each retains its binding and actual obtain-review lens
reports (review-lens-result/1), including originating `resolvedFindings`
(or native `resolutions`), with `resolution` or `reason` text. Candidate references are
exact opaque strings; the final reference must match the retained queue head (the reviewed
candidate, not the runner's synced head) before the view can show approval. A
runner sync onto a moved base keeps the evidence; a new hand-off does not.

```sh
python3 -B scripts/delivery_state.py --dir <state directory> capture-review \
  --subject <subject> --candidate <reviewed candidate> \
  --outcome <round-1.json> --outcome <round-2.json>
```

`review-evidence --subject <subject>` reports `captured`, `not-captured`,
`incomplete`, `unavailable` or `run-named`, and exits 1 for anything but `captured`.
The lead reads it when checking a hand-off.

The command stores original outcome bytes in a digest-addressed
`review-artifacts/` file and appends a `review.evidence` observation. Repeating
the same capture or an older exact prefix does not append another event or
replace newer history. An extension must retain every prior source byte;
rewritten prior rounds are refused. It never creates a run,
backdates a round, supplies product admission evidence, or converts journal
counts into approval. Keep the original outcomes too. The artifact is bounded
at 4 MiB, each source at 1 MiB, at most 32 rounds and 16 lenses; reading all
observations has an 8 MiB total budget, including failed reads. Symlink artifacts are refused. Missing,
malformed, changed or mismatched evidence is unavailable; a planned candidate
with no head is unbound until hand-off. Existing product runs keep their own
capture and evidence path. This optional display path does not qualify a release.

A later journal review makes an earlier capture unavailable until its extended
outcomes are captured. Originating reopenings remain unresolved; deferrals need
a tracked follow-up and cannot discharge a carried P0 or P1.

A refused capture names the round, the lens and the field at fault, so the lane
can mend its outcome files before it hands off.

A capture binds to the head its last round reviewed. When a commit after that
round needs no new round, such as a fixture-only change, only the lead may
carry the evidence to the queued head:

```bash
python3 -B scripts/delivery_state.py --dir <state directory> carry-review --writer lead \
  --subject <subject> --from <head the evidence covers> --to <queued head> --reason "<why no new round>"
```

Before carrying, the lead reads the diff between the two heads and judges that
nothing in it needs review; the tool does no git work. The carry journals a
`decision.recorded` entry with `reviewCarry` naming both heads and the capture's
digest, and the views then show the capture at the queued head with that
decision, never as a review of the new head. A carry never changes what the
evidence says: a capture whose last round is not aligned, or that leaves a P0 or
P1 open, is refused. Each later head needs its own carry, from the head the last
carry reached, and a later review round or capture ends a carry. Like a capture,
a carry is display and acceptance evidence only: it creates no run, is no review
round in the stats, and is no product admission evidence.

A lead-directed base merge before hand-off uses a story-bound authorization. The
`candidate.handed` entry can then carry the original review digest, its reviewed
and handed heads, and the authorized base commits. The original review retains
its candidate identity.
