---
reviewSlug: close-on-proof-and-learning
---

# Close a delivery only on what it proved and what it taught

A delivery's close doesn't wait for the things only the lead can settle. Compound-learning is left until after the
archive, so it gets skipped (V26-3530 was the latest). The lead has no command to mark a finish line met, so in
V26-3585 the lead did it by hand with the state tool. Friction comes up in passing and only some of it is filed;
V26-3553 kept its gap log by hand. This epic, V26-3623, makes the close wait on a recorded proof for each line the
record can't prove, and on a recorded compounding decision. Gaps are journalled when they happen and reach that
decision as evidence.

The epic is delivered on its own integration base, `integrate/close-on-proof`, and the lead then takes it into `main`
as the guide's "Reviewing an integration base for the default branch" says.

Checked against `main` at `998da472` on 2026-11-18:

- `finished_lines` and `leads_line` (`skills/run-delivery/scripts/run_delivery.py`) already skip a line whose
  `covers` names no criteria and no proofs, so the supervisor never marks such a line met. That landed with V26-3170
  (#333). `deferred_findings` already drops a deferral that a later round of the lens that filed it lists as closed,
  fixed or withdrawn. That landed with V26-3340 (#335). Both of V26-3477's defects were seen on linear-v2.2.0, before
  either fix. What V26-3477 asks for already holds on the base.
- `journal_finish_met` journals `finish.met` with `{finish}` only. `journal-kinds.json` gives `finish.met` one field,
  `finish`. No command marks a lead-held line, and the state tool's `patch --set` refuses a pointer to a key that
  doesn't exist yet without saying what would work.
- `waits_on` reads "The close waits on" from the supervisor's last pass log line (`closeHeldBy`). So after a line is
  marked by hand, the status line lists it as unmet until the next pass.
- `close_holds` holds on planned or unmerged stories, unmet lines, busy lanes, uncleaned checkouts and holding
  "Needs you" items. Nothing about compounding. `close_out` runs `full_health_step`, `followups_step` and
  `closed_step` (which journals `delivery.closed`), then the archive and the finish marks, and writes the friction
  stub (`friction_step`) last, beside the archive.
- `lead-close` runs only after `delivery.closed` and checks nothing about compounding.
- No `gap.noted` or `compounding.recorded` kind exists. 33 kinds have no `tracker` entry, and the tracker publisher
  posts nothing for them.
- The guide's "Lead checklist" is the lead's brief. `skills/run-delivery/templates/lane-brief.md` is the lane's, and
  the guide's "Lane brief template" mirrors it.

## Finish line

- Both plan lenses align.
- V26-3614, V26-3571 and V26-3561 have landed on `integrate/close-on-proof` through the merge queue. V26-3477 is
  settled with a no-change hand-off whose evidence names each of its criteria.
- The epic's four acceptance criteria hold on the integration base. Each is shown by a test named in Scenarios.
- The lead has taken the integration base into `main` by the guide's recipe: `main` merged in, the full local suite
  passed on macOS with Python 3.11 at the integration head with `--workers 4 --budget tests/suite-budget.json` and
  inside the budget, the integration review aligned, and the change request merged by the operator. This is a
  lead-held line: it names no criteria and no proofs.
- The epic goes out as one candidate release, is applied in one adopter, and is then promoted. Tagging is the
  operator's and not this delivery's. The record gains kinds and an optional field: a minor version.

## Decisions

1. **`lead-finish LINE --evidence TEXT|@FILE`** (V26-3614). It marks a lead-held finish line met and journals
   `finish.met` with `{finish, evidence, by: "lead"}`. The patch and the journal entry are one state-tool write, so a
   failure leaves neither. It refuses, journalling nothing:
   - a line the record doesn't hold;
   - a line `leads_line` doesn't call the lead's, saying "the record proves this line: the supervisor marks it when
     its criteria and proofs are done";
   - a line already met;
   - empty evidence.
   `finish.met` gains optional `evidence` (prose) and `by` (`lead` or `supervisor`). The supervisor's own entries
   carry `by: "supervisor"` and no evidence. An entry with neither still validates, so older records read as before.
   The single write is a new state-tool operation in `delivery_state.py` that applies an element patch and appends a
   journal entry under one lock.
2. **The status line reads finish lines from the record** (V26-3614). `waits_on` keeps the last pass's other holds,
   but takes its "finish lines not met" part from the record's current `finish`. So a line `lead-finish` marks drops
   out of the next status line, with no pass in between. `lead-finish` then renders the live view once, through the
   function a supervisor pass renders with, and prints the status line. V26-3561 extends this to the compounding
   decision (Decision 9).
3. **The state tool's `--set` refusal names the fix** (V26-3614). `patch --set` refuses a pointer that passes through
   an array element, such as `/finish/0/met`, whether or not the key exists. That refusal names the
   `--element ... --patch` form that does the same write, with the element's id and the key filled in.
4. **V26-3477 settles with no change.** Its lane hands it off with `handoff --no-change` at a base head that holds
   V26-3614. The evidence names each criterion with the test that shows it:
   - criterion 1: a line with empty coverage stays unmarked across passes, and `lead-finish` marks it;
   - criterion 2: a covered line is still marked once its stories merge;
   - criterion 3: a deferral that a later round lists as fixed files no follow-up;
   - criterion 4: a record with every story merged and a lead-held line unmet journals no `delivery.closed`.
   Where no existing test shows a criterion, the lane adds it to V26-3614's test module before V26-3614 hands off.
   That keeps V26-3477 a no-change story. If the lane finds that a criterion fails on the base, it hands off blocked
   on it, and the lead re-plans the story as a code change.
5. **`note-gap`** (V26-3571). `note-gap --class workflow|repo|host --text T --why W [--subject KEY] [--evidence FILE]
   [--command TEXT] [--same-as N]` journals `gap.noted`. Leads and lanes can both run it. The logic lives in a new
   module, `skills/run-delivery/scripts/gaps.py`, wired into `run_delivery.py` as `pr_review_gates.py` is.
   - `gap.noted` data is `{class, text, why, by, release?, generation?, command?, evidence?, occurrenceOf?}`. `by`
     is `lead` or the lane's id. `--subject` sets the entry's subject. When the lead runs it, the subject defaults to
     the delivery's key; when a lane runs it, to the lane's current story, since the state tool lets a lane journal
     only on its own work.
   - A lane journals it under its own writer: the state tool's lane-writer allowlist in `delivery_state.py` gains
     `gap.noted`, as it holds the review kinds and `base.merge.authorized`.
   - A workflow gap records the release id and generation from the running generation's entry in
     `.agent-skills/active.json`, as the round brief reads them. It needs `--evidence`, and is refused without it.
   - The evidence file is copied into the record's `gaps/` folder under the entry's id. Data keeps its path relative
     to the record, and its sha256.
   - `--command` records the command and arguments involved. The evidence is its output, or the script the agent
     wrote.
   - Kind entry: altitude `story`, a quiet tone, no `tracker` entry, so nothing is posted to the adopter's tracker
     for any class.
6. **Matching is explicit and exact** (V26-3571). A gap is open from its `gap.noted` until the close-out routes it;
   no command closes one earlier. Before journalling, `note-gap` lists the open gaps of the same class (workflow gaps
   on the same release; repo and host gaps on any release, since they match on class alone), each with its journal
   id. A new note
   whose text, casefolded with whitespace collapsed, equals an open gap's, or that names `--same-as N`, is journalled
   as an occurrence: `occurrenceOf: N`, and no new gap. `--same-as` naming an entry that isn't a `gap.noted`, or one
   of another class, is refused. No fuzzy matching: the listing is there so the agent picks `--same-as`.
7. **The close-out routes gaps by class** (V26-3571).
   - Repo gaps become follow-ups in `followups_step`, keyed `gap-<journal id>`, as deferred findings are, and the
     tracker publisher files them as it files those. Each follow-up carries `story` set to the gap's subject (the
     delivery's key when none was named), `finding` set to `gap-<journal id>` and `title` set to the gap's text, so
     `tracker_publisher.py` files it unchanged.
   - Workflow gaps go into `<archive>.workflow-gaps.md` beside the archive: grouped by release, each with its text,
     why, command, occurrence count and evidence path. The archive itself carries the evidence files.
   - Host gaps are listed in the friction stub only.
   - The friction stub gains a "Gaps noted" section listing every gap by class, for compound-learning.
   - Sending the workflow report anywhere is the adopter's call, not this delivery's.
8. **The briefs name the trigger** (V26-3571). The guide's "Lead checklist", the lane brief template and the guide's
   "Lane brief template" each say: note friction with `note-gap` when it happens, class it as workflow, repo or host,
   and when you write a script to get behaviour the workflow or a tool should provide, note a workflow gap with the
   script as its evidence before moving on. One-off reads for understanding don't count; anything you'd need again
   does. A corpus pin in `tests/test_run_delivery_gaps.py` holds this wording in each of the three, together with the
   absence of a brief that says only "note friction".
9. **The close holds on a compounding decision** (V26-3561). `close_holds` adds "the compound-learning decision"
   while no `compounding.recorded` exists after the delivery's latest reopening. It holds the close as an unmet line
   does. The status line and "Needs you" name it as what the close waits on, and "Needs you" gives the friction
   stub's path. As Decision 2 does for finish lines, `waits_on` reads the decision's part from the record, so the
   status line `lead-compound` prints no longer names it.
10. **The friction stub is written before the close** (V26-3561). The supervisor writes it at
    `<lead files>/friction.md` on the first pass where nothing but the decision holds the close, and never while
    anything else holds it. It rewrites the stub's generated sections on each later pass until the close-out archives
    it. The stub carries "Shown, never holding" as known then, and V26-3571's "Gaps noted". The lead's own text under
    "What was hard" is kept. If the close-out reaches the archive step and no pass has written the stub, the
    close-out writes it first, as `friction_step` does today, so every supervised close archives one. The archive
    step copies the stub into the archive, and `closed.json` names the archived copy. "What the close-out left to
    you" is written after the archive, as now. `closed_words` stops telling the lead to write up the friction note
    after the close, and names the archived copy.
11. **`lead-compound`** (V26-3561). `lead-compound --required --ref REF` or `lead-compound --not-required --why T`
    journals `compounding.recorded` with `{outcome: "required", reference}` or `{outcome: "not-required", why}`.
    Each of these is refused, journalling nothing:
    - `--required` with no reference, or an empty one;
    - `--not-required` with no reason, or an empty one;
    - any run while open work stands, read exactly as `close_holds` reads it: a planned story not settled or a
      queued story not merged, each leaving out the subjects a plan change removed. The refusal names what is open.
      So the decision is made once the work is done, with every gap noted.
    The entry releases the hold on the next pass. A delivery reopened after a close needs a new decision. Kind entry:
    altitude `headline`, posts on the epic.
12. **`lead-close` refuses without the decision** (V26-3561). With no `compounding.recorded` since the latest
    reopening, it exits non-zero naming the decision before it stops anything. `lead-close --without-compounding
    WHY` journals `compounding.recorded` with `{outcome: "skipped", why}` and closes. The reason is in the journal
    and in the close-out summary `lead-close` prints. The supervisor treats a journalled `delivery.closed` the same
    way: with no decision after the latest reopening, its close-out holds before the archive step and names the
    decision, as `lead-close` refuses.
13. **The written paths agree** (V26-3561). The guide's §8, its supervised and manual close-out paragraphs, and
    "Reviewing an integration base for the default branch" all say the same order: note gaps as they happen; when
    the last story lands, write up the friction stub, run compound-learning and record its decision; a note rides the
    integration change request and is reviewed with it; then the close. run-delivery's `SKILL.md` names `lead-finish`,
    `note-gap` and `lead-compound` in its command table, and its "Cleaning up" says the same order. A corpus pin in
    `tests/test_run_delivery_compounding.py` holds the order's wording in §8's supervised paragraph, its manual
    paragraph and the integration recipe.
14. **Older records** (V26-3561). The exemption covers only a close that has finished: one whose `delivery.closed`
    entry a `finished` mark or `closed.json` names. Such a record still reads as closed with no
    `compounding.recorded`, and `close_progress`, `closed_status` and the views are unchanged for it. Any other
    journalled `delivery.closed` with no later decision is held as Decision 12 says. For a close stopped partway with
    no decision, `finishing_command` names `lead-compound` first, or `lead-close --without-compounding`.

## Out of scope

- Waking the lead when a "Needs you" item holds the finish (V26-3618).
- Dropping a resolved queue stop from the status line (V26-3436).
- A command for the integration review's rounds (V26-3511, V26-3609).
- What counts as a learning, and where notes are kept: compound-learning owns both.
- Sending the workflow report upstream automatically.
- Fuzzy matching of gap text.

## Approval Handoffs

- The operator approves this plan before `lead-setup`.
- The operator grants the lanes `push` and `draftPullRequests`.
- The operator merges the integration change request into `main`, and tags the candidate and then the final release.

## Execution posture and sensors

Test-first for V26-3614, V26-3571 and V26-3561. V26-3477 is characterization only: existing tests, or tests added
under V26-3614, show its criteria on the base.

Sensors:
- `tests/test_run_delivery_finish.py` (new, V26-3614 and V26-3477).
- `tests/test_run_delivery_gaps.py` (new, V26-3571).
- `tests/test_run_delivery_compounding.py` (new, V26-3561).
- `tests/test_orchestration_state.py` for the `--set` refusal and the combined write.
- `tests/test_run_delivery_supervisor.py` and `tests/test_run_delivery.py`, whose close paths V26-3561 changes.
- `tests/test_journal_kinds.py`, `tests/test_journal_shape.py` and `tests/fixtures/journal-kinds/vectors.json` for
  the kinds.
- `scripts/validate-registry.py` and `scripts/update-provenance.py --check`.

The new modules reuse the supervisor fixtures that `tests/test_run_delivery_supervisor.py` builds, through a
`SharedCorpus` per module. They add no class that runs alone and keep the suite inside `tests/suite-budget.json`. A
module that would push the run over its budget makes other tests in the same change cheaper; no budget number moves.

## Scenarios

`tests/test_run_delivery_finish.py`:
- `lead-finish deploy --evidence "release v3.4.0 applied"` on a lead-held line sets `met`. It journals one `finish.met`
  with `{finish: "deploy", evidence, by: "lead"}`, and the status line printed next no longer names `deploy`.
- `--evidence @file` reads the file's text.
- Refused, each journalling nothing:
  - a covered line, with the "record proves" words;
  - an unknown line;
  - a line already met;
  - empty evidence.
- A supervisor pass after `lead-finish` journals no second `finish.met` for the line.
- A supervisor-marked line carries `by: "supervisor"`.
- V26-3477's four criteria, as Decision 4 lists them, each where the base lacks a test. Criterion 4's row records
  nothing else that could hold the close, so the lead-held line is its only hold.

`tests/test_orchestration_state.py`:
- `patch --set '{"/finish/0/met": true}'` is refused, and the message names
  `--element 'finish[id=<id>]' --patch '{"met": true}'`. This holds both on a line without `met` and on one with
  `met: false`.
- The combined patch-and-journal operation writes both or neither: with the journal append made to fail, the record's
  `finish` is unchanged.

`tests/test_run_delivery_gaps.py`:
- A workflow gap with evidence journals `gap.noted` carrying class, the receipt's release id and generation, the
  command, and the evidence's path and sha256. The copied file's bytes match.
- A workflow gap with no evidence is refused, journalling nothing.
- Repo and host gaps need no evidence.
- Matching, each asserting whether `occurrenceOf` is set:
  - the same text on the same release, as a workflow gap: an occurrence;
  - text differing only in case and runs of whitespace: an occurrence;
  - different text, same class and release: a new gap;
  - `--same-as N`: an occurrence;
  - the same workflow text on another release: a new gap;
  - the same repo text on another release: an occurrence;
  - the same text in another class: a new gap;
  - `--same-as` naming a non-gap, or a gap of another class: refused, journalling nothing.
- A lane's `note-gap` with no `--subject` journals under the lane's writer, on the lane's current story, with `by`
  the lane's id.
- Close-out over a record holding two workflow gaps on two releases (one with an occurrence), one repo gap noted
  without `--subject`, and one host gap:
  - one follow-up `gap-<id>` for the repo gap, with the delivery's key as its story, and the tracker publisher's
    planned posts include it;
  - `<archive>.workflow-gaps.md` has one heading per release, and the gap with an occurrence shows a count of 2;
  - the archive holds each evidence file, with a sha256 matching its `gap.noted` data;
  - the friction stub's "Gaps noted" names all four gaps under their classes;
  - the tracker publisher's planned posts hold no `gap.noted`, and no follow-up for a workflow or host gap.
- The corpus pins for Decision 8's wording in the three briefs.

`tests/test_run_delivery_compounding.py`:
- A record with every story merged, every line met and no decision: a pass journals no `delivery.closed`. The pass
  log, the status line and "Needs you" name the compound-learning decision. The friction stub exists at the path
  "Needs you" prints.
- `lead-compound --required --ref docs/solutions/x.md` releases the close on the next pass, and so does
  `lead-compound --not-required --why "nothing new"`. The status line each prints no longer names the decision.
- Refused, each exiting non-zero and journalling no `compounding.recorded`:
  - `--required` with no reference, and with an empty one;
  - `--not-required` with no `--why`, and with an empty one;
  - any run while a story is still unmerged, naming it.
- With a story a plan change removed stopped in the queue and every other story merged, `lead-compound` journals and
  the next pass releases the close.
- With a story still unmerged, a pass writes no `friction.md`.
- The stub across passes: the lead adds text under "What was hard", then notes a gap, then a pass runs. The new gap is
  in "Gaps noted" and the lead's text is unchanged. The archive holds the stub, and `closed.json` names the archived
  copy.
- A close whose stub no pass wrote, because its last story merged and the decision was recorded in one pass, still
  archives a stub that `closed.json` names.
- `lead-close` with `delivery.closed` journalled by hand and no decision exits non-zero naming it, before it stops
  anything. `--without-compounding "operator closed early"` journals the skipped decision and closes, and the reason
  is in its summary.
- With the supervisor running, `delivery.closed` journalled by hand and no decision: the next pass archives nothing
  and names the decision.
- A fixture record whose close finished before this change, with no `compounding.recorded`, reads as closed in
  `closed_status` and `close_progress`. One stopped partway with no decision gets `lead-compound` from
  `finishing_command`.
- A delivery reopened twice holds after each reopening until a decision later than that reopening.
- The epic's acceptance, with the decision recorded first so the line is the only hold: every story merged and one
  lead-held line unmet, so no close; `lead-finish` releases it on the next pass. And with every line met and no
  decision, no close; `lead-compound` releases it.
- The corpus pins for Decision 13's order.
- Existing close paths: every test in `tests/test_run_delivery_supervisor.py` and `tests/test_run_delivery.py` that
  drives a record to `delivery.closed` records its decision with `lead-compound` first, and keeps its
  `delivery.closed` assertion. Their friction-note assertions, the stop message's path among them, move to the
  archived copy `closed.json` names. None is deleted.

`tests/fixtures/journal-kinds/vectors.json`:
- `finish.met` with and without `evidence` and `by`;
- `gap.noted` for each class, with and without `occurrenceOf`;
- `compounding.recorded` for each outcome;
- a `gap.noted` with an unknown class, rejected.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3614, V26-3477 | `tests/test_orchestration_state.py`, `tests/test_run_delivery_finish.py` |
| 1 | 2 | V26-3571 | `skills/run-delivery/scripts/gaps.py`, `skills/run-delivery/templates/lane-brief.md`, `tests/test_run_delivery_gaps.py` |
| 2 | 1 | V26-3561 | `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/references/journal-kinds.json`, `tests/fixtures/journal-kinds/vectors.json`, `tests/test_run_delivery_compounding.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery.py` |

At tier 1 the two lanes share six files. The lead grants each lane disjoint regions in them with
`lead-own --region` against the integration base's head at `lead-init`, anchored as follows:

- `delivery_state.py`: lane 1 `_assign`, where the `--set` refusal is raised, and `main`'s parser registration
  through its dispatch, where the combined operation's function and command are added; lane 2 the lane-writer
  allowlist in `append_journal`.

- `run_delivery.py`
  - Lane 1: `waits_on` through `supervisor_line`; `journal_finish_met`, where `lead-finish` is added; and the
    lead-command registrations from `lead-decide` to `lead-close` in `main`.
  - Lane 2: the `import pr_review_gates` and `import tracker_publisher` lines; `followups_step` through `close_out`;
    and the lane-command registrations from `review-prepare` to `review-reduce` in `main`.
- `SKILL.md`: lane 1 the `lead-decide` row; lane 2 the `handoff` row. Each lane adds its command's row inside its
  span.
- `orchestration-guide.md`: lane 1 "6. Check the finish lines live"; lane 2 "Lead checklist" and "Lane brief
  template".
- `journal-kinds.json` and `vectors.json`: lane 1 the `finish.met` entries; lane 2 the `blocker.recorded` entries,
  beside which it adds `gap.noted`.

Lane 2 holds the `provenance.lock.json` listing, which gains `skills/run-delivery/scripts/gaps.py`. Both lanes
refresh digests in `catalog.json`, `profiles/*` and `provenance.lock.json` as the repository's rules say for a
releasable skill change; those are regenerable files under the listing guard. Once V26-3571 merges, the lead revokes
lane 2's regions and grants lane 1 the tier-2 row's files whole.

At tier 1 no lane owns `tests/test_run_delivery_supervisor.py` or `tests/test_run_delivery.py`. If a tier-1 change
breaks an existing assertion there, the lane asks the lead for a region over that assertion and fixes it in the same
change. An assertion is never left red or deleted.

No ports, browser sessions or databases are shared. Both lanes' supervisor fixtures run in their own temporary
folders.

## Stories

### V26-3614. Give the lead a command that marks a finish line met, with its evidence

Lane 1, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/orchestrate-work/references/journal-kinds.json`, `skills/orchestrate-work/references/orchestration-guide.md`,
`tests/fixtures/journal-kinds/vectors.json`, `tests/test_orchestration_state.py` and
`tests/test_run_delivery_finish.py`.

Implement Decisions 1 to 3. Name `lead-finish` in `SKILL.md` and in the guide's "6. Check the finish lines live".

### V26-3477. Keep external finish lines pending and retain only final review deferrals at close-out

Lane 1, tier 1, after V26-3614. Characterization. Files: `tests/test_run_delivery_finish.py`.

Decision 4: hand off with no change, the evidence naming each criterion and the test that shows it.

### V26-3571. Note gaps as they happen, and route workflow gaps to the workflow, not the adopter

Lane 2, tier 1, not blocked. Test-first. Files: `skills/run-delivery/scripts/gaps.py`,
`skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/SKILL.md`,
`skills/run-delivery/templates/lane-brief.md`, `skills/orchestrate-work/scripts/delivery_state.py`,
`skills/orchestrate-work/references/journal-kinds.json`,
`skills/orchestrate-work/references/orchestration-guide.md`, `tests/fixtures/journal-kinds/vectors.json` and
`tests/test_run_delivery_gaps.py`.

Implement Decisions 5 to 8.

### V26-3561. Make a delivery's close wait for its compound-learning decision

Lane 1, tier 2, after V26-3614 and V26-3571. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/SKILL.md`, `skills/orchestrate-work/references/orchestration-guide.md`,
`skills/orchestrate-work/references/journal-kinds.json`, `tests/fixtures/journal-kinds/vectors.json`,
`tests/test_run_delivery_compounding.py`, `tests/test_run_delivery_supervisor.py` and `tests/test_run_delivery.py`.

Implement Decisions 9 to 14.
