---
reviewSlug: workflow-reliability
---

# Make the delivery workflow reliable

Thirty-three backlog tickets, each with its own scope in the tracker, delivered under one epic. The tickets are binding for what each one does,
apart from the changes under Decisions. This plan decides only what they cannot: order, file ownership and how the work reaches `main`.

## Finish line

- Each ticket is merged to `main` through the merge queue, or closed with its recorded reason where this plan allows that, and `main`'s CI on the
  last merged commit passes on the Ubuntu job and `cut-release`, with the run linked.
- The full local suite passes on macOS with Python 3.11 at that commit.
- V26-3105's real `ready`, `merge`, tracker snapshot, forge snapshot and online `doctor` results are recorded on its ticket.
- Every deferral has an open follow-up item, and the epic and its items are closed with this evidence.

## Out of scope

- The release and lifecycle tickets (V26-2803, V26-2804, V26-2805, V26-3096, V26-3097, V26-3098), which come next as their own delivery.
- The bundled runtime tickets (V26-2383, V26-2352, V26-2348, V26-2335, V26-2313, V26-2838).
- The parked tickets (V26-2828, V26-3080, V26-2302, V26-3083, V26-3070, V26-3071, V26-3072).
- Athena's side of V26-2986 and V26-2807: the `github:pr-merge` helper, titles from `harness:plan`, and V26-2807's proof-merge record, which this
  binding can't produce because its provider is null.
- Cutting a release, which waits for the operator's word.

## Units and order

Three lanes. Tiers are ownership hand-overs rather than calendar stages, and within a tier a lane works in the order listed.

| Tier | Lane | Tickets | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3093, V26-3099 | `delivery_state.py`, `schemas.json`, `orchestrate-work` `SKILL.md`, `tests/test_orchestration_state.py`, `tests/test_registry.py` |
| 1 | 2 | V26-3086, V26-2951 | `run_delivery.py`, `tests/test_run_delivery.py` |
| 1 | 3 | V26-3092, V26-3101, V26-3102 | `tests/test_merge_queue.py`, `scripts/run-tests.py` and its test, the four older solution notes, `tests/test_compound_learning_corpus.py`, `.agents/orchestration.json` |
| 2 | 1 | V26-3094, V26-3064, V26-2986 | `merge_queue.py`, `tests/test_merge_queue.py`, `schemas.json` and `tests/test_registry.py` until V26-3094 merges, `scripts/update-provenance.py`, `tests/test_update_provenance.py` |
| 2 | 2 | V26-3108, V26-3095, V26-3103, V26-3091 | `run_delivery.py`, `tests/test_run_delivery.py`, `delivery_state.py`, `schemas.json` and `tests/test_registry.py` after V26-3094, `tests/test_orchestration_state.py`, `tests/test_lane_rows.py`, `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery_tracker.py`, the recorded-pid and gate-condition notes |
| 2 | 3 | V26-3110, V26-3068 | after V26-3091 `skills/run-delivery/adapters/`, `references/adopting.md`, `tests/test_run_delivery.py`, and `catalog.json` and the profiles if an output is added |
| 3 | 1 | V26-3089, V26-3005, V26-2807 | `merge_queue.py`, `tests/test_merge_queue.py`, `delivery_state.py`, `schemas.json`, `tests/test_orchestration_state.py`, `tests/test_registry.py`, `skills/delivery-views/`, its page tests, `references/orchestration-guide.md`, `skills/run-delivery/templates/lane-brief.md` |
| 3 | 2 | V26-3085, V26-3104, V26-3087, V26-3074 | `run_delivery.py`, `tests/test_run_delivery.py`, `tests/test_run_delivery_plan_review.py`, `skills/run-delivery/templates/round-brief.md`, `skills/obtain-review/references/round-brief-template.md`, `tests/test_round_brief_template_corpus.py` |
| 3 | 3 | V26-3100 | `agent_skills/generations.py`, `tests/test_lifecycle.py` |
| 4 | 2 | V26-3065, V26-3066, V26-3067, V26-3069, V26-3073 | `run_delivery.py`, `tests/test_run_delivery.py`, the tracker-properties validator and its test |
| 5 | 1 | V26-3088, V26-3107, V26-3109 | `merge_queue.py`, `tests/test_merge_queue.py`, `run_delivery.py`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`, `delivery_state.py`, `schemas.json`, `tests/test_orchestration_state.py`, `tests/test_registry.py`, `skills/delivery-views/`, its page tests, `references/orchestration-guide.md`, `scripts/release_channel.py`, `tests/test_release_channel.py`, `.agents/orchestration.json` |
| 6 | lead | V26-3105 | `.agents/delivery/candidate.py`, the views' `meta.planFile` flag |

**Moving ownership.** No two lanes hold one file at a time, and a file passes when the ticket holding it merges. `lead-accept` enforces a lane's
`owns`, and `doctor --delivery` refuses an overlap, so the lead rewrites `lanes.*.owns` in the delivery file at each hand-over, and commits it locally, where `lead-accept` reads it, because `lead-close` and `lead-plan` refuse
uncommitted changes. It pushes pending rewrites only while no candidate is synced or checking, because a push moves the queue's base, and before `lead-close`. `provenance.lock.json` moves to the lane being accepted immediately before each `lead-accept`, because a lane commits a refreshed
lock for its own gate. A lane that finds it needs a file its row doesn't list raises it with the lead, who moves it or re-sequences, before hand-off.
A ticket's test-pin updates belong to that ticket, including the `schemas.json` shape pins and the tracker updates table when a journal kind is added.

## Stories

Each story lists its lane, tier, what blocks it, the files it changes and its sensors. Lane ownership is the table's.

### V26-3093. Keep a planned story's started flag through a rejected hand-off

Lane 1, tier 1, not blocked. Files: `delivery_state.py`, `schemas.json`. Sensors: `tests/test_orchestration_state.py`, `tests/test_registry.py`.

### V26-3099. End the queue's waitingOn by who set it, not by focusBy

Lane 1, tier 1, after V26-3093. Files: `delivery_state.py`, `schemas.json`, `orchestrate-work` `SKILL.md`. Sensors: `tests/test_orchestration_state.py`,
`tests/test_registry.py`.

### V26-3086. Keep run-delivery tests' cached release alive across test modules

Lane 2, tier 1, not blocked. Start by running the ticket's two-module command in both orders. If it passes both ways, close with no change and record
why. Files: `tests/test_run_delivery.py`. Sensors: that command.

### V26-2951. run-delivery characterization tests leave in-app serve processes running

Lane 2, tier 1, after V26-3086. Reproduce first, with a real serve process orphaned by a real kill. Files: `tests/test_run_delivery.py`, and
`run_delivery.py` if `app-check` itself orphans it. Sensors: `tests/test_run_delivery.py`, and the ticket's no-`http.server`-left check.

### V26-3092. Merge-queue tests fail intermittently on a loaded machine

Lane 3, tier 1, not blocked. The Bounded flake work decision applies. Files: `tests/test_merge_queue.py`, and `.agents/orchestration.json` only for
a declaration that decision allows. Sensors: the two named tests, under the stated load.

### V26-3101. Stop run-tests.py losing a SIGTERM during discovery

Lane 3, tier 1, after V26-3092. Files: `scripts/run-tests.py` and its test. Sensors: that test.

### V26-3102. Bring the older solution notes up to the store's format

Lane 3, tier 1, after V26-3101. Files: the four notes, `tests/test_compound_learning_corpus.py`. Sensors: that test.

### V26-3094. Don't drop a candidate's new outputs when the merge queue resolves a regenerable conflict

Lane 1, tier 2, after V26-3092. The V26-3094 decision applies. Files: `merge_queue.py`, `schemas.json`, `scripts/update-provenance.py`.
Sensors: `tests/test_merge_queue.py`, `tests/test_update_provenance.py`, `tests/test_registry.py`.

### V26-3064. Have the merge queue check the PR review before it merges

Lane 1, tier 2, after V26-3094. A row where a current review merges goes with the refusal rows, so a step that always refuses fails. Files:
`merge_queue.py`. Sensors: `tests/test_merge_queue.py`.

### V26-2986. Merge queue reports merge_refused after a successful github:pr-merge

Lane 1, tier 2, after V26-3064. This repository's half only, under the Partial tickets decision. A row puts a secret in the merge command's stderr and
proves it doesn't reach the stop detail unscrubbed. Files: `merge_queue.py`. Sensors: `tests/test_merge_queue.py`.

### V26-3108. Keep a lane's own word on the row the queue rests, so cleanup can't miss it

Lane 2, tier 2, after V26-3093, V26-3099 and V26-3094. Files: `delivery_state.py`, `run_delivery.py`, `schemas.json`. Sensors: `tests/test_lane_rows.py`,
`tests/test_run_delivery_supervisor.py`, `tests/test_registry.py`.

### V26-3095. Close the test gaps left at the review bound in the lane and lead commands

Lane 2, tier 2, after V26-3108. Files: `tests/test_run_delivery_supervisor.py`, `tests/test_run_delivery.py`, `tests/test_run_delivery_tracker.py`.
Sensors: those tests.

### V26-3103. Make capture-review say what it refused, and keep cards across a fixture commit

Lane 2, tier 2, after V26-3095. Propose the carry-over to the lead before any code. Files: `delivery_state.py`, `tests/test_orchestration_state.py`, and
`run_delivery.py` if the carry-over needs a command. Sensors: `tests/test_orchestration_state.py`.

### V26-3091. Tighten the recorded-pid solution note and test the pid -1 and pid 1 guard

Lane 2, tier 2, after V26-3103 and V26-3102. Files: the recorded-pid and gate-condition notes, `tests/test_run_delivery.py`, and the supervisor test its
comment adds. Sensors: `tests/test_run_delivery.py`, `tests/test_compound_learning_corpus.py`.

### V26-3109. Pin the line map's remaining closed-delivery behaviours with tests, and finish its wording

Lane 1, tier 5, after V26-3107. One story with one hand-off, holding the tests, the wording, the flakes and the close fix, because the state tool refuses a
second hand-off of a merged story. The V26-3109 and Bounded flake work decisions apply. Files: `skills/delivery-views/`, its page tests,
`scripts/release_channel.py`, `run_delivery.py`, `delivery_state.py`, `schemas.json`, and `.agents/orchestration.json` only for a declaration that decision
allows. Sensors: `tests/test_delivery_views_page.py`, `tests/test_release_channel.py`, `tests/test_run_delivery.py`, `tests/test_run_delivery_supervisor.py`,
`tests/test_orchestration_state.py`, `tests/test_registry.py`.

### V26-3110. Forge adapters' merge --source should refuse a pull request that targets another base

Lane 3, tier 2, after V26-3102 and V26-3091. `--destination` stays optional. Files: `skills/run-delivery/adapters/`, `references/adopting.md`,
`tests/test_run_delivery.py`. Sensors: `tests/test_run_delivery.py`.

### V26-3068. Ship a ready-made candidate command for common forges

Lane 3, tier 2, after V26-3110. It adds a listed output, so `catalog.json`, the profiles and the lock change with it. The queue runs from the installed
generation, which still takes the base side of a lock conflict, so a conflict at sync would drop the new output and `validate-registry` would stop it.
Expect that, and re-add the output if it happens. Files: `skills/run-delivery/adapters/`, `references/adopting.md`, `tests/test_run_delivery.py`.
Sensors: `tests/test_run_delivery.py`, `scripts/validate-registry.py`.

### V26-3089. Wake the lead when a check fails, not when the base rerun ends

Lane 1, tier 3, after V26-3108, V26-3103 and V26-2986. Files: `merge_queue.py`, `delivery_state.py`, `schemas.json`, `skills/delivery-views/`,
`references/orchestration-guide.md`. Sensors: `tests/test_merge_queue.py`, `tests/test_orchestration_state.py`, `tests/test_delivery_views_page.py`.

### V26-3005. orchestrate-work: in-flight planned work carries its run id before hand-off

Lane 1, tier 3, after V26-3089. Files: `delivery_state.py`, `schemas.json`, `skills/delivery-views/`, `skills/run-delivery/templates/lane-brief.md`.
Sensors: `tests/test_orchestration_state.py`, `tests/test_delivery_views_page.py`.

### V26-2807. Give each merge check a readable name the live view can show

Lane 1, tier 3, after V26-3005. This repository's half only, under the Partial tickets decision. Files: `merge_queue.py`, `schemas.json`,
`skills/delivery-views/`, `references/orchestration-guide.md`. Sensors: `tests/test_merge_queue.py`, `tests/test_orchestration_state.py`,
`tests/test_delivery_views_page.py`.

### V26-3085. Keep the two round briefs from drifting apart

Lane 2, tier 3, after V26-3091 and V26-3068. Files: `skills/run-delivery/templates/round-brief.md`, `skills/obtain-review/references/round-brief-template.md`,
`tests/test_run_delivery.py`, `tests/test_round_brief_template_corpus.py`. Sensors: those tests.

### V26-3104. Close the review round gaps a run-delivery rehearsal found

Lane 2, tier 3, after V26-3085. Start by checking the first item against the current brief, which appears to say the opposite. Propose the other two items
to the lead before any code. Files: `run_delivery.py`, `skills/run-delivery/templates/round-brief.md`, `tests/test_run_delivery.py`. Sensors:
`tests/test_run_delivery.py`.

### V26-3087. Give a lens one checkout per delivery instead of one per round

Lane 2, tier 3, after V26-3104. The ticket has no criterion, so this plan states it: a lens keeps one checkout across a delivery's rounds, at each round
that checkout holds exactly that round's candidate, and the environment is re-prepared when a dependency changed. A row plants an untracked file, an ignored
file and a dependency change between rounds. Files: `run_delivery.py`,
`tests/test_run_delivery.py`. Sensors: `tests/test_run_delivery.py`.

### V26-3074. Have plan-review write a validated review record

Lane 2, tier 3, after V26-3087. Files: `run_delivery.py`, `tests/test_run_delivery_plan_review.py`. Sensors: `tests/test_run_delivery_plan_review.py`.

### V26-3100. Keep the suite's temp churn from creeping back

Lane 3, tier 3, not blocked. The V26-3100 decision applies. Files: `agent_skills/generations.py`, `tests/test_lifecycle.py`. Sensors: `tests/test_lifecycle.py`.

### V26-3065. Serve the lead's integration build under the in-app lease

Lane 2, tier 4, after V26-3074. The test binds a real listener on an ephemeral port it owns and asserts the named pid is that listener's. Files:
`run_delivery.py`, `tests/test_run_delivery.py`. Sensors: `tests/test_run_delivery.py`.

### V26-3066. Record the evidence page's per-story notes in the record

Lane 2, tier 4, after V26-3065. Files: `run_delivery.py`, `tests/test_run_delivery.py`. Sensors: `tests/test_run_delivery.py`.

### V26-3067. Make ship's tooling strip robust to later tooling changes and merged bases

Lane 2, tier 4, after V26-3066. Files: `run_delivery.py`, `tests/test_run_delivery.py`. Sensors: `tests/test_run_delivery.py`.

### V26-3069. Warn early when a lane's work needs a file another lane owns

Lane 2, tier 4, after V26-3067. Files: `run_delivery.py`, `tests/test_run_delivery.py`. Sensors: `tests/test_run_delivery.py`.

### V26-3073. Small setup fixes: build without a story key, tracker validation without AGENTS.md

Lane 2, tier 4, after V26-3069. Files: `run_delivery.py`, the tracker-properties validator and their tests. Sensors: those tests.

### V26-3088. Prove the merge queue's candidate step at setup, and show why it failed

Lane 1, tier 5, after V26-3073 and V26-2807. Files: `merge_queue.py`, `run_delivery.py`, `references/orchestration-guide.md`, `tests/test_merge_queue.py`,
`tests/test_run_delivery.py`. Sensors: those tests and `tests/test_orchestration_adopter.py`.

### V26-3107. Have the lead review their own integration changes before merging to the default branch

Lane 1, tier 5, after V26-3088. If the close-out refuses to merge without this review, that refusal gets its own behaviour row, since a corpus pin proves
only that the words are there. Files: `references/orchestration-guide.md`, `run_delivery.py`, `tests/test_run_delivery.py`. Sensors: `tests/test_run_delivery.py`,
the `orchestrate-work` corpus pins.

### V26-3171. Check only the timed-out group's own pids in run-delivery's timeout test

Lane 2, added by the lead when the queue stopped with `base_broken` on it, ahead of lane 2's other work. The test's machine-wide
`pgrep -f "sleep 30"` fails whenever a lane's own test run has a `sleep 30` alive, so it broke the base for V26-2951's check. Files:
`tests/test_run_delivery.py`. Sensors: `tests/test_run_delivery.py`, run while an unrelated `sleep 30` is alive.

### V26-3181. Find why a SupervisorPostureTests case failed once in a loaded full run

Lane 2, added by the lead after V26-3091, because it can stop the queue as V26-3171's flake did. Lane 2 found the cause: `archive_delivery` names
the archive folder to the second and creates it without `exist_ok`, so two archives in the same second collide with `FileExistsError`. Files:
`run_delivery.py`, `tests/test_run_delivery_supervisor.py`. Sensors: `tests/test_run_delivery_supervisor.py`, including two archives in one second.

### V26-3191. Retry run-delivery's lead writes when the record moved under them

Lane 2, added by the lead after V26-3181, because it failed V26-3091's queue check: `lead_assign` patches pinned to the version it read, so a
supervisor write in between fails the guide's end-to-end test with `version_stale`. Files: `run_delivery.py`, `tests/test_run_delivery.py`.
Sensors: `tests/test_run_delivery.py`, with a write forced between read and patch.

### V26-3178. Refuse a hand-off whose --run another subject already holds

Lane 1, added by the lead while V26-2986 waits for V26-3064 to merge: a follow-up lane 1 found at V26-3005's hand-off. Files: `delivery_state.py`,
`tests/test_orchestration_state.py`. Sensors: `tests/test_orchestration_state.py`, `tests/test_registry.py`.

### V26-3105. Prove this repository's own delivery path on its first real run

The lead, tier 6, after all others. The merges and snapshots of this delivery are its evidence. The lead also fixes or declines the two small gaps. Files:
`.agents/delivery/candidate.py`, the views' `meta.planFile` flag and its page test. Sensors: `doctor` online, and `tests/test_own_deliveries.py`.

## Decisions

- **Three lanes, not four.** Review rounds and lead commands both live in `run_delivery.py`, so separate lanes for them would only take turns.
  Lane 2 carries both. The four groups stay as the epic's outcomes.
- **Straight to `main`.** The standing binding's base is `main`, so stories land there through the queue with no integration branch. That makes each
  merge real evidence for V26-3105, and leaves no integration change for V26-3107's review to cover in this delivery.
- **This delivery runs on the installed generation.** Its fixes reach the delivery only through a later release, so each lane works around the
  gaps its own tickets name. New binding keys wait for that release too, because the installed queue refuses keys it doesn't know.
- **Partial tickets.** V26-2986 here keeps the merge command's stderr in the runner's stop detail. V26-2807 here adds the optional title to the provider
  contract, the record, the runner and the generic renderer. Each closes when its half merges, and Athena's side goes to a follow-up item, filed before
  it closes, carrying the criteria left unmet.
- **V26-3094.** The binding gains an optional way to name, per regenerable path, a command that prints a version's listed outputs; for
  `provenance.lock.json` that command is `scripts/update-provenance.py`'s. The queue compares the listing at the merge base, the base and the candidate.
  If the candidate's listing changed, it stops with `source_conflict` naming the path. If only the base's changed, it takes the base side and regenerates,
  as it does today. A path with no such command regenerates as today. `AGENTS.md` lets a person take the changed side by hand; the queue is deliberately
  narrower, and never decides for a candidate. Rows, with a lock-shaped fixture, both at sync and in a batch merge: a digest-only conflict regenerates and
  merges; the candidate adds, the candidate removes, and both sides add different outputs each stop; a base-only addition regenerates; a `gen/**` path
  regenerates. A listing command that fails, prints nothing or prints something unparsable at any of the three versions, or a version without the path, stops
  with `source_conflict` naming the path and never regenerates. The digest-only and candidate-adds rows run the real `update-provenance.py` listing over a
  real-format lock, and `tests/test_update_provenance.py` proves that listing is unchanged by a digest-only edit, changes when an output is added or removed,
  reads the version it's given, and refuses a malformed lock. This repository's binding adopts the key after the release, so until then the installed
  behaviour stands, and an open follow-up item, filed when V26-3094 closes, tracks that adoption.
- **V26-3110.** `--destination` stays optional, so existing bindings keep working and this stays a minor.
- **V26-3109's stopped-partway limit is fixed on the page, by the operator's ruling.** A delivery whose close stopped partway says so and names the
  command that finishes it.
  - *What separates the cases.* The record gets two marks, each carrying the closing journal entry's id. The state tool writes a started mark in the same
    write as `delivery.closed`, so a hand-journaled close has it too. A finished mark is written when the archive has landed, in the live record and in the
    archived copy and its views, and a stop before that leaves the live record without it. Started with no finished mark for that entry, and no live close
    running, reads as stopped partway. No marks reads as an older record, closed as before. A mark for an earlier entry doesn't count, so a delivery closed,
    reopened and closed again is judged on the last close. `closed.json` isn't used, because it sits beside the flow logs rather than in what the renderer
    reads, and it's written after the final render.
  - *Which command.* The page names `lead-run --resume` when the record meets the close condition: no planned story, nothing unmerged, every finish line met
    and no busy lane. Otherwise it names `lead-close` and says that it doesn't wait for the tracker, because a resumed supervisor holds on what's unmet and never
    finishes that close. To make this hold after a reopen, `closed.json` carries the closing entry's id, `lead-run`'s refusal and `archive_step`'s done-once return
    apply only when that id is the latest `delivery.closed` entry's, and a `closed.json` with no id keeps today's behaviour. `closed_step` uses the latest entry,
    and journals a new one when the latest precedes the reopen. The status command and `doctor` read the mark when the record has a started mark, so they agree
    with the page.
  - *Boundary.* On the supervisor's path the finished mark follows `archive_step`, which runs only once the tracker covers the closing entry, and a resume that
    takes the done-once return still writes it, in the live record and the kept archive. `lead-close` writes it after the archive, before `closed.json`, and
    renders again after it. A stop after the mark, in `friction_step` or after `closed.json`, reads as finished by decision, because only local follow-up is
    left, and the status command still reports it.
  - *Rows, run through the real commands, not composed by hand.* Supervisor: stopped at the publisher wait, in `archive_step` before `closed.json`, and after
    `closed.json` before the mark, each read as unfinished; in `friction_step`, read as finished. `lead-close`: stopped before the final render and in the
    archive, read as unfinished; after `closed.json`, read as finished. Each of those rows read as unfinished pins `lead-run --resume` as the named command, runs it, and
    asserts the close finishes with the tracker covered and the mark in the live record and the archive. An early close with an unmet finish line, journaled by hand and stopped in `lead-close` before the mark, pins `lead-close`, runs it, and asserts the mark in the
    live record and the archive and that it reports unposted updates; a resumed supervisor on that record holds and writes no mark. An older `closed.json` with no
    id is still refused and still returns its archive. A clean close on each path, and a clean second close
    on each path, asserted on the live record, the archived copy and the rendered page. A record written without the marks. A close waiting on the host
    outbox under a live supervisor, which shows the existing Needs you box and doesn't name a resume. A delivery closed, reopened, closed again and stopped.
- **V26-3100.** Add no churn guard, since noise exceeds the margin, and record that on the ticket. Cut the extraction if the inspection can do without it.
  Cache it only if keyed on the sha256 of both the archive and metadata bytes read at inspection, never on a path or mtime, with nothing cached across a
  verification failure, and with rows that replace the archive and then the metadata at one path between two inspections and assert refusal or the new
  version, and that an extraction altered on disk isn't trusted. Otherwise record no cache and close with the guard decision.
- **Design choices left to a lane.** V26-3103's carry-over and V26-3104's other two items are proposed to the lead at ticket start, before any code.
- **Bounded flake work.** V26-3092 and the flakes in V26-3109 each get five full runs at the recorded load of about 10, with each attempt's result and measured load on
  the ticket. These runs are exempt from the one-suite-per-head rule and still use the shared slot. A failure that won't reproduce in those runs may be declared
  on the binding's `flakes` only by exact failure ids, as anchored patterns with no wildcard across a module and no whole-check form. A failure with no readable id, such as
  V26-3109's unit-limit timeout or its lost page-lane output, isn't declared, and stays open as a follow-up item with what was tried. A declaration's `ref` is an open follow-up item, not the ticket being closed,
  and counts under the finish line's deferrals.
- **Posture.** Each ticket's own posture applies. Where it names none, `test-first`, and `characterization-first` for V26-2951, V26-3092 and V26-3067.

## Sensors and shared resources

- Each story's sensors in its lane, plus `scripts/validate-registry.py`, `tests/test_registry.py` and `scripts/update-provenance.py --check`.
- The queue's selected checks at each merge, and the full macOS run on Python 3.11 before the epic closes.
- One full suite per hand-off head, with four workers at most, and reviewers run affected modules only. Nobody uses `pkill` or `killall`.
- The in-app port is held through its lease. A test that needs a held port binds its own ephemeral one and doesn't stub the holder lookup.
- A pin proved red refreshes the file's digest with it, so that the failure is the pin's own.

## Approvals ahead

- `LINEAR_API_KEY` and `GITHUB_TOKEN` exported in the lead's shell, for the views' snapshots throughout and V26-3105's online `doctor`.
- Filing the follow-ups: Athena's side of V26-2986 and V26-2807, this repository adopting V26-3094's key, and any the Bounded flake work decision produces.
- Any tag. The record-shape tickets (V26-3093, V26-3099, V26-3108, V26-3005, V26-3089, V26-2807, V26-3109) mean the next release goes out
  first as a candidate, applied in one adopter.
