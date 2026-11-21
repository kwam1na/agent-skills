---
reviewSlug: lane-commands
date: 2026-11-20
execution_posture: test-first
---

# Give lanes and leads commands for what they do by hand today

Lanes and leads keep working around the same gaps in the lane tools. The lead force-moves a lane's branch after the
merge queue has left its sync merge there. Line spans are granted by grepping and widened one line at a time. Lanes
and record rows are added or patched with the state tool's raw `patch`, inside a retry loop. A lane is told to start
the lead's wait. A lane ends turn after turn while its lenses run. Friction logs recorded each of these, and no
command covers them yet. This epic, V26-3694, adds the commands and the information. Agents use them at their own
judgement. It adds no rules.

The epic is delivered on its own integration base, `integrate/lane-commands`. The lead then takes it into `main` as
the guide's "Reviewing an integration base for the default branch" says.

Checked against `main` at `9a084797` on 2026-11-20:

- `Runner.sync` in `skills/orchestrate-work/scripts/merge_queue.py` builds the base merge in a checkout, then calls
  `push`, which pushes `synced:refs/heads/<branch>` under `--force-with-lease=refs/heads/<branch>:<head>`. `check`
  then re-reads the branch and stops with `candidate_moved` unless it is at the synced head. `merge` resolves the
  branch, requires it at the synced head, then runs the binding's `ready`, `review_gate` and `merge` commands. This
  repository's `merge` is `gh pr merge {branch} --squash --delete-branch --match-head-commit {expectedHead}`, so the
  forge merges whatever the branch holds at that moment. A batch's `land` builds its merge with `commit-tree` and
  pushes it the same way before calling `merge`. `PRIVATE_REF` (`refs/merge-queue`) is only a local fetch target.
  `candidate.stopped` records `reason`, `detail` and `requeued`, and never the synced head.
- In `skills/orchestrate-work/scripts/region_ownership.py`, `change_report` counts an insertion as a span's only
  when `start < first < end`. `lead-own --region` takes `--start/--end` or `--around TEXT`, and `--insert
  before|after` widens a span by one unclaimed line. A claim a base edit crosses is carried forward only when its
  story lands (`settle`). Any other stale claim is printed "Regrant it against the current base".
- `co_change_warning` in `skills/run-delivery/scripts/run_delivery.py` is printed by `lead_assign` and nothing else.
  `lead-assign` takes `--lane/--title/--note/--force`, and `lead-accept` has no way to take in a file outside the
  lane.
- `laneOutside` items are built in `run_delivery.py`, with `fields.detail` "It doesn't hold the file." or
  `f"It {said}, outside its span."`. `tests/test_lane_changes.py` asserts only `text` and `fields["path"]`.
- No command adds a lane. `lead_plan`, `change_regions`, `change_ownership`, carve's `--give` and `lead_assign`
  (through `effective_ownership`) each refuse a lane the record or the delivery file doesn't hold.
- `delivery_state.py patch` requires `--version` and refuses when the whole record's version moved, which the queue
  moves often. An unknown field is refused as `"<pointer>: no value is allowed here"`, through `patch_invalid`.
- `Config.state_of` defaults the record to `lane_checkout(<delivery key>) / <state directory>`. A story whose key is
  the delivery's has that same folder as its lane checkout, and `lane_start` then refuses it. Neither
  `setup_problems` nor `Config.check_lanes` compares the delivery key with the story keys.
- `seat_of` seats a hook's session from the payload's `cwd` alone. `lead_may_stop` holds a session seated `lead`
  and tells it to start `lead-wait`. Nothing reads the payload's `session_id`. Claude Code exports
  `CLAUDE_CODE_SESSION_ID` to the commands a session runs.
- `review_prepare` tells the lane to save each reviewer's final JSON to its result path. There is no `review-wait`.
  `templates/lane-brief.md`'s "Your turn" names only `pr-review --wait` as a foreground wait.

## Finish line

- The plan's lenses align.
- V26-3643, V26-3649, V26-3640, V26-3432, V26-3434, V26-3578, V26-3628, V26-3676 and V26-3427 have landed on
  `integrate/lane-commands` through the merge queue.
- The epic's acceptance holds on the integration base. Each criterion is shown by a test named in Scenarios.
- The lead has taken the integration base into `main` by the guide's recipe: `main` merged in, the full local suite
  passed on macOS with Python 3.11 at the integration head with `--workers 4 --budget tests/suite-budget.json`, the
  integration review aligned, and the change request merged. This is a lead-held line: it names no criteria and no
  proofs.
- No release is cut. The operator has ruled that this delivery ends with its changes merged, so the candidate the
  epic proposed isn't tagged here.

## Decisions

1. **The queue keeps its sync merge off the lane's branch until it merges** (V26-3643, absorbing V26-3644).
   - `sync` pushes the synced head to `refs/merge-queue/<key>` on the remote, under a lease on that ref, instead of
     to `refs/heads/<branch>`. The lane's branch stays exactly where the lane pushed it.
   - `check` reads the candidate from that ref. It still stops with `candidate_moved` when the lane's branch has
     moved off the handed head, or the queue ref off the synced head.
   - `merge` moves the lane's branch to the synced head only as its last step before the binding's `ready`,
     `review_gate` and `merge` commands, under `--force-with-lease=refs/heads/<branch>:<handed head>`. The forge
     merges a branch, and a review gate reads the pipeline at the branch's head, so the branch must hold the synced
     head for those commands. A batch's `land` does the same. After the push, the queue re-resolves the candidate
     through the binding's candidate step until it reports the synced head, within `checkTimeoutSeconds`, before it
     runs `ready`, so a forge that updates a change request's head late doesn't refuse the merge for a mismatched
     head. A candidate step that never reports it stops the candidate as `candidate_moved`, and the branch moves back.
   - When `ready`, the review gate or `merge` then fails, the queue moves the branch back to the handed head, under a
     lease on the synced head. Only the queue's own synced commit is dropped. If the lease fails because someone
     pushed since, the queue leaves the branch alone.
   - `candidate.stopped` gains an optional `leftOnBranch`, the commit the queue left on the branch when it couldn't
     move it back. Every other stop leaves the branch at the handed head and omits the field. `journal-kinds.json`
     and its vectors gain the field.
   - The queue deletes `refs/merge-queue/<key>` on the remote once the story merges. A stopped candidate's ref stays
     until the story's next sync overwrites it under its lease, or the story merges.
   - `lead-authorize-base-merge` and the lane's `base-merge` are unchanged: with the branch at the handed head, the
     lane's authorized merge of it pushes as a fast-forward.
2. **A stale outside-span detail is pinned** (V26-3649). `tests/test_lane_changes.py` asserts `fields["detail"]` for
   both the whole-file and the span item, beside its `text` assertions. No production change.
3. **Spans by anchor pair** (V26-3640). `lead-own --region PATH --anchor TEXT --through TEXT` names the span from the
   one line at `--base` holding `--anchor` to the first line at or after it holding `--through`, both as exact
   substrings, inclusive. No match, or more than one `--anchor` match, is refused, listing each match's line and text
   (at most 10), as `--around` is refused. The Python rule that no span cuts a statement applies unchanged.
   `--anchor/--through` replaces `--start/--end` and `--around`, and is refused with either. Carve's `--give` takes
   it as `anchor=TEXT..through=TEXT`. The grant prints the span's first and last lines, as every grant does.
4. **An edit at a span's edge** (V26-3640). `change_report` counts an insertion at `first == start` or
   `first == end` as the span's when no claim of another lane covers the line on the far side of that edge, or that
   side is the file's edge. A hunk that replaces the span's last line with that line plus new lines is then the
   span's. An insertion just past a span whose next line another lane claims is still refused, naming that lane.
   An insertion between two spans of the same lane and story is theirs, and one between two lanes' spans is refused.
   `check_change` and `change_report` take the other lanes' claims as an optional keyword, so existing positional
   callers are unchanged. Other lanes' claims are mapped only to judge an insertion at an edge. A foreign claim that
   can't be mapped to the base counts as covering the far-side line, so that edge insertion is refused, naming that
   lane. An interior hunk never reads another lane's claim. Review preparation, `claims-check`, `lead-claims`
   (through `claims_report`), hand-off, acceptance and the queue's `check_regions` all pass the other lanes' claims,
   so all of them judge alike. The existing pins that refuse every edge insertion move with
   it, each rewritten as an allow and deny pair at its own entry point, never deleted:
   `RegionDiffTests.test_insertions_at_edges_and_changes_spanning_claims_are_refused` in `tests/test_merge_queue.py`,
   `test_an_insertion_at_a_spans_edge_and_a_mode_change_are_outside_it` in `tests/test_lane_changes.py`, and the
   `claims-check` `--insert` request in `tests/test_region_grants.py`.
5. **Carrying a stale span** (V26-3640). `lead-own --carry PATH --lane L [--story KEY]` maps each claim of that lane
   (and story) on the path from its own base to the current base head. It writes only when every base hunk since the
   claim's base lies wholly outside the claim's lines. It is refused, writing nothing, naming each hunk that
   intersects, so the lead regrants that one. It journals `ownership.carried` with `by: lead`. The kind gains `by` as
   an optional field.
6. **Co-change warnings become claims** (V26-3432). `lead-assign --own-co-changed` adds each file the co-change
   warning names to the lane's `owns` in the delivery file and to the record's ownership, through `change_ownership`'s
   checks, in the same commit `lead-plan` makes. A file another lane holds isn't added. The command names it with the
   `lead-own --move` that would take it. Without the flag, `lead-assign`'s warning now ends with the flag rather than
   "Move each to ... now".
7. **A pin outside the lane, accepted with a reason** (V26-3432). `lead-accept <key> --take PATH --why T` grants the
   lane a whole-file claim on `PATH` in the record, by `lead-own --grant`'s checks, and journals the reason as the
   lead's decision. Then it accepts as usual. It is refused when another lane holds the path, and when the candidate
   doesn't change it. It is repeatable.
8. **An element-scoped patch** (V26-3434). `patch` already takes `--element SELECTOR`, such as `lanes[id=lane-2]`,
   with `--set` pointers relative to the selected element. `show` gains `--element SELECTOR`, printing the element
   and the `sha256` of its canonical JSON. `patch --element SELECTOR --element-digest D` takes no `--version`, and
   refuses only when the selected element's current digest isn't `D`. The check and the write happen under the
   record's lock. `journal_patched` takes the same pair. `--version` keeps today's behaviour, and `--element-digest`
   without `--element` is a usage error.
9. **A refused unknown field names the allowed ones** (V26-3434). `patch`'s `patch_invalid` refusal, for an
   `additionalProperties` error, adds the object's allowed fields, sorted, and the closest one when `difflib` finds
   one. So `status` on a lane row says `state`. `_errors` is unchanged, so the registry's own wording and its pins in
   `tests/test_journal_kinds.py` stay as they are.
10. **A delivery key that is a story's key** (V26-3578). When the delivery file names no `stateDirectory`, the
    binding none, and `DELIVERY_STATE` is unset, `Config.state_of` puts the record of a delivery whose key is one of
    its stories' keys at `<work directory>/deliveries/<key>/<state directory>`, beside the lanes folder under the
    configured work directory, which git ignores. Every other
    delivery keeps today's default. `doctor --delivery` reports this as `warn`, naming the folder and
    `stateDirectory` to choose another. `lead-init` prints the same line. Whichever of the two default paths already
    holds a record is used, so a running delivery doesn't lose its record, and one whose colliding story is later
    removed keeps the record it has. When both hold one, every command refuses, naming both paths. `doctor
    --delivery` reports `wrong` when the record's folder isn't ignored by git, as it does for the lane checkout
    folder.
11. **A lane added in one command** (V26-3628). `lead-add-lane --lane L [--grant PATTERN]... [--why T]` does three
    things in one commit in the lead's checkout. It adds `L` to the delivery file's `lanes`, with `owns` set to the
    grants. It adds the lane's row to the plan's `Units and order` table, at the highest tier, with no items. It
    writes `/laneOwnership/L` and a `lanes[]` row in one root state-tool `patch`, through `patch_from`, which pins
    the version, re-reads and retries when the queue moved it. The record is never written outside the state tool.
    `L` must be `lane-N`, since the plan's Units table and lane sentences read only numbered lanes: any other name
    is refused. Every grant goes through `lead-own
    --grant`'s checks: overlap with other lanes' claims, shared outputs and region claims. Any refusal writes
    nothing. It refuses a lane the delivery already holds. `lead-own`, `lead-assign` and `lead-plan change --kind
    added --lane L` accept the lane afterwards. A `--lane` new to the delivery is still refused there, now naming
    `lead-add-lane`.
12. **A hook seats a session by its id** (V26-3676). `lane-start` records `CLAUDE_CODE_SESSION_ID`, when set, as its
    story's lane session in the record's `sessions.json` beside the state. Every write of the file reads, changes and
    replaces it atomically under the record's state lock, so lanes starting at once don't lose each other's entry. `lead-init`, `lead-run --resume` and
    `lead-wait` record the lead's. `seat_of` takes the payload. A `session_id` the file holds seats the session by
    the recorded role, whatever its `cwd`. An unknown id, or a payload with none, is seated by `cwd` as today, so
    another host is unchanged. `lead-may-stop` and `delivery-orient` both use it. A lane session is never held and
    never told to start `lead-wait`.
13. **A lane waits on its lenses in the foreground** (V26-3427).
    - `review-prepare`'s prompts tell each reviewer to write its final JSON to its result path as its last step,
      then reply with the same JSON. `templates/round-brief.md` names that write as the lens's one permitted write,
      an exception to its read-only rule and to touching no checkout but its own.
    - The lane still saves a reviewer's final message to the path when the reviewer returned without writing it.
    - `review-wait <key> --round N [--seconds S]` returns 0 once every lens's result path in `round-N.meta.json`
      holds JSON that parses. It exits 4 with the lenses still out when `S` runs out, default 540, inside a host's
      ten-minute command limit, so the lane runs it again.
    - It never judges a result: that stays `review-reduce`'s.
    - `review-prepare`'s output names `review-wait` before `review-reduce`. The lane brief's "Your turn" names it
      beside `pr-review --wait`.
14. **The written paths agree.** `skills/run-delivery/SKILL.md`'s rows for `lead-own`, `lead-assign`, `lead-accept`,
    `lead-plan`, `review-prepare` and `hook` change as each story changes them. The table gains rows for
    `lead-add-lane` and `review-wait`. `skills/run-delivery/references/config.md`'s "Host hooks" says how a session
    is seated. The orchestration guide's merge-queue section says where the queue keeps its synced head, and
    `skills/orchestrate-work/SKILL.md` too where it says so.

## Out of scope

- V26-3349: a lane re-runs the PR reviewers.
- V26-3635's second half: `lead-setup` drafting finish lines.
- V26-3498: already pinned by `tests/test_merge_queue.py`.
- The Athena-side lane tickets V26-3296, V26-3177, V26-3338 and V26-3136.
- Removing a lane, or moving a running lane's stories.
- Filtering the lead's host notes from a linked lane.
- Any release, candidate or final.

## Approval Handoffs

- The operator approved this plan for delivery once its review settles, and granted the lead judgement to land it,
  on 2026-11-20.
- The operator granted the lanes `push`, `draftPullRequests` and `readyPullRequests` on 2026-11-20.
- The lead merges the integration change request into `main` under that grant.

## Execution posture and sensors

Test-first for every story. V26-3649 is test-only.

Sensors:
- `tests/test_merge_queue.py`, whose push pins (`test_the_push_uses_a_lease_and_never_targets_the_base` and the
  others the ticket names) move to the queue ref (V26-3643).
- `tests/test_journal_kinds.py`, `tests/test_journal_shape.py` and `tests/fixtures/journal-kinds/vectors.json` for
  `leftOnBranch` and `ownership.carried`'s `by`.
- `tests/test_lane_changes.py` (V26-3649).
- `tests/test_region_grants.py` and `tests/test_region_claims.py` (V26-3640).
- `tests/test_lead_assign_owns.py` (new, V26-3432).
- `tests/test_orchestration_state.py` (V26-3434).
- `tests/test_delivery_key_collision.py` (new, V26-3578).
- `tests/test_lead_add_lane.py` (new, V26-3628), `tests/test_lead_plan_changes.py` and `tests/test_lane_rows.py`.
- `tests/test_run_delivery_hooks.py` (V26-3676).
- `tests/test_run_delivery_review_wait.py` (new, V26-3427), `tests/test_round_brief_template_corpus.py` and
  `tests/test_waits_review.py`.
- `tests/test_run_delivery.py`, whose refusal and review-prepare pins the stories may move.
- `tests/test_own_deliveries.py`, which holds every test module to the binding's catalogue.
- `scripts/validate-registry.py` and `scripts/update-provenance.py --check`.

Each new module is catalogued in `.agents/orchestration.json` in the same diff, as `docs/own-deliveries.md` says.
The new modules reuse the fixtures the existing modules build, through a `SharedCorpus` per module. They add no class
that runs alone and keep the suite inside `tests/suite-budget.json`. A module that would push the run over its budget
makes other tests in the same change cheaper. No budget number moves.

## Scenarios

`tests/test_merge_queue.py` (V26-3643):
- A sync pushes the synced head to `refs/merge-queue/<key>` and leaves `refs/heads/<branch>` at the handed head.
- A check that fails leaves the branch at the handed head. The lane then pushes a fix on top as a fast-forward, with
  no forced push.
- A candidate stopped after sync, then authorized with `lead-authorize-base-merge`: the lane's merge of the base into
  its handed head pushes as a fast-forward and hands off.
- A merge whose forge command fails moves the branch back to the handed head, and the stop omits `leftOnBranch`.
- The same for a `ready` refusal (`test_a_refusing_ready_or_merge_stops`) and for a missing, stale or slow review
  (`test_a_missing_stale_or_slow_review_stops_and_nothing_merges`): after each stop the branch is at the handed head
  and `leftOnBranch` is absent.
- A batch in which one member's merge fails: that member's branch ends at its handed head.
- A queue ref moved off the synced head stops the check with `candidate_moved`.
- A candidate step that reports the handed head for its first reads after the push, and the synced head after that:
  the queue waits and merges.
- A candidate step that never reports the synced head within a short `checkTimeoutSeconds`: the candidate stops as
  `candidate_moved`, the branch is at the handed head, and `leftOnBranch` is absent.
- The sync push to `refs/merge-queue/<key>` is under a lease on that ref.
- The same with a commit pushed to the branch after the queue moved it: the branch is left alone, and
  `candidate.stopped` names `leftOnBranch`.
- A batch landing moves each member's branch only at its merge.
- Every push the queue makes to `refs/heads/` is under a lease, and none targets the base.
- The queue ref is deleted after the merge, and a later sync of a stopped candidate overwrites it under its lease.

`tests/test_lane_changes.py` (V26-3649):
- The whole-file item's `fields.detail` is "It doesn't hold the file.", and the span item's names the line it
  changed, for example "It changed line 2, outside its span.".

`tests/test_region_grants.py` and `tests/test_region_claims.py` (V26-3640):
- c1: `--anchor`/`--through` resolves to the lines between them and records the base and coordinates. Ambiguous,
  missing and reversed anchors are refused with the matches listed.
- An anchor pair whose span cuts a Python statement is refused with the existing statement-check wording.
- `--anchor` with `--start`, and `--anchor` with `--around`, are each refused.
- Carve's `--give lane-3:anchor=A..through=B` grants the same span `lead-own --region --anchor A --through B` grants.
- c2: replacing a span's last line with that line plus new lines is accepted.
- An insertion just before a span's first line, with no other claim on the line above, is accepted, and so is an
  insertion before line 1 of a span that starts there.
- An insertion just before a span's first line is refused, naming that lane, when another lane claims the line above.
- c3: an insertion just past a span is refused when another lane claims the next line, naming that lane.
- An insertion between two spans of one lane and story is accepted, and between two lanes' spans refused.
- A lane's change strictly inside its own span is accepted while another lane's claim on the same file is stale.
- An edge insertion next to another lane's stale claim is refused, naming that lane.
- `lead-claims` reports an accepted edge insertion as held, not as needing a claim.
- The edge cases hold at each entry point, each with an allow and a deny row: the queue's `check_regions`
  (`tests/test_merge_queue.py`), review preparation and `claims-check` (`tests/test_region_grants.py`), the hand-off's
  `laneOutside` items (`tests/test_lane_changes.py`), and `lead-accept` (`tests/test_lead_assign_owns.py`).
- `--carry` carries a span whose lines a base edit shifted but didn't touch, journalling `ownership.carried` with
  `by: lead`. It refuses one whose lines the edit touched, naming the hunk, and writes nothing.
- With two claims on the path, one touched and one not, `--carry` writes neither.
- `--story KEY` carries only that story's claim.
- A base hunk ending on the line just before the claim's first line, without touching it, carries.

`tests/test_lead_assign_owns.py` (V26-3432):
- `lead-assign --own-co-changed` adds the warned file to the lane's `owns` and the record, in one commit, with no
  hand edit.
- Without the flag, the warning ends by naming `--own-co-changed`.
- A warned file another lane holds is named with its `--move`, and isn't added.
- `lead-accept --take PATH --why T` accepts a one-line pin in an unowned file and journals the reason. It is refused
  when another lane holds the file, when the path is a shared output, and when the candidate doesn't change it, and
  each refusal leaves the record and journal unchanged.
- Two `--take` flags take both files.

`tests/test_orchestration_state.py` (V26-3434):
- An element-scoped patch to a lane row succeeds after the queue writes another part of the record.
- The same patch is refused when the row itself changed, and nothing is written.
- `--element-digest` without `--element` is a usage error, and the record's bytes and journal are unchanged.
- The existing selector-form element patches `run_delivery.py` sends still pass.
- `show --element` prints the sha256 of the element's canonical JSON, compared with one the test computes itself.
- `journal_patched` with the element and digest behaves the same.
- `status` on a lane row is refused, naming `state` among the allowed fields, and as the closest match.
- `--version` patches behave as today.

`tests/test_delivery_key_collision.py` (V26-3578):
- c1: a delivery whose key is a story's key inits and its story's `lane-start` succeeds, with the record under
  `<work directory>/deliveries/<key>/`.
- c2: `doctor --delivery` warns of the collision and names the folder.
- A delivery file's `stateDirectory` still wins, `DELIVERY_STATE` wins over both, and a record already at the old
  path is used where it is.
- A delivery whose key is no story's keeps today's default, and `doctor` warns of nothing.
- After the colliding story is removed with `lead-plan change`, the record at the new path is still found.
- Under a configured `workDirectory`, the record goes under that folder's `deliveries/`.
- With a record at both paths, the command refuses, naming both.
- A record folder git doesn't ignore is reported `wrong` by `doctor --delivery`.
- `lead-init` prints the record's folder for a colliding key.

`tests/test_lead_add_lane.py` (V26-3628):
- c1: `lead-add-lane` adds the lane to the delivery file, the plan's Units table and the record in one commit and one
  patch, and `lead-own` and `lead-assign` then accept it.
- c2: a grant overlapping another lane's claim, a shared output or a region claim is refused, and nothing is written
  or committed.
- A lane the delivery already holds is refused, and the delivery file, the plan, the record and the commit count are
  unchanged.
- In a plan with two tiers, the lane's row is added at tier 2.
- `--lane extra` is refused, naming the `lane-N` form, and nothing is written.
- c3: `lead-plan change --kind added --lane L` then adds a story on the new lane, and before it the refusal names
  `lead-add-lane`.
- c4: the command makes exactly one state-tool patch, so the record's version moves by one for it, carrying both
  `/laneOwnership/L` and the new `lanes[]` row in the state tool's changes file, and it writes the record by no
  other path.

`tests/test_run_delivery_hooks.py` (V26-3676):
- A lane session, recorded at `lane-start`, whose Stop payload's `cwd` is the lead's checkout is allowed and told
  nothing about `lead-wait`.
- The lead's recorded session in a lane checkout is still held while a lane works.
- A payload with an unknown or no `session_id` is seated by `cwd`, as today.
- `delivery-orient` orients a recorded lane session as its lane whatever its `cwd`.
- `lead-init`, `lead-run --resume` and `lead-wait` each record the lead's session.
- Two lane-start writes interleaved between read and write both survive.

`tests/test_run_delivery_review_wait.py` (V26-3427):
- `review-wait` returns 0 once every result path holds JSON that parses, and exits 4 naming the lenses still out
  when its seconds run out.
- With one result path holding truncated JSON, `review-wait --seconds 1` exits 4 and names that lens.
- With no `--seconds`, its deadline is 540 seconds, read through an injected clock rather than a real wait.
- The rendered round brief names the result path as the lens's one permitted write.
- `review-prepare`'s output names `review-wait`, and its prompts tell the reviewer to write its result.
- The lane brief's "Your turn" names `review-wait`.

Docs (Decision 14), in each story's own test module:
- A corpus pin holds each new or changed `SKILL.md` row (`lead-add-lane`, `review-wait`, and the new flags of
  `lead-own`, `lead-assign` and `lead-accept`), config.md's sentence on how a hook seats a session, and the guide's
  sentence on where the queue keeps its synced head.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3643 | `skills/orchestrate-work/scripts/merge_queue.py`, `tests/fixtures/journal-kinds/vectors.json`, `tests/test_journal_kinds.py`, `tests/test_journal_shape.py` |
| 1 | 2 | V26-3649, V26-3640, V26-3432 | `skills/orchestrate-work/scripts/region_ownership.py`, `tests/test_lane_changes.py`, `tests/test_region_grants.py`, `tests/test_region_claims.py`, `tests/test_lead_assign_owns.py` |
| 1 | 3 | V26-3434, V26-3578, V26-3628 | `skills/orchestrate-work/scripts/delivery_state.py`, `tests/test_orchestration_state.py`, `tests/test_delivery_key_collision.py`, `tests/test_lead_add_lane.py`, `tests/test_lead_plan_changes.py`, `tests/test_lane_rows.py` |
| 1 | 4 | V26-3676, V26-3427 | `skills/run-delivery/templates/lane-brief.md`, `skills/run-delivery/templates/round-brief.md`, `tests/test_run_delivery_hooks.py`, `tests/test_run_delivery_review_wait.py`, `tests/test_round_brief_template_corpus.py`, `tests/test_waits_review.py` |

The four lanes are independent. Within lane 2, V26-3649 goes first because it is the smallest and touches only a
test. V26-3640 changes `region_ownership.py`, which V26-3432's `--take` grants through. Within lane 3, V26-3434's
element patch goes first because it is the smallest. V26-3628 doesn't depend on it.

The lanes share these files. The lead grants each lane disjoint regions in them with `lead-own --region --around`
against the integration base's head at `lead-init`:

- `skills/run-delivery/scripts/run_delivery.py`
  - Lane 2: `change_regions` and the region resolution it calls; `claims_report`; `candidate_ownership`; carve's give parsing; `co_change_warning`;
    `lead_assign`'s warning block; `lead_accept`'s ownership check; the `lead-own`, `lead-assign` and `lead-accept`
    parsers.
  - Lane 3: `Config.state_of`; `setup_problems`; `lead_plan`'s unknown-lane refusal; a new `lead_add_lane` beside
    `change_ownership`; its parser beside `lead-own`'s.
  - Lane 4: `seat_of` through `orientation`; `lane_start`'s start record; `lead_wait`'s lease write; `review_prepare`'s
    printed prompts; a new `review_wait` beside `review_reduce`; its parser beside `review-reduce`'s.
- `tests/test_merge_queue.py`: lane 2 the `RegionDiffTests` class, for Decision 4's edge pins; lane 1 everything
  else, as two spans, one from the file's first line to the line before `class RegionDiffTests`, and one from the
  class after it to the file's end. The lead writes the three claims in one `lead-own --carve`. Lane 1's new tests go
  strictly inside its spans, and either lane asks for an `--insert` widening at a span's edge.
- `tests/test_run_delivery.py`: none at start. A lane whose change moves an existing assertion asks the lead for a
  region over it and fixes it in the same change. An assertion is never left red or deleted.
- `skills/orchestrate-work/references/journal-kinds.json`: lane 1 `candidate.stopped`; lane 2 `ownership.carried`.
- `skills/run-delivery/SKILL.md`: lane 2 the `lead-own`, `lead-assign` and `lead-accept` rows; lane 3 the `lead-plan`
  row and a new `lead-add-lane` row after it; lane 4 the `review-prepare` and `hook` rows and a new `review-wait` row
  after `review-reduce`'s.
- `skills/run-delivery/references/config.md`: lane 4 "Host hooks".
- `skills/orchestrate-work/references/orchestration-guide.md` and `skills/orchestrate-work/SKILL.md`: lane 1 the
  merge queue's sync and merge paragraphs.
- `.agents/orchestration.json`: each lane a span for its new modules' catalogue entries.

Every lane refreshes digests in `catalog.json`, `profiles/*` and `provenance.lock.json` with the provenance writer.
Those are regenerable files under the listing guard, and no lane adds or removes a listed output.

No ports, browser sessions or databases are shared. Lane 1's queue tests use their own bare remotes in temporary
folders.

## Stories

### V26-3643. Keep the merge queue's sync merges off a lane's branch, so a lane can push its own base merge

Lane 1, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/references/journal-kinds.json`, `skills/orchestrate-work/references/orchestration-guide.md`,
`skills/orchestrate-work/SKILL.md`, `tests/fixtures/journal-kinds/vectors.json`, `tests/test_journal_kinds.py`,
`tests/test_journal_shape.py` and `tests/test_merge_queue.py`.

Implement Decision 1, and Decision 14's queue parts.

### V26-3649. Pin the outside-span laneOutside item's detail

Lane 2, tier 1, not blocked. Test-first. Files: `tests/test_lane_changes.py`.

Implement Decision 2.

### V26-3640. Grant line spans by anchor, and treat an edit at a span's edge as the span's

Lane 2, tier 1, after V26-3649. Test-first. Files: `skills/orchestrate-work/scripts/region_ownership.py`,
`skills/run-delivery/scripts/run_delivery.py`, `skills/orchestrate-work/references/journal-kinds.json`,
`skills/run-delivery/SKILL.md`, `tests/test_region_grants.py`, `tests/test_region_claims.py`,
`tests/test_lane_changes.py` and `tests/test_merge_queue.py`.

Implement Decisions 3 to 5.

### V26-3432. Offer lead-assign's co-change warnings as owns candidates

Lane 2, tier 1, after V26-3640. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md` and `tests/test_lead_assign_owns.py`.

Implement Decisions 6 and 7.

### V26-3434. Let the lead patch a lane row without racing the queue's version

Lane 3, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/scripts/delivery_state.py` and
`tests/test_orchestration_state.py`.

Implement Decisions 8 and 9.

### V26-3578. Keep a delivery's record out of its own story's lane checkout when the two share a key

Lane 3, tier 1, after V26-3434. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py` and
`tests/test_delivery_key_collision.py`.

Implement Decision 10.

### V26-3628. Let the lead add a lane to a running delivery with one command

Lane 3, tier 1, after V26-3578. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `tests/test_lead_add_lane.py`, `tests/test_lead_plan_changes.py` and
`tests/test_lane_rows.py`.

Implement Decision 11.

### V26-3676. Don't tell a lane session to start the lead's lead-wait

Lane 4, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/references/config.md`, `skills/run-delivery/SKILL.md` and `tests/test_run_delivery_hooks.py`.

Implement Decision 12.

### V26-3427. Keep lanes waiting on their own lens results inside a turn

Lane 4, tier 1, after V26-3676. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/templates/lane-brief.md`, `skills/run-delivery/templates/round-brief.md`,
`skills/run-delivery/SKILL.md`,
`tests/test_run_delivery_review_wait.py`, `tests/test_round_brief_template_corpus.py` and `tests/test_waits_review.py`.

Implement Decision 13.
