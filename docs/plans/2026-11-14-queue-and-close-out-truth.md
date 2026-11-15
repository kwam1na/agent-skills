---
reviewSlug: queue-and-close-out-truth
---

# Make the merge queue and the close-out tell the truth

Epic V26-3305, first delivery. The epic has twelve children. This delivery takes the seven that correct what the
queue, the close-out or the views report: V26-3300, V26-3258, V26-3170, V26-3340, V26-3271, V26-3424 and V26-3302.
The other five add mechanisms and are a later delivery (Out of scope). Each story was checked against `main` at
`0879492` on 2026-11-14, and each section says what that check found.

Start from `main` at `0879492`. The integration base carries the verified `linear-v2.3.0` release, cut from that
commit on 2026-11-14 so that lanes can share a file. Deliver on the integration branch `integrate/queue-truth`, so delivery commits and the record's bookkeeping do not move `main`. The generation that
delivers this work is not the one that will contain it: a change made in `skills/` shows in a delivery here only after
a release holding it is applied.

## Finish line

- Both plan lenses align.
- Each of the seven stories meets its tracker criteria and lands on `integrate/queue-truth` through the merge queue,
  or closes with its reason recorded.
- The integration reaches `main` through a reviewed change request. Before it does, the full local suite passes on
  macOS with Python 3.11 at the integration candidate, run with `--workers 4 --budget tests/suite-budget.json`, and
  the suite stays inside the budget.
- CI's Ubuntu and `cut-release` jobs pass for that push to `main`.

## Decisions

The operator made the first four on 2026-11-14. The rest are this plan's answers to questions the tickets leave open,
and the operator approves them with the plan.

1. Two deliveries. This one takes the seven stories above.
2. An integration base, `integrate/queue-truth`, with `main` as its integration target.
3. The base keeps the `focused` posture, so no close-out or post-merge `fullHealth` runs during the delivery. The
   full suite runs once, at final integration, and is this delivery's gate.
4. Lanes may push and open draft change requests, and the lead may mark them ready.
5. A stopped base rerun is one whose process died by a signal (a negative return code), one that exited with a
   status that reports a signal (128 plus a signal's number, from 129 up to 128 plus the platform's highest
   signal, as `scripts/run-tests.py` exits 130 when it is interrupted or terminated), or one that ended without a verdict because the runner itself was interrupted. A
   status of 128 itself, or one above that range, is a failure. A check that exits with a status in the range and
   no signal sent cannot be told from one that was stopped, and reads as stopped; Decision 6's bound covers it. A
   stopped rerun is not recorded as a base rerun's result, failed or passed. A rerun that times out is still a failure, as
   `test_a_base_rerun_that_times_out_is_never_retried_even_under_a_whole_check_flake` pins today.
6. After a stopped rerun the candidate goes back to `queued` and is checked again without the lead, once for each
   pair of candidate head and base head. The queue is not held, and the lead's wait does not wake for
   `base.rerun.stopped`. A second stopped rerun at the same pair stops the candidate with a reason of its own,
   `base_rerun_stopped`, and journals `candidate.stopped`, which the wait already wakes on, with no `base.broken`.
   The reason joins the result schema's list and the guide's table of what the lead does for each reason.
7. The lead names a repair on its queue row, through the state tool, and the row's field is a record-shape addition.
   While the base awaits repair, `serve` takes only that row, alone and never in a batch. Its merge journals
   `base.repaired`, naming the candidate, and clears the mark. A `base.repaired` journalled any other way clears the
   mark too, and the lead withdraws a mark through the command that sets it.
8. When `base.repaired` is journalled, by a repair's merge, by the lead or by a `fullHealth` pass, a candidate that
   stopped with `base_broken` on the head that broke goes back to `queued`. Nothing else stopped is requeued. Only
   a row at `queued` can be named as the repair.
9. Batch members define one check alike when `check_definition`'s digest matches. The first member's reasons and
   title are the ones kept.
10. The base rerun's cross-talk change is wording only: the stop's detail and the guide name it as possible. The
    verdict does not change.
11. A finish line whose `covers` names no criteria and no proofs is the lead's. The supervisor never marks it met,
    and it holds the close until the lead marks it. A line with no `covers` key still means every criterion, as
    delivery-views documents, and `doctor --delivery` warns of one.
12. The supervisor marks a finish line met once. A line it already journalled as met and now finds unmarked was
    unmarked by the lead, and stays so.
13. A deferred finding is fixed when a later round of the same lens lists it in `resolvedFindings` with the state
    `closed`, `fixed` or `withdrawn`. One listed as `open` or `deferred`, listed by another lens, or not listed stays
    deferred. The verification brief stops excusing earlier deferred findings: a lens re-checks each one and lists
    those it finds fixed.
14. A delivery declares `"noDeferrals": true` in its delivery file. V26-3340 and V26-3424 both read it.
15. Under `noDeferrals`, a finding still deferred when its story merges is journalled as a blocker and shown in
    "Needs you". No follow-up is filed for it, and it holds the close until the lead resolves it with `lead-resolve`.
16. An evidence note is an evidence string that contains `Declined`, `not filed` or `surviving mutant`, compared
    without regard to case. Under `noDeferrals` the round brief tells each lens to report that it declined nothing,
    left nothing unfiled and left no mutant alive in the words "nothing left unfixed", which hold none of the three
    phrases, and to use the three phrases only for something left unfixed. That brief wording is pinned. `lead-accept` prints the last round's notes under `noDeferrals` only.
    Accepting a note by name is not built.
17. A raised finding keeps how it was filed, as a new `filed` field beside its `state`, through both projections.
    The round card's pill reads "Approved with N deferrable notes", or "Approved with 1 deferrable note" for one,
    and each such row reads "Deferred" with the severity words the card already uses.
18. V26-3302 is delivered as documentation: its behaviour landed with V26-3358.
19. Three lanes work at once and share four text files by region, which `linear-v2.3.0` supports and no delivery
    here has used before. The lead grants each region in the record with `lead-own --region` after `lead-init`,
    against the base head at that moment, and regrants one against the current base after each merge that edits
    inside it, or when an edit makes its mapping ambiguous. A lane refreshes `provenance.lock.json`'s digests without owning it. Moving a story to
    another lane is the lead's recorded decision.

## Out of scope

- V26-3425, V26-3264, V26-3319, V26-3318 and V26-3320, which stay open under the epic for a later delivery.
- A stopped post-merge `fullHealth`, which records no result today.
- `wait` waking on `base.verified`, and a merge after `delivery.closed` restarting `fullHealth`, both raised in
  V26-3170's comments.
- A forge adapter without `get`, or a queue row with no recorded change request, reading as unmerged at cleanup.
- Accepting an evidence note by name at `lead-accept`.
- A release tag. The batch release that carries this work is cut separately.

## Approval Handoffs

- The operator approves this plan, with its decisions, before `lead-setup`.
- The operator approves the final integration's change request to `main`.

## Execution posture and sensors

Every story but V26-3302 is test-first. V26-3302 is sensor-only. A lane runs its story's test modules through
`scripts/run-tests.py -p <module>` and the two gates, and never the full suite. The lead selects each candidate's
checks from the modules its section names, and the two standing guards join every selection.

A row that asserts new behaviour is proven red against the code before its change. A row that asserts behaviour
kept passes on today's code, so it is proven red against an over-correcting mutant of the new code, reverted
afterwards. The mutants include: every non-zero rerun classified as stopped; `deferred_findings` ignoring the last
round; a missing `covers` key treated as the lead's; batching on a check's id alone; the pill's count shown on every
approval; the hand-off refusal applied without the setting; `select` comparing whole check objects; `noDeferrals`
read by the key's presence and not its value; every round's notes read at hand-off; and any empty `criteria`
treated as the lead's. A wording deliverable has a presence pin, proven by
removing the phrase, beside any absence pin. A proof that mutates a file `provenance.lock.json`
covers refreshes that file's digest with the mutation, as `AGENTS.md` says. When a story changes a releasable skill,
it refreshes that skill's provenance digest through `scripts/update-provenance.py`; the catalog's and the profiles'
wording is the lead's, as Units and order says.

V26-3300 and V26-3258 each add to the record's shape: a journal kind and a queue row field. Search the existing
tests for literal lists and counts of journal kinds and row fields, and update those pins in the same story. The
kinds are listed in `skills/orchestrate-work/references/schemas.json`,
`skills/run-delivery/scripts/tracker_publisher.py`, `skills/delivery-views/assets/line-map.js` and the guide.

V26-3340 withdraws a fallback from a brief. Search by meaning across every skill in the profile for wording that
excuses a lens from re-checking an earlier deferred finding, and pin its absence with its synonyms. Pin the
replacement rule's presence beside it, in the verification brief and in the review contract, through
`tests/test_round_brief_template_corpus.py` and `tests/test_review_bound_corpus.py`.

The suite's time budget binds this delivery: a story that adds tests keeps the suite inside
`tests/suite-budget.json`, and a new test class that keeps to its own folders, ports and processes is `run_quietly`.
A story that adds a fixture reuses one its class already builds where it can.

No story needs a shared port, a browser session or a database. Fixtures use their own temporary repositories.

## Units and order

Three lanes run at once. Lane 1 takes the merge queue's stories, lane 2 the close-out's and the hand-off's, and
lane 3 the round card. The table holds each lane's whole-file claims.

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3300, V26-3258 | `skills/orchestrate-work/scripts/`, `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/SKILL.md`, `skills/run-delivery/scripts/tracker_publisher.py`, `skills/delivery-views/assets/line-map.js`, `tests/test_merge_queue.py`, `tests/test_orchestration_state.py`, `tests/test_orchestration_adopter.py`, `tests/test_orchestration_render.py`, `tests/test_lane_rows.py`, `tests/test_lead_wait.py`, `tests/test_run_delivery_tracker.py`, `tests/delivery_views_page/line-map-kinds.test.mjs`, `docs/solutions/best-practices/a-stopped-base-rerun-is-not-a-broken-base-2026-11-14.md` |
| 1 | 2 | V26-3170, V26-3340, V26-3424, V26-3302 | `skills/run-delivery/SKILL.md`, `skills/run-delivery/references/`, `skills/run-delivery/templates/`, `skills/review-work/`, `skills/obtain-review/`, `tests/test_run_delivery.py`, `tests/test_round_brief_template_corpus.py`, `tests/test_review_bound_corpus.py`, `tests/test_review_and_compounding.py`, `docs/solutions/best-practices/cleanup-of-a-squash-merged-story-2026-11-14.md` |
| 1 | 3 | V26-3271 | `skills/delivery-views/assets/run-summary.js`, `skills/delivery-views/assets/run-summary.css`, `skills/delivery-views/scripts/review_projection.py`, `skills/delivery-views/scripts/render.py`, `tests/test_delivery_views_page.py`, `tests/delivery_views_page/golden.test.mjs`, `tests/delivery_views_page/history-golden.test.mjs`, `tests/delivery_views_page/line-map.test.mjs`, `tests/delivery_views_page/round-reports.mjs`, `tests/test_delivery_views_review_projection.py`, `tests/test_delivery_views_render.py`, `tests/test_delivery_views_history.py`, `tests/fixtures/delivery-views/` |

Four text files are shared by region (Decision 19). No lane holds a whole-file claim on any of them:

- `skills/run-delivery/scripts/run_delivery.py`: lane 1 holds the span of `needs_you`, and lane 2 the spans before
  and after it.
- `tests/test_run_delivery_supervisor.py`: lane 1 holds the span of `SupervisorPostMergeTests`, which its "Needs
  you" row joins, and lane 2 the spans before and after it.
- `skills/orchestrate-work/references/orchestration-guide.md`: lane 2 holds the spans of the finish-line wording
  and of the review result's shape and the close-out's follow-ups. Lane 1 holds the rest.
- `skills/delivery-views/SKILL.md`: lane 2 holds the span of the `covers` wording, and lane 3 the rest.

A region covers edits inside it only. A story that has to add lines at a span's edge, or outside its lane's spans,
asks the lead, who widens the claim or moves it. Creating a file takes a whole-file claim, and the path of every
file a story creates is in the table: the page test and the solution note of V26-3300, and the solution note of
V26-3302. No lane claims a folder that another lane's new file would fall in.

Every story that changes a file under `skills/` refreshes `provenance.lock.json` with the provenance writer. The
queue regenerates it at sync, so two lanes' refreshes do not conflict. No story adds or removes a listed output.
No story edits `catalog.json` or `profiles/`, which no lane owns: the lead writes the catalog's and the profiles'
wording for this delivery's skill changes once, on the integration branch, before the change request to `main`.

## Stories

### V26-3300. Tell a stopped base rerun from a failed one, and record the hazard

Lane 1, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/`, `skills/run-delivery/scripts/tracker_publisher.py`,
`skills/delivery-views/assets/line-map.js`, `tests/test_merge_queue.py`, `tests/test_orchestration_state.py`,
`tests/test_orchestration_adopter.py`, `tests/test_run_delivery_tracker.py`, `tests/test_lead_wait.py`,
`tests/delivery_views_page/line-map-kinds.test.mjs` and
`docs/solutions/best-practices/a-stopped-base-rerun-is-not-a-broken-base-2026-11-14.md`.

Found on `main`: `Runner.rerun_on_base` in `merge_queue.py` reads any non-zero return code as a failure, so a rerun
the lead stopped journals `base.broken` and holds every candidate.

Classify a stopped rerun by Decision 5. Journal `base.rerun.stopped` for it, put the candidate back to `queued`,
journal no `base.broken` and leave the queue free, within Decision 6's bound. A rerun
that fails, or times out, still journals `base.broken`. Register the journal kind wherever kinds are listed. Write
the solution note: the symptom, `base_broken: <check> also fails on <base>` right after a stop, the cause, and the
recovery for a lead on a generation without the fix.

Scenarios. Each fixture check writes its own pid, and the test stops it by that pid, in a session of its own. Each
stopped row asserts `base.rerun.stopped`, no `base.broken`, the candidate at `queued`, and `serve` taking the next
candidate:

- a check that dies from the signal;
- a check that traps SIGTERM and exits 143, and one that traps SIGINT and exits 130, as `scripts/run-tests.py` does;
- a rerun that ends without a verdict because the runner is interrupted;
- a rerun stopped during its flake retry;
- a check that exits 137 with no signal sent.

No stopped row adds an entry of any result to `meta.baseReruns`, and `select` at that base head prints no warning for
the check. A rerun that exits 1 by itself still breaks the base, and so does one that exits 128, one that exits
255, and one that times out. A rerun stopped twice at the
same candidate and base heads leaves the candidate stopped as `base_rerun_stopped`, journals `candidate.stopped`
and no `base.broken`, and `serve` takes the next candidate. A new base head lets the candidate be put back once
more, and so does a new candidate head on the same base head. The guide's reason table has a row for
`base_rerun_stopped`, pinned. `wait` does not return on `base.rerun.stopped`. The line map shows the new kind's label, in a page test file of
this story's own.

### V26-3258. Let a base repair go through a held queue first, and batch selections by what they run

Lane 1, tier 1, after V26-3300. Test-first. Files: `skills/orchestrate-work/`, `skills/run-delivery/scripts/run_delivery.py`,
`tests/test_merge_queue.py`, `tests/test_orchestration_state.py` and `tests/test_run_delivery_supervisor.py`. In
`run_delivery.py` and the supervisor's tests it works inside lane 1's regions only.

Found on `main`: nothing lets a lead name a repair, so `serve` takes nothing while the base awaits repair, and the
lead journals `base.repaired` before the fix has landed. `Runner.lead_checks` compares two members' checks as whole
objects, reason text included.

Let the lead name a queued candidate as the repair (Decision 7). While the base awaits repair, `serve` runs that
candidate alone and first; its merge journals `base.repaired` and requeues the candidates the break stopped
(Decision 8). Compare batch members' checks by what runs (Decision 9), and have `select` warn when another queued
selection defines the same check id differently. Name possible cross-talk in the base rerun's stop and in the guide
(Decision 10), with the guide saying how to keep a queue's checks apart from lanes' own runs. Say in "Needs you" and
the guide that queued candidates resume on `base.repaired` with no `--requeue`.

Scenarios, for the repair:

- A held queue holds a queued candidate whose selection would batch with the repair's, ahead of the named repair,
  and two candidates the break stopped. `serve` takes the repair alone: the other queued row is still `queued` and
  unchecked when the repair's check starts. One `base.repaired` names only the repair, the repair's mark is gone
  after its merge, and the other three are then taken.
- The same fixture also holds a candidate stopped for its own failing check and one stopped with `base_broken` on
  another base head. Both are still stopped, with their reasons unchanged, after `base.repaired`.
- A repair named while the base is healthy is refused. Two repairs named at once are refused. A row that is not
  `queued` is refused as the repair.
- A repair that fails its own checks leaves the base awaiting repair and names itself.
- After a `base.repaired` the lead journals by hand, no row carries the mark. A withdrawn mark lets another row be
  named.
- With no repair named, two candidates stopped by the break and one stopped for its own failing check, a
  `base.repaired` the lead journals by hand puts the two back to `queued`, `serve` takes them, and the third stays
  stopped with its reason unchanged.
- A `base_broken` stop's detail, read from a fixture run, names possible cross-talk. "Needs you" for a held queue
  says candidates resume on `base.repaired` with no `--requeue`. The guide's wording for both is pinned.

For batching and `select`:

- Two selections that differ only in reason text batch together, and so do two that differ only in title. The
  batched check's recorded reasons and title are the first member's.
- Two that differ in argv are refused as today, and so are two that differ only in `timeoutSeconds`.
- `select` warns on a differing definition and still records the selection. It prints no warning when another
  queued selection defines the same id with different reasons or title only.

### V26-3170. Stop a finish line with no criteria reading as met

Lane 2, tier 1, not blocked. Test-first. Files: `skills/run-delivery/`, `skills/orchestrate-work/references/orchestration-guide.md`,
`skills/delivery-views/SKILL.md`, `tests/test_run_delivery_supervisor.py` and `tests/test_run_delivery.py`.

Found on `main`: the ticket's wake on a failed `fullHealth` and its test's process scope are delivered. Its first
item remains: `finished_lines` and `journal_finish_met` in `run_delivery.py` mark a line with empty `covers` met on
the next pass, and re-mark a line the lead unmarked.

Leave a line that covers nothing to the lead (Decision 11), and mark a line met once only (Decision 12). Bring the
guides' wording in line with it.

Scenarios. In each "never" row every unit's proofs are done, so an unready fixture cannot satisfy it:

- a line with `covers: {}`, one with `covers: null`, and one with `covers: {"criteria": [], "proofs": []}`, is never journalled as met by a
  supervisor pass, and each holds the close, which names it; once the lead marks and journals it, the close follows;
- a line with `covers: {"criteria": [], "proofs": ["merged"]}` is marked by the supervisor once that proof is done;
- a line with no `covers` key, and one with `"criteria": "all"`, is marked by the supervisor when every
  criterion's proofs are done;
- a line the lead unmarks stays unmarked through later passes;
- `doctor --delivery` warns of a line with no `covers` key and does not fail;
- the orchestration guide and the delivery-views skill text carry the lead-owned-line wording, and each pin fails
  when it is removed.

### V26-3340. Stop the close-out filing follow-ups for review findings the lane already fixed

Lane 2, tier 1, after V26-3170. Test-first. Files: `skills/run-delivery/`, `skills/review-work/`, `skills/obtain-review/`,
`skills/orchestrate-work/references/orchestration-guide.md`, `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery.py`,
`tests/test_round_brief_template_corpus.py`, `tests/test_review_bound_corpus.py` and `tests/test_review_and_compounding.py`.

Found on `main`: `deferred_findings` in `run_delivery.py` reads every round's `findings` and never
`resolvedFindings`, and the verification brief says an earlier deferred finding needn't be listed there, so the
record cannot say that a deferred finding was fixed.

Make the review record say it (Decision 13), state the rule in the review contract, and have the close-out file a
follow-up only for a finding still deferred at the story's last captured round. Add the `noDeferrals` declaration
(Decision 14) and its breach (Decision 15).

Scenarios:

- a finding deferred in round 1 and listed in round 2 by its lens as `closed`, as `fixed` or as `withdrawn` files
  no follow-up, one case each;
- a finding deferred in the last captured round files exactly one;
- a finding deferred in round 1 that round 2 does not list, lists as `deferred`, or lists as `open` files exactly
  one, and so does one that only another lens's round-2 result lists as `fixed`;
- under `noDeferrals`, a finding still deferred at merge files none, is journalled as a blocker once across two
  supervisor passes, shows in "Needs you" and holds the close until resolved;
- under `noDeferrals`, a finding deferred in round 1 and listed `fixed` in round 2, and one listed `withdrawn`,
  journals no blocker and does not hold the close;
- a delivery file with `"noDeferrals": false` behaves as one without the key;
- a lens result that leaves an earlier deferred finding out of `resolvedFindings` is still a valid result;
- the verification brief and the review contract carry the re-check-and-list rule, and neither carries the old
  excuse.

### V26-3271. Say on a round card that an approval carried deferrable findings

Lane 3, tier 1, not blocked. Test-first. Files: `skills/delivery-views/assets/run-summary.js`,
`skills/delivery-views/assets/run-summary.css`, `skills/delivery-views/scripts/review_projection.py`,
`skills/delivery-views/scripts/render.py`, `skills/delivery-views/SKILL.md`, `tests/test_delivery_views_page.py`,
`tests/delivery_views_page/`, `tests/test_delivery_views_review_projection.py`, `tests/test_delivery_views_render.py`,
`tests/test_delivery_views_history.py` and `tests/fixtures/delivery-views/`.

It builds to Decision 13's rule, which this plan fixes, and so does not wait for V26-3340: its fixtures list a
fixed finding in a later round's `resolvedFindings`, as a record reads under that rule.

Found on `main`: `card()` in `run-summary.js` shows "Approved" for every approval, and both projections overwrite a
raised finding's filed state with its final one.

Keep how each raised finding was filed, and say so on the card (Decision 17). The history page inlines the same
script, so it follows.

Scenarios:

- projecting a two-round fixture's round files, one P2 and three P3 deferred in round 1 and all fixed in round 2,
  gives each of the four raised findings `filed` as `deferred` with its final state resolved, on each of the two
  projections;
- the golden for that fixture's round 1 is rendered from the projected output, not from a hand-written card input.
  It reads "Approved with 4 deferrable notes", each of the four rows reads "Deferred", and the line that says all
  four were fixed is unchanged;
- a fixture where one of the deferred findings is not fixed in round 2 still counts and marks all of them;
- a round with one deferred finding reads "Approved with 1 deferrable note";
- a round with no findings still reads "Approved", and a `notes-open` round still reads "Approved, notes open";
- a plan-review card's goldens do not move;
- each new row fails when the wording it pins is removed.

### V26-3424. Count a lens's evidence notes at hand-off under a no-deferrals ruling

Lane 2, tier 1, after V26-3340. Test-first. Files: `skills/run-delivery/`, `tests/test_run_delivery.py` and
`tests/test_run_delivery_supervisor.py`.

Found on `main`: `handoff` checks only that the last round aligned and every lens answered, and never reads a
result's `evidence`.

Under `noDeferrals`, refuse a hand-off whose last round's evidence holds a note (Decision 16), naming each note and
its lens, print the last round's notes at `lead-accept`, and have the round brief say how a lens reports that
nothing is left.

Scenarios, under `noDeferrals` unless a row says otherwise:

- a hand-off whose last round has a "Declined" note is refused naming it, and so is one with a "not filed" note,
  one with a "surviving mutant" note, and one with a lower-case "declined" note;
- a last round with two notes from two lenses is refused naming both notes and both lenses;
- a last round whose evidence says "nothing left unfixed" and also holds a "Declined" note is refused;
- a last round whose evidence says "nothing left unfixed" passes, and a brief prepared under the setting carries
  the instruction to say so, which is pinned;
- a note in an earlier round that the last round does not repeat refuses nothing;
- `lead-accept` prints each of the last round's notes in plain text with its lens, and not a note only an earlier
  round carries;
- without the setting, every hand-off above passes as today, a brief carries no such instruction, and `lead-accept`
  prints no notes.

### V26-3302. Say what cleanup does for a story its forge merged by squash

Lane 2, tier 1, after V26-3424. Sensor-only. Files: `skills/run-delivery/references/adopting.md`,
`tests/test_run_delivery.py` and `docs/solutions/best-practices/cleanup-of-a-squash-merged-story-2026-11-14.md`.

Found on `main`: V26-3358 delivered the behaviour and its tests. The adoption guide's "Merge strategy" paragraph
still says a squashed story's cleanup reports it unmerged and leaves its checkout, and no solution note records the
symptom.

Correct the paragraph: cleanup removes a squash-merged story's checkout when its recorded change request is merged
and every tip is at or below the merged head, and still reports unmerged for a forge adapter without `get` or a row
with no recorded change request. Write the solution note with the symptom and the manual recovery. No runtime
behaviour changes.

Scenarios: the guide's pins hold the corrected wording and fail when it is removed, and an absence pin holds that
the old sentence, that cleanup reports a squashed story unmerged and leaves its checkout, is gone. The note has the
corpus's shape.
