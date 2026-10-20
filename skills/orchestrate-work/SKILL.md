---
name: orchestrate-work
description: Deliver a decomposed epic with one lead session coordinating lane sessions, a lead-owned merge queue, and a delivery state document that any view can render.
---

# Orchestrate Work

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

## Roles and write ownership

- **Lead.** One session. Only the lead assigns tickets, starts the runner,
  holds or grants leases, approves releases with the operator, and moves
  tracker items to their final state. The lead may also take tickets itself.
- **Lane.** A session, or in degraded mode a subagent or the lead itself. A
  lane delivers one assigned ticket at a time, and never merges, releases, or
  holds a lease it was not granted.
- **State writers.** The lead writes every state field except the
  runner-owned ones. The runner writes only its result documents, journal
  entries, and its own fields: `queue[i].stage`, `queue[i].checks`,
  `queue[i].syncedHead`, `meta.checking`, `meta.baseHead` and
  `meta.releasedHead`. Every write goes through the state tool, which checks
  the declared writer against this ownership.

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
- owner intent that is genuinely ambiguous, where neither the plan nor
  standing direction settles it.

Each escalation is one consolidated question with a recommendation; while it
waits, the lead keeps every unblocked lane working, giving a lane blocked on
it other unblocked work. Lanes escalate to the lead, never to the operator; a
question only the operator can answer reaches the operator through the lead.

- **Never hold approved work.** An approved, handed-off candidate enters the
  queue at once, not batched behind unrelated work or held for a convenient
  moment.
- **Never leave a lane idle.** When a lane hands off, the lead assigns it the
  next unblocked ticket or follow-up in the same step. An idle lane while
  unblocked work exists is a lead defect, and the lead records it.
- **Reconcile at every event.** At every event the lead reconciles the
  delivery record with the forge, the tracker and the lanes, so the operator
  never has to prompt a status check. For the tracker, the lead lists the
  epic's child items with its host's tracker tools and compares them with the
  record. An item the record lacks, such as a follow-up filed but never
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
lane report treated as a claim to check. Without a session capability, the
lead runs lanes as subagents or in sequence. Leases, including those held
outside the epic, follow the same protocol.

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
`python3 -B` from the repository it serves, naming the state directory, the
remote and the base branch every time:

```text
python3 -B <skill>/scripts/merge_queue.py --state <state directory> --remote <remote> --base <base branch> [--lead <holder>] [--wait <seconds>] <subcommand>
```

- The runner reads the repository's binding, `.agents/orchestration.json`,
  from the tip of `<remote>/<base>`, never from a candidate or the operator's
  checkout. A binding that names a different base is refused with
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
  on a fresh checkout of the synced head.
- `merge <candidate>` merges the checked head through the binding's `ready`
  and `merge` commands and confirms the new base.
- `run <candidate>...` does sync, check and merge in order for each candidate,
  stopping at the first failure. When the base moves after a sync, `run`
  re-syncs and re-checks once; a second move stops with `base_moved`. A
  candidate is merged only onto the exact base it was checked against.
- `release <ref> [--approved-by <text>]` runs the binding's release targets on
  the base tip or an ancestor of it.

The runner works only in detached checkouts under the binding's `workRoot`,
never touches the operator's checkout or the base branch, and writes a result
document and journal entries for every subcommand. Every stop names a code,
such as `candidate_moved`, `source_conflict`, `sync_step_failed`,
`check_failed`, `base_broken` or `merged_unverified`; the lead reads the code,
acts, and names the candidate again to re-queue it. A candidate that already
merged is refused with `already_merged`. The lead checklist lists every code
and what to do about it.

`--approved-by` records an approval the operator already gave in the
conversation. It is not a gate the runner can verify: pass it only after the
operator approves that release.

## Views and publishing

The runner renders the views itself after each of its state writes, passing
the binding's `state` member to the render wrapper; a failed render is noted
in its result and never stops it. After each of the lead's own state writes,
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
  `static-page`), that reads the validated `{state, journal}` on stdin and
  writes one view on stdout to its `output` inside the state directory. A
  publisher is any argv that puts the same document somewhere a view reads,
  or a `host` publisher that leaves an outbox for the lead (see below).
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
operator should never have to ask whether it is up to date. At every event,
such as a hand-off, a merge, a decision or a tracker change, run the sync loop
in this order:

1. **Snapshot.** With the host's own forge and tracker tools, list the epic's
   work and write it as `work-snapshot/1` files (in
   `references/schemas.json`): `{takenAt, source, items}`, where `source` is
   `forge` or `tracker`. Each item is `{subject, title?, state,
   changeRequest?, head?}`. A tracker state is `backlog`, `started`,
   `completed` or `canceled`; a forge state is `open`, `draft`, `merged` or
   `closed`. The state tool never calls a forge or a tracker itself.
2. **Reconcile.**

   ```bash
   python3 -B scripts/delivery_state.py --dir <state directory> reconcile --snapshot <forge snapshot> --snapshot <tracker snapshot>
   ```

   - A tracker child the record lacks is added: `backlog` as a follow-up,
     `started` as planned work (the state's `planned` list).
   - A change request is linked to its queue or planned item, and an open one
     moves planned work to review.
   - A merge the runner never recorded is written as the runner's, with a
     `candidate.merged` journal entry saying so. The runner records its own
     merges, so this is a recovery path, not the normal one.
   - Anything it cannot settle, such as a completed child still planned, is
     reported and left for the lead.
   - A snapshot older than `--max-age` seconds (900 by default) is refused
     with `snapshot_stale`, and one dated ahead of this machine's clock with
     `snapshot_invalid`; nothing is written: list the work again.
   - Tracker snapshots apply before forge snapshots, whatever their order, so
     one pass settles both.
   - A merge is never recovered while the runner holds the queue: it is
     reported, and the next pass after the runner stops records it if the
     runner did not.
   - It is idempotent and prints its changes as JSON, or `no drift`.
3. **Render**, with the wrapper as above. Command publishers run as part of
   it. A `host` publisher, `{"id": "<id>", "kind": "host"}`, runs nothing: it
   writes `outbox/outbox.json` in the state directory, holding the record
   `version`, the `journalThrough` id it reaches, the state and every journal
   entry not yet acknowledged. Readers ignore fields they do not know, so
   later projections can join these.
4. **Publish** the outbox with the host's own tool, for a view whose store
   only the host can write.
5. **Acknowledge** what went out:

   ```bash
   python3 -B scripts/delivery_state.py --dir <state directory> ack --version <outbox version> --through <outbox journalThrough>
   ```

   Acknowledge the outbox's own `version` and `journalThrough`, never the
   record's: an acknowledgement beyond what the outbox held is refused with
   `ack_invalid`. The next outbox then carries only later entries. `show` reports a
   publication or an outbox that lags the record, so an unpublished view is
   always visible.

## Running the epic

Work through [the lead checklist](references/orchestration-guide.md#lead-checklist): plan,
decompose, assign, run the queue, publish, check the finish lines live,
release with the operator's approval, and close out.

## Telemetry and tracker updates

Telemetry is best-effort. Each existing run emission is attempted at most
once. A failure is skipped and never delays, gates or reorders the delivery.

Tracker updates follow the same rule: one attempt, never a gate. The lead
posts one at each journal milestone, as
[the tracker updates table](references/orchestration-guide.md#tracker-updates)
maps them, and each lane posts on its own ticket at the points its brief
names. With no tracker tools, delivery proceeds and the gap is noted.

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
- `show` prints the validated `{state, journal}` pair that renderers read,
  with a `publication` block saying whether what was published lags it.
- Output is ASCII JSON on every platform and console; a patch read from
  stdin is UTF-8, with or without a byte-order mark. Text that has no UTF-8
  form, such as a lone surrogate, is refused. The state is UTF-8 and the
  journal ASCII, both with `\n` line ends, so their bytes are the same
  everywhere.
- `extensions` holds a repository's own fields. It is validated as an object
  only.
