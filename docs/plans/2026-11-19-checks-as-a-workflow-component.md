---
reviewSlug: checks-as-a-workflow-component
date: 2026-11-19
execution_posture: test-first
context: A check that passes in a lane counts for nothing later. The queue runs it again, and the binding's guards run only in the queue, so a lane meets them after review and acceptance. This plan adds three primitives (one reproducible run record, one applicability answer, measured cost) and changes defaults on top of them. Every default can be stepped off with a reason, which is recorded. A command refuses only to protect the shared base.
---

# Checks as a workflow component

The epic is [V26-3645](https://linear.app/v26-technologies/issue/V26-3645). It takes in V26-3428 (guards run where a
change is checked) and V26-2348 (cheap checks first). V26-3652 (formatting) waits on this plan and is out of scope.

## The problem, as the code has it

In V26-3614 the lane ran seven test modules. Its branch then merged one base commit, 2b7e502b, which changed only
`.agents/deliveries/V26-3623.json`. The queue ran all seven again. It then failed on the guard
`tests.test_linear_qualification`, which the lane had never run, after three review rounds and acceptance. Skipping a
second queue run took an operator decision made by hand.

The parts a model of checks needs already exist, but nothing joins them:

- **Definitions.** `selection.catalogue`, `alwaysRun` and `fallback` in `.agents/orchestration.json` share one id
  namespace. `check_definition` in `delivery_state.py` digests one entry. Lane `gates` in `.agents/run-delivery.json`
  are a separate list of argv entries with no ids in common with any of them.
- **One way of running.** The queue runs each check in a fresh worktree of the commit (`Runner.checkout`), prepared
  by the binding's `prepare` steps. The environment it builds (`Runner.environment`) is the queue process's whole
  environment minus git's location variables, plus the binding's `environment` and the check's profile. A lane runs
  its checks in its own working checkout, with whatever environment its session has.
- **Two reuse tests in `merge_queue.py`.**
  - `reusable` accepts a lane's hand-off evidence only at the exact same tree, and only for ids in `reusableChecks`,
    which may name `alwaysRun` and `fallback` checks only.
  - `carry_plan` keeps the queue's own earlier pass across a base move that changes nothing the check reads. It uses
    `selection.readers` entries marked `complete`, plus `selection.unread`.
- **No lane records.** run-delivery never writes the hand-off evidence that `reusable` needs. `gates.json` records
  the gate run at HEAD, without definition digests. `handoff --checks` lists ids the lane says it ran, which is a
  claim, not a record.
- **Guards.** `selection.guards` (`tests.test_linear_qualification`, `tests.test_registry`) are added to every lead
  selection, so they run in the queue only. Their measured medians in the V26-3606 record are 29 s and 23 s.
- **Cost.** `meta.checkHistory` keeps each check's last five passing durations. Nothing outside the queue's progress
  view reads them.

This repository's `durable-text` lane gate is a stopgap for the missing guard run. Its `catalogue` gate runs the
3-second `StandingBinding` class, so a lane meets the catalogue check before the queue does. A pin in
`tests/test_own_deliveries.py` (`StandingBinding.test_every_lane_gate_runs_the_catalogue_and_durable_text_guards`)
holds both in place.

The paired TypeScript harness has no queue, carry or reuse code. A search of `product-runtime/` and
`agent-delivery-harness/packages` finds none. This plan changes Python only.

## What a reader should be able to do

- A lane runs any check by id with one command. The command runs it the way the queue does and records the run. The
  hand-off carries the records.
- The queue reuses a recorded pass whenever it can show that its own run would see the same inputs: the same
  definition, the same content on every path the check reads, the same declared environment and the same host
  tools. Otherwise it journals what differs.
- A lead sees, before accepting, which checks have a pass that applies at the handed head, which don't and why, and
  how long each takes. Where the paths can't be proven unchanged, the lead can grant reuse with a reason, and the
  grant is recorded.
- A lane meets the binding's guards at `gate`, before review. A lead push can't publish a base that a guard fails
  unless the lead records why.

## Finish line

1. In this repository, a lane's recorded pass of a catalogue check is reused by the queue in both cases where the
   check's inputs are unchanged: at an identical tree, and across a base move that changes no path in the check's
   known read set. Each reuse is journaled as `check.reused`.
2. A lane record made with a different declared environment, different host tools, or in the lane's own checkout
   isn't reused. The queue's note names what differs: the variable names, the tool names or the checkout.
3. A V26-3614-shaped candidate reuses its seven passes when the lead accepts it with a reuse grant and a reason. The
   reuse is journaled as `check.reused`, with `because` naming the lead's `decision.recorded` entry.
4. `gate` runs the binding's guards first by default, and this repository's stopgap `durable-text` gate is gone.
5. Apart from Decisions 4 and 13, settings that set none of the new fields behave as before. Every existing test
   passes unchanged, apart from the pins listed under Stories.
6. The full suite passes on macOS with Python 3.11 within `tests/suite-budget.json`.

## Decisions

1. **Primitives, defaults and recorded overrides, with no prescribed rules.** Each gap is closed by a record, an
   answer or a measurement that agents read where they decide. Commands do the common thing by default. An agent
   steps off a default with `--why`, which records its reason. A command refuses only to protect the shared base,
   which is Decision 7's `lead-push` refusal. Every story's criteria include that no fixed rule replaces the agent's
   judgement.
2. **One run record.** Every tooling run of a check records these fields:
   - `check` and `definition`, the `check_definition` digest from the binding in that head's tree;
   - `head` and `tree`;
   - `result`, `seconds` and `at`;
   - `where`: `runner` for a fresh prepared worktree, or `checkout` for the lane's own checkout;
   - `environment`: a digest of the declared environment's values, plus its variable names;
   - `host`: the platform (system, release and machine), plus a digest of each tool Decision 12 names.

   Queue rows gain `environment` and `host`, and keep the rest of their shape. Only the tooling writes records, from
   runs it made. An agent can't type one in. Records never hold a variable's value, only its name and the digest.
3. **One applicability answer.** `applies(record, check, tree, binding, facts)` in `merge_queue.py` returns applies,
   or every reason it doesn't. `facts` are the environment and host the queue's own run would have. A record applies
   at `tree` when all of these hold:
   - its result is `passed`;
   - its `where` is `runner`;
   - its definition digest equals the check's digest under `binding`;
   - its `environment` digest and its `host` facts equal `facts`;
   - its head is present, with the record's tree;
   - the paths that differ between the record's tree and `tree` (`git diff --name-only --no-renames`) are empty,
     **or** all match `selection.unread`, **or** none is in the check's known read set.

   The binding file always counts as read. A read set is known only when every `readers` entry naming the check is
   `complete`, as today. `reusable` and `carry_plan`'s per-check test both call `applies`. `carry_plan` builds its
   record from the queue's row and the synced head that row was checked at. A row recorded before this change has no
   `environment` or `host`, and carry treats those as matching, as it does today.

   Each caller keeps its outer conditions:
   - `reusable` never reuses a prerequisite of another selected check;
   - `carry_plan` carries nothing when the move overlaps the candidate's own diff, and drops a carried prerequisite
     whose dependant runs again;
   - a batch is never carried;
   - an `alwaysRun` check carries only on a move that is wholly unread.

   Today's carry compares no definition digest. A changed definition means a changed binding file, which already
   stops every carry, so adding the comparison changes no carry outcome. What changes is `reusable`: a lane's record
   now applies across a base move, not only at an identical tree.
4. **Lane records are reusable by default (operator, 2026-11-19).** This repository is the only adopter, so the
   long-term shape wins over upgrade cost.
   - Every check whose definition the tree determines is reusable. That means its argv, its profile's environment
     and the binding's `prepare` steps use no placeholder other than `{treeSha}`. For any other check, `applies` names
     the placeholder as the reason. These are the validations `reusableChecks` enforces today, turned from binding
     errors into stated facts.
   - A binding can exclude a check with `reuse.exclude: [{"check": <id>, "why": <text>}]`. The queue's note quotes
     the `why`, and `doctor` reports an unknown id.
   - `reusableChecks` is removed. A binding that sets it is refused with `binding_invalid`, and the fix line names
     `reuse.exclude`.
   - The release that carries CHK-2 is a major version.

   This repository sets no `reuse.exclude`. A prerequisite such as `hosted.install` is never reused in a run where
   another selected check needs it.
5. **A lead grant covers the paths that can't be proven unchanged (operator, 2026-11-19).** `lead-accept --reuse
   <ids> --why <text>` records the grant in the candidate's `leadSelection` at the handed head, and journals
   `decision.recorded`.
   - A grant may name only a check the hand-off carries a record for. `select` rejects any other id as invalid input.
   - For a granted id, `applies` skips only the path comparison. A record made in the lane's own checkout, under
     another definition, environment or host, or that failed, still runs.
   - When the queue reuses a granted record, `check.reused`'s `because` names the decision.
6. **Guards run first at `gate` by default (operator, 2026-11-19).** `gate` runs `selection.guards` through the
   runner (Decision 12), before the repository's gates. It prints each guard's measured median and the prepare time,
   and stops at the first failure. A guard with a record that applies at the same tree isn't run again. `gate
   --skip-guards --why <text>` skips them and records the skip and its reason in `gates.json`. `handoff` carries that
   reason into the hand-off, so the lead sees it at acceptance. `handoff`, `review-prepare` and `lead-accept` show
   each guard's record at the head. They don't run guards themselves.
7. **`lead-push` runs guards against what it publishes.** It uses the runner, because the lead's own checkout isn't
   assumed to be prepared. It refuses on a failure, naming the guard, which protects the shared base. `--skip-guards
   --why <text>` publishes anyway and journals `decision.recorded`, for the case of a lead pushing a fix to a broken
   guard itself.
8. **Cost is measured, not declared.** No `cost` field is added, and the guard ceiling V26-3428 proposed is dropped.
   Commands show `meta.checkHistory` medians where a choice is made.
9. **Lane gates may name a check by id.** A `gates` entry in `run-delivery.json` may be a string naming a catalogue,
   `alwaysRun` or `fallback` id. It runs through the runner, from that definition. Inline entries run in the lane's
   checkout, as today. `doctor` reports an unknown id. It resolves a program by path only when that path is tracked
   or on `PATH`. An untracked relative program, such as `.venv/bin/python`, is reported as made by `prepare` rather
   than lost, so a fresh clone still passes `doctor`.
10. **No new journal kinds.** Reuse uses `check.reused` and `check.carried` as they are, and overrides use
    `decision.recorded`. `tests/fixtures/journal-kinds/vectors.json` doesn't change.
11. **Plan review has no round bound.** It runs until its lenses align, or until the operator ends it.
12. **A reusable run is one the queue would reproduce.** `merge_queue.py run-checks --repo <checkout> --head
    <commit> --checks <ids>` runs checks the way the queue does. It makes a fresh worktree under the binding's
    `workRoot`, runs `prepare`, builds the environment as Decision 13 says, and runs each check with the queue's own
    code. It prints one record per id (Decision 2) with `where: runner`, and writes no delivery state, as `evidence`
    doesn't today.
    - Host facts are the platform, plus the sha256 of each tool's resolved executable. The tools are argv[0] of the
      check and of each `prepare` step, plus any names in `reuse.tools`, resolved on the declared `PATH` with links
      followed. This repository lists `git`, `node` and `npm`.
    - The queue computes the same facts once per run, after `prepare`, and passes them to `applies`.
    - `merge_queue.py evidence` is removed. `run-checks` is how a lane gets a record.
13. **The queue's environment is declared, not inherited.** Both the queue and `run-checks` build a check's
    environment from these sources only:
    - a fixed baseline of inherited names: `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `TMPDIR`, `LANG`, and every
      `LC_*` name;
    - the names the binding lists in `inherit`;
    - the binding's `environment`, the profile, the git identity and the hooks directory, as today.

    Everything else in the process environment is dropped. A check that needed a dropped variable fails until the
    binding lists it in `inherit`, and the failure output names the dropped variables. This repository lists none to
    start with. Its own delivery's queue runs show whether that holds, and a miss becomes an `inherit` entry with its
    reason.

## Amendments made during delivery

None yet.

## Out of scope

- **What no record can capture.** That means nondeterminism (network, clock, randomness, concurrency and flaky
  tests), external services, and programs a check calls that neither its argv nor `reuse.tools` names. These stay
  what `fullHealth` catches after a merge. The queue's own runs share every one of them, so reuse adds no gap beyond
  the queue's own.
- Declaring read sets or `unread` paths for this repository's catalogue. Decision 5 covers V26-3614's case without
  them, and which paths are truly unread is a judgement for a later change.
- Inferring read sets from imports or file access.
- Keeping a prepared runner worktree between runs. Each run prepares afresh, as the queue does, and commands show the
  prepare time.
- Running guards inside `review-prepare` or `lead-accept`. Both show the guard records.
- Formatting (V26-3652), shared-machine capacity (V26-3638), and any change to the TypeScript harness.
- Changing the existing `handoff` refusal when the gates haven't passed at the head.

## Approval Handoffs

- The operator approves this plan once its review aligns.
- After approval, the five stories are filed under V26-3645, and their tracker keys replace `CHK-1` to `CHK-5` in
  this file before `lead-setup`.
- The operator chooses the delivery base, `main` or an integration branch, at `lead-setup`.

## Execution posture and sensors

The plan's posture is test-first. CHK-2 is characterization-first, because it moves `reusable` and `carry_plan`
behind one function and has to show that their current outcomes hold before it widens `reusable`.

Sensors, run through `.venv/bin/python scripts/run-tests.py -p <module>` for each story's own modules:

- CHK-1, CHK-2 and CHK-5: `tests/test_merge_queue.py`. CHK-1 and CHK-2 also run `tests/test_orchestration_state.py`.
- CHK-3: `tests/test_run_delivery.py`.
- CHK-4: `tests/test_run_delivery.py`, `tests/test_own_deliveries.py` and `tests/test_linear_qualification.py`.
- Every story: `scripts/validate-registry.py` and `scripts/update-provenance.py --check`.
- CHK-1's declared environment is also exercised by every later story's own queue run in this delivery.
- At the finish line: the full suite on macOS with Python 3.11, `--workers 4 --budget tests/suite-budget.json`.

New cases go into existing test modules and reuse fixtures those classes already build, so no catalogue entry is
added and the suite stays inside its budget. Each story refreshes `provenance.lock.json` with the provenance writer.
A conflict in that file alone is resolved as `AGENTS.md` says.

## Scenarios

1. **Runner record.** `run-checks` at a head writes one record per id with `where: runner`, the definition digest,
   the environment digest and names, and the host facts. A failure is recorded with its log, and the command exits
   non-zero (CHK-1).
2. **Declared environment.** A variable outside the baseline and `inherit` doesn't reach a check, in the queue or in
   `run-checks`. A check that fails without it has the dropped names in its failure output. Listing the name in
   `inherit` makes it pass (CHK-1).
3. **Same facts.** For the same head, the queue's facts equal `run-checks`' facts on the same machine (CHK-1).
4. **Characterization.** Every existing `reusable` and `carry_plan` outcome in `tests/test_merge_queue.py` holds after
   both call `applies` (CHK-2).
5. **Lane record, identical tree.** The record applies, and the queue journals `check.reused` (CHK-2).
6. **Lane record across an unread base move.** The base merge changes only paths matching `selection.unread`, and the
   record applies (CHK-2).
7. **Lane record across a move outside the read set.** The check has a complete read set and the move touches none
   of it, so the record applies (CHK-2).
8. **A move inside the read set, or an unknown read set with differing trees.** The check runs. The note names the
   path, or says the read set is unknown (CHK-2).
9. **Facts differ.** One record differs from the queue's facts in an environment variable, one in a tool digest,
   and one has `where: checkout`. Each runs, and the note names the differing variable names, the tool names or the
   checkout. It never prints a value (CHK-2).
10. **Other reasons.** A changed definition, a failed record, a prerequisite of another selected check, a binding
    file change, an excluded check (with its `why` quoted) and a `{baseHead}` placeholder each run with their own
    reason. A binding that still sets `reusableChecks` is refused, naming `reuse.exclude` (CHK-2).
11. **Grant.** With trees that differ and an unknown read set, `--reuse` and `--why` make the record apply, and
    `check.reused`'s `because` names the `decision.recorded` entry. A granted record that differs in facts or
    definition, or that failed, still runs (CHK-2 for the queue side, CHK-4 for `lead-accept`).
12. **A lane runs checks by id.** `check <key> <ids>` calls `run-checks` at HEAD and keeps the records in the story's
    review folder (CHK-3).
13. **Hand-off evidence.** `handoff` attaches, for each reusable check, the latest passing record whose head is the
    handed head or an ancestor of it. It shows each `--checks` id as recorded, with its head, or as named only, and
    refuses neither (CHK-3).
14. **Gate by id.** A `gates` entry `"validate-registry"` runs through the runner and leaves a record. An inline gate
    leaves a `where: checkout` record. `doctor` reports an unknown id, and a fresh clone with no `.venv` passes
    `doctor` (CHK-3).
15. **Guards at gate.** A diff that `tests.test_linear_qualification` rejects fails `gate`, naming the guard, before
    the repository's gates run. At the same tree, a second `gate` reuses the guard pass. `--skip-guards --why`
    records the skip and its reason, and the hand-off shows them (CHK-4).
16. **`lead-push`.** A lead commit that a guard rejects is refused, and nothing is published. With `--skip-guards
    --why` it publishes, and `decision.recorded` is journaled (CHK-4).
17. **What the lead sees.** `lead-accept`'s proposal shows, for each selected check, applies or why not, plus its
    measured median (CHK-4).
18. **Order.** In a queue run, guards start first, then the rest longest first by median, with checks that have no
    history after those that do. A failing guard ends the run before any other check starts. A passing run records
    the same results as before (CHK-5).
19. **Nothing new set.** Settings with no `reuse.exclude`, no `inherit`, no guards and no id gates gate and carry as
    before. The only changes are Decisions 4 and 13 (every story).

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | CHK-1, CHK-2, CHK-5 | `skills/orchestrate-work/scripts/merge_queue.py`, `skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/schemas.json`, `skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/SKILL.md`, `tests/test_merge_queue.py` and `tests/test_orchestration_state.py` |
| 1 | 2 | CHK-3 | `skills/run-delivery/scripts/run_delivery.py`, `skills/run-delivery/references/config.md`, `skills/run-delivery/SKILL.md` and `tests/test_run_delivery.py` |
| 2 | 2 | CHK-4 | `.agents/run-delivery.json`, `.agents/orchestration.json` and `tests/test_own_deliveries.py` |

`provenance.lock.json` is shared, and the provenance writer resolves it. CHK-3 starts once CHK-1's `run-checks` has
merged, and runs alongside CHK-2. CHK-4 needs `applies` from CHK-2 and the records from CHK-3.

## Stories

### CHK-1. Run a check the way the queue does, anywhere, with the facts that make it reproducible

Lane 1, tier 1, not blocked. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/references/orchestration-guide.md`, `tests/test_merge_queue.py` and
`tests/test_orchestration_state.py`.

- Declared environment for the queue and the runner, with `inherit` and dropped names in failure output
  (Decision 13).
- `run-checks`, with records and host facts as Decisions 2 and 12 describe, and `reuse.tools`. `evidence` is
  removed.
- Queue rows gain `environment` and `host`. The queue computes its facts once per run, after `prepare`.
- Pins: the `evidence` cases in `tests/test_merge_queue.py` and `tests/test_orchestration_state.py` move to
  `run-checks`. `delivery_state.py`'s mention of `evidence` names `run-checks`.
- No fixed rule replaces the agent's judgement. The facts are recorded, and nothing is refused on them here.

### CHK-2. Answer whether a recorded pass applies, and reuse a lane's pass by default

Lane 1, tier 1, after CHK-1. Characterization-first. Files: `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/scripts/delivery_state.py`, `skills/orchestrate-work/references/schemas.json`,
`skills/orchestrate-work/references/orchestration-guide.md`, `skills/orchestrate-work/SKILL.md`,
`tests/test_merge_queue.py` and `tests/test_orchestration_state.py`.

- `applies` as Decision 3 describes. `reusable` and `carry_plan`'s per-check test call it, and each keeps its outer
  conditions.
- Reuse by default, `reuse.exclude`, and `reusableChecks` refused (Decision 4). Update the schema, the guide's binding
  table, and the guide's lane hand-off section, which today tells lanes to paste `evidence` output.
- `select --reuse <ids> --why <text>` records the grant in `leadSelection` and journals `decision.recorded`, and
  `applies` honours it (Decision 5).
- Pins: the `reusableChecks` cases in `tests/test_merge_queue.py` become `applies` reasons, plus one refusal.
- No fixed rule replaces the agent's judgement. The queue's default is reuse when `applies` says so, and the grant is
  the recorded override.

### CHK-3. Record a lane's check runs, and hand off with them

Lane 2, tier 1, after CHK-1. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/references/config.md`, `skills/run-delivery/SKILL.md` and `tests/test_run_delivery.py`.

- `check <key> <ids>` calls `run-checks` at HEAD and keeps the records in the story's review folder. It prints each
  check's median and the prepare time.
- `gate` keeps a record for each gate inside `gates.json`: `runner` for a gate named by id, `checkout` for an inline
  one (Decision 9). `doctor` resolves gates named by id.
- `handoff` attaches records as Scenario 13 describes, in the evidence shape `delivery_state.py handoff --evidence`
  already takes.
- No fixed rule replaces the agent's judgement. Nothing is refused for a missing record.

### CHK-4. Run guards where a change is checked, and show the lead what still applies

Lane 2, tier 2, after CHK-2 and CHK-3. Test-first. Files: `skills/run-delivery/scripts/run_delivery.py`,
`skills/run-delivery/references/config.md`, `skills/run-delivery/SKILL.md`, `tests/test_run_delivery.py`,
`.agents/run-delivery.json`, `.agents/orchestration.json` and `tests/test_own_deliveries.py`.

- Guards at `gate` (Decision 6) and at `lead-push` (Decision 7). `handoff`, `review-prepare` and `lead-accept` show
  each guard's record at the head.
- `lead-accept --reuse <ids> --why <text>` passes the grant to `select`, and its proposal shows Scenario 17's facts.
- This repository:
  - removes the `durable-text` gate, which `gate`'s run of the `tests.test_linear_qualification` guard covers;
  - keeps the `catalogue` gate, which runs one 3-second class. The queue joins `tests.test_own_deliveries` through a
    reader rather than as a guard, and making it a guard would add a 31-second median to every candidate;
  - sets `reuse.tools` to `git`, `node` and `npm`.

  `GUARDS` in `tests/test_own_deliveries.py` doesn't change. The pin
  `StandingBinding.test_every_lane_gate_runs_the_catalogue_and_durable_text_guards` is replaced by one that pins the
  `catalogue` gate and the absence of `durable-text`.
- No fixed rule replaces the agent's judgement. Every default here has a `--why` override, and only `lead-push`
  refuses.

### CHK-5. Start guards first and the longest checks next

Lane 1, tier 1, after CHK-2. Test-first. Files: `skills/orchestrate-work/scripts/merge_queue.py`,
`skills/orchestrate-work/references/orchestration-guide.md` and `tests/test_merge_queue.py`.

- `run_checks` orders guards first, then the rest longest first by `meta.checkHistory` median. Prerequisites still
  come before the checks that need them, and a failing guard ends the run (Scenario 18).
- No fixed rule replaces the agent's judgement. Ordering changes nothing about which checks run.
