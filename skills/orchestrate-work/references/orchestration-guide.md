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
  writes `stage`, `syncedHead`, `checks`, `meta.baseHead`,
  `meta.releasedHead` and `meta.checking`; a lead attempt is refused with
  `field_not_owned`.
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

| Journal kind | Tracker update |
| --- | --- |
| `lane.assigned` | On the ticket: it moves to started, naming the lane and what it delivers. |
| `candidate.handed` | On the ticket: the hand-off checked as a claim and queued, with its change request, reviewed head and review rounds. |
| `check.started` | None: checking is transient, and its outcome posts with `candidate.merged` or `candidate.stopped`. |
| `check.finished` | None: its outcome posts with `candidate.merged` or `candidate.stopped`. |
| `candidate.merged` | On the ticket: the merge commit, the checks run, and any check reported but not awaited; the ticket moves to its review or completed state. |
| `candidate.stopped` | On the ticket: the stop code and what happens next, whether returned to the lane, re-queued or pending a decision. |
| `base.broken` | On the epic: the failing check, and that the queue is stopped. |
| `base.repaired` | On the epic: the fix that landed and the gap it closed. |
| `lease.granted` | On the ticket whose work needs the lease: the lease, its holder and when it will be released. |
| `lease.released` | On the same ticket: the lease is released. |
| `release.started` | None: the release posts once it finishes. |
| `release.finished` | On the epic: the release shipped, its commit and targets, and who approved it. |
| `finish.met` | On the epic: the finish line and the evidence that met it. |
| `decision.recorded` | On the ticket the decision concerns, or the epic: what, why, and who decided, the operator or the lead. A deferred finding links its follow-up item. |
| `blocker.recorded` | On the blocked ticket: the blocker, and whose answer or work unblocks it. |
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

### 2. Decompose

- Through the tracker capability, create the epic and one ticket per unit,
  each ticket nested under the epic, carrying its unit's scope, required
  scenarios, sensors and dependencies.
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

- Check each hand-off as a claim (the coordination protocol) before queueing
  it. Never hold approved work: an approved candidate enters the queue at
  once, not batched behind unrelated work or held for a convenient moment.
- Add the candidate to `queue[]` with its `head`, `leadInputs`,
  `reportedRounds` and `runId`, and journal `candidate.handed`. The queue
  element now carries the candidate. In the same step, reassign the lane
  (step 3): its `lanes[]` element moves to `assigned` with its new focus. Set
  it to `handed` only when no unblocked work exists for it, and record that.
- Before the run, make sure the lead holds every lease a named lead input
  needs: its holder in `leases[]` must equal the name passed as `--lead`. Get
  it through the lease sequence in the coordination protocol (request,
  acknowledge, then record the lead as holder and journal `lease.granted`),
  never by overwriting another holder.
- Run the queue with the runner's `run` subcommand, in queue order.
- On a stop, read its code in the result document, act, then name the
  candidate again to re-queue it. A re-queued candidate runs again, still
  pinned to its reviewed `head` or the runner's last `syncedHead`; write a new
  reviewed `head` into its queue element first when the lane hands off again.
  A candidate returned to its lane takes priority over the lane's current
  assignment: set the lane's `focus` to the returned candidate, and say so in
  the message that returns it.

  | Code | What the lead does |
  | --- | --- |
  | `candidate_moved` | The lane pushed after hand-off. Check the new head as a fresh hand-off, write it as `head`, and re-queue. |
  | `candidate_unknown`, `candidate_unresolved` | Add the candidate to `queue[]`, or fix its reference, and re-queue. |
  | `already_merged` | Nothing to do; mark the lane `done` only if it was left `handed` (see the merge rule below). |
  | `source_conflict` | Return the candidate to its lane to resolve the listed paths. |
  | `lease_required` | Obtain the named lease through the lease sequence, record the lead (the `--lead` name) as holder, and re-queue. |
  | `lease_withdraw_failed` | Withdraw the lease material by hand, then re-queue. Nothing was pushed. |
  | `sync_step_failed` | A lead input, regenerator or post-sync step failed or timed out. Fix the step or its input, and re-queue. |
  | `unstable_regeneration` | The regenerators keep changing files. Fix the regenerator before re-queueing. |
  | `undeclared_change`, `unreviewed_change` | The sync produced changes the binding does not declare, or code the lenses did not review. Return it to the lane, or declare the path in the binding. |
  | `prepare_failed`, `dirty_checkout` | A checkout could not be prepared, or was not clean after preparing or checking. Fix `prepare` or the check that writes files. |
  | `no_checks`, `profile_unknown` | The selection was empty while files changed, or a check named an unknown profile. Fix the provider, `alwaysRun`, `fallback` or `profiles`. |
  | `check_failed` | Return the failing check's output to the lane. |
  | `base_broken` | Stop and follow the base-broken rules. |
  | `base_moved` | The base moved after the candidate was synced. `run` re-syncs and re-checks once and stops on a second move; a standalone `check` or `merge` stops at once. Re-queue. |
  | `not_synced` | Run `sync` before `check` or `merge`. |
  | `push_refused`, `ready_refused`, `merge_refused` | The forge refused the push, the `ready` gate, or the merge. Read the output, resolve it, and re-queue. |
  | `merged_unverified` | Stop the queue and confirm the base by hand before anything else merges. |
  | `binding_invalid` | Fix the binding on the base branch. |
  | `queue_locked` | Another runner holds the queue. Wait for it, or raise `--wait`. |
  | `runner_error` | Read the result document and the runner's output; investigate before retrying. |

- When a candidate merges, move its ticket to its review or completed state
  through the tracker capability. Mark its lane `done` only when the lane was
  left `handed`, with nothing assigned at hand-off; a reassigned lane is
  already working its next ticket.

### 5. Publish

- After every runner result and every lead write, render the views with
  `render_state.py`. When the host stores the live document with its own
  tool, follow the publishing step.

### 6. Check the finish lines live

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

- Run the binding's `fullHealth` once, on the final base: in a fresh detached
  checkout of the final base tip, after the binding's `prepare` steps, with
  the binding's `environment` set and its placeholders filled in. Never run
  it in the operator's own checkout.
- Confirm every line in `finish[]` is met, or record why it is not.
- Move every ticket, and the epic, to its final state through the tracker
  capability.
- Record every follow-up in `followups[]`, each with its tracker reference.
- Align the operator's checkout with the final base, and clean up every lane
  checkout the epic created.
- Preserve what the epic taught with `$compound-learning`.
- Journal `delivery.closed`, and render the views one last time.

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

One attempt each, never a gate. With no tracker tools, carry on and say so
in your hand-off.

**Questions**

Escalate only to the lead, never to the operator: one short question, then
stop. The lead answers, or takes it to the operator.

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
  - List the epic's child items with the host's tracker tools and compare
    them with the record.
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

These rules hold whatever the host offers for sessions and messages.

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
  Never repeat a message to force it through.
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
- every declared lead input exists in the binding.

When a claim does not hold, return the hand-off to the lane with what failed.

### Without a session capability

When the host cannot start or message other sessions, the lead runs each
lane as a subagent, or runs the lanes in sequence itself. The lane profile,
the hand-off and the queue are unchanged; only the transport differs.

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
by the skill's `orchestration-binding.json` schema. The runner reads it from
the tip of the remote base branch named on its command line, never from a
candidate or the operator's checkout, so a candidate can never change how it
is itself checked or merged. A binding whose `base` names a different branch
is refused with `binding_invalid`.

### Commands and placeholders

Every command is an argv array; there are no shell strings. A placeholder
replaces a whole argv element, or a whole environment value, and never
substitutes inside a longer string:

`{candidate}`, `{branch}`, `{head}`, `{expectedHead}`, `{base}`,
`{baseHead}`, `{treeSha}`, `{ref}` and `{workRoot}`.

### Fields

| Field | Contents |
| --- | --- |
| `schemaVersion` | `orchestration-binding/1`. |
| `base` | `{remote, ref}`: the branch every candidate merges into. It must match the runner's `--remote` and `--base`. |
| `workRoot` | The directory for the runner's temporary checkouts. |
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
| `environment` | Variables set for every argv the runner runs. |
| `ready` | An argv run before merging; a non-zero exit stops with `ready_refused`. |
| `merge` | An argv that must use `{expectedHead}`. A binding whose `merge` lacks it is invalid. |
| `release` | Ordered targets, each with an `id`, `argv`, `stopsOnFailure` and `requiresApproval`, and an optional `timeoutSeconds`. |
| `fullHealth` | The argv for the close-out run. |
| `leases` | Each lease's `id`, `description` and `defaultHolder`, plus optional `materialize` and `withdraw` argv. |
| `state` | `{directory, renderers, publishers}`. `directory` is relative to the repository root unless absolute. Each renderer is `{id, builtin, output}` or `{id, argv, output}`, never both, with an optional `timeoutSeconds`; `output` is a relative path inside the state directory. Each publisher is `{id, argv}`, with an optional `timeoutSeconds`. After each of its state writes the runner passes this member to the render wrapper. |

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

### The affected-checks provider

The provider is the repository's own answer to "which checks does this change
need?", in format `affected-checks/1`.

- **Input,** on stdin: `{base, baseHead, head, changes: [{path, status,
  oldPath?}]}`. The runner computes `changes` with rename detection.
- **Output:** `{checks: [{id, argv, cwd, reasons, prerequisites,
  environmentProfile?, timeoutSeconds?}], widened, uncovered: [paths],
  notes?}`.
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

### Checking a binding

- Every argv is an array of strings, and placeholders stand alone.
- `merge` uses `{expectedHead}`.
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
