# Gap log: story review lens selection

Gaps in the workflow or the tool found while delivering this plan, each recorded when it was met.

## Plan review briefs don't name which carried findings are still open (workflow)

- **What happened.** In round 3 of this plan's review, every lens result was refused by `plan-review reduce` with
  "resolvedFindings entry '...' isn't an open finding this lens raised earlier". Each lens had re-listed findings that
  round 2 had already closed. This happened with resumed reviewers and with fresh ones on the same brief: five refused
  results across two candidates, against one that got the format right.
- **Why the tool couldn't prevent it.** The plan brief says `resolvedFindings` holds "one entry for every finding this
  lens raised in an earlier round and has not closed" and "every carried finding must appear". It carries every
  earlier result in full but never names which ids are still open. The installed generation's story round brief has
  the same gap, so this delivery's own story review can meet it too.
- **What was done.** With the operator's approval, round 3's reviewers were started with the brief's exact prompt
  plus one sentence naming the lens's open findings.
- **Where it stands.** Tracked as V26-3430. Its brief half is fixed on `main` by a0dffff5 ("Name the still-open
  finding ids in each verification brief", PR #379), which merged after v3.1.0 was tagged, so the installed generation
  this delivery runs still has the old brief. The round-3 clarification stands in for it until a release carrying
  a0dffff5 is applied. V26-3430 is still in Backlog; whether its other half, `reduce` skipping a closed finding, is
  done was not checked.

## A holding item in the needs-you list never wakes the lead (workflow)

- **What happened.** From `lead-init` at 19:27 the supervisor's merge queue `serve` was refused every minute,
  `binding_invalid: workRoot must not contain a checkout of the repository`, because another delivery's interrupted
  `lead-suite` run had left its detached checkout under `.orchestration/work/suite/`. The live view showed "The
  supervisor's serve won't stay up", holding up the finish, for about 12 minutes. The operator saw it there first.
- **Why the tool couldn't tell the lead.** `lead-wait` wakes only on journal entries. The supervisor works out a
  flapping child on each pass and writes it only to `needs-you.json`, never to the journal, so no wake reaches the lead
  for an item the view itself marks as holding up the finish. Separately, an interrupted `lead-suite` leaves its
  checkout behind, and nothing removes it before the next delivery's queue meets it.
- **What was done.** The stale checkout was removed with `git worktree remove`, as `lead-suite` would have, after
  checking that it was clean, detached and held open by no process. Two one-off shell watches then checked that `serve`
  stayed up, because nothing would report it.
- **Where it stands.** Tracked as V26-3618: a holding needs-you item should journal an entry the lead's wait hears, and
  the stale suite checkout should be cleaned up, or named with its fix, before `serve` refuses on it.

## No command adds a lane to a live delivery (workflow)

- **What happened.** The operator asked for V26-3618, added to this delivery as lane-1's second story, to run on a new
  lane-2 alongside lane-1, with the two lanes holding disjoint regions of `run_delivery.py` and the supervisor tests.
- **Why the tool couldn't.** A delivery's lanes are the record's `laneOwnership`, which only `lead-init` (and
  `lead-own --seed` on a legacy record) writes. `lead-own --grant`, `--move` and `--region`, `lead-assign` and
  `lead-plan change --kind added --lane` each refuse a lane the record doesn't hold, and nothing adds one. The only
  routes left are a hand edit of the record or `lead-init --replace` mid-delivery.
- **What was done.** The operator kept V26-3618 on lane-1, after V26-3606, rather than set it up as a delivery of its
  own or hand-edit the record. Lane-1's claims, briefly carved into regions, were restored to whole files.
- **Where it stands.** Tracked as V26-3628, with the region mechanics in V26-3591, as a comment there. The carve hit its c2: lane-1's
  whole-file claims were revoked, lane-2's grants were then refused, and the V26-3618 lines were left unowned until
  lane-1's claims were restored. It also hit its c3: three adjacent lane-1 regions would have left boundary lines where
  no insertion fits, so whole files were restored instead. Adding a lane to a live delivery is not yet in V26-3591's
  scope.

## A running delivery can't move onto an integration base (workflow)

- **What happened.** After V26-3618 was stopped in the merge queue by another delivery's integration merge into `main`,
  the operator asked to land the rest of this delivery through an integration branch instead of `main`.
- **Why the tool couldn't.** A delivery's base is fixed at setup. `lead-setup --integration` refuses a key that already
  has a delivery file on `main`, and the record holds the base it was set up on. Moving would have meant a new delivery
  key, taking V26-3618, V26-3620 and V26-3624 out of this one, and restarting V26-3618 on a new branch with its PR
  retargeted.
- **What was done.** The operator kept the delivery on `main`. V26-3618 merges the new base under an authorized base
  merge and hands off again.
- **Where it stands.** Tracked as V26-3629.
