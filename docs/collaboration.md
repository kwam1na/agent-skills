# Two engineers, one delivery

A delivery record can be shared: two engineers, each in their own clone on their
own machine, write one plan together, approve it jointly, and then deliver it as
co-leads or hand it back to one lead. The record is shared through git alone, as
commit chains on the binding's base remote. No server, account or extra service
is involved. A record nobody shares behaves exactly as it always has.

The commands below are `run_delivery.py` commands, run as
`python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py`.
The run-delivery skill page lists each with its refusals, and
`references/config.md` there has the rules and the `collab` settings.

## Seats

A seat is one writer of a shared record: in practice one clone on one machine.
Its id, `s-` and twelve hex characters, is made the first time the clone opens or
joins a record, and kept in that clone's own git config as
`agent-skills.seat.<key>`. Nobody configures it, and no environment variable sets
or overrides it. The clone's worktrees, its lead checkout and its lanes' checkouts,
share its git config, so they write as the same seat.

- **A seat is not a person.** One person on two machines holds two seats. Two
  clones under one git identity are still two seats, and each other's peers.
- **A label is only shown.** A seat's label comes from `git config user.name` when
  the seat is made, and `collab label --label L` changes it. No check, refusal or
  ownership rule reads a label.
- **Identity is trust-based.** Anyone who can push to the base remote can write
  any seat's chain, so a seat id proves nothing about who wrote it, as `--who` and
  `--granted-by` prove nothing today. Shared records add no authentication.

Each seat's writes travel as a commit chain at `refs/agent-skills/<key>/<seat>`.
Peers' chains come down by explicit refspec into `refs/agent-skills-peers/`, so no
branch or tag appears on the forge and `git fetch` defaults are untouched. Each
seat runs a supervisor, whose sync loop pushes the seat's own chain after a local
write and fetches and ingests its peers' every `collab.pollSeconds` (default 5, at
least 2). Nothing else pushes or ingests a chain.

## Starting: `plan-open`

The first engineer commits the plan, with a `reviewSlug` in its front matter, and
runs:

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py plan-open --plan docs/plans/my-plan.md --key WORK-123
```

That opens the delivery's planning record under the work directory's
`deliveries/WORK-123/`, with this clone's seat, admitted and an approver, holding
every scope. It starts the record's supervisor: its live view, its sync loop and,
where the repository configures it, hosted publishing. A preview the plan already
had is handed over, so the plan has one view from then on. The key stays the
delivery's through approval, so the hosted link never changes.

## Joining: `collab join`

The second engineer clones the repository and runs:

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py collab join --key WORK-123
```

On a clone with no record yet, it builds the record from the seats' chains, so
the first seat's `record.started` is its entry 1 on both machines. It starts the
supervisor, then gives the clone a seat that asks to be admitted. The `delivery`
holder admits it:

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py collab admit --seat s-0123456789ab
```

`--no-approver` admits a seat that writes but doesn't approve the plan. Until a
seat is admitted, its only write is its own request. Every `collab join` prints,
in this order, what is new since this seat's last: the story, the plan's diff
since it last read the plan, the feedback still open, and the scopes it holds.
Run it again to catch up.

## Following each other

Two commands print one line per peer change and nothing else, each line flushed,
for a host monitor or a plain terminal to read:

- `plan-watch --plan P` prints each peer push of the plan's branch;
- `collab watch` prints each peer entry the sync loop ingests.

Both exit 0 at `--timeout` (default 1800 seconds), or with `--until RE` exit 0 on
the first matching line and 3 at the timeout if none matched. Neither pushes or
ingests. Peer journal entries also wake each seat's `lead-wait`: a join, a
request for a scope, and feedback.

## Writing the plan together

Run `plan-sync --plan P` after an edit. It composes the plan with the remote
branch section by section, commits and pushes it. Each seat changes only the plan
sections it holds, and a plan section nobody was given is the `delivery` holder's.
A change outside them is refused with `plan.section_not_held`, naming the section
and the request that asks for it:

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py collab request --scope "plan:## Decisions" --reason "I'll write the decisions"
```

Its holder passes it on with `collab give --scope S --to SEAT`. With
`collab.autoGrant` on, the `delivery` holder's supervisor grants a requested plan
section nobody holds on its next pass. Two seats changing one section at once are
refused with `plan.sections_overlap`, naming the section with both diffs, before
anything is written.

- **Feedback** is a fact both seats see, on one of the plan's sections:
  `collab feedback add --anchor "## Decisions" --text T [--blocking]`. Only the
  holder of that section settles it, once, with `collab feedback resolve --id N
  --outcome accepted|declined|answered --reason R`. Blocking feedback shows in
  Needs you and holds approval until it is settled.
- **Re-keying.** `plan-rekey --plan P --map OLD=NEW,...` replaces placeholder
  story keys with tracker keys in one commit. Only the `delivery` holder runs it.

## Approving together

Each approver seat endorses the plan at its current commit, after syncing:

```bash
python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-plan endorse --plan docs/plans/my-plan.md
```

The plan's view reads "Endorsed by <labels>" once every admitted approver seat has
endorsed the commit it shows, and names an endorsement of an older commit as
stale. `lead-init` then refuses with the first of these the plan fails: an
approver seat that hasn't endorsed it, one whose endorsement is of an older commit,
a plan that isn't the one plan review aligned on (with each recorded re-keying
applied), and open blocking feedback. `lead-setup` and `lead-init` take the
planning record over in place: its journal, chains and hosted identity carry on,
and the record becomes the delivery's.

## Co-leading, handovers and hand-back

After approval the two engineers can stay co-leads with split lanes, or hand back
to one lead. Who holds what is the record's scope register, derived from its
journal and never stored twice: `delivery`, `queue`, each `lane:<id>` and each
`plan:<section>` has exactly one holder, and only its holder writes it. The
record's first seat holds every scope until its holder passes one on.

- **Lanes.** A lane belongs to the seat holding its `lane:` scope. Its sessions
  are unchanged: the lane reports to that seat's session, which accepts its
  hand-offs. A lane is passed on with `collab give --scope lane:<id> --to SEAT`,
  refused with `scope.handover_busy` while that lane still has a checkout on the
  outgoing machine.
- **The queue.** Only the `queue` holder's supervisor runs the merge queue and full
  health. `collab give --scope queue --to SEAT` waits for the queue to be idle, no
  check running and no merge in progress, stops this machine's queue, then passes
  it on. The new holder's supervisor starts its queue on the pass that ingests the
  handover. In between, no machine serves the queue, and the line map says
  "queue moving to <label>".
- **`delivery`.** The `delivery` holder's supervisor reconciles the record, runs
  the tracker and hosted publishers, and closes the delivery. Handing it over
  waits for no publication in flight, stops both publishers on this machine, and
  carries the hosted identity and the tracker's position to the new holder, so
  the public link is unchanged and no tracker update is posted twice.
- **Full health, evidence and the close.** Full health runs once a base head, on the `queue` holder's machine, and
  every seat reads its result. Only the `delivery` holder records and publishes evidence: a lane's in-app checks
  travel to it in its seat's chain, so it records a peer lane's story from the checks that lane ran. The `delivery`
  holder's supervisor closes the delivery once every peer machine has cleaned up its checkouts, and pushes the
  archived close as it closes. A peer counts the delivery closed only once that archived close reaches it, and its
  supervisor then stops.
- **Hand-back.** Every scope can be passed back to one seat, which then leads
  alone. The other seat can keep following the record, or stop.

## Credentials

Each machine reads its own environment. A machine that may hold `delivery` needs
the repository's hosted publishing token and its tracker credentials, in the same
variables its configuration names. A machine that only holds lanes needs neither.
Nothing secret travels in a seat's chain.

## Holds and splits

- **Generation held.** Every seat of a shared record writes with one release's
  state writer. A peer's chain written by another release is held, not ingested:
  Needs you says so, and the line map's Seats chapter shows the peer as "held:
  generation differs". It is ingested once the lagging machine runs its own
  `lead-upgrade-writer`. No machine upgrades another.
- **Updates that don't fit.** A peer chain whose updates would leave this record
  invalid is held the same way, as "held: its updates don't fit this record".
- **A seat split.** A clone whose push finds its own seat's chain moved on the
  remote, because another clone wrote as the same seat, stops: it keeps its unpushed
  chain locally at `refs/agent-skills-split/<key>/<seat>`, marks its record held,
  and refuses every later write with `collab.seat_split`. Needs you names the split.
  The fix is a fresh seat for that clone, which needs admission like any other.

## Hosts

Shared records add nothing to the host projections: every command above is a
`run_delivery.py` command, and the watch streams are plain lines a host's monitor
reads as a terminal does. See [host projections](host-adapters.md).
