# Orchestration Guide

The lead's reference for running an epic. Each section stands alone; the
skill links to each by name.

## Lead checklist

Work through these steps in order. Each step names the state it writes; write
it through the state tool, then render the views.

### Writing the state

- **Null is written with `--set`, never with a merge patch.** A merge patch
  that sets a field to null deletes it, so a required nullable field such as
  `meta.releasedHead`, a queue element's `syncedHead` or `meta.checking.now`
  then fails with `patch_invalid: missing ...`. Either leave the field out of
  the patch, or assign null by JSON Pointer:

  ```bash
  python3 -B <skill>/scripts/delivery_state.py --dir <state> patch --writer lead --version <n> --patch '{"meta": {"headline": "<headline>"}}'
  python3 -B <skill>/scripts/delivery_state.py --dir <state> patch --writer lead --version <n> --element 'lanes[id=<lane>]' --set '{"/waitingOn": null}'
  ```

  Write as `--writer runner` only before the repository has the runner (see
  below); while the runner is live, only it writes its own fields.

- **Ownership is enforced.** The lead adds each handed candidate to `queue[]`
  as `queued`, with `syncedHead` null and `checks` empty. Only the runner
  writes `stage`, `syncedHead`, `checks`, `checkSelection`, `meta.baseHead`,
  `meta.releasedHead`, `meta.checking` and `meta.baseReruns`; a lead attempt is refused with
  `field_not_owned`. Only the `select` command writes a queue element's
  `leadSelection`.
- **Planned work and change requests are the lead's.** The optional
  `planned[]` list holds work under way but not yet handed to the queue,
  `{subject, title, lane, stage: planned|building|review, changeRequest?, runId?, trackerStarted?}`, selected
  as `planned[subject=<subject>]`; a queue or planned element's optional
  `changeRequest` names its change request. `reconcile` maintains both from
  snapshots. A record that kept these under `extensions` moves them onto
  these fields; no product tool writes `extensions`.
- **Each candidate names the run that built it.** A queue element's `runId`
  is the opaque id of that run in the adopter's delivery product, joining the
  record to the run's history. The lane sets it at hand-off with
  `handoff --run <id>`; it is null when the adopter records no runs. A
  planned element may carry the same optional `runId`, written by your patch;
  `reconcile` keeps it, and the hand-off carries it into the queue. Handing
  off the same subject again keeps the stored run unless another is named.
  `reportedRounds` is the fallback only when the journal holds no
  `review.round` entries for the subject.
- **Ticket titles and summaries come from the tracker.** The optional
  `tickets` map, `{<subject>: {title, summary?}}`, holds each ticket's own
  title and summary for work the record held when it was written; an entry
  stays once written. `reconcile` keeps it from tracker snapshots; the lead
  never writes it by hand.
- **One element, one version, per write.** Update queue and lane elements one
  at a time with `--element 'queue[subject=<subject>]'`. Each write needs the
  current `--version`, so read it again with `show` between writes; a stale
  version is refused and nothing is written.
- **Adding an element replaces its array.** `--element` selects an existing
  element only; an unknown one is refused with `selector_unmatched`. To add a
  queue or lane element, read the state with `show`, then write the whole
  array in a lead merge patch: every existing element copied exactly from
  that read, including the runner's fields, plus the new element. A copy that
  changes a runner-owned field is refused with `field_not_owned`.
- **Live state comes before the work.** While a candidate's checks run, the
  runner names every selected check in `meta.checking.now.steps` and in that
  queue element's `checks`, in run order. It marks a check `running` before
  the check starts and records its result when it ends, writing both places in
  one patch, so the checking panel and the queue row always agree. A check
  whose prerequisite failed reads `skipped`; one that never started reads
  `pending`. Whatever acts as the runner should write live state the same way:
  before the work starts, not after it.
- **A killed run's live state is cleared by the next one.** A runner killed
  outright cannot clear what it wrote. The operating system releases the
  queue lock when its holder dies, so the next run to take the lock knows
  that anything still `running`, mid-step or named by the checking panel is
  stale. Before doing anything else, it journals `candidate.stopped` with the
  reason `interrupted` for each such item, then stops the item, sets its
  `running` checks back to `pending`, and clears the panel. For an item killed
  while `merging`, the entry says to check the forge, since the merge may have
  landed. A run that still holds the lock is never reset: no other run can
  take the lock while it lives.

### Before the repository has the runner

A repository may start recording an epic before it adopts the runner, or
seed the state for an epic already under way. Then whatever performs its
merges acts as the runner and writes the runner's fields as `--writer
runner`:

1. Write every lead-owned field in one lead patch, with each queue element as
   `queued`, `syncedHead` null and `checks` empty.
2. For each queue element, read the version, then write its `stage` (and its
   `syncedHead` when one exists) with `--writer runner --element
   'queue[subject=<subject>]'`.
3. Read the version, then write `meta.baseHead` as the runner; set
   `meta.checking` with `--set`.
4. Journal a decision (`--kind decision.recorded`) saying where the record
   starts, and validate.

### Tracker updates

At each journal milestone the lead also posts one tracker update, with the
host's own tracker tools, mirroring the journal entry it accompanies. Each
journal kind maps to exactly one instruction below. An update is best-effort,
like telemetry: one attempt, never a gate. With no tracker tools, delivery
proceeds and the lead notes the gap in its close-out.

Where the repository runs run-delivery with its tracker publisher in the
binding, run-delivery's tracker publisher posts every update in this table,
once each, from its own ledger, and makes the moves its configuration names:
the lead posts none of them by hand. With `tracker.updates` set to `manual`,
the lead and the lanes make them instead, as the commands print them.

| Journal kind | Tracker update |
| --- | --- |
| `lane.assigned` | On the ticket: it moves to started, naming the lane and what it delivers. |
| `review.round` | None: a lane posts each round on its own ticket, as its brief says, and the lead posts the integration review's rounds on the epic. |
| `review.evidence` | None: the lane already posted each captured round on its own ticket; the evidence shows in the record and the views. |
| `candidate.handed` | On the ticket: the hand-off checked as a claim and queued, with its change request, reviewed head and review rounds. |
| `check.started` | None: checking is transient, and its outcome posts with `candidate.merged` or `candidate.stopped`. |
| `check.finished` | None: its outcome posts with `candidate.merged` or `candidate.stopped`. |
| `check.failed` | None: its outcome posts with `candidate.stopped` once the failure is classified. |
| `check.retried` | On the flake's tracking reference: one more retry, naming the candidate and the failing tests, as evidence for fixing the flake. |
| `check.reused` | None: the candidate's work item already says it was handed off and is being checked; the reused check shows in the record and the views. |
| `check.selected` | None: the lead's selection and its reason show in the record and the views, and the checks it ran post with `candidate.merged` or `candidate.stopped`. |
| `selection.refused` | None: the stop posts with `candidate.stopped`, and the provider's words show in the record and the views. |
| `hook.held` | None: a host hook held, or warned about, a lead's stop while no listener ran, and the entry shows in the record and the views. |
| `candidate.merged` | On the ticket: the merge commit, the checks run, and any check reported but not awaited; the ticket moves to its review or completed state. |
| `candidate.stopped` | On the ticket: the stop code and what happens next, whether returned to the lane, re-queued or pending a decision. |
| `base.verified` | None: the post-merge `fullHealth` passed at the base head the named merge reached, and the result shows in the record and the views. |
| `base.broken` | On the epic: the stop code and its detail (the failing check, an unverified merge, a base that moved under a batch, or a batch that landed in part), and that the queue is stopped; `base.repaired` reopens it. |
| `base.repaired` | On the epic: the fix that landed and the gap it closed. |
| `base.moved` | On the epic, record the previous and published base heads and the default branch commit. |
| `base.rerun.started` | None: the candidate's failure already reached the lead as `check.failed`, and the record and the views show the re-run in progress. |
| `base.rerun.finished` | None: its verdict posts with `candidate.stopped`, or with `base.broken` when the base fails the check too. |
| `lease.granted` | On the ticket whose work needs the lease: the lease, its holder and when it will be released. |
| `lease.released` | On the same ticket: the lease is released. |
| `release.started` | None: the release posts once it finishes. |
| `release.finished` | On the epic: the release shipped, its commit and targets, and who approved it. |
| `finish.met` | On the epic: the finish line and the evidence that met it. |
| `decision.recorded` | On the ticket the decision concerns, or the epic: what, why, and who decided, the operator or the lead. A deferred finding links its follow-up item. |
| `blocker.recorded` | On the blocked ticket, or the epic when every ticket is blocked: the blocker, and whose answer or work unblocks it. |
| `delivery.closed` | On the epic: the close-out summary; the epic moves to its final state. |

The lead posts the updates in this table, including every cross-lane
decision. Lanes post on their own tickets at the points the lane brief names.

### 1. Plan

- Produce the plan with `$plan-work`: the outcome, scope, the contract
  decisions every unit is bound by, the units with their owned files and
  required scenarios, the dependency graph, and the observable finish lines.
- Have the plan reviewed before any lane starts, and keep its review
  evidence beside it.
- Initialise the state with the epic's headline, and record each finish line
  in `finish[]` with `met: false`.
- Give the delivery what its views need, before the first lane starts:
  - `meta.planFile` names the approved plan, so a line map with no plan chapter
    says so rather than rendering as if nothing were missing.
  - `plan.json` in the state directory is the plan chapter's one input, a
    delivery-plan/1 document. `run-delivery`'s `lead-init` writes it. Without
    `run-delivery`, write it from the approved plan and check it with the
    delivery-views `check-plan` command.
  - The binding's `state` member declares the line map as a renderer and names it
    in `liveHome`, and the live view starts with `--binding`, or the built-in page
    shows instead and says so.
  - Each lane keeps its review evidence. A brief written from the template below
    keeps its review step; a delivery run through `run-delivery` gets the same
    from `review-reduce` instead.

### 2. Decompose

- Through the tracker capability, create the epic and one ticket per unit,
  each ticket nested under the epic, carrying its unit's scope, required
  scenarios, sensors and dependencies.
- Open each ticket's Summary with what the work does, in a sentence or two;
  where it came from, such as "deferred from" another ticket, comes after.
  The views show that opening as the ticket's summary, from the tracker.
- Without a configured tracker, keep the tickets in the plan and report that
  no tracker operation was performed.
- Write the lane brief from the lane brief template below once, for every
  lane.

### 3. Assign

- Assign only tickets whose dependencies have merged, or that the plan says
  may start against an agreed contract.
- In the same step: send the assignment as one message, retitle the lane, add
  or update its element in `lanes[]` with `state: assigned`, and journal
  `lane.assigned`.
- Never leave a lane idle. When a lane hands off, assign it the next unblocked
  ticket or follow-up in the same step as recording the hand-off. When no
  unblocked work exists, record that; an idle lane while unblocked work
  exists is a lead defect, recorded as a decision.
- When two lanes must agree a shared contract, route the agreement through the
  lead, and record it as a decision.

### 4. Run the queue

With run-delivery's supervisor running, it keeps the queue's `serve` running,
reconciles the record each pass, and cleans up each merged story once its lane
has moved off it. The lead accepts each hand-off; the rest of this step is the
supervisor's.

- Lanes write their own hand-off, review rounds and focus through the state
  tool (the lane brief says how). Its `handoff` command moves a lane's planned
  work into `queue[]` at `queued`, with its head, change request, rounds and run,
  and any suite evidence the lane gives, sets the lane to `handed`, and
  journals `candidate.handed`; you do not. Only a hand-off writes evidence:
  a patch that changes `queue[i].evidence`, yours or the runner's, is refused.
  Journal a `review.round`, or queue a hand-off yourself, only for a lane
  that could not write to the record, with the times, verdicts and findings
  it gives, so its stop's stats are complete.
- Work whose criteria already hold on the base needs no change. The lane
  hands it off with `handoff --no-change --lane L --subject S --base-head SHA
  --evidence-text TEXT`: its planned element moves to `review` carrying
  `noChange`, the evidence and the base head it ran at, the lane is `handed`,
  and `candidate.handed` is journaled. It never enters `queue[]`. `handoff
  --accept <subject>` settles it: stage `settled`, `noChange.settledAt` set,
  and its lane idle when it holds nothing else. `--reject` sends it back to
  `building` without the evidence. A settled element stays in `planned`, is
  never removed, and counts as done: the close and every finish line count
  it, `next` treats it as a done blocker, reconcile leaves it as it is, and
  the views show it "closed, no change needed" with its evidence. Without
  `meta.handoffsNeedAcceptance` it settles at the hand-off. No base commit is
  made.
- Check each hand-off as a claim (the coordination protocol), a no-change one
  included. When you set
  the record's `meta.handoffsNeedAcceptance` to true, every hand-off waits as
  `pending` until you run `handoff --accept <subject>`, or
  `handoff --reject <subject> --reason <why>` to send it back to the lane's
  plan. Recording a merge also ends the wait, so both commands then refuse. A
  merge made outside the queue before you accepted says so in its journal entry
  and its reconcile report. `lead-accept` can still record your decisions on
  it, once, if it was handed off; work that never was has no claims to check.
  When `lead-accept` can't take it, journal your decision instead. Under the
  supervisor the merge holds the close and keeps its checkout until you do one
  or the other. Never hold approved work: an approved candidate enters the queue at
  once, not batched behind unrelated work or held for a convenient moment.
- When the binding sets `selection.lead`, you may choose a candidate's checks
  from its diff instead of the provider: run `select --subject <s> --binding
  <binding> --head <reviewed head> --checks <ids or json> --reason "<why>"`,
  after the hand-off and before the runner takes it. Under a binding with a
  catalogue, `--checks run-delivery,corpus-guards` names checks by id. See "Lead-selected checks"
  for when to, and what it costs. `select --subject <s> --clear` gives the
  choice back to the provider.
- Reassign the lane (step 3) once it has handed off: its `lanes[]` element
  moves to `assigned` with its next ticket. Leave it `handed` only when no
  unblocked work exists for it, and record that.
- Start the runner's `serve` once, and leave it running: it takes whatever is
  queued, in order, whenever the merge slot is free, as one batch when two or
  more wait and alone otherwise, retrying a declared flake once. Set
  `meta.hold` (`{reason, since}`) to pause it, as before a release, and clear
  it to resume. The hold takes effect at serve's next turn: a batch already
  being checked still lands, so release only once `meta.checking.now` is
  empty. Before starting `serve` on a record kept without it, check that no
  earlier stop left the base unconfirmed: an older stop that stopped the
  queue journaled no `base.broken`, so journal one, or set the hold, until
  the base is confirmed. A stop that stops the queue journals `base.broken`; `serve` takes
  nothing more until you confirm the base and journal `base.repaired`. A
  stopped candidate waits for its lane to hand it off again; under `serve`, so
  does one the runner refuses, such as one with a lead input the binding does
  not declare. A stop or refusal whose cause is the setup, such as a removed
  checkout, a binding made invalid on the base or a remote that does not
  answer, stops nothing: `serve` holds itself with the setup error as the
  hold's reason, journals `blocker.recorded`, and puts every candidate that
  turn stopped or refused back to `queued`, keeping its synced head and
  clearing its checks; the blocker entry names them. A stop's code cannot say
  whether the setup caused it, so a candidate whose check failed in that turn
  is checked again. Repair the setup, then clear `meta.hold`; the queue resumes
  in order, with no lane handing off again. A brief network failure holds
  serving the same way, so clear the hold once the remote answers. A
  candidate whose merge had begun when the setup broke stays stopped, and
  the blocker entry names it: check the forge before it is handed off again.
  A turn whose setup broke can still journal `candidate.stopped` for its
  candidates before they are put back.
- Leave the status line to the record: the views derive it from what is being
  checked, what landed last, which lanes are blocked and what is next. Set a
  status of your own with `headline --text`, only for what the record cannot
  say, such as a release in progress; it expires after the binding's
  `state.statusOverrideMaxAgeSeconds`, four hours by default, so it cannot go
  stale. The views mark it as set by the lead and say until when, so a manual
  line never passes for the derived one.
- Before the run, make sure the lead holds every lease a named lead input
  needs: its holder in `leases[]` must equal the name passed as `--lead`. Get
  it through the lease sequence in the coordination protocol (request,
  acknowledge, then record the lead as holder and journal `lease.granted`),
  never by overwriting another holder.
- Without `serve`, run the queue with the runner's `run` subcommand, in queue
  order. When two or more approved candidates wait and the checks are slow,
  run them with `batch` instead, in queue order: one check run covers them all
  (see "What a batch proves" in the binding guide).
- On a stop, read its code in the result document and act. Under `serve`, a
  stopped candidate runs again once its lane hands it off again, at a new
  head or the same one; without `serve`, name the candidate again to re-queue
  it. A re-queued candidate runs again, still pinned to its reviewed `head` or
  the runner's last `syncedHead`.
  A candidate returned to its lane takes priority over the lane's current
  assignment: set the lane's `focus` to the returned candidate, and say so in
  the message that returns it.

  | Code | What the lead does |
  | --- | --- |
  | `candidate_moved` | The lane pushed after hand-off. Check the new head as a fresh hand-off, write it as `head`, and re-queue. |
  | `candidate_unknown`, `candidate_unresolved` | Add the candidate to `queue[]`, or fix its reference, and re-queue. A `candidate_unresolved` stop carries the candidate command's exit code or timeout, the end of its stderr, and the end of its stdout, scrubbed (see step output below), and names the log that keeps its whole output. |
  | `already_merged` | Nothing to do; mark the lane `done` only if it was left `handed` (see the merge rule below). |
  | `source_conflict` | Return the candidate to its lane to resolve the listed paths. |
  | `lease_required` | Obtain the named lease through the lease sequence, record the lead (the `--lead` name) as holder, and re-queue. |
  | `lease_withdraw_failed` | Withdraw the lease material by hand, then re-queue. Nothing was pushed. |
  | `sync_step_failed` | A lead input, regenerator or post-sync step failed or timed out. Fix the step or its input, and re-queue. |
  | `unstable_regeneration` | The regenerators keep changing files. Fix the regenerator before re-queueing. |
  | `undeclared_change`, `unreviewed_change` | The sync produced changes the binding does not declare, or code the lenses did not review. Return it to the lane, or declare the path in the binding. |
  | `prepare_failed`, `dirty_checkout` | A checkout could not be prepared, or was not clean after preparing or checking. Fix `prepare` or the check that writes files. |
  | `no_checks`, `profile_unknown` | The selection was empty while files changed, or a check named an unknown profile. Fix the provider, `alwaysRun`, `fallback` or `profiles`. |
  | `selection_invalid` | Your check selection no longer fits the binding, or two batch members' selections define one check differently. Select again, or clear it, and re-queue. |
  | `provider_refused` | Under `selection.refusalStops`, the provider exited non-zero, so no check ran and the candidate is held. Its words are in `checkSelection.detail` and in `selection.refused`. Select its checks for its reviewed head, then re-queue it. |
  | `check_failed` | Return the failing check's output to the lane. |
  | `base_broken` | Stop and follow the base-broken rules. |
  | `base_moved` | The base moved after the candidate was synced. `run` re-syncs and re-checks once and stops on a second move; a standalone `check` or `merge` stops at once. Re-queue. |
  | `not_synced` | Run `sync` before `check` or `merge`. |
  | `push_refused`, `ready_refused`, `merge_refused` | The forge refused the push, the `ready` gate, or the merge. Read the output, resolve it, and re-queue. |
  | `merged_unverified` | Stop the queue and confirm the base by hand before anything else merges. |
  | `batch_tree_mismatch` | The base's tree is no longer the one the batch recorded, so something landed that the batch did not check. Stop the queue, confirm the base by hand, then batch the members that did not land again; they are back at `queued`. |
  | `binding_invalid` | Fix the binding on the base branch. |
  | `queue_locked` | Another runner holds the queue. Wait for it, or raise `--wait`. |
  | `runner_error` | Read the result document and the runner's output; investigate before retrying. |

- When a candidate merges, move its ticket to its review or completed state
  through the tracker capability. Mark its lane `done` only when the lane was
  left `handed`, with nothing assigned at hand-off; a reassigned lane is
  already working its next ticket.

### 5. Publish

With run-delivery's supervisor running, it keeps the live view running and
renders, publishers included, after each of its own writes.

- Start the live view at the beginning of the delivery and point the
  operator at its address; it re-renders on every record write, so it is
  never stale.
- `render_state.py live --status-file PATH` re-reads a supervisor's status
  file on every page it serves: `pid`, `state` (`running` or `stopped`),
  `at`, and optionally `why`, `said` and `restart`. While it says `running`
  and its pid runs, the page shows no banner. Once it says `stopped`, the page
  says the supervisor is down, since when and why; a file still marked
  `running` for a pid that no longer runs says the supervisor stopped without
  recording why. Each names the file's `restart` command, and an open page
  follows the file without a reload. Without the option the view renders as
  it always has.
- The live view is sized for a handful of pages on this machine. Each open
  page holds one connection and one thread, and each update carries the
  whole record. A page that reconnects gets the whole current record at
  once, so it never needs the change log's history to catch up.
- After every runner result and every lead write, render the views with
  `render_state.py`. When the host stores the live document with its own
  tool, follow the publishing step.

### 6. Check the finish lines live

With run-delivery's supervisor running, the supervisor judges `finish.met` by
the record's covers and proofs: a line whose covered criteria have every proof
it names done is marked met and journalled once, and an entry the lead
journalled by hand is skipped. Only a finish line the record can't prove is
the lead's: a finish line the record can't prove holds the close until the
lead checks it as below, marks it met and journals it.

- For each finish line, run the observable check the plan names, against the
  merged base, not against a lane's checkout.
- Set the line's `met` to true only on that evidence, and journal
  `finish.met`.

### 7. Release with approval

- Release only after step 6 has met the finish lines, and release exactly the
  `mergedHead` of the last successful `merge` result: the commit the runner
  verified as the queue's own merge. `meta.baseHead` names the same commit
  only while that merge is the runner's most recent result; a later sync or
  check rewrites it to whatever tip it observed, which may hold commits the
  queue did not merge. Never pass a local branch name, and confirm any other
  commit by hand before releasing it.
- Ask the operator to approve each release target that requires approval,
  naming that commit and the targets.
- Only after the operator approves in the conversation, run the runner's
  `release` subcommand with `--approved-by` recording who approved. The flag
  is a record of that approval, not a gate. A target that requires approval
  stops with `approval_required` without it; `ref_invalid` and
  `ref_not_on_base` refuse a ref that is not the base tip or an ancestor, and
  a failing target stops with `release_failed`.
- The runner journals `release.started` and `release.finished` and stops at
  the first failure of a target that stops on failure; read its result
  rather than journalling the release again.

### 8. Close out

With run-delivery's supervisor running, the lead does none of these by hand.
`fullHealth` is the post-merge tier, and under `gate` it doesn't wait for the
close: after each merge or batch, the supervisor runs it at the new base head in
a fresh checkout of that head, under the binding's `fullHealthTimeoutSeconds`.
It journals `base.verified` on a pass, `base.broken` on a failure and
`base.repaired` on the first pass after a failed run, each naming the merge the
run follows, and writes the result to the record. It runs `fullHealth` once per
base head, unless the base declares the `focused` posture: a failed head runs
again only on `lead-resolve full-health`, and the next run waits for another
merge. A `base.broken` it journals stops the queue as any other does, and its
`base.repaired` reopens it; a pass never repairs a break the queue found, which
the lead confirms. If the base turns `focused` while such a break stands, the
break holds nothing at the close, since no run comes to clear it, but it stays
listed in "Needs you" and serve still waits for `base.repaired`. The run is a
child of its own, so the supervisor keeps passing beside it: it keeps `serve`
and the live view up and "Needs you" current, where the run holds the close
until it ends. One run at a time holds its lock, so two passes never start two
for one head, and a run that outlives a killed supervisor is adopted by the next
one rather than started again; `lead-close` stops it, and it records no result.
Once nothing holds the close, the supervisor runs
`fullHealth` at the final head only when no run at that head has happened, as
in a delivery that merged nothing, then files a follow-up for each deferred
finding, journals `delivery.closed` and waits until the tracker has every
update, archives the record, aligns the operator's checkout when it is clean,
and writes a friction note stub for `$compound-learning`, in that order. The lead's part is what holds the close,
which every lead command names from the supervisor's last pass, and the
friction note. The steps below are for a delivery without it.

The record marks how far a close got, in `meta.closing`, with two marks that
each name the `delivery.closed` journal entry they belong to. The `started`
mark goes into the record with that entry, whoever journals it through the state
tool, the supervisor or the lead by hand. The `finished` mark is written once
the close's archive has landed, by the supervisor's close-out or by
`lead-close`, in the live record and in the archived copy, whose views are
rendered again to show it. The supervisor's friction note comes after it.
`closed.json` names the closing entry too.

Only the latest close counts. New work after a close, a `lane.assigned`,
`candidate.handed` or `check.started` entry, reopens the delivery, and closing
it again journals a new `delivery.closed`. A mark or a `closed.json` naming an
earlier entry says nothing of the new close, which archives again. A delivery
`lead-close` closed stays closed to `lead-run` until a later `delivery.closed`
is journaled.

- **Finished:** both marks name the closing entry. The status line run-delivery's lead
  commands print, `doctor`'s supervisor row and the line map say the delivery
  is closed.
- **Stopped partway:** the `started` mark names the closing entry, no
  `finished` mark does, and no close is running. The status line and `doctor`'s
  supervisor row, as `wrong`, say the close stopped partway and name what
  finishes it, as the line map's "The close stopped partway" box does.
  `lead-run --resume` finishes it when the record meets the close condition: no
  planned story, nothing unmerged, every finish line met and no busy lane. A
  lane checkout that cleanup hasn't removed yet doesn't count, since the
  resumed supervisor's own pass removes it. Otherwise `lead-close` finishes it,
  since a resumed supervisor would hold on what is still open; `lead-close`
  doesn't wait for the tracker and lists the updates it still owes. A resumed
  supervisor finds the archive it already made once `closed.json` names the
  close, and archives afresh otherwise, leaving the earlier folder without the
  `finished` mark; `lead-close` always archives the record afresh. Marking a
  close finished again changes nothing.
- **Unmarked:** no `started` mark names the closing entry, as in a record from
  before the marks or a `delivery.closed` journaled by a tool that writes none.
  The close still runs and archives, writes no mark, and reads as closed from
  `closed.json` alone.

- Read `.agents/delivery-posture.json` at the tip of the delivery's base, fetched
  fresh: never a candidate's copy or your own checkout's. Under `focused`, no run
  starts, a failed run at that head holds nothing, nothing runs in its place, and
  the journal, the close-out summary and the plan chapter
  say the run was skipped under the posture, naming its source and reason. `gate`,
  no file, or a file that is malformed or unreadable is a gate.
- Unless the delivery's base declares the `focused` posture at its tip, run the binding's `fullHealth` once, on the final base, unless a run at that head already stands from a merge: in a fresh detached
  checkout of the final base tip, after the binding's `prepare` steps, with
  the binding's `environment` set and its placeholders filled in. Never run
  it in the operator's own checkout. Under `gate` it is the backstop for every
  lead-selected check set: anything a selection left out runs here. Under
  `focused` nothing is: a check a selection left out never runs.
- Confirm every line in `finish[]` is met, or record why it is not.
- Move every ticket, and the epic, to its final state through the tracker
  capability.
- Record every follow-up in `followups[]`, each with its tracker reference.
- Align the operator's checkout with the final base, and clean up every lane
  checkout the epic created.
- Preserve what the epic taught with `$compound-learning`.
- Journal `delivery.closed`, and render the views one last time.
- Mark the close finished with `delivery_state.py close-finished --entry <its
  journal id>`, and render again; a close left with only its `started` mark
  reads as stopped partway.

Every unit reached the delivery's base reviewed by two lenses, but the lead's own
integration work has had no reviewer. The delivery reaches the default branch in
three steps, with the supervisor or without it:

1. **Integrate.** Build the product-only change, with run-delivery's `ship`
   where the repository uses it. Merge the default branch into the delivery's
   base first when it moved, resolve its conflicts, and fix what the merged
   tree breaks.
2. **Review the integration.** Run `$review-work` with the delivery's lenses and
   round bound on the lead's own changes since the last unit merged: removing
   the delivery-only files, any merge of the default branch with its conflict
   resolutions, read as `git show --cc` and not as the whole incoming branch,
   and any fix the merged tree needed. A delivery whose integration changed
   nothing beyond removing the delivery-only files still runs it, on that
   removal. Findings are triaged as a lane's are, and each round is journaled
   under the delivery as `review.round`, with the delivery's key as its subject.
3. **Merge to the default branch** only once that review is aligned.

Where the lead closes by hand, take these steps before journaling
`delivery.closed`, so the review's rounds reach the archived record. Under the
supervisor, which archives the record once nothing holds the close, the rounds
land in the live record after the archive, and the lead's post of each round on
the epic is their durable trace.

### Reviewing an integration base for the default branch

Use the delivery's recorded `integrationTarget`. Fetch the remote target and
integration base, and create a separate integration-review branch from the
integration tip. Merge the fetched target into it with normal Git ancestry.
Review each substantive conflict and its resolution. Resolve a provenance
conflict with the repository's digest writer.

Restore the target's standing binding and delivery posture from the fetched
target. Also restore its installed generation, receipt, host projections and
hook settings as a set. Include any other branch-specific settings in this
restore. Retain product source, tests, catalog, profiles, provenance, the final
plan and its review record, the delivery configuration, lane brief and lane
notes. The configuration's integration branch and target document where the
delivery ran. Keep local records, logs and temporary fixtures in the delivery
archive.

Before integration review, compare the target-owned paths against the fetched
target. Confirm the standing binding and the target's exact archive digest in
the receipt. Run the repository's integration gates in that restored tree.
Then run the integration review above against the exact candidate, including
the merge and any resolutions. Open the reviewed change request into the target
and let the repository's merge process land it. If the target moves before
landing, incorporate it and repeat the affected review and gates.

## Lane brief template

The lead writes one brief per epic and sends it to every lane with its first
assignment. A lane's ticket and its unit in the plan add the scope; the brief
carries the rules that are the same for every lane. Fill each `<placeholder>`
from the plan and the repository's own instructions, and keep everything else.

---

**Epic: <epic title>. These are the lane rules.**

**Plan**

- The approved plan is `<plan path>`. Its review evidence is `<plan review
  evidence path>`.
- Read your ticket in the tracker, or in the lead's assignment message when
  no tracker is configured, then your unit in the plan.
- The plan's contract decisions are binding. Do not redesign them; raise a
  disagreement with the lead instead.

**Repository**

- Read `<repository instruction file>` first. Its rules apply.
- Start each ticket in its own isolated checkout from `<base ref>`:
  `<checkout command>`. Never edit the lead's checkout or the base branch.
- Name branches `<branch pattern>`.
- `<shared resources a lane must not touch without a lease, such as a shared
  deployment or database>`.

**The focused lane profile**

1. Run your unit's required scenarios first, written test-first or
   characterization-first as the unit says.
2. Then run your unit's sensors only: `<sensor commands>`. Never run a full
   suite in the lane `<except: name any suite the repository says is small
   enough to run whole>`.
3. Review with two lenses as subagents, `lens.outcome-correctness` and
   `lens.adversarial-testing`. Pass them the ticket's contract and the plan
   unit, and tell them not to spawn subagents. Resume the same reviewers each
   round until both approve, within `$review-work`'s round bound. At the
   bound, stop and report the blocker to the lead.
4. Run the repository's compounding check: `<compounding check>`.
5. Open a draft change request: `<change request command>`, titled
   `<title pattern>`, with a body of Summary, Why, Validation, then the
   ticket link.

**Triage of findings**

- Blocking findings are always fixed in the lane.
- Cheap non-blocking findings, about an hour or less, are fixed in the lane
  with no ticket.
- Expensive non-blocking findings come to the lead.
- A finding first raised in a later round, against code an earlier round
  approved, is judged on scope before it blocks.

**Hand-off**

Send the lead one message containing:

- the candidate reference;
- the reviewed head;
- the review rounds;
- the validation;
- the run id, or `none`;
- every declared lead input the candidate needs, by its binding id;
- every expensive non-blocking finding, with its lens and why it is
  expensive, or `none`.

Do not merge, release, or hold a lease you were not granted. The lead merges
through the queue after syncing the candidate with its base.

**Shared files**

- `<shared registries, indexes or generated files every lane adds to>`.
  Each lane adds only its own entries.
- When two lanes collide in a shared file, the later change request rebases.
  Keep each edit to your own entries, and regenerate generated files rather
  than merging them by hand.

**Tracker updates**

Post on your own ticket, with your host's tracker tools:

- at start;
- at each decision you make: what and why;
- at each review round: the lenses' verdicts, the findings and the fixes;
- at each finding you pass to the lead to defer, naming it; the lead's
  decision post links the follow-up;
- at hand-off: the change request, the head, the rounds and the validation.

When you begin, also move your ticket to started with the same tools: the
next reconcile takes that alone to move its stop from planned to building.
Your draft change request does the same from the forge, for a delivery that
reads no tracker snapshot.

One attempt each, never a gate. With no tracker tools, carry on and say so
in your hand-off.

**Review rounds**

At the end of each review round, alongside its tracker post, record the round
in the delivery record:

```bash
python3 -B <skill>/scripts/delivery_state.py --dir <state> journal --writer lane --kind review.round --subject <subject> --lane <lane> --review '{"round": <n>, "startedAt": "<dispatched>", "finishedAt": "<last verdict>", "verdicts": {"<lens>": "approve"}, "findings": {"P0": 0, "P1": 0, "P2": 0, "P3": 0}}'
```

**Review evidence**

Ask each lens for its result in review-lens-result/1, on top of the shape
`$obtain-review`'s round brief gives, and put these fields in each lens's brief:
`schemaVersion` set to that name, `lensId` its lens, `subjectRef` your subject,
`candidateRef` the full commit it reviewed, `round`, `outcome` as `aligned` or
`changes-requested`, `evidence`, and `findings`, each with `id`, `severity` and
`summary`. From round 2 on add `resolvedFindings`, each finding of that lens still
open going into the round, with its `id`, `state` and `resolution`; a finding closed
in evidence alone stays open. `state` is one of `closed`, `fixed`, `withdrawn`,
`dropped`, `deferred` or `open`, and a `deferred` one also carries `deferredIssue`,
naming its follow-up item. A P0 is never deferred, and a P2 or P3 can be. A P1
can be deferred only when a verification round files it new against lines
outside the delta: its id is listed in `lateFindings` and it carries
`deferredIssue`. Closing a carried finding as `deferred` stays a P2 or P3 move.

Save every lens's result for every round, one outcome file per round, as the lens
returned it, outside your checkout, and keep the files until close-out:

```json
{"round": <n>, "outcome": "<round outcome>", "entries": [{"lens": "<lens>", "state": "obtained", "binding": {"subjectRef": "<subject>", "candidateRef": "<commit reviewed>", "round": <n>}, "result": <the lens's result>}]}
```

A round's outcome is `aligned` only when every lens aligned and no finding is open,
and `changes-requested` otherwise. Right after your hand-off, which gives the record
your reviewed head, capture every round in order against that candidate:

```bash
python3 -B <skill>/scripts/delivery_state.py --dir <state> capture-review --subject <subject> --candidate <reviewed head> --outcome <round-1.json> --outcome <round-2.json>
```

Then send the lead your closing message, with what the capture returned, or why you
couldn't capture. A missing earlier round is refused, and journal counts alone show
no review on the line map, so a round's file can't be written afterwards from
memory.

**Focus and hand-off**

Say what you are doing as it changes, and hand your work off yourself, through
the state tool; the lead no longer writes either for you:

```bash
python3 -B <skill>/scripts/delivery_state.py --dir <state> focus --lane <lane> --text "<what you are doing>" [--state working|reviewing|blocked] [--waiting-on <who>]
python3 -B <skill>/scripts/delivery_state.py --dir <state> handoff --lane <lane> --subject <subject> --head <reviewed head> --change-request <reference> --rounds <n> [--run <run id>] [--evidence '<evidence JSON>']
```

Add `--run` with the id of the run that built the work when your repository
records runs; leave it out when it records none.

When the binding lists a check in `reusableChecks` and you ran that check
yourself, at a head you pushed, and it passed, add its evidence to the
hand-off, once for each such check: the queue skips running it again when the
tree it would check is the one you checked. Print the evidence as a step of its
own and hand it off only if that step succeeded:

```bash
python3 -B <skill>/scripts/merge_queue.py --repo <lane checkout> --remote <remote> --base <base> evidence --check <reusable check> --head <head you ran it on>
```

It exits 1 with the reason whenever it refuses: the head is not a commit, its
tree has no valid binding for this base, the check is not reusable in that
binding, or git fails. A refusal pasted into `--evidence` is refused as the
evidence command's. Record only a run that finished at the head
you name; add `--result failed` for one that failed, or leave the evidence out.

The hand-off queues your planned work at its reviewed head; the runner takes
it from there. To hand off a stopped candidate again, run the same command
with its head, new or unchanged. Anything outside your own lane is refused with
`writer_not_owned`.

One attempt, never a gate. When you cannot write to the record, give each
round in your hand-off as
`round <n>: <startedAt> → <finishedAt>, <verdict per lens>, findings P0/P1/P2/P3`,
and the lead records it.

**Questions**

Escalate only to the lead, never to the operator: set `focus --text "<your one
short question>" --state blocked --waiting-on lead`, so the lead's `wait` sees it
and carries the question, then send the question and stop. The lead answers, or
takes it to the operator. When the answer comes, set `focus --text "<what you are
doing>" --state working --waiting-on ""` before going on.

**Local load**

Run at most `<worker cap, default 4>` local test workers. Stop only processes you
started, by the pid you recorded, never by pattern.

**Run events**

`<the repository's run-event command, or "none">`. One attempt, skipped on
any error. Put `run: <id>` or `run: none` in your hand-off.

**Parallel lanes**

`<which units run alongside which, and which shared contract each agrees with
whom; name the lane that owns each shared file>`.

**Toolchain**

`<toolchain versions, environment variables, and known failures unrelated to
any lane, with how to recognise them>`.

## Lead authority and escalation

The lead owns the judgement calls within the approved plan and the operator's
standing direction: finding triage and deferrals, review-churn scope calls,
fixing forward on a broken base, lane assignment and rebalancing, lease
arbitration, merge order, and plan-text corrections that keep the approved
finish line. For each, the lead decides, journals `decision.recorded` with
what, why and who decided, posts the tracker update, and informs the lanes
and the operator it concerns. It does not ask.

The operator is brought in only for:

- a release or production approval the binding requires;
- a change to the approved scope or finish line;
- an irreversible or outward-facing action not already authorized;
- a permission or access grant the lead does not hold;
- a host that cannot start or message sessions, so no lane can start;
- owner intent that is genuinely ambiguous, where neither the plan nor
  standing direction settles it.

Any other question is the lead's to settle. An escalation is one consolidated
question with the lead's recommendation, and the lead keeps every unblocked
lane working while it waits. Lanes escalate to the lead, never to the
operator.

- **Never hold approved work.** An approved, handed-off candidate enters the
  queue at once.
- **Never leave a lane idle.** The lead assigns a lane's next unblocked
  ticket or follow-up in the same step as its hand-off. An idle lane while
  unblocked work exists is a lead defect, and the lead records it.
- **Reconcile at every event.** At every event, whether a hand-off, a runner
  result, a lane message or an operator message, the lead reconciles the
  delivery record with the forge, the tracker and the lanes before acting, so
  the operator never has to prompt a status check. Tracker reconciliation
  mirrors the forge's, so no item depends on the lead remembering to record
  it:
  - List the epic's child items, with the binding's `trackerSnapshot`
    command when it declares one and the tracker capability's `list-work`
    otherwise, and compare them with the record. While `serve` runs with
    `trackerSnapshot` and `forgeSnapshot` declared, it does this listing and
    the reconcile on its interval; the lead settles what it leaves in
    `meta.sourceReports` and checks `meta.sources` for a source gone stale.
  - Add any item the record lacks, such as a follow-up or deferral filed but
    never recorded, before the views are published. It goes to `followups[]`,
    which needs no escalation. It becomes planned work only when the approved
    plan already covers it; promoting it otherwise is a scope change for the
    operator.
  - When an item's tracker state disagrees with the record, the record wins
    for states the lead or the runner owns, and the tracker item is
    corrected, except a change the operator made. That is standing direction:
    the lead updates the record and journals `decision.recorded`, never
    reverting it.
  - Reconciliation is best-effort, like the updates: one attempt. With no
    tracker tools, or a listing that fails, the lead notes the gap; it never
    delays acting or publishing.

## Coordination protocol

These rules hold on any host that can start and message sessions. On a host
that cannot, no lane starts, under
[Without a session capability](#without-a-session-capability).

### Messages

- Send one consolidated message per lane per milestone: an assignment, a
  decision, a hand-off, a merge. Do not stream progress; a lane's own task
  list and its final report carry that. This rule covers session messages,
  not the tracker updates the lane brief and the tracker updates table
  require.
- Retitle a lane in the same step as its assignment, so its title always
  names the ticket it is working on.
- When the host holds or refuses a message, keep the undelivered text in the
  lead's notes, inform the operator once (a notice, not a question), and send
  it when delivery resumes.
  Never repeat a message to force it through. A refusal that persists means the
  host cannot message sessions: follow
  [Without a session capability](#without-a-session-capability).
- A lane that is blocked on a decision it cannot make sends the lead one
  short question and stops. Lanes never go to the operator directly; the lead
  answers, or escalates under the lead authority rules. Progress and results
  arrive with the hand-off.

### Reports are claims

Treat every lane report as a claim to check before acting on it:

- the candidate reference resolves, and its head is the reviewed head;
- the reported validation names commands and results, not adjectives;
- the review rounds name each lens's verdict, and every blocking finding is
  closed by the lens that raised it;
- every declared lead input exists in the binding;
- review evidence was captured for the reviewed head, or the lane said why not.
  Check this on the lane's closing message, which follows its capture.
  `delivery_state.py review-evidence --subject <subject>` says which, and exits 1
  unless every journaled round is captured at that head. An item naming a run says
  `run-named`, since that run keeps its own evidence: confirm it in the run's history.

When a claim does not hold, return the hand-off to the lane with what failed.

### Without a session capability

When the host cannot start or message other sessions, no lane starts. A lane
is always a session of its own: never a subagent, and never the lead working in
its own session. The lead journals `blocker.recorded` and brings it to the
operator, who may choose to deliver the tickets one at a time through
`$execute-work`. Subagents remain how a lane realizes its review lenses.

A host that loses the ability to start or message sessions during a delivery is
this blocker and not a held message. So is one that still refuses a message one
retry later, at the next milestone or, with nothing else pending, at the lead's
next event. No further lane starts. A candidate a lane hands off is still checked
as a claim: it enters the queue if the report holds. One whose report fails
returns to the lane on a host that can still message it, and otherwise waits for
the operator's answer. On a host that can still message its running lanes, they
keep working and the lead keeps assigning them as usual. On one that cannot, each
running lane reaches its hand-off and takes no new assignment, which is not an
idle-lane defect.

### The lead's wake

A lead does not end its turn while a lane is working or a candidate is queued
unless a `wait` is running in the background. It restarts the wait after each
wake, with the arguments the last one printed. The host's notes are a
convenience and never the lead's only wake. Where nothing can run in the
background, the lead tells the operator that it won't wake on its own.

```bash
python3 -B <skill>/scripts/delivery_state.py --dir <state> wait [--after <journal id>] [--timeout <seconds>] [--poll <seconds>] [--output <file>]
```

With run-delivery, the wait a lead starts is `lead-wait`, which wraps this `wait`:

```bash
python3 -B <run-delivery skill>/scripts/run_delivery.py --delivery <key> lead-wait [<wait arguments>]
```

It passes its arguments to `wait` and keeps the wait's output and exit. Without `--after` it starts after the
journal's last entry, and without `--output` it writes the result to the flow log's `<delivery>.wait.json`. While it
runs it keeps a lease in the record, `<state>/lead-wait.json`, holding its `pid`, `startedAt`, `updatedAt`, `after`
and `checkout`, rewritten through a temporary file every 60 seconds and removed when it ends or is stopped. A lease is
live while its pid is alive and its `updatedAt` is under 180 seconds old; a lease whose pid has died, or one 180
seconds old, is no listener. The supervisor, the live view and the lead commands read it through one function, so they
never disagree:

- `lead-init`, `lead-assign`, `lead-accept` and `lead-run --resume` end, while a lane works or a candidate is queued,
  with the exact `lead-wait` to start in the background: the last wait's `restart`, so nothing journalled since that
  wait read is missed, or, with none, one after the journal's last entry. While one listens they name it instead, and
  they print nothing about it when nothing works or is queued.
- The supervisor's "Needs you", and so the live view, says "The lead isn't listening" with that command while a lane
  works or a candidate is queued and no lease is live, and every lead command's status line says the lead isn't
  listening. A running `lead-wait` clears both.

Where this generation installs host hooks, run-delivery's `lead.may-stop` hook also holds a lead's stop while a lane
works or a candidate is queued and no `lead-wait` listens, and journals `hook.held`, which never wakes a `wait`; its
settings and when it holds are in run-delivery's configuration reference, "Host hooks". On a host whose own hooks
this generation doesn't install, the printed command, the status line and "Needs you" are what keep the rule.

`wait` reads the record and the machine only, writes nothing but its own `--output`
file, and reads no CI or forge. It returns on the first thing needing the lead:

- a journal entry after `--after` of kind `candidate.handed`, `check.failed`,
  `candidate.merged`, `candidate.stopped`, `base.broken` or `blocker.recorded`;
- a lane in the `blocked` state;
- machine pressure: five-minute load above `--load-multiple` times the cores
  (2 by default), free disk under `--min-free-bytes` (20 GiB), or swap grown by
  more than `--swap-growth-bytes` (4 GiB).

It exits 0 on a wake, 1 on an error, such as a malformed journal or a missing
state directory, 2 on a usage error, and exits 3 when `--timeout` passes, 30
minutes by default. It
prints what it found: `woke`, `events`, `blockedLanes`, `pressure`, `quietLanes`,
`machine`, `unavailable`, `through` and `restart`. Without `--after` it starts after the
journal's last entry, so pass it on every restart: `restart` holds the arguments
for the next wait, so an entry written between two waits is not missed, along with
this wait's timeout, poll and limits. They also mark a held condition as seen, so
a lane still blocked on the same question, low disk or steady high load doesn't
wake the lead again and again, and swap growth keeps its baseline. A lane that
blocks again or changes its question wakes the lead, even if nothing read it
clear in between, and so does a different lane or signal. A held load or disk
signal wakes it again only once it has cleared, 10% inside its limit, and
returned. A machine value this system can't read is named in
`unavailable` and on stderr, and is never treated as healthy. Swap is read from
`/proc/meminfo` or `vm.swapusage`; on any other system it is unavailable.

One run prints one JSON document, an error included. A lead that runs the wait in the background passes
`--output <file>` and reads that file, not the host's capture of the command's output, which may add lines of its own,
such as an exit trailer after the document, that a JSON reader refuses. The file is outside the state directory,
holds the same one document, and is replaced whole by each run, a failed one too. `restart` carries `--output`, so the
next wait writes there again.

Whenever a wait names pressure, whatever else woke it, the lead finds what is
loading the machine before taking more work, and reduces this delivery's own
load first, such as by capping test workers. It reports what it can't reduce to
the operator, such as other work or a system service. It stops nothing it didn't
start without the operator's word.

### Leases

A lease grants one holder a shared resource, such as a shared deployment, for
a bounded piece of work. Holders may be outside the epic, for example a peer
lead elsewhere; record each by name in `leases[]`.

Every lease follows the same sequence, and the lead journals each step:

1. **Request.** The would-be holder asks the current holder, through the lead
   when the holder is outside the epic.
2. **Acknowledge.** The holder agrees and names when it will release.
3. **Start.** The lead records the new holder in `leases[]` and journals
   `lease.granted`.
4. **Done.** The holder reports that its work on the resource is finished.
5. **Release.** The lead records the release and journals `lease.released`.

A lane never holds a lease it was not granted. The runner runs a lead input
that needs a lease only when `leases[]` shows the lead holds it, and stops
with `lease_required` otherwise.

## Triage and base-broken rules

### Review findings

Each finding a review lens raises is triaged once, by the lane, before the
next round:

| Finding | Where it is resolved |
| --- | --- |
| Blocking | Fixed in the lane, always. |
| Cheap non-blocking, about an hour or less | Fixed in the lane, with no ticket. |
| Expensive non-blocking | Listed in the lane's hand-off, for the lead. |

The lead decides each expensive finding: defer it with a tracker follow-up, or
drop it. Either way the lead records a decision in the journal
(`--kind decision.recorded`), naming the finding and why.

A finding first raised in a later round, against code an earlier round
approved, is judged on scope before it blocks. A real defect still blocks;
scope that the earlier round did not ask for does not.

Every blocking finding is closed by the lens that raised it, in a later
round. A lane's own judgement does not close another lens's finding.

### A broken base

The runner classifies a failing check as `base_broken`, not `check_failed`,
only when every path the check names exists unchanged at the base head, and
the same check also fails on a fresh checkout of the base head. A broken base
stops the whole queue, because no candidate can be checked against it.

When the runner reports `base_broken`:

1. **Fix forward first.** Land the smallest change that makes the check pass
   on the base, through the queue like any candidate.
2. **Close the gap that let it through.** Either declare the missing
   membership in the repository's affected-checks provider, so the check is
   selected for the changes that can break it, or add the check to the
   binding's `alwaysRun`.
3. **Record a decision** in the journal (`--kind decision.recorded`), naming
   the check, the cause and the gap closed. Journal `base.repaired` when the
   base passes again.

Then restart the queue from the first stopped candidate.

## Binding guide

A repository adopts this workflow with one binding file,
`.agents/orchestration.json`, of format `orchestration-binding/1`, described
by the `orchestration-binding/1` schema in the skill's `schemas.json`, beside
this guide. The runner reads it from the tip of the remote base branch named
on its command line, never from a
candidate or the operator's checkout, so a candidate can never change how it
is itself checked or merged. A binding that serves a different branch, by
neither its `base` nor its `bases`, is refused with `binding_invalid`.

### Base mismatch

The runner refuses `--base` with `binding_invalid` unless the binding on that
base serves it, as its `base.ref` or a match in its `bases`. In a pattern, `*`
matches within one segment of the branch name, so `integration-*` serves
`integration-2` but not `integration-2/fix`. To merge into that base, add it,
or a pattern that matches it, to `bases`. The runner reads the binding from the
base it merges into, so a base cut before the binding gained `bases` still
holds the old binding: commit the binding with `bases` on that base and push
it.

### Commands and placeholders

Every command is an argv array; there are no shell strings. A placeholder
replaces a whole argv element, or a whole environment value, and never
substitutes inside a longer string:

`{candidate}`, `{branch}`, `{head}`, `{expectedHead}`, `{base}`,
`{baseHead}`, `{treeSha}`, `{ref}`, `{workRoot}` and `{checkMode}`.

`{checkMode}` is available only to the `ready` and `merge` hooks invoked by
`Runner.merge`. Its exact value is `single` when that merge has no batch-synced
head, or `batch` when it does. The runner derives it from that call, never from
queue state or an environment flag. Missing or non-enum context values are
refused with `binding_invalid`. It identifies how the candidate was checked;
it is not a freshness proof or permission to skip any admission check.

### Fields

| Field | Contents |
| --- | --- |
| `schemaVersion` | `orchestration-binding/1`. |
| `base` | `{remote, ref}`: the default branch candidates merge into. `remote` must match the runner's `--remote`, and `--base` must be `ref` or match `bases`. |
| `bases` | Optional: the other long-lived branches this binding serves, such as `["main", "integration-*"]`. Each entry is an exact branch name, or a pattern whose `*` matches any run of characters within one segment of the name, never a `/`. The runner reads the binding from the base it merges into, so each base it serves carries a binding that lists it. Without `bases`, the binding serves `ref` alone. |
| `workRoot` | The directory for the runner's temporary checkouts, relative to the checkout's top level unless absolute. The runner deletes what it finds there, so it checks the path against git's own view, never the current directory: `--repo` must be a checkout's top level; `workRoot` must not contain any checkout git lists, the git directory or the common git directory; inside a git directory it sits beside git's own folders there (`objects`, `refs`, `reftable`, `worktrees`, `hooks`, `info`, `logs`, `modules`, `lfs`, `rr-cache`, `branches`, `remotes`, `rebase-merge`, `rebase-apply`, `sequencer`), never in one; inside any checkout it holds no tracked file, sits in no submodule, and is ignored there; `checkouts` and `empty-hooks` under it are never symlinks or junctions. Paths are compared without case or Unicode normal form. The runner marks each checkout it adds, so a killed run's leftovers under `workRoot/checkouts` are pruned at the next start; an unmarked worktree there is refused until its owner removes it with `git worktree remove`. Each refusal is `binding_invalid`, naming the rule. |
| `candidate` | An argv that resolves a candidate reference to `{branch, head}` as JSON. |
| `prepare` | An argv list run in every fresh checkout, such as installing dependencies or creating a ref the checks need from `{baseHead}`. |
| `hooks` | `bypass` or `run`. See "Git hooks" below. |
| `regenerable` | `{paths, regenerators}`: the generated-only paths, which may use `*` and `**` globs, and the regenerator argv list. |
| `postSync` | Entries of `{argv, paths}`; each declares the paths it may change. |
| `leadInputs` | Each input's `id`, `argv` and `paths`, plus an optional `lease` it needs and an optional `timeoutSeconds`. |
| `profiles` | `{name: {environment}}`, with placeholder values. A check names one profile; an unknown profile is refused. |
| `provider` | `{argv, cwd, timeoutSeconds, providerInputs}`, or null when the repository has no provider; null counts as a missing provider, so `fallback` is added. `providerInputs` are the paths whose change also runs the candidate's own provider. |
| `alwaysRun` | Checks in the affected-check entry shape, run for every candidate. |
| `fallback` | Checks in the affected-check entry shape, added when impact is unknown. |
| `checkTimeoutSeconds` | The default check timeout. |
| `checkTimeoutCeiling` | The maximum any check may ask for. |
| `checkOutput` | Optional. `{failurePattern}`: a regular expression with a named group `id` and an optional named group `message`, matched line by line against a failed check's output. Each match is a failing test or step, shown in the record, the journal and the views. |
| `flakes` | Optional. Known flakes, each `{check, tests?, ref}`: the check's id, the regular expressions its flaky failure ids match in full (leave `tests` out when the whole check is flaky), and the reference that tracks the flake. See "Flakes and retries". |
| `parallelChecks` | Optional. How many checks marked `parallelSafe` may run at once; 1 when absent. A check without `parallelSafe` always runs alone. |
| `selection` | Optional. `{lead, refusalStops?, catalogue?, guards?, readers?}`: `lead: true` lets the lead choose a candidate's checks with the state tool's `select` command, in place of the provider's answer and `fallback`. `refusalStops: true`, which needs `lead: true`, makes a provider that exits non-zero stop the candidate with `provider_refused` instead of widening to `fallback`. `catalogue` names checks the lead selects by id, `guards` lists catalogue ids added to every lead selection, and `readers` maps paths to the catalogue checks that read them. Each is off when absent, so every candidate is checked as before. See "Lead-selected checks" and "The check catalogue". |
| `reusableChecks` | Optional. Ids of the binding's own checks (`alwaysRun` or `fallback`) the runner may reuse from a lane's hand-off evidence instead of running them again. Each may use no placeholder but `{treeSha}`, and no binding check may name it as a prerequisite. None when absent, so nothing is reused until you opt in. See "Reused checks". |
| `batch` | Optional. `{culpritSearch}`: how `batch` finds the member that failed a batch's checks, `bisect` (the default, fewest check runs) or `serial` (each prefix in order). |
| `environment` | Variables set for every argv the runner runs. It must not set `GIT_LITERAL_PATHSPECS`, `GIT_GLOB_PATHSPECS`, `GIT_NOGLOB_PATHSPECS` or `GIT_ICASE_PATHSPECS`, which would change the paths the workRoot checks match; these names are matched without case. The runner drops an inherited setting of them before running git or any declared argv; a profile's environment may still set them for its checks. |
| `ready` | An argv run before merging; a non-zero exit stops with `ready_refused`. |
| `merge` | An argv that must use `{expectedHead}`. A binding whose `merge` lacks it is invalid. |
| `release` | Ordered targets, each with an `id`, `argv`, `stopsOnFailure` and `requiresApproval`, and an optional `timeoutSeconds`. |
| `fullHealth` | The argv for the post-merge tier: run by the supervisor after each merge or batch, and at close-out only when no run at the final head has happened. Required, and not run while the base declares the `focused` posture. |
| `fullHealthTimeoutSeconds` | Optional. The seconds one `fullHealth` run may take, a positive integer of at most 2147483; `checkTimeoutSeconds` when absent. `checkTimeoutCeiling` doesn't bound it, since it bounds the queue's checks and the post-merge tier is not one of them. |
| `leases` | Each lease's `id`, `description` and `defaultHolder`, plus optional `materialize` and `withdraw` argv. |
| `state` | `{directory, renderers, publishers, snapshotMaxAgeSeconds?, trackerSnapshot?, forgeSnapshot?, refreshSeconds?, workHints?, liveHome?}`. `liveHome` is the id of a declared argv renderer whose HTML view the live server serves at its front page, with the live store injected as at `/view/<output>`; the view must carry `<meta name="delivery-state-live">`. Without it the front page is the built-in static page. A `liveHome` that names a built-in renderer, an unknown id, or a view whose output is not an HTML file is refused. `trackerSnapshot`, `{argv, timeoutSeconds?}`, is the repository's command that prints the delivery's `work-snapshot/1` tracker snapshot; `{parent}` in its argv is the record's `meta.trackerParent`, and `{stateDirectory}` in it, or in a renderer's, a publisher's or `workHints` argv, is the resolved state directory the command runs for, so a repository that keeps several records reads the one being served rather than the one its binding names; the command owns whatever access it needs. `forgeSnapshot`, the same shape, prints the forge snapshot of the delivery's change requests; this workflow ships no forge or tracker client, so both are the repository's own, or the ready-made commands described below. `serve` runs them from the repository's checkout every `refreshSeconds`, 180 when absent and at most 86400, and 0 turns that off; it does not start on a block or interval it cannot use. Each command's stdout is read until it exits, plus a two-second drain: output that a process it leaves behind writes later is not read, and output written during the drain can spoil the snapshot. Each runs in its own process group with a per-run marker in its environment; afterwards, interrupted or not, the state tool stops what is left in that group or carries the marker, never the caller or its parents, pid 1, another user's process or one that started before the run, and stops nothing when it cannot read the process table. A child that clears its environment, or whose environment the system hides, such as some system binaries on macOS, survives. On Windows only the command itself is stopped, and only when it overran or was interrupted. `directory` is relative to the repository root unless absolute. Each renderer is `{id, builtin, output}` or `{id, argv, output}`, never both, with an optional `timeoutSeconds`; `output` is a relative path inside the state directory. Each publisher is `{id, argv}`, with an optional `timeoutSeconds`; one with a `kind` and no `argv` runs nothing, and the binding is refused, naming the fix: name the tracker publisher instead. `workHints`, `{argv, timeoutSeconds?}`, is the repository's command that prints a `work-hints/1` document of each item's files, priority and `blockers` (the subjects it comes after in the plan's order, read beside the tracker's `blockedBy`) for `next`, run the same way; run-delivery's `lead-hints` is one. `snapshotMaxAgeSeconds`, a whole number of seconds, is how old a forge or tracker snapshot may be before `reconcile` refuses it, 900 when absent. After each of its state writes the runner passes this member to the render wrapper; when it is valid and declares no renderer and no publisher, the runner starts no wrapper, and reads the record itself instead, noting a failure as the wrapper would. |

Ready-made views and snapshot commands ship in the separate `delivery-views`
skill, for a profile that includes it: a live line map and a delivery history
page, declared as argv renderers, and tracker and forge snapshot commands for
`trackerSnapshot` and `forgeSnapshot`. The repository configures them rather
than writing its own, and this workflow runs them like any other renderer or
snapshot command. That skill's guide describes their configuration.

### Git hooks

- Until the binding is read, every git command the runner issues runs with a
  private, empty hooks directory, so no repository hook runs before the
  binding says whether hooks may.
- With `hooks: "bypass"`, every git command the runner issues uses an empty
  hooks directory, and every declared argv gets the same setting through
  git's environment configuration, so git commands inside `merge`, the
  regenerators and the other declared steps skip hooks too. The runner
  records that it bypassed them.
- With `hooks: "run"`, hooks run as the repository installed them.

### Git configuration

The runner's own git commands pin the settings its work depends on, so a
user's global or system configuration cannot change what it commits, sees or
merges:

- nothing is signed or required to be signed (`commit.gpgsign`,
  `push.gpgSign`, `merge.verifySignatures`);
- bytes are committed as the tools wrote them, unless the repository's own
  `.gitattributes` says otherwise (`core.autocrlf`, `core.safecrlf`, and no
  global `core.attributesFile`);
- no global excludes file hides an undeclared change (`core.excludesFile`);
- renames are always followed, so a rename never looks like a conflict, and a
  renamed directory never silently moves a file the candidate added there
  (`merge.renames`, `merge.directoryRenames`, `diff.renames`);
- no recorded resolution resolves a conflict for it (`rerere`);
- status reads the files themselves (`core.fsmonitor`, `core.untrackedCache`);
- merges and commits never open an editor;
- nothing reaches into submodules (`submodule.recurse`).

These are pinned, not isolated: dropping global and system configuration would
also drop the sign-in helpers, address rewrites and transport settings that
the runner's fetch and push rely on. A pin overrides every configuration
level, the repository's own included. Settings outside this set, and
`.gitattributes`, still apply, and the argv the binding declares runs with the
user's full configuration apart from the hooks setting above. Under
`hooks: "run"`, a global `core.hooksPath` still chooses which hooks run.

The runner's own git reads the global and system configuration of the user
who started it: `HOME`, `XDG_CONFIG_HOME`, `GIT_CONFIG_GLOBAL`,
`GIT_CONFIG_SYSTEM` and `GIT_CONFIG_NOSYSTEM` come from the runner's own
environment, never from the binding's `environment`; on Windows so do
`USERPROFILE`, `HOMEDRIVE` and `HOMEPATH`, and a name is matched without case.
A binding can therefore give its declared argv another home or configuration
file without changing which configuration files the runner's own git reads.
Other variables in the binding's `environment`, such as an SSH command or a
proxy, still reach the runner's own fetch and push. The pins above already
cover the ignore and attribute files a home could supply.

### The affected-checks provider

The provider is the repository's own answer to "which checks does this change
need?", in format `affected-checks/1`.

- **Input,** on stdin: `{base, baseHead, head, changes: [{path, status,
  oldPath?}]}`. The runner computes `changes` with rename detection.
- **Output:** `{checks: [{id, argv, cwd, reasons, prerequisites,
  environmentProfile?, timeoutSeconds?, parallelSafe?, title?}], widened,
  uncovered: [paths], notes?}`. A check's `title` is a readable name for a
  person, such as "Webapp e2e (Playwright)": the runner copies it into the
  record, cut to 200 characters, and the views show it in place of the id,
  which stays the check's name everywhere else. A binding's `alwaysRun` and
  `fallback` checks, and a lead's selection, may carry one too. Any provider
  that reports work to a person should give it a display name like this
  beside its stable id. A check marked `parallelSafe` may run at the same time
  as other such checks, up to the binding's `parallelChecks`; mark only checks
  that share nothing a concurrent run could disturb. The runner starts checks
  in order and still honours prerequisites.
- The runner refuses the output as malformed when an argv is not a non-empty
  list of strings, a `cwd` resolves outside the checkout, ids repeat, a
  prerequisite names an unselected check, the prerequisites form a cycle, or
  a timeout exceeds the ceiling.
- The selection is the provider's checks plus `alwaysRun`. `fallback` is
  added when the provider is missing, fails, times out or is malformed, when
  `uncovered` is not empty, or when it selects nothing although files
  changed. When the selection is still empty while files changed, the
  candidate stops with `no_checks`. Unknown impact always widens the
  selection.
- When a candidate changes any `providerInputs`, the candidate's own provider
  runs too, and the selection is the union.

### Lead-selected checks

When the provider over-selects, so that a change to a few files runs most of
the repository's checks and pulls unrelated failures into the queue, the
binding can let the lead choose a candidate's checks from its diff instead.
It is off unless the binding sets `selection: {"lead": true}`.

- **Recording.** `select --subject <s> --binding <binding> --head <reviewed
  head> --checks <json> --reason "<why>"` records the checks on the queue
  row's `leadSelection`, with the reason and the time, and journals
  `check.selected`. Each check has the shape of an `alwaysRun` entry: `{id,
  argv, cwd, reasons, prerequisites, environmentProfile?, timeoutSeconds?,
  parallelSafe?, title?}`; `--checks -` reads them from stdin. The tool refuses a
  binding without `selection.lead`, a head that is not the row's reviewed
  `head`, an empty list, an id the binding's own checks use, an unknown
  profile, a timeout above the ceiling, a `cwd` outside the checkout, and
  prerequisites outside the list or in a cycle. Only `select` writes
  `leadSelection`: a patch that changes it, yours or the runner's, is
  refused. `select --subject <s> --clear` drops it. Under a binding with a
  catalogue, `--checks` also takes catalogue ids; see "The check catalogue".
- **Use.** While the binding allows it and the selection names the
  candidate's reviewed head, the runner checks the candidate with the lead's
  checks plus `alwaysRun`; no provider runs and `fallback` is not added. A
  candidate without a selection is checked by the provider as before.
- **A new head voids it.** A hand-off at a new head drops the selection, and
  a selection whose head is no longer the row's reviewed head is ignored with
  a note, so new code is never checked with checks chosen for old code.
- **Batches.** A batch uses the lead's checks only when every member has a
  selection for its reviewed head: the union of them, in member order, each
  check once. When any member has none, the provider selects for the whole
  batch, and a note names the members without one. Two members that define
  one check id differently stop the batch with `selection_invalid`.
- **Shown as the lead's.** The result's `selection.lead` lists each member's
  selection, head and reason, and its `providers` is empty. Under a binding
  that enables the option, the runner writes each row's `checkSelection`,
  `{by: "lead", head, reason}` or `{by: "provider"}`; the views say the checks
  were selected by the lead, with the reason, and the stats carry
  `checksSelectedBy`. So a lead's choice never passes for the provider's. A
  row the lead selected nothing for, whose checks the runner widened to
  `fallback` for one of its `fallbackReasons`, is
  `{by: "provider", fallback: [ids]}`, and the views and run-delivery's
  supervisor status line say it checks on the fallback, naming it. A hand-off
  may name the checks its lane ran, `handoff --checks`, kept as the row's
  `namedChecks`.
- **Failures.** A lead-selected check that fails is classified as any other:
  `base_broken` when the base fails it unchanged, `check_failed` otherwise.
- **The trade-off.** A selection is the lead's judgment, not the repository's
  impact analysis: a check the lead leaves out does not run before the merge.
  Select only when you can name, from the diff, what the change can affect, and
  say why in the reason. The post-merge `fullHealth` run after each merge
  remains the backstop for anything a selection missed, unless the base declares
  the `focused` posture.
- **A provider's refusal.** Without `selection.refusalStops`, a provider that
  exits non-zero widens the selection to `fallback`, the widest run the binding
  has. With `refusalStops: true`, a provider exiting non-zero is a refusal,
  such as a selection over its budget. The runner runs no check, records
  `checkSelection: {by: "provider", outcome: "provider_refused", detail}` with
  the provider's own words, journals `selection.refused` naming the candidate,
  and stops it with `provider_refused`. The candidate is never merged until
  your selection for its reviewed head is checked in place of the provider's
  answer. That selection is bounded by `checkTimeoutCeiling` alone, and its
  reason records why a run wider than the provider's budget goes through. A
  provider that times out or answers malformed still widens. A refusal of a
  batch's checks stops every member, since the provider answered for their
  combined diff: select each, or re-queue them to run alone.

### The check catalogue

Spelling out full check entries for every selection is slow, and a selection
keyed to the files a story touches misses the tests and fixtures that read
those files' output. Three optional members of the binding's `selection` make
a selection cheaper to write and harder to get wrong. None changes a binding
or a selection that leaves it out.

- **Catalogue.** `selection.catalogue` lists checks, each shaped like an
  `alwaysRun` entry, with an id no `alwaysRun` or `fallback` check uses and
  prerequisites only among the catalogue's own ids. `select --checks
  run-delivery,corpus-guards` takes comma-separated catalogue ids, and
  `--checks -` reads them, or a JSON list, from stdin. Each id resolves to its
  catalogue entry, bringing its prerequisites with it, and an unknown id is
  refused with `select_invalid`, naming it and the catalogue's ids. A JSON list
  as before still selects exactly what it lists; a string in it is a catalogue
  id. What `leadSelection` records is the resolved full entries, so the runner
  checks a selection by ids exactly as one by full entries.
- **Guards.** `selection.guards` lists catalogue ids added to every lead
  selection, whatever it names, including a JSON list that names no catalogue
  id and an empty one: a repository's corpus and qualification tests, say. A
  guard the lead names itself is selected once; a full entry of the same id
  defined differently is refused. The runner adds, with a note, any guard a
  recorded selection lacks, such as one recorded before the binding named it.
- **Readers.** `selection.readers` is a list of `{paths, checks}`: when the
  candidate's diff from the base changes a path matching one of `paths` (a
  rename counts both of its paths), its `checks`, catalogue ids, join the
  selection. Name every reader of a file here, the captured fixtures written
  against a schema included, rather than inferring them from what a test
  mentions: a test that only names the file is not a reader unless the binding
  says so. `select` reads the diff from `--repo`, the current directory when
  left out, against the base's remote-tracking ref there (or `--base-head`),
  and refuses rather than skips the readers when it can't. Fetch the base
  first.
- **What was added.** The `check.selected` entry names each check added
  beyond what you named, and why: a guard, a prerequisite, or the changed path
  it reads.
- **The base-head warning.** `select` prints `baseHead`, `warnings` and
  `notes` beside the item. It warns, naming the check and the head, when the
  latest of `meta.baseReruns` for a selected check at the current base head
  failed: the base may fail it too, and the candidate would stop as
  `base_broken`. A passing latest re-run there says nothing. A failure at an
  older base head, or of a check you didn't select, says nothing. A head this
  delivery's queue never re-ran a check on, such as one another delivery's
  merge made, gives no warning, and a note says that nothing is known there.
- **Checked.** The runner refuses a binding whose catalogue repeats an id,
  reuses a binding check's id, names a prerequisite outside it, forms a cycle,
  names an unknown profile or exceeds the ceiling, or whose guards or readers
  name an id outside it or a reader with no path or check. `select` refuses
  such a binding with `binding_invalid`.

### Lead inputs and leases

A lead input is a step only the lead may authorise, such as a regeneration
that needs a shared deployment. A lane names the inputs its candidate needs in
its hand-off; the runner runs exactly those, during sync.

- An input that needs a lease runs only when `leases[]` shows the lead holds
  it; otherwise the candidate stops with `lease_required`.
- The lease's `materialize` argv runs in the sync checkout just before the
  input, writing whatever marker or environment the input needs. Its
  `withdraw` argv runs on every exit from the input, before anything is
  committed. When `withdraw` fails, the candidate stops with
  `lease_withdraw_failed` and nothing is pushed.
- No other checkout ever receives lease material.

### What sync proves

After merging the base in and running the lead inputs, the regenerators and
the post-sync steps (repeated until a pass changes nothing, at most twice),
the runner proves two things before pushing:

- every working-tree change is inside a declared path (`regenerable`, the
  `postSync` paths, or the named lead inputs' paths); otherwise it stops with
  `undeclared_change`;
- with the declared paths removed, the synced tree equals the merge of the
  reviewed head with the base head; otherwise it stops with
  `unreviewed_change`.

So the code that merges is the code the lenses reviewed, plus only what the
binding declares may change.

### What the record shows about checks

- **Progress.** Each step in `meta.checking.now.steps`, and each check on the
  queue row, carries the check's `title` when it has one. Each step carries its
  `startedAt`, and its `finishedAt` once it ends. A step with history also
  carries `expectedSeconds`, the median of that check's last five passing
  durations. A check with no history has no estimate, never a guess. The
  views show a running step's elapsed time against the expected time, in
  whole seconds, and mark one that has run over: the markdown view as of the
  record's last write, the page as of the moment it draws, on load and at
  each update it receives, by its own clock.
- **The Node a check ran.** Each check the runner runs records `node` on its
  result and its queue row: the version of the first `node` on the `PATH` it
  ran with, after the binding's `environment` and its profile's, as
  `node --version` prints it without the `v`, or `null` when that `PATH` held
  none. When the generation's runtime declares a `nodeMinimum` and `node` is
  below it, the entry also carries `nodeBelowMinimum: true`; otherwise,
  including a `null` `node`, it has no such member. The mark explains a
  failure and never causes one: the check's state is what the check returned.
  A skipped or reused check records neither, and a record written before the
  runner recorded them still validates. The views show the Node in the check's
  detail, with the mark beside it.
- **History.** `meta.checkHistory` keeps each check's last five passing
  durations and how often a declared flake made it retry, and the last five
  whole check runs. Only the runner writes it.
- **Step output.** Every step the runner runs for a candidate keeps its whole
  output when it refuses: `prepare`, the candidate command, a regenerable
  listing, each regenerator, lead input, lease materialize and withdraw, each
  `postSync` step, the push, `ready`, the review gate and `merge`. The output is
  written to a log of its own under `logs/` in the state directory, and the stop
  names that log. Except for the review gate's, whose stop says only how it
  failed, the stop also carries the last 400 characters of the step's stderr
  (200 for a listing), or says that it timed out or printed nothing. A provider that
  fails is not a stop; its record's `detail` carries the same scrubbed tail.
  The scrub has a limit. Before the tail is cut it removes the values, four
  characters or longer, of the environment variables the runner gives its steps,
  the binding's `environment` included, whose names look secret (a token, a
  secret, a password, an API or private key, or an auth value), the token shapes it knows, an
  authorization header to the end of its line, and the value after `bearer`,
  `token`, `password`, `passwd`, `secret` or `api key`. It removes nothing else:
  a secret outside the environment that matches none of these reaches the stop,
  the record and the journal. The log is never scrubbed, so keep `logs/` as
  private as the secrets themselves.
- **Failure detail.** A failed or timed-out check's whole output is kept under
  `logs/` in the state directory, each run in a file of its own; prune old
  logs with the rest of the record. The binding's `checkOutput.failurePattern`
  is matched against each line of the output and of the error output on its
  own; a match with no `id`, or an empty one, names nothing, and an id found
  twice keeps its first message. The result names every failing test with its
  message, and the log. The queue row names the same tests and log, and so
  does the `check_failed` stop, a batch culprit's included, up to ten tests
  and then how many more; the journal entry and the views carry the same.
- **Hearing of a failure.** The runner journals `check.failed` as soon as a check
  fails or times out, naming the same tests and log, once for each candidate
  being checked. A declared flake journals it only if its retry fails too, and a
  batch's culprit search adds none to its whole run's. The entry wakes a running
  `wait`, and is not the outcome: the turn goes on, holding the merge slot, to
  tell the candidate's failure from the base's own, and `candidate.stopped`, with
  `base.broken` when it is the base's, follows. The lead can read the log and warn
  the lane meanwhile, but stops, re-queues and repairs nothing on this entry.
- **Re-running on the base.** That comparison runs the whole check, not just its
  failing tests. A binding's `argv` is opaque to the runner, which has no general
  way to name a test to it, and `failurePattern` may name steps rather than tests.
  A narrower run would also change the answer: a test the candidate adds doesn't
  exist on the base, so it could fail there for that reason alone and read as a
  broken base, and a test that fails only beside others could pass there alone.
  The re-run's output is kept under `logs/` beside the candidate's, and the
  queue row's check carries it as `baseRerun`, with the failing tests the
  binding's failure pattern finds in it. Each re-run that reaches a verdict
  also joins `meta.baseReruns`, newest last and at most 100: the check's id,
  the base head the re-run checked out, `passed` or `failed`, and the
  candidate it ran for. Those outlive the row's checks, which a re-queue
  clears, and `select` reads them (see "The check catalogue"). A declared flake failing there is
  retried once, as on the candidate, before it can read as a broken base. A
  `base_broken` stop and its `base.broken` entry name the check, its failing
  tests and the re-run's log, in the words `check.failed` uses for the
  candidate's run, and `base.rerun.finished` links each re-run's log.
- **Queue estimates.** The state tool's `show` adds a position and an expected
  start for each candidate waiting at `queued`. The start counts what remains
  of the run in progress once (a batch is one run), then one median run for
  each candidate ahead. Without run history the start is unknown. Both are
  estimates, never promises. The markdown view shows them as of the
  record's last write; the page works them out again from the record's
  history by its own clock, and draws itself again each half minute, so a
  quiet record never shows a start that has already passed.

### Flakes and retries

A failed check is retried once, and only once, when every failure it shows
is a declared flake: the binding's `flakes` names the check without `tests`,
or every failing id matches one of its `tests` patterns in full. The runner
journals `check.retried` with the failing ids and the flake's reference
before it retries, and counts the retry in the check's history. A retry that
fails again stops the candidate as usual, and its result keeps both failures.
Never retried: a mix of a flake and anything else; a timeout; and, for a flake
declared with `tests`, a failure whose ids the pattern cannot read, since the
patterns can only vouch for failures they can name. A declaration without
`tests` covers the whole check, whatever its output says. The same rule holds
on the base re-run: a flake failing there is retried once there, journaled as
`check.retried` and counted, before the queue stops with `base_broken`.

### Reused checks

A long check the lane already ran need not run again on the very tree it
passed on. A lane names such a run in its hand-off with `--evidence`, which
`merge_queue.py evidence` prints: the check, the head it ran on, that head's
tree, a digest of the check's definition (its argv before placeholders are
filled in, cwd, prerequisites, timeout, its profile's and the binding's
environment, and the binding's `prepare` steps and `hooks`, as the binding in that head's
own tree has them), and the result. After syncing, the runner reuses the run,
and does not run the check, only when all of these hold:

- the binding lists the check in `reusableChecks`;
- the evidence is this candidate's own, from its hand-off;
- no check selected for this run names it as a prerequisite, so nothing runs
  without its output;
- it is for the same check and the same definition digest as the binding on
  the base now;
- its result is `passed`;
- the synced tree is the evidence's tree, and the runner's own repository
  reads that tree at the evidence's head.

Any difference runs the check as usual: the base moved, the lane pushed after
it ran the check, the binding changed the check, or the run failed or is
missing. When there was evidence for the check but it was not reused, the
result's notes say why. A reusable check may use no placeholder but
`{treeSha}`, in its argv or its profile's environment, and no other binding
check may name it as a prerequisite; with any reusable check, the `prepare`
steps may use no placeholder but `{treeSha}` either. The binding is refused otherwise, since
only the tree may differ between the lane's run and the one it stands for. The record shows a reused check as `reused`, with the head of the
lane's run, in the result (`reusedFrom`), the queue row, the checking panel,
the journal (`check.reused`) and both views. A reused check adds nothing to the
check history, and a run that reused one is not counted as a whole run, so it
does not shorten the queue estimates. The merge is pinned to the synced head as always, so nothing
lands that was not checked by tree. In a batch, a member's evidence counts only
when the batch's combined tree is that member's checked tree.

### What a batch proves

`batch` builds one tree from the base tip, adding each member in queue order
with the same steps a sync runs: it merges the member in, takes the base side
of a conflict confined to `regenerable` paths and regenerates, runs the
member's lead inputs and the post-sync steps, and proves the same two things
a sync proves, against the tree built so far. A member that stops on any of
these drops out of the batch with that code; the rest continue. The runner
records the commit and tree after each member, and journals `check.started`
for every member.

- **One check run.** The selection and the checks run once, on the tree after
  the last member. Every member's queue row shows the batch's live check
  states, and the checking panel's title names the batch and its members.
- **Tree-verified landing.** Each member lands in order. Before it lands, the
  base's tree must equal the tree recorded before that member; otherwise the
  batch stops with `batch_tree_mismatch` and the members left go back to
  `queued`. The runner then commits the member's recorded tree, with the
  member's head and the base as parents, pushes it to the member's branch,
  and merges it through `ready` and `merge`, which must land exactly that
  tree on that base. Every tree that lands is one the batch built, even when
  a post-sync step's output depends on commit ids, and the tree after the
  last member is the one the batch checked. The trees in between were
  checked only as part of the whole: when a landing stops after another
  member landed, the base holds such a tree, so the stop also stops the queue
  and its result says up to which member. Run the full health check before
  anything else merges.
- **Placeholders.** Each member resolves from a checkout of the base, with
  the base head as `{baseHead}`, as a sync does. Its post-sync steps and lead
  inputs see the tree built so far as `{baseHead}`. `prepare` runs once in the
  batch's checkout of the base, with the base branch and head as `{branch}`
  and `{head}`. The batch's checks see the last member's branch as
  `{branch}`, and the batch's commit as `{head}` and `{expectedHead}`.
- **A failing batch** merges nothing at first. The runner checks prefixes of
  the batch, by bisection or in order as `batch.culpritSearch` says, until it
  finds the first member whose arrival makes the checks fail, and stops it
  with `check_failed`, naming it the culprit; when that member is the first,
  the base-broken rule applies to it. The members before it were checked
  together as a passing prefix, so they land; the members after it go back to
  `queued` and form the next batch. A flaky check can name the last member as
  the culprit; re-queue it.

### Checking a binding

- Every argv is an array of strings, and placeholders stand alone.
- `merge` uses `{expectedHead}`.
- `candidate` uses `{candidate}`, so each subject resolves to its own branch and head.
- Run the `candidate` command once by hand, with a branch that exists, and see it print the JSON
  answer. run-delivery's `doctor` checks that its program and any script it names exist, and `serve`
  holds itself, taking nothing, while they don't.
- Every check's `environmentProfile` names a profile in `profiles`.
- Every check timeout is at or below `checkTimeoutCeiling`.
- Every `leadInputs` lease names a lease in `leases`.
- `base` names the same remote and branch the lead passes to the runner.
- `state.directory` is the directory the lead passes to the state tool.

## Publishing step

The delivery's views are rendered from its state, and a publisher puts the
state where a live view reads it. Most repositories declare publishers in the
binding's `state.publishers`, and the render wrapper, `render_state.py`, runs
them after every state write, recording any failure without stopping.

Some hosts store a live document only through their own tool, not through a
command the binding can name. Then publishing is a lead step:

1. After each runner result (a sync, a check, a merge, a release or a stop),
   and after each lead write to the state, read the validated document with
   the state tool's `show` subcommand.
2. Write exactly that document, unchanged, to the host's store with the
   host's own tool. Do not edit or summarise it: a view must be able to show
   the same state the files hold.
3. When the store refuses or fails, record it in the lead's notes and carry
   on. A view is never a reason to stop a delivery. Publish again after the
   next result.

A live page built by the `static-page` renderer reads the store through a
small adapter object the host provides, and shows the store's document only
when its `schemaVersion` matches the page's own. Publish only documents the
state tool produced, so the versions always agree.


## Updating an integration base

Use `run-delivery lead-sync --merge` from the lead checkout when an ongoing integration base needs the
newer default branch. The delivery declares `integrationTarget`, and `baseMove.provenanceWriter` names
its repository's writer argv. Supply verified archive and metadata files matching the default branch's
applied receipt. The target archive must support the base-move writer transition and read the current record.

The command parks the supervisor and takes the queue lock. It merges the default branch, applies its
verified generation through the supported product lifecycle, and regenerates provenance. Digest-only
lock conflicts select a side with the correct output listing. If both sides changed that listing differently,
merge the listing deliberately. Substantive conflicts name their paths. Resolve and stage them, keep the
merge open, then run `lead-sync --merge --resume`.

The pending move records the original base, default commit, prepared tree and published head. Publication
uses a normal fast-forward push. The applied writer and record base head reconcile together, and `base.moved`
records the result once. A retry recognizes an already published head and finishes reconciliation without
another commit. The supervisor refuses processing while the pending move remains. Existing merged SHAs
stay reachable through the merge parents, and their recorded identities are retained.

Lanes start newly assigned stories from the moved remote base. Existing branches follow the lead's
base-merge authorization and review rules. Review and gate the integration candidate on its current head.

When a flake affects both the integration base and the default branch, land the shared fix on the default
branch too. Record a `decision.recorded` entry on the delivery naming the fix, the upstream change request
and its pending-upstream status. A temporary `lead-flake` declaration still names the exact current base,
check and failing test. Record the upstream landing when it occurs, and bring it in through the base move.
