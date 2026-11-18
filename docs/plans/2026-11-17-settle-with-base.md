---
reviewSlug: settle-with-base
---

# Settle a story with its base before the queue has to

Most of the time a story spends between hand-off and merge goes to queue stops, not to review or to the story itself.
There are two kinds:

- **The base moved, and the queue is the first to see it.** The lane hands off against the base it started from. A
  conflict, or a base change to a file the lane owns, then surfaces only at the queue's sync. That costs a stop, a lead
  round trip, a base merge, often a review round, and a second hand-off with its checks run again.
- **A failed check whose cause is already known.** The queue re-runs the failed check on the base to decide whose
  failure it is. Until that re-run ends, nothing the lead or the lane knows can change what the queue does.

Epic V26-3585. Seen in V26-3431, V26-3530/V26-3538 and V26-3543/V26-3546; each story below names its incident.

Checked against `main` at `0eb0aee` on 2026-11-17:

- `lead-authorize-base-merge` (`run_delivery.py`) journals `base.merge.authorized` with writer `lead`, and prints one
  `git merge --no-ff --no-log -m '<key>: Merge delivery base' -m 'Delivery-Base-Merge: <token>' <base>` per base
  commit. It prints nothing for a merge that conflicts.
- `verify_base_merge` checks each first-parent commit after the reviewed head. It reads the merge's
  `Delivery-Base-Merge` trailer with `git show -s --format=%(trailers:key=Delivery-Base-Merge,valueonly)`, not the
  whole message. On git 2.54, a conflicted merge finished with `git commit --no-edit` keeps git's `# Conflicts:`
  lines, and the trailer still reads. So V26-3577's failure does not reproduce on this machine's git. Which git the
  V26-3431 lane ran, and what it typed, is not recorded. The amended commits (`0e8a7a8`, `d40ab69`) are the only ones
  left.
- `check_base_merge_delta` refuses outright when the base changed any lane-owned file between the old and the new
  merge base (`the base merge changes lane-owned file ...`). For other paths it compares each path's `git diff --raw`
  line before and after. That line holds the post-image blob id, so a file both sides changed never compares equal.
- `handoff` fetches the base, but never checks whether the head merges into it.
- The queue's `merge_in` stops with `source_conflict` on a conflict outside `regenerable.paths`. For a conflicted path
  with a listing (`provenance.lock.json`, listed by `scripts/update-provenance.py --list-outputs`), `check_listing`
  stops when the candidate's listing differs from the merge base's, whatever the base did. Otherwise it takes the base
  side, and the regenerator refreshes digests in `settle`.
- The queue's base re-run runs synchronously inside the serve pass (`classify` → `rerun_on_base`), and holds the merge
  slot. Its state is `/meta/checking/now/baseRerun`. The state tool's `handoff` refuses a row that is not `queued` or
  `stopped` (`handoff_invalid: <key> is checking; hand it off once it stops`). `carry_review` refuses a head that is not
  the queued one. No lead command stops just the re-run. A re-run stopped by a signal is no verdict, and it puts the
  candidate back at its old head once per head pair, as `docs/solutions/best-practices/a-stopped-base-rerun-is-not-a-broken-base-2026-11-14.md`
  records. The lead can journal `base.broken` with source `lead` through the state tool, but no run-delivery command
  does, and the queue does not read it as ending a re-run.
- The line map (`skills/delivery-views/assets/line-map.js`) shows "Rerunning X on the main line… · started". It gives
  no purpose, no expected end and no alternative.

## Finish line

- Both plan lenses align.
- Each story's acceptance criteria hold, proven by the tests named under Scenarios.
- All five stories have landed on `integrate/settle-with-base` through its merge queue.
- The integration base has been reviewed for `main`, as **Reviewing an integration base for the default branch** in
  the orchestrate-work guide describes, and merged into `main`. The full local suite has passed on macOS with Python
  3.11 at `main`'s head after that merge, run with `--workers 4 --budget tests/suite-budget.json`, inside the budget.
- V26-1654 is closed by Decision 4.
- This delivery's own hand-offs and queue run on the installed generation, so they can't show the new behaviour.
  The fixture tests are the proof.
- Tagging a release is not this delivery's. Every change is additive, a minor version, released at the next batch
  boundary.

## Decisions

1. **One carry rule: compare the story's own change before and after the merge.** It is used by V26-3572, V26-3576
   and V26-3577. For each base merge after the reviewed head, take the story delta before the merge,
   `git diff -U0 <old merge base> <prior>`, and the delta after it, `git diff -U0 <new merge base> <merge>`. They are
   equal when the set of changed paths is the same, and each path has the same status (added, deleted, modified), the
   same mode, and the same hunks in the same order. Hunks are compared as their removed and added lines, ignoring line
   numbers and context. The review carries when every merge's deltas are equal. Who owns the file the base touched
   does not matter. The refusal `the base merge changes lane-owned file` goes, and so does the per-path `--raw`
   comparison. A region claim must still map from the old base to the new one, as today; that check is about
   ownership, not about the carry. When the deltas differ, the refusal names each path that differs, and for the first
   differing hunk the removed and added lines on each side, at most 20 lines.
2. **A base that moved without a conflict needs no merge before hand-off** (operator's ruling, 2026-11-17).
   `handoff` refuses only when the head does not merge cleanly into the current base outside `regenerable.paths`. A
   clean move is left to the queue's sync, as today.
3. **The lane authorizes its own base merge before hand-off** (operator's ruling, 2026-11-17). A new lane command,
   `base-merge <key>`, run in the lane checkout, fetches the base, and journals `base.merge.authorized` with writer
   `lane`, the lane's reviewed head and the base head as its one base commit. It then prints the merge command and its
   finishing command (Decision 5). It refuses when the story is queued, checking or merged: a candidate the queue holds
   is the lead's, through `lead-authorize-base-merge`, as today. It refuses when the lane checkout's `HEAD` is not the
   last aligned round's `candidateRef`, or when the base head is already in its history. `verify_base_merge` and the
   state tool's `base_merge_evidence` accept an authorization written by the story's lane, with writer `lane`, the
   same as one by the lead. The entry's data gains an optional `by: lane`, which the kind registry's words read, so the
   record says who authorized it. An entry without `by` is the lead's.
4. **A round forced by a base merge counts against the bound** (operator's ruling, 2026-11-17). Under Decision 1, a
   merge forces a round only when the story's own change differs, so that round is about the story. This closes
   V26-1654. The orchestrate-work guide says so where it describes carrying a review across a base merge.
5. **The finishing command is printed, and comment lines never decide a carry.** `lead-authorize-base-merge` and
   `base-merge` print, after the merge commands: if a merge stops on a conflict, resolve it, `git add` the files, then
   run `git commit -m '<key>: Merge delivery base' -m 'Delivery-Base-Merge: <token>'`. `verify_base_merge` reads the
   message with `git show -s --format=%B`, drops every line starting with `#`, and reads the trailer from the rest with
   `git interpret-trailers --parse`. Then no git version or cleanup setting decides it. When the trailer is still
   missing or wrong, the refusal names the merge commit and prints the exact `git commit --amend -m ... -m ...` that
   fixes it, for the last merge only.
6. **The queue takes the candidate's listing when the base changed only digests.** V26-3549. In `merge_in`, for a
   conflicted listed path, compare the three listings. The candidate's equals the merge base's: take the base side, as
   today. The base's equals the merge base's and the candidate's differs: take the candidate's side, and `settle`'s
   regenerator refreshes the digests. Both differ from the merge base: stop with `source_conflict`, as today, and say
   that both sides changed the listing. `check_shared_outputs`, the guard for a lane without the listing's claim, is
   unchanged.
7. **A new hand-off supersedes a base re-run.** V26-3557. The state tool's `handoff` accepts a hand-off for a row that
   is `checking` while `/meta/checking/now/baseRerun` runs for that subject, at a head other than the checked one. It
   is pending, as any hand-off, and `carry_review` targets it. When the lead accepts it, the state tool journals
   `base.rerun.superseded` (new kind: `{base, head, by}`, where `head` is the old head and `by` is the acceptance's
   entry). The queue polls the record every 2 seconds while a base re-run check runs. On seeing that entry it stops its
   own check process group, by the pid it started, and journals `base.rerun.stopped` with `why` naming the entry. It
   writes no `base.rerun.finished`, does not re-queue the old head, and the row takes the accepted head as `queued`. A
   verdict reached after the entry was journalled is dropped: the queue reads the record before it writes a verdict.
8. **The lead can name the base broken before the re-run's verdict.** A new lead command,
   `lead-base-broken --head H --check ID --test T --why W [--repair KEY]`, journals `base.broken` with source `lead`
   and the head, check, test and reason, naming the repair when given, as `name_repair` does. When a base re-run is
   running at that base head, it also journals `base.rerun.superseded` with `by` naming the `base.broken` entry. The
   queue then stops the re-run as in Decision 7. The candidate under check is stopped as `base_broken`, with
   `supersededAt` set to its failed head. `repaired_state` re-queues a `base_broken` row only when its head differs from
   `supersededAt`, so the failed head is never queued again. When the repair is the failing story itself, its next
   hand-off at the fixed head is the repair. `base.broken`'s data gains optional `check`, `test` and `why`, and the
   kind registry's words show them for source `lead`.
9. **The views say what a re-run is for.** While `/meta/checking/now/baseRerun` runs, the line map's checking panel
   says which check failed on which candidate, that the re-run decides whether the base or the candidate broke it, and
   its expected end. The expected end is its start plus the check's last recorded duration from the queue history,
   and is left out when there is none. It names the two alternatives: a new hand-off from the lane, which the lead
   accepts (Decision 7), or `lead-base-broken` (Decision 8). Without a running re-run nothing changes.
10. **The catalog and profile sentences are the lead's.** Every story changes the one-line `degradedBehavior` of
    `catalog.json` and the one-line `description` of each `profiles/*.json`, so two lanes would always conflict there.
    The lead writes each story's sentence in one commit on the integration base, pushed with `lead-push` right after
    that story merges. `main` receives code and sentences together, in the integration base's merge.
    `provenance.lock.json` stays regenerable: each lane runs the writer.

## Out of scope

- Skipping the base re-run by default. With no lead decision and no new hand-off, a failed check re-runs on the base
  exactly as today.
- Letting a lane end a re-run without a hand-off the lead accepts.
- Requiring a base merge when the base moved without a conflict (Decision 2).
- Test flakes that make the queue journal `base.broken` (V26-3573, V26-3478).
- V26-3553's gap "the queue's sync merge blocks a lane's next push". After V26-3572, a candidate that merged the
  current base gets no sync commit unless the base moves again, so that gap arises less often. It is not closed here.
- V26-3564, which tells a lane about a change outside its ground before hand-off. V26-3572's conflict check runs
  before the ownership check at hand-off, and does not replace it.
- A release tag.

## Approval Handoffs

- The operator approves this plan before `lead-setup`.
- The operator grants lanes `push` and `draftPullRequests`, and the lead `readyPullRequests` (granted 2026-11-17).
- The operator approves merging `integrate/settle-with-base` into `main` after its review.

## Execution posture and sensors

Test-first for every story. Sensors:

- `tests/test_run_delivery.py` for `handoff`, `base-merge`, `lead-authorize-base-merge` and the carry.
- `tests/test_run_delivery_supervisor.py` for `lead-accept` of a superseding hand-off and for `lead-base-broken`.
- `tests/test_orchestration_state.py` for the state tool's `handoff`, `carry_review`, `base_merge_evidence` and
  `repaired_state`.
- `tests/test_merge_queue.py` for the listing merge and the superseded re-run.
- `tests/test_journal_kinds.py`, `tests/test_journal_shape.py` and `tests/fixtures/journal-kinds/vectors.json` for
  the kind registry.
- `tests/delivery_views_page/` for the line map.
- `scripts/validate-registry.py` and `scripts/update-provenance.py --check` for provenance.

The queue's base re-run tests are deadline-sensitive. A new superseding test joins `StoppedBaseRerunTests` and reuses
its fixture, and it is marked `run_quietly` as that class is. The suite stays inside `tests/suite-budget.json`.

## Scenarios

V26-3577, in `tests/test_run_delivery.py`:

- `lead-authorize-base-merge`'s output includes the finishing `git commit -m ... -m ...` for each merge.
- A merge that conflicts, finished with the printed command, carries the review at hand-off.
- The same merge finished with `git commit --no-edit`, which keeps `# Conflicts:` lines, carries.
- A merge whose message lost its trailer is refused, and the refusal prints the exact `--amend` command. Running that
  command makes the next hand-off carry.

V26-3576, in `tests/test_run_delivery.py`:

- The base and the story each append an entry to the same lane-owned JSON file. The authorized merge keeps both, and
  the review carries.
- A merge whose resolution drops one of the story's added lines is refused, and the refusal names the path and that
  line.
- A base change to a lane-owned file the story never touched carries.
- A region claim that no longer maps after the merge is still refused.

V26-3572, in `tests/test_run_delivery.py`:

- A head that conflicts with the current base outside `regenerable.paths` is refused by `handoff`. The refusal names
  the files and prints `base-merge <key>`.
- A conflict only in `provenance.lock.json` does not refuse.
- `base-merge` journals an authorization with writer `lane`, and prints the merge and finishing commands. After the
  merge, `handoff` carries the review, and `lead-accept` accepts it.
- `base-merge` refuses on a queued story, and on a checkout not at the last aligned round's head.
- A conflict-free head on an unmoved base hands off as today, and so does one on a base that moved without a conflict.
- The rendered lane brief says, in this order: run `base-merge`, merge, rerun gates and checks, push, hand off.

V26-3549, in `tests/test_merge_queue.py`:

- A candidate adds an output and the base only refreshed digests. The sync takes the candidate's listing, the merged
  lock lists both sides' outputs, and its digests are current. There is no stop.
- Both sides changed the listing: `source_conflict`, naming both.
- The candidate changed only digests and the base added an output: the base side, as today.
- The same three cases in a batch merge.

V26-3557, in `tests/test_orchestration_state.py`, `tests/test_merge_queue.py`,
`tests/test_run_delivery_supervisor.py` and `tests/delivery_views_page/`:

- A hand-off at a new head is accepted by the state tool while the subject's base re-run runs. One at the checked head
  is still refused.
- `lead-accept` of that hand-off journals `base.rerun.superseded`. The queue stops the re-run's check, journals
  `base.rerun.stopped` naming the entry, writes no verdict, and queues the new head. The old head is never checked
  again.
- A re-run whose verdict arrives after the superseding entry writes none.
- `lead-base-broken` with `--repair` journals `base.broken` with check, test and reason, names the repair, and ends
  the running re-run. The failed head is not re-queued, including after `base.repaired`.
- With no lead decision and no new hand-off, the re-run and its verdicts are unchanged. The existing re-run tests
  pass unmodified.
- The line map's checking panel shows the purpose, the expected end with and without history, and the two
  alternatives.
- The guide pin: the orchestration guide says when ending a re-run early is sound, and that the lead's entry is then
  the evidence for the base's state.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3577, V26-3576, V26-3572 | `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/templates/lane-brief.md`, `skills/run-delivery/SKILL.md`, `tests/test_run_delivery.py` |
| 1 | 2 | V26-3549, V26-3557 | `skills/orchestrate-work/scripts/merge_queue.py`, `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/scripts/render_state.py`, `skills/orchestrate-work/references/journal-kinds.json`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/SKILL.md`, `skills/delivery-views/assets/line-map.js`, `tests/test_merge_queue.py`, `tests/test_orchestration_state.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_journal_kinds.py`, `tests/test_journal_shape.py`, `tests/fixtures/journal-kinds/`, `tests/delivery_views_page/` |

Lane 1 changes two lane-2 files for V26-3572: `delivery_state.py`'s `base_merge_evidence`, which must accept a
lane-written authorization, and `journal-kinds.json`'s `base.merge.authorized`, which gains `by`. Lane 2 changes three
lane-1 files for V26-3557: `run_delivery.py` gains `lead-base-broken`, and `lead-accept` takes a superseding hand-off;
`SKILL.md` gains its row; `tests/test_run_delivery.py` is not touched by lane 2. Lane 1 likewise documents carrying a
review in the orchestration guide (Decision 4). Before assigning each of those stories, the lead turns the whole-file
claims on these files into line spans with `lead-own --revoke` and `lead-own --region`, against the current base: the
owning lane keeps spans over what it changes, and the other lane gets spans over the functions and rows the story
names. The lane's assignment message names them.

`catalog.json` and `profiles/*` are the lead's (Decision 10). `provenance.lock.json` is regenerable.

V26-3577 and V26-3549 stand alone and are small, so each lane starts with one. V26-3572 needs V26-3576's carry rule,
and both need V26-3577's trailer reading, so lane 1 runs them in order. V26-3557 does not depend on the rest. It runs
after V26-3549 only because they share `merge_queue.py`.

## Stories

### V26-3577. Print how to finish a conflicted authorized base merge, so its message stays exact

Lane 1, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/templates/lane-brief.md` and `tests/test_run_delivery.py`.

Implement Decision 5 in `lead_authorize_base_merge` and `verify_base_merge`, and the brief's line on finishing a
conflicted merge.

### V26-3576. Carry a review across a base merge whose only change to a lane-owned file is the base's own addition

Lane 1, tier 1, after V26-3577. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/templates/lane-brief.md`, `skills/orchestrate-work/references/orchestration-guide.md` and
`tests/test_run_delivery.py`.

Implement Decision 1 in `check_base_merge_delta`, with its refusal text. Write Decision 4 in the guide, and update the
brief's sentence on what a carry needs.

### V26-3572. Have a lane merge the base before it hands off, so the queue doesn't find the conflict

Lane 1, tier 1, after V26-3576. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/templates/lane-brief.md`, `skills/run-delivery/SKILL.md`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/journal-kinds.json` and
`tests/test_run_delivery.py`.

Implement Decision 2's conflict check in `handoff`, after its fetch, with `git merge-tree --write-tree` against the
base head, and Decision 3's `base-merge` command. The brief makes the order a step: `base-merge`, merge, gates and
checks, push, hand off. Add `base-merge` to `SKILL.md`'s command table.

### V26-3549. Let the merge queue merge a candidate's new provenance outputs when the base only changed digests

Lane 2, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/references/orchestration-guide.md` and `tests/test_merge_queue.py`.

Implement Decision 6 in `merge_in`, for a sync and a batch alike, and update the guide's lines on a conflicted listed
path.

### V26-3557. Let the lead replace a failing candidate's head, and end a base re-run nobody needs

Lane 2, tier 1, after V26-3549. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/journal-kinds.json`,
`skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/SKILL.md`,
`skills/delivery-views/assets/line-map.js`, `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `tests/test_merge_queue.py`, `tests/test_orchestration_state.py`,
`tests/test_run_delivery_supervisor.py`, `tests/test_journal_kinds.py`, `tests/fixtures/journal-kinds/` and
`tests/delivery_views_page/`.

Implement Decisions 7, 8 and 9. Add `base.rerun.superseded` to the kind registry with its vectors. Add
`lead-base-broken` to run-delivery's `SKILL.md`. In the guide's "A stopped base re-run" and "A broken base", say when
ending a re-run early is sound, which is when the lead knows the cause or holds the fix, and that the lead's entry is
then the evidence for the base's state.
